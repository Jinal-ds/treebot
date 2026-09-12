# AI Tree Chatbot — Phase 1 (Postgres + ChromaDB)

## What this is
A per-tree chatbot: scan a QR code (simulated here via `?tree=<id>` or the
dropdown), see that tree's details, and chat with a bot that only answers
questions about that tree, botany in general, or comparisons with other trees
in the dataset — powered by a free Hugging Face model.

## Architecture (Phase 1)
- **PostgreSQL** — single source of truth for both permanent tree data and
  live chat sessions (schema auto-created and auto-seeded on first startup).
- **ChromaDB (embedded mode)** — real retrieval: given a visitor's question,
  the top-3 most relevant OTHER tree profiles are pulled in for comparisons,
  instead of stuffing all 29 trees into every prompt.
- **FastAPI**, single Uvicorn worker. Embedded ChromaDB is only safe with one
  worker process — see the compatibility note in the project docs before ever
  running `--workers > 1` without also moving Chroma to client-server mode.
- **Hugging Face Inference Providers** (free tier) for the actual LLM call.

## Local setup
```bash
pip install -r requirements.txt

# Point at your own local Postgres (or use the one already running if you
# followed along in this session):
export DATABASE_URL="postgresql://postgres:postgres@localhost:5432/treebot_test"
export HF_TOKEN="hf_your_fine_grained_token"
export HF_MODEL="meta-llama/Llama-3.1-8B-Instruct"  # swap if this 404s on your account

uvicorn main:app --reload
```
Visit http://localhost:8000 — the dropdown simulates scanning different QR codes.

## First-run note (read before your demo)
On first startup, ChromaDB downloads a small (~80MB) embedding model from a
public S3 bucket. This was **not fully testable in the sandbox this was built
in**, because that sandbox blocks `amazonaws.com` outbound — Render does not
have this restriction, so it should download normally, but this means:
- **Deploy and test this today, not 10 minutes before your demo** — if the
  download is slow or fails on first boot, you need time to notice and retry.
- If startup ever fails with an embedding-model download error on Render,
  restart the service once (transient network hiccups are the most likely
  cause) — the retry logic in Chroma's own downloader should handle it.

Everything else — Postgres schema/seeding, session read/write, tree-switch
handling, the retrieval filter logic (excluding the current tree, top-3
trim) — was tested directly against a real running Postgres instance and a
real Chroma collection in this session, not just written and assumed correct.

## Deploying to Render
1. Push this folder to a GitHub repo.
2. Render dashboard → **New → Blueprint** → point at the repo. `render.yaml`
   provisions both the free Postgres database and the web service, and wires
   `DATABASE_URL` between them automatically.
3. When prompted, paste your `HF_TOKEN` (fine-grained, with "Make calls to
   Inference Providers" enabled).
4. Deploy. First build installs `chromadb`, which is a larger dependency —
   expect the build itself to take a few minutes longer than a plain FastAPI app.

## Known limits of this phase (by design, not oversight)
- Single worker only — see `render.yaml` (no `--workers` flag set).
- Render's free Postgres expires 30 days after creation (14-day grace period
  after that). Fine for this demo; revisit before the project runs longer.
- `HF_MODEL` defaults to a guess — check the Inference Providers playground
  on huggingface.co if it doesn't work for your account, and override via the
  `HF_MODEL` environment variable, no code change needed.
