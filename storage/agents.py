"""
CRUD operations for codemanager.agents.

All functions accept an asyncpg.Pool as their first argument and return
plain dicts (never asyncpg Record objects).

asyncpg gotcha — JSONB columns: asyncpg cannot automatically encode Python
lists/dicts to jsonb without a type codec registered on the connection.
We pass jsonb values as JSON strings with an explicit ::jsonb cast in SQL.
On the way out, asyncpg returns jsonb as a Python str, so we json.loads()
any jsonb field we know about (capabilities).
"""

from __future__ import annotations

import json

import asyncpg

from storage.database import acquire


def _loads_if_str(value):
    """Return parsed JSON if value is a string, else return value as-is."""
    if isinstance(value, str):
        return json.loads(value)
    return value


def _row_to_dict(row) -> dict:
    """Convert an asyncpg Record to a plain dict, decoding JSONB fields."""
    d = dict(row)
    # capabilities is a jsonb column — decode if asyncpg returned it as str
    if "capabilities" in d:
        d["capabilities"] = _loads_if_str(d["capabilities"])
    return d


async def create_agent(
    pool: asyncpg.Pool,
    name: str,
    ecosystem: str,
    api_key_hash: str,
    capabilities: list,
) -> dict:
    """
    Insert a new agent row and return the full row as a dict.

    `capabilities` is stored as JSONB; pass a plain Python list.
    `registered_at` and `last_seen` default to NOW() in the DB.
    """
    sql = """
        INSERT INTO codemanager.agents
            (id, name, ecosystem, api_key_hash, capabilities, registered_at, last_seen)
        VALUES
            (gen_random_uuid(), $1, $2, $3, $4::jsonb, NOW(), NOW())
        RETURNING *
    """
    async with acquire(pool) as conn:
        row = await conn.fetchrow(sql, name, ecosystem, api_key_hash, json.dumps(capabilities))
        return _row_to_dict(row)


async def get_agent_by_key_hash(
    pool: asyncpg.Pool,
    api_key_hash: str,
) -> dict | None:
    """
    Fetch an agent by its API key hash, atomically updating last_seen.

    Returns None if no matching agent is found.
    """
    sql = """
        UPDATE codemanager.agents
        SET last_seen = NOW()
        WHERE api_key_hash = $1
        RETURNING *
    """
    async with acquire(pool) as conn:
        row = await conn.fetchrow(sql, api_key_hash)
        return _row_to_dict(row) if row is not None else None


async def get_agent_by_id(
    pool: asyncpg.Pool,
    agent_id: str,
) -> dict | None:
    """Fetch a single agent by UUID. Returns None if not found."""
    sql = "SELECT * FROM codemanager.agents WHERE id = $1"
    async with acquire(pool) as conn:
        row = await conn.fetchrow(sql, agent_id)
        return _row_to_dict(row) if row is not None else None


async def list_agents(pool: asyncpg.Pool) -> list[dict]:
    """Return all agents ordered by registration time, newest first."""
    sql = "SELECT * FROM codemanager.agents ORDER BY registered_at DESC"
    async with acquire(pool) as conn:
        rows = await conn.fetch(sql)
        return [_row_to_dict(r) for r in rows]
