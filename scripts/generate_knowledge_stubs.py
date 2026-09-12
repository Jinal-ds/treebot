"""
One-off migration: seeds knowledge/<id>.json stub files from the placeholder
uses/wood_quality/fun_fact/description fields in trees_data.json.

Run once, by hand, from the repo root:
    python scripts/generate_knowledge_stubs.py

Safe to re-run: skips any knowledge/<id>.json that already exists, so it will
never clobber content you've since hand-edited. Delete a file first if you
want to regenerate it from the placeholder JSON.
"""
import json
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
SEED_PATH = BASE_DIR / "trees_data.json"
KNOWLEDGE_DIR = BASE_DIR / "knowledge"

PLACEHOLDER_LABEL = "Phase 1 placeholder notes"


def build_chunks(t: dict) -> list:
    chunks = []
    if t.get("description"):
        chunks.append({"source": PLACEHOLDER_LABEL, "url": None, "text": t["description"]})
    if t.get("uses"):
        chunks.append({"source": f"{PLACEHOLDER_LABEL} — uses", "url": None, "text": t["uses"]})
    if t.get("wood_quality"):
        chunks.append({"source": f"{PLACEHOLDER_LABEL} — wood quality", "url": None, "text": t["wood_quality"]})
    if t.get("fun_fact"):
        chunks.append({"source": f"{PLACEHOLDER_LABEL} — fun fact", "url": None, "text": t["fun_fact"]})
    return chunks


def main():
    KNOWLEDGE_DIR.mkdir(exist_ok=True)
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))

    written, skipped = 0, 0
    for t in data["trees"]:
        out_path = KNOWLEDGE_DIR / f"{t['id']}.json"
        if out_path.exists():
            skipped += 1
            continue
        stub = {
            "tree_id": t["id"],
            "common_name_en": t["common_name_en"],
            "chunks": build_chunks(t),
        }
        out_path.write_text(json.dumps(stub, indent=2, ensure_ascii=False), encoding="utf-8")
        written += 1

    print(f"knowledge/: {written} files written, {skipped} already existed and were left untouched.")


if __name__ == "__main__":
    main()
