"""
ChromaDB (embedded mode) vector store for hand-collected tree knowledge.

Documents come from knowledge/<tree_id>.json — one file per tree, each with
one or more source-attributed text chunks the user collects by hand from
Wikipedia, forestry sites, botanical databases, etc. (see knowledge/README.md).
This is NOT auto-fetched; nothing in this module makes outbound network calls.

Two retrieval paths:
- the current tree's own chunks (deep-dive content for the tree the visitor
  is standing at — Postgres only holds a short teaser, this is where the
  real detail comes from)
- other trees' chunks (for comparison questions)

Embedded mode (PersistentClient) is safe here because Phase 1 runs a single
Uvicorn worker — see the compatibility note in the project docs before ever
adding --workers > 1 without also moving this to client-server mode.

Chroma's default embedding function runs a small model locally via ONNX —
no API key and no extra cost, which matters given we have no paid LLM subscription.
"""

import json
from pathlib import Path

import chromadb

CHROMA_PATH = str(Path(__file__).parent / "chroma_data")
KNOWLEDGE_DIR = Path(__file__).parent / "knowledge"

CHUNK_MAX_CHARS = 1000

_client = None
_collection = None


def init_store():
    global _client, _collection
    _client = chromadb.PersistentClient(path=CHROMA_PATH)
    _collection = _client.get_or_create_collection(name="tree_knowledge")
    return _collection


def get_collection():
    if _collection is None:
        raise RuntimeError("Chroma collection not initialised — call init_store() first")
    return _collection


def _split_long_text(text: str, max_chars: int = CHUNK_MAX_CHARS) -> list:
    """Split on paragraph breaks first; only fall back to sentence breaks for a
    single paragraph that alone still exceeds max_chars. Never splits mid-word.
    Most hand-written chunks are well under max_chars and pass through as one piece."""
    if len(text) <= max_chars:
        return [text]
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [s.strip() + "." for s in text.split(". ") if s.strip()]
    pieces, buf = [], ""
    for p in paragraphs:
        if buf and len(buf) + len(p) + 1 > max_chars:
            pieces.append(buf)
            buf = p
        else:
            buf = f"{buf} {p}".strip()
    if buf:
        pieces.append(buf)
    return pieces or [text]


def load_knowledge_files() -> list:
    """Read every knowledge/<tree_id>.json file. Missing directory or files are
    tolerated (returns []) so the app still boots before any files are authored."""
    if not KNOWLEDGE_DIR.exists():
        return []
    files = []
    for path in sorted(KNOWLEDGE_DIR.glob("*.json")):
        with open(path, "r", encoding="utf-8") as f:
            files.append(json.load(f))
    return files


def sync_knowledge():
    """Rebuild the tree_knowledge collection from knowledge/*.json on every
    startup (mirrors db.refresh_qr_urls's "always resync" approach, unlike the
    old seed-once behaviour this replaces). Uses deterministic IDs and
    collection.upsert(), so edited/added chunks are reflected on next restart
    with no manual cache-clearing, and re-running is always safe."""
    collection = get_collection()
    files = load_knowledge_files()

    ids, documents, metadatas = [], [], []
    for entry in files:
        tree_id = entry["tree_id"]
        common_name_en = entry.get("common_name_en", "")
        for i, chunk in enumerate(entry.get("chunks", [])):
            for j, sub_text in enumerate(_split_long_text(chunk["text"])):
                ids.append(f"{tree_id}::{i}.{j}")
                documents.append(sub_text)
                metadatas.append({
                    "tree_id": tree_id,
                    "common_name_en": common_name_en,
                    "source": chunk.get("source", "unknown"),
                    "url": chunk.get("url") or "",
                })

    if ids:
        collection.upsert(ids=ids, documents=documents, metadatas=metadatas)


def retrieve_tree_chunks(query: str, tree_id: str, top_k: int = 6):
    """The current tree's own most-relevant knowledge chunks for this question.
    Higher top_k than the cross-tree comparison path, since this is the
    primary content the chatbot draws on for the tree the visitor is at."""
    collection = get_collection()
    if collection.count() == 0:
        return []
    results = collection.query(
        query_texts=[query],
        n_results=min(top_k, collection.count()),
        where={"tree_id": tree_id},
    )
    metas = results.get("metadatas", [[]])[0]
    docs = results.get("documents", [[]])[0]
    return list(zip(metas, docs))


def retrieve_related_trees(query: str, exclude_tree_id: str, top_k: int = 3):
    """Other trees' chunks for comparison questions, ranked by relevance,
    using Chroma's native $ne filter (works cleanly now that a tree can have
    many chunks, unlike the old fetch-extra-and-filter-in-Python approach)."""
    collection = get_collection()
    if collection.count() == 0:
        return []
    results = collection.query(
        query_texts=[query],
        n_results=min(top_k, collection.count()),
        where={"tree_id": {"$ne": exclude_tree_id}},
    )
    metas = results.get("metadatas", [[]])[0]
    docs = results.get("documents", [[]])[0]
    return list(zip(metas, docs))
