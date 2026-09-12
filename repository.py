"""
Repository layer — every runtime SQL query used by request handlers.

Connection pooling and schema/seeding live in db.py instead; this file only
knows how to read and write rows once a pool already exists.
"""

import json
from typing import Optional

import db


async def list_trees() -> list:
    async with db.get_pool().acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, common_name_en, common_name_gu, common_name_hi FROM trees ORDER BY common_name_en;"
        )
        return [dict(r) for r in rows]


async def get_tree(tree_id: str) -> Optional[dict]:
    async with db.get_pool().acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM trees WHERE id = $1;", tree_id)
        return dict(row) if row else None


async def get_or_create_session(session_id: str, tree_id: str) -> dict:
    async with db.get_pool().acquire() as conn:
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


async def append_turn(session_id: str, user_message: str, assistant_reply: str):
    async with db.get_pool().acquire() as conn:
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


async def refresh_qr_urls(base_url: str):
    """Recompute each tree's QR target link from the current BASE_URL. Safe to
    run on every startup — cheap, idempotent, and keeps links correct if the
    app moves between localhost and a deployed URL."""
    async with db.get_pool().acquire() as conn:
        await conn.execute(
            "UPDATE trees SET qr_url = $1 || '/t/' || id;",
            base_url.rstrip("/"),
        )


async def log_scan(tree_id: str, user_agent: Optional[str], referrer: Optional[str]):
    async with db.get_pool().acquire() as conn:
        await conn.execute(
            "INSERT INTO qr_scans (tree_id, user_agent, referrer) VALUES ($1, $2, $3);",
            tree_id, user_agent, referrer,
        )


async def get_scan_counts() -> list:
    """Per-tree scan analytics: total scans and last-scanned time, trees with
    zero scans included so new/unscanned trees are still visible."""
    async with db.get_pool().acquire() as conn:
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
