"""
CRUD operations for codemanager.agent_visits.

All functions accept an asyncpg.Pool as their first argument and return
plain dicts (never asyncpg Record objects).
"""

from __future__ import annotations

from datetime import datetime, timezone

import asyncpg

from storage.database import acquire


async def log_visit(
    pool: asyncpg.Pool,
    project_id: str,
    agent_id: str,
    query: str | None = None,
    summary: str | None = None,
    usefulness: int | None = None,
    confidence: float | None = None,
    model_used: str | None = None,
) -> dict:
    """
    Record an agent visit to a project and return the inserted row as a dict.

    All fields except project_id and agent_id are optional.
    `usefulness` should be a smallint (e.g. 1-5 or 0-10 — caller's choice).
    """
    sql = """
        INSERT INTO codemanager.agent_visits
            (id, project_id, agent_id, query, summary, usefulness,
             confidence, model_used, timestamp)
        VALUES
            (gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7, NOW())
        RETURNING *
    """
    async with acquire(pool) as conn:
        row = await conn.fetchrow(
            sql,
            project_id,
            agent_id,
            query,
            summary,
            usefulness,
            confidence,
            model_used,
        )
        return dict(row)


async def get_visits(
    pool: asyncpg.Pool,
    project_id: str,
    limit: int = 50,
) -> list[dict]:
    """
    Return up to `limit` visits for a project, ordered newest-first.
    """
    sql = """
        SELECT * FROM codemanager.agent_visits
        WHERE project_id = $1
        ORDER BY timestamp DESC
        LIMIT $2
    """
    async with acquire(pool) as conn:
        rows = await conn.fetch(sql, project_id, limit)
        return [dict(r) for r in rows]


async def get_recent_visits(
    pool: asyncpg.Pool,
    since: str | datetime,
    limit: int = 200,
) -> list[dict]:
    """
    Return visits across ALL projects since a given ISO timestamp, newest-first.
    Used by Jojo's daily digest to detect what changed since the last run.
    """
    # asyncpg requires a datetime object, not an ISO string
    if isinstance(since, str):
        since = datetime.fromisoformat(since.replace("Z", "+00:00"))

    sql = """
        SELECT
            av.*,
            p.name AS project_name,
            p.description AS project_description,
            p.status AS project_status
        FROM codemanager.agent_visits av
        JOIN codemanager.projects p ON p.id = av.project_id
        WHERE av.timestamp > $1::timestamptz
        ORDER BY av.timestamp DESC
        LIMIT $2
    """
    async with acquire(pool) as conn:
        rows = await conn.fetch(sql, since, limit)
        return [dict(r) for r in rows]
