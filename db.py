"""
PostgreSQL access layer — connection pool, schema setup, seeding, and queries.

Two tables:
- trees: permanent tree data (seeded once from trees_data.json)
- sessions: one row per visitor session, holding their chat history as JSONB

Why asyncpg directly (no ORM): keeps this dependency-light and lets FastAPI's
async event loop actually benefit from non-blocking database calls, consistent
with the async-first choice made for the rest of this app.
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
                botanical_name TEXT,
                family TEXT,
                native_status TEXT,
                area_name TEXT,
                latitude DOUBLE PRECISION,
                longitude DOUBLE PRECISION,
                description TEXT,
                uses TEXT,
                wood_quality TEXT,
                flowering_season TEXT,
                fun_fact TEXT
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
            INSERT INTO trees (id, common_name_en, common_name_gu, botanical_name, family,
                                native_status, area_name, latitude, longitude, description,
                                uses, wood_quality, flowering_season, fun_fact)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
            ON CONFLICT (id) DO NOTHING;
            """,
            [
                (
                    t["id"], t["common_name_en"], t["common_name_gu"], t["botanical_name"],
                    t["family"], t["native_status"], t["area_name"], t["latitude"],
                    t["longitude"], t["description"], t["uses"], t["wood_quality"],
                    t["flowering_season"], t["fun_fact"],
                )
                for t in rows
            ],
        )


async def list_trees():
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, common_name_en, common_name_gu FROM trees ORDER BY common_name_en;"
        )
        return [dict(r) for r in rows]


async def get_tree(tree_id: str) -> Optional[dict]:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM trees WHERE id = $1;", tree_id)
        return dict(row) if row else None


async def all_trees() -> list:
    async with get_pool().acquire() as conn:
        rows = await conn.fetch("SELECT * FROM trees;")
        return [dict(r) for r in rows]


async def get_or_create_session(session_id: str, tree_id: str) -> dict:
    async with get_pool().acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM sessions WHERE session_id = $1;", session_id
        )
        if row:
            if row["tree_id"] != tree_id:
                # Visitor switched to a different tree under the same browser session —
                # start a fresh conversation for the new tree.
                await conn.execute(
                    """
                    UPDATE sessions
                    SET tree_id = $2, chat_json = '[]'::jsonb, last_active_at = now()
                    WHERE session_id = $1;
                    """,
                    session_id, tree_id,
                )
                return {"session_id": session_id, "tree_id": tree_id, "chat_json": []}
            return {
                "session_id": row["session_id"],
                "tree_id": row["tree_id"],
                "chat_json": json.loads(row["chat_json"]),
            }
        await conn.execute(
            "INSERT INTO sessions (session_id, tree_id) VALUES ($1, $2);",
            session_id, tree_id,
        )
        return {"session_id": session_id, "tree_id": tree_id, "chat_json": []}


async def refresh_qr_urls(base_url: str):
    """Recompute each tree's QR target link from the current BASE_URL. Safe to
    run on every startup — cheap, idempotent, and keeps links correct if the
    app moves between localhost and a deployed URL."""
    async with get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE trees SET qr_url = $1 || '/t/' || id;",
            base_url.rstrip("/"),
        )


async def log_scan(tree_id: str, user_agent: Optional[str], referrer: Optional[str]):
    async with get_pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO qr_scans (tree_id, user_agent, referrer) VALUES ($1, $2, $3);",
            tree_id, user_agent, referrer,
        )


async def get_scan_counts() -> list:
    """Per-tree scan analytics: total scans and last-scanned time, trees with
    zero scans included so new/unscanned trees are still visible."""
    async with get_pool().acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                t.id AS tree_id,
                t.common_name_en,
                t.common_name_gu,
                t.qr_url,
                COUNT(s.id) AS scan_count,
                MAX(s.scanned_at) AS last_scanned_at
            FROM trees t
            LEFT JOIN qr_scans s ON s.tree_id = t.id
            GROUP BY t.id, t.common_name_en, t.common_name_gu, t.qr_url
            ORDER BY scan_count DESC, t.common_name_en ASC;
            """
        )
        return [dict(r) for r in rows]


async def append_turn(session_id: str, user_message: str, assistant_reply: str):
    async with get_pool().acquire() as conn:
        await conn.execute(
            """
            UPDATE sessions
            SET chat_json = chat_json || $2::jsonb,
                last_active_at = now()
            WHERE session_id = $1;
            """,
            session_id,
            json.dumps(
                [
                    {"role": "user", "content": user_message},
                    {"role": "assistant", "content": assistant_reply},
                ]
            ),
        )
