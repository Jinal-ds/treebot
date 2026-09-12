"""
ChromaDB (embedded mode) vector store for tree profiles.

This is genuine retrieval, not prompt-stuffing: each tree's profile is one
document. Given a visitor's question, we embed it and pull back the top-K most
relevant OTHER tree profiles (for comparisons), while the current tree's own
full profile is always included separately and in full.

Embedded mode (PersistentClient) is safe here because Phase 1 runs a single
Uvicorn worker — see the compatibility note in the project docs before ever
adding --workers > 1 without also moving this to client-server mode.

Chroma's default embedding function runs a small model locally via ONNX —
no API key and no extra cost, which matters given we have no paid LLM subscription.
"""

from pathlib import Path

import chromadb

CHROMA_PATH = str(Path(__file__).parent / "chroma_data")

_client = None
_collection = None


def init_store():
    global _client, _collection
    _client = chromadb.PersistentClient(path=CHROMA_PATH)
    _collection = _client.get_or_create_collection(name="tree_profiles")
    return _collection


def get_collection():
    if _collection is None:
        raise RuntimeError("Chroma collection not initialised — call init_store() first")
    return _collection


def profile_text(tree: dict) -> str:
    return (
        f"{tree['common_name_en']} ({tree['botanical_name']}), family {tree['family']}. "
        f"{tree['description']} Uses: {tree['uses']} Wood quality: {tree['wood_quality']} "
        f"Flowering season: {tree['flowering_season']} Fun fact: {tree['fun_fact']}"
    )


def seed_if_empty(trees: list):
    collection = get_collection()
    if collection.count() > 0:
        return
    collection.add(
        ids=[t["id"] for t in trees],
        documents=[profile_text(t) for t in trees],
        metadatas=[{"tree_id": t["id"], "common_name_en": t["common_name_en"]} for t in trees],
    )


def retrieve_related_trees(query: str, exclude_tree_id: str, top_k: int = 3):
    """Return up to top_k (metadata, document) pairs for trees other than exclude_tree_id,
    ranked by relevance to `query`. Over-fetches and filters in Python rather than relying
    on a metadata filter operator, so this works across Chroma versions."""
    collection = get_collection()
    if collection.count() == 0:
        return []
    n = min(top_k + 5, collection.count())
    results = collection.query(query_texts=[query], n_results=n)
    metas = results.get("metadatas", [[]])[0]
    docs = results.get("documents", [[]])[0]
    pairs = [(m, d) for m, d in zip(metas, docs) if m["tree_id"] != exclude_tree_id]
    return pairs[:top_k]
