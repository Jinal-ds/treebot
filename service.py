"""
Service layer — business logic. Router handlers call into these functions;
these functions call repository.py (Postgres), vectorstore.py (ChromaDB), and
the Hugging Face LLM. Nothing here talks HTTP except raising HTTPException,
which is the pragmatic FastAPI convention for "this didn't work" in a small app.
"""

import io
import os

import httpx
import qrcode
from fastapi import HTTPException

import db
import repository
import vectorstore

HF_TOKEN = os.environ.get("HF_TOKEN", "")
HF_MODEL = os.environ.get("HF_MODEL", "meta-llama/Llama-3.1-8B-Instruct")
HF_ROUTER_URL = "https://router.huggingface.co/v1/chat/completions"

# Used to build the link each tree's QR code points to. Must be the public
# URL the QR codes will actually be scanned against (e.g. the Render URL in
# production) — override via env when deploying.
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")

CURRENT_TREE_TOP_K = 6
OTHER_TREES_TOP_K = 3

LANGUAGE_NAMES = {"en": "English", "gu": "Gujarati", "hi": "Hindi"}


async def startup():
    await db.init_pool()
    vectorstore.init_store()
    vectorstore.sync_knowledge()
    await repository.refresh_qr_urls(BASE_URL)


async def shutdown():
    await db.close_pool()


def build_system_prompt(tree: dict, own_chunks: list, retrieved: list, language: str = "en") -> str:
    if own_chunks:
        own_block = "\n".join(f"- (source: {meta['source']}) {doc}" for meta, doc in own_chunks)
    else:
        own_block = "(no additional knowledge chunks found for this tree yet)"

    if retrieved:
        others_block = "\n".join(f"- {meta['common_name_en']}: {doc}" for meta, doc in retrieved)
    else:
        others_block = "(no other tree data retrieved for this question)"

    language_name = LANGUAGE_NAMES.get(language, "English")

    return f"""You are a friendly, knowledgeable botanical guide stationed physically at ONE specific tree in a park: the {tree['common_name_en']} ({tree['botanical_name']}).

Here are the core facts you know about THIS tree:
- English name: {tree['common_name_en']}
- Gujarati name: {tree['common_name_gu']}
- Hindi name: {tree.get('common_name_hi', '')}
- Botanical name: {tree['botanical_name']}
- Family: {tree['family']}
- Native status: {tree['native_status']}
- Location: {tree['area_name']} (lat {tree['latitude']}, long {tree['longitude']})
- Flowering season: {tree['flowering_season']}
- Short description: {tree['description']}

Here is additional detailed knowledge about THIS tree, collected from various sources — you may mention the source (e.g. "According to {{source}}...") if the visitor asks where a fact comes from:
{own_block}

The visitor's question was used to search our tree database, and these OTHER trees came back as potentially relevant (use them ONLY if the visitor is asking to compare, otherwise ignore them):
{others_block}

RULES YOU MUST FOLLOW:
1. Only answer questions about this tree, trees/plants/botany in general, or direct comparisons with the other trees listed above. Comparison questions are welcome and expected.
2. If a visitor asks anything unrelated to trees, plants, or nature (general knowledge, people, companies, coding, math, current events, etc.), politely decline and steer the conversation back to the tree. Do not answer the off-topic part even partially, even if it seems harmless.
   Example: "Who is the CEO of Apple?" -> "I'm just here to talk about this {tree['common_name_en']} tree and its plant friends in the park — I can't help with questions outside that. Would you like to know about its uses, or how it compares to another tree here?"
3. Keep answers conversational, concise, and friendly, like a real guide speaking to a visitor — not a textbook.
4. If you don't know something specific, say so honestly rather than inventing details.
5. Respond ONLY in {language_name}, regardless of what language the facts above are written in. Translate naturally — don't just transliterate.
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


async def list_trees() -> list:
    return await repository.list_trees()


async def get_tree(tree_id: str) -> dict:
    tree = await repository.get_tree(tree_id)
    if not tree:
        raise HTTPException(status_code=404, detail=f"No tree with id '{tree_id}'")
    return tree


async def chat(tree_id: str, session_id: str, message: str, language: str):
    tree = await repository.get_tree(tree_id)
    if not tree:
        raise HTTPException(status_code=404, detail=f"No tree with id '{tree_id}'")

    session = await repository.get_or_create_session(session_id, tree_id)
    own_chunks = vectorstore.retrieve_tree_chunks(message, tree_id, top_k=CURRENT_TREE_TOP_K)
    retrieved = vectorstore.retrieve_related_trees(message, exclude_tree_id=tree_id, top_k=OTHER_TREES_TOP_K)
    system_prompt = build_system_prompt(tree, own_chunks, retrieved, language)

    reply = await call_llm(system_prompt, session["chat_json"], message)
    await repository.append_turn(session_id, message, reply)
    return reply


async def qr_png(tree_id: str) -> bytes:
    tree = await repository.get_tree(tree_id)
    if not tree:
        raise HTTPException(status_code=404, detail=f"No tree with id '{tree_id}'")

    target = tree["qr_url"] or f"{BASE_URL.rstrip('/')}/t/{tree_id}"
    img = qrcode.make(target)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


async def record_scan(tree_id: str, user_agent: str, referrer: str) -> dict:
    tree = await repository.get_tree(tree_id)
    if not tree:
        raise HTTPException(status_code=404, detail=f"No tree with id '{tree_id}'")
    await repository.log_scan(tree_id, user_agent, referrer)
    return tree


async def scan_analytics() -> list:
    return await repository.get_scan_counts()
