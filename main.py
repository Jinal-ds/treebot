"""
AI Tree Chatbot — Phase 1 backend (v2: Postgres + ChromaDB).

- PostgreSQL holds permanent tree data AND live chat sessions (one shared DB).
- ChromaDB (embedded mode) does real retrieval: given a visitor's question,
  we pull back the most relevant OTHER tree profiles for comparisons, instead
  of stuffing every tree into every prompt.
- Still a single Uvicorn worker for Phase 1 — see the compatibility note in
  the project docs about what must change together if that ever becomes N workers.
"""

import io
import os
from contextlib import asynccontextmanager
from typing import List

import httpx
import qrcode
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

import db
import vectorstore

load_dotenv()

BASE_DIR = os.path.dirname(__file__)

HF_TOKEN = os.environ.get("HF_TOKEN", "")
HF_MODEL = os.environ.get("HF_MODEL", "meta-llama/Llama-3.1-8B-Instruct")
HF_ROUTER_URL = "https://router.huggingface.co/v1/chat/completions"

# Used to build the link each tree's QR code points to. Must be the public
# URL the QR codes will actually be scanned against (e.g. the Render URL in
# production) — override via env when deploying.
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")

RETRIEVAL_TOP_K = 3


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init_pool()
    vectorstore.init_store()
    trees = await db.all_trees()
    vectorstore.seed_if_empty(trees)
    await db.refresh_qr_urls(BASE_URL)
    yield
    await db.close_pool()


app = FastAPI(title="AI Tree Chatbot — Phase 1 (Postgres + ChromaDB)", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    session_id: str
    message: str


class ChatResponse(BaseModel):
    reply: str


def build_system_prompt(tree: dict, retrieved: List[tuple]) -> str:
    if retrieved:
        others_block = "\n".join(f"- {meta['common_name_en']}: {doc}" for meta, doc in retrieved)
    else:
        others_block = "(no other tree data retrieved for this question)"

    return f"""You are a friendly, knowledgeable botanical guide stationed physically at ONE specific tree in a park: the {tree['common_name_en']} ({tree['botanical_name']}).

Here is everything you know about THIS tree:
- English name: {tree['common_name_en']}
- Gujarati name: {tree['common_name_gu']}
- Botanical name: {tree['botanical_name']}
- Family: {tree['family']}
- Native status: {tree['native_status']}
- Location: {tree['area_name']} (lat {tree['latitude']}, long {tree['longitude']})
- Description: {tree['description']}
- Uses: {tree['uses']}
- Wood quality: {tree['wood_quality']}
- Flowering season: {tree['flowering_season']}
- Fun fact: {tree['fun_fact']}

The visitor's question was used to search our tree database, and these OTHER trees came back as potentially relevant (use them ONLY if the visitor is asking to compare, otherwise ignore them):
{others_block}

RULES YOU MUST FOLLOW:
1. Only answer questions about this tree, trees/plants/botany in general, or direct comparisons with the other trees listed above. Comparison questions are welcome and expected.
2. If a visitor asks anything unrelated to trees, plants, or nature (general knowledge, people, companies, coding, math, current events, etc.), politely decline and steer the conversation back to the tree. Do not answer the off-topic part even partially, even if it seems harmless.
   Example: "Who is the CEO of Apple?" -> "I'm just here to talk about this {tree['common_name_en']} tree and its plant friends in the park — I can't help with questions outside that. Would you like to know about its uses, or how it compares to another tree here?"
3. Keep answers conversational, concise, and friendly, like a real guide speaking to a visitor — not a textbook.
4. If you don't know something specific, say so honestly rather than inventing details.
"""


async def call_llm(system_prompt: str, history: list, user_message: str) -> str:
    if not HF_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="HF_TOKEN is not set on the server. Add it as an environment variable.",
        )

    messages = [{"role": "system", "content": system_prompt}]
    messages += history
    messages.append({"role": "user", "content": user_message})

    payload = {"model": HF_MODEL, "messages": messages, "max_tokens": 400, "temperature": 0.4}
    headers = {"Authorization": f"Bearer {HF_TOKEN}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(HF_ROUTER_URL, json=payload, headers=headers)

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502, detail=f"LLM provider error ({resp.status_code}): {resp.text[:300]}"
        )
    data = resp.json()
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as exc:
        raise HTTPException(status_code=502, detail=f"Unexpected LLM response shape: {data}") from exc


@app.get("/api/trees")
async def list_trees():
    return await db.list_trees()


@app.get("/api/tree/{tree_id}")
async def get_tree(tree_id: str):
    tree = await db.get_tree(tree_id)
    if not tree:
        raise HTTPException(status_code=404, detail=f"No tree with id '{tree_id}'")
    return tree


@app.post("/api/chat/{tree_id}", response_model=ChatResponse)
async def chat(tree_id: str, req: ChatRequest):
    tree = await db.get_tree(tree_id)
    if not tree:
        raise HTTPException(status_code=404, detail=f"No tree with id '{tree_id}'")

    session = await db.get_or_create_session(req.session_id, tree_id)
    retrieved = vectorstore.retrieve_related_trees(req.message, exclude_tree_id=tree_id, top_k=RETRIEVAL_TOP_K)
    system_prompt = build_system_prompt(tree, retrieved)

    reply = await call_llm(system_prompt, session["chat_json"], req.message)
    await db.append_turn(req.session_id, req.message, reply)
    return ChatResponse(reply=reply)


@app.get("/api/analytics/scans")
async def scan_analytics():
    return await db.get_scan_counts()


@app.get("/api/qr/{tree_id}.png")
async def qr_code(tree_id: str):
    tree = await db.get_tree(tree_id)
    if not tree:
        raise HTTPException(status_code=404, detail=f"No tree with id '{tree_id}'")

    target = tree["qr_url"] or f"{BASE_URL.rstrip('/')}/t/{tree_id}"
    img = qrcode.make(target)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.get("/t/{tree_id}")
async def scan_tree(tree_id: str, request: Request):
    """The URL every tree's QR code actually points to. Logs a scan event for
    analytics (which tree is being scanned, and how often), then hands off to
    the same SPA frontend that '/' serves, which reads the tree id from the
    path and loads that tree's details + chat."""
    tree = await db.get_tree(tree_id)
    if not tree:
        raise HTTPException(status_code=404, detail=f"No tree with id '{tree_id}'")

    await db.log_scan(tree_id, request.headers.get("user-agent"), request.headers.get("referer"))
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


@app.get("/analytics")
def analytics_page():
    return FileResponse(os.path.join(BASE_DIR, "static", "analytics.html"))


@app.get("/healthz")
async def healthz():
    trees = await db.list_trees()
    return {
        "status": "ok",
        "trees_in_postgres": len(trees),
        "chroma_docs": vectorstore.get_collection().count(),
    }
