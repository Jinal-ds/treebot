"""
Database connection layer — pool lifecycle, schema creation, and seeding.

This file only owns *connecting to* and *initializing* Postgres. Runtime
queries used by request handlers live in repository.py instead — that split
keeps "how do we get a connection and what does the schema look like" (this
file) separate from "what do we ask the database for" (repository.py).
"""

import json
import os
from pathlib import Path
from typing import Optional

import asyncpg

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/treebot_test"
)
BASE_DIR = Path(__file__).parent
SEED_PATH = BASE_DIR / "trees_data.json"

_pool: Optional[asyncpg.Pool] = None


async def init_pool() -> asyncpg.Pool:
    global _pool
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    await _create_schema()
    await _seed_if_empty()
    await _shorten_existing_teasers()
    await _backfill_hindi_names()
    return _pool


async def close_pool():
    if _pool:
        await _pool.close()


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised — call init_pool() first")
    return _pool


async def _create_schema():
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS trees (
                id TEXT PRIMARY KEY,
                common_name_en TEXT NOT NULL,
                common_name_gu TEXT,
                common_name_hi TEXT,
                botanical_name TEXT,
                family TEXT,
                native_status TEXT,
                area_name TEXT,
                latitude DOUBLE PRECISION,
                longitude DOUBLE PRECISION,
                description TEXT,  -- short 1-2 sentence teaser only; full narrative content lives in ChromaDB (see knowledge/)
                flowering_season TEXT
            );
            """
        )
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id UUID PRIMARY KEY,
                tree_id TEXT NOT NULL REFERENCES trees(id),
                chat_json JSONB NOT NULL DEFAULT '[]'::jsonb,
                last_active_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_last_active ON sessions (last_active_at);"
        )
        await conn.execute(
            "ALTER TABLE trees ADD COLUMN IF NOT EXISTS qr_url TEXT;"
        )
        await conn.execute(
            "ALTER TABLE trees ADD COLUMN IF NOT EXISTS common_name_hi TEXT;"
        )
        await conn.execute("ALTER TABLE trees DROP COLUMN IF EXISTS uses;")
        await conn.execute("ALTER TABLE trees DROP COLUMN IF EXISTS wood_quality;")
        await conn.execute("ALTER TABLE trees DROP COLUMN IF EXISTS fun_fact;")
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS qr_scans (
                id SERIAL PRIMARY KEY,
                tree_id TEXT NOT NULL REFERENCES trees(id),
                scanned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                user_agent TEXT,
                referrer TEXT
            );
            """
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_qr_scans_tree_id ON qr_scans (tree_id);"
        )
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_qr_scans_scanned_at ON qr_scans (scanned_at);"
        )


def _short_teaser(description: str) -> str:
    """Trim a long placeholder description down to its first sentence, so the
    Postgres 'description' column reads as a short teaser immediately, without
    waiting on trees_data.json to be hand-edited. The full text still lives on
    in trees_data.json (used by the knowledge-stub migration script) and can
    be hand-improved here later — this is just a sane automatic default."""
    first_sentence = description.split(". ")[0].strip().rstrip(".")
    return f"{first_sentence}."


async def _seed_if_empty():
    async with get_pool().acquire() as conn:
        count = await conn.fetchval("SELECT count(*) FROM trees;")
        if count and count > 0:
            return
        with open(SEED_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        rows = data["trees"]
        await conn.executemany(
            """
            INSERT INTO trees (id, common_name_en, common_name_gu, common_name_hi,
                                botanical_name, family, native_status, area_name,
                                latitude, longitude, description, flowering_season)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
            ON CONFLICT (id) DO NOTHING;
            """,
            [
                (
                    t["id"], t["common_name_en"], t["common_name_gu"], t.get("common_name_hi"),
                    t["botanical_name"], t["family"], t["native_status"], t["area_name"],
                    t["latitude"], t["longitude"], _short_teaser(t["description"]), t["flowering_season"],
                )
                for t in rows
            ],
        )


async def _shorten_existing_teasers():
    """_seed_if_empty only runs against an empty table, so a database seeded
    before the teaser-shortening rule existed never had it applied. Run this
    unconditionally on every startup (same 'always resync' precedent as
    repository.refresh_qr_urls) so already-seeded rows catch up too. Idempotent:
    applying _short_teaser to an already-short teaser just returns it unchanged."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch("SELECT id, description FROM trees;")
        for row in rows:
            if not row["description"]:
                continue
            short = _short_teaser(row["description"])
            if short != row["description"]:
                await conn.execute(
                    "UPDATE trees SET description = $1 WHERE id = $2;", short, row["id"]
                )


async def _backfill_hindi_names():
    """Same reasoning as _shorten_existing_teasers: common_name_hi was added
    after this database was first seeded, so existing rows never got it.
    Backfill from trees_data.json on every startup; a no-op once every row
    already has a value."""
    async with get_pool().acquire() as conn:
        missing = await conn.fetch(
            "SELECT id FROM trees WHERE common_name_hi IS NULL;"
        )
        if not missing:
            return
        with open(SEED_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        hindi_by_id = {t["id"]: t.get("common_name_hi") for t in data["trees"]}
        for row in missing:
            hi_name = hindi_by_id.get(row["id"])
            if hi_name:
                await conn.execute(
                    "UPDATE trees SET common_name_hi = $1 WHERE id = $2;", hi_name, row["id"]
                )
