"""
Service layer — business logic. Router handlers call into these functions;
these functions call repository.py (Postgres), vectorstore.py (ChromaDB), and
the LLM (via an OpenAI-compatible chat completions endpoint). Nothing here
talks HTTP except raising HTTPException, which is the pragmatic FastAPI
convention for "this didn't work" in a small app.

LLM provider: Groq (api.groq.com), not Hugging Face — switched after HF's
free monthly inference credits ran out mid-project. Groq's free tier has no
card requirement and, per direct testing, qwen/qwen3.8-27b gives clean,
complete answers in English/Gujarati/Hindi with no truncation or repetition
(unlike the openai/gpt-oss-* models on the same free tier, which burn large
hidden "reasoning" token budgets before producing visible output — a bad
fit here). Kept as an OpenAI-compatible endpoint deliberately, same as HF's
router was, so swapping provider again later only means changing these
three values, not rewriting call_llm().
"""

import asyncio
import io
import os

import httpx
import qrcode
from fastapi import HTTPException

import db
import repository
import vectorstore

LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen3.8-27b")
LLM_API_URL = os.environ.get("LLM_API_URL", "https://api.groq.com/openai/v1/chat/completions")

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
    await repository.refresh_qr_urls(BASE_URL)
    # Embedding ~170 knowledge chunks on Render's free-tier CPU is slow enough
    # (several minutes) to blow past Render's port-detection window if done
    # here — the service would never go live. Run it in the background
    # instead: the app serves core-fact answers immediately, and retrieval
    # (vectorstore.retrieve_tree_chunks/retrieve_related_trees, which already
    # tolerate an empty/partial collection) picks up richer chunks as soon as
    # this finishes.
    asyncio.create_task(_sync_knowledge_background())


async def _sync_knowledge_background():
    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(None, vectorstore.sync_knowledge)
        print("[startup] Knowledge base sync complete.")
    except Exception as exc:
        print(f"[startup] Knowledge base sync failed: {exc!r}")


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
- Fruiting season: {tree.get('fruiting_season', 'not recorded')}
- Average lifespan: {tree.get('avg_lifespan', 'not recorded')}
- CO2 sequestration: {tree.get('co2_sequestration', 'not recorded')}
- Pollination method: {tree.get('pollination_method', 'not recorded')}
- Pollinator(s): {tree.get('pollinator', 'not recorded')}
- Useful parts: {tree.get('useful_parts', 'not recorded')}
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
5. HARD LIMIT: your entire reply must be under 100 words, in any language. If asked something broad (e.g. "tell me everything" or "tell me about its uses, history, and medicine"), pick the 2-3 most interesting points rather than covering everything — a short, complete answer is always better than a long one cut off mid-sentence. Never leave a sentence unfinished. If there's more to say, end by inviting a specific follow-up question instead of trying to fit it all in.
6. Respond ONLY in {language_name}, regardless of what language the facts above are written in. Translate naturally — don't just transliterate.
"""


async def call_llm(system_prompt: str, history: list, user_message: str) -> str:
    if not LLM_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="LLM_API_KEY is not set on the server. Add it as an environment variable.",
        )

    messages = [{"role": "system", "content": system_prompt}]
    messages += history
    messages.append({"role": "user", "content": user_message})

    payload = {"model": LLM_MODEL, "messages": messages, "max_tokens": 700, "temperature": 0.4}
    headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(LLM_API_URL, json=payload, headers=headers)

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502, detail=f"LLM provider error ({resp.status_code}): {resp.text[:300]}"
        )
    data = resp.json()
    try:
        reply = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as exc:
        raise HTTPException(status_code=502, detail=f"Unexpected LLM response shape: {data}") from exc

    finish_reason = data["choices"][0].get("finish_reason")
    if finish_reason == "length":
        reply = _trim_to_last_sentence(reply)
    return reply


SENTENCE_ENDINGS = (".", "।", "!", "?", "۔", "॥")


def _trim_to_last_sentence(text: str) -> str:
    """If the model got cut off mid-sentence (finish_reason='length'), trim
    back to the last complete sentence instead of showing a dangling
    fragment — a shorter complete answer reads better than a longer broken
    one. Works across scripts since it just looks for common sentence-ending
    punctuation, not language-specific tokenization."""
    last_end = max((text.rfind(p) for p in SENTENCE_ENDINGS), default=-1)
    if last_end == -1:
        return text  # no sentence boundary found at all — return as-is rather than returning nothing
    return text[: last_end + 1].strip()


async def list_trees() -> list:
    return await repository.list_trees()


async def get_tree(tree_id: str) -> dict:
    tree = await repository.get_tree(tree_id)
    if not tree:
        raise HTTPException(status_code=404, detail=f"No tree with id '{tree_id}'")
    return tree


async def chat(tree_id: str, session_id: str, message: str, language: str):
    # These three don't depend on each other's results, so run them
    # concurrently instead of stacking three round trips (two to Neon, one
    # CPU-bound embedding) back to back — this was a real, measured chunk of
    # the ~9-10s per-reply latency in production.
    loop = asyncio.get_running_loop()
    tree, session, query_embedding = await asyncio.gather(
        repository.get_tree(tree_id),
        repository.get_or_create_session(session_id, tree_id),
        loop.run_in_executor(None, vectorstore.embed_query, message),
    )
    if not tree:
        raise HTTPException(status_code=404, detail=f"No tree with id '{tree_id}'")

    own_chunks = vectorstore.retrieve_tree_chunks(query_embedding, tree_id, top_k=CURRENT_TREE_TOP_K)
    retrieved = vectorstore.retrieve_related_trees(query_embedding, exclude_tree_id=tree_id, top_k=OTHER_TREES_TOP_K)
    system_prompt = build_system_prompt(tree, own_chunks, retrieved, language)

    reply = await call_llm(system_prompt, session["chat_json"], message)
    # Saving the turn doesn't need to delay the reply reaching the visitor —
    # run it in the background. Worst case on a crash mid-write: one turn's
    # history is lost, which is an acceptable trade for cutting a full extra
    # Neon round trip off every single reply.
    asyncio.create_task(repository.append_turn(session_id, message, reply))
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
