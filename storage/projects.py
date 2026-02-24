"""
CRUD operations for codemanager.projects and codemanager.snapshots.

All functions accept an asyncpg.Pool as their first argument and return
plain dicts (never asyncpg Record objects).

asyncpg gotcha — JSONB columns: asyncpg cannot automatically encode Python
lists/dicts to jsonb without a type codec registered on the connection.
We serialize jsonb values with json.dumps() and use ::jsonb casts in SQL.
On the way out asyncpg may return jsonb as a Python str; we decode it.

asyncpg gotcha — vector columns: there is no built-in pgvector codec, so
embeddings are passed as the string '[x,y,…]' and cast with ::vector in SQL.
The RETURNING clause returns the vector as a Python str in that same format.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from storage.database import acquire


# ── Encoding helpers ──────────────────────────────────────────────────────────

def _vec_to_str(embedding: list[float]) -> str:
    """Convert a Python float list to the pgvector literal '[x,y,…]'."""
    return "[" + ",".join(str(x) for x in embedding) + "]"


def _loads_if_str(value):
    """Return parsed JSON if value is a string, else return value as-is."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _project_row(row) -> dict:
    """Convert an asyncpg Record for a projects row into a plain dict."""
    d = dict(row)
    # No jsonb columns in the project SELECT (summary/status/etc. are text).
    # embedding is returned as a str from pgvector — leave it as-is.
    return d


def _snapshot_row(row) -> dict:
    """Convert an asyncpg Record for a snapshots row into a plain dict."""
    d = dict(row)
    # file_tree and key_findings are jsonb — decode if asyncpg returned str
    if "file_tree" in d:
        d["file_tree"] = _loads_if_str(d["file_tree"])
    if "key_findings" in d:
        d["key_findings"] = _loads_if_str(d["key_findings"])
    return d


# ── Projects ──────────────────────────────────────────────────────────────────

async def create_project(
    pool: asyncpg.Pool,
    name: str,
    path: str | None = None,
    github_url: str | None = None,
    description: str | None = None,
) -> dict:
    """
    Insert a new project with status='registered' and return the full row.
    """
    sql = """
        INSERT INTO codemanager.projects
            (id, name, path, github_url, description, status, created_at)
        VALUES
            (gen_random_uuid(), $1, $2, $3, $4, 'registered', NOW())
        RETURNING *
    """
    async with acquire(pool) as conn:
        row = await conn.fetchrow(sql, name, path, github_url, description)
        return _project_row(row)


async def get_project(
    pool: asyncpg.Pool,
    project_id: str,
) -> dict | None:
    """
    Fetch a project by UUID.  If a snapshot exists, the latest snapshot's
    id and timestamp are included as `latest_snapshot_id` and
    `latest_snapshot_at`.

    Returns None if the project does not exist.
    """
    sql = """
        SELECT
            p.*,
            s.id          AS latest_snapshot_id,
            s.timestamp   AS latest_snapshot_at
        FROM codemanager.projects p
        LEFT JOIN LATERAL (
            SELECT id, timestamp
            FROM   codemanager.snapshots
            WHERE  project_id = p.id
            ORDER  BY timestamp DESC
            LIMIT  1
        ) s ON TRUE
        WHERE p.id = $1
    """
    async with acquire(pool) as conn:
        row = await conn.fetchrow(sql, project_id)
        return _project_row(row) if row is not None else None


async def list_projects(
    pool: asyncpg.Pool,
    status_filter: str | None = None,
) -> list[dict]:
    """
    Return all projects ordered by creation time, newest first.

    Pass `status_filter` (e.g. 'registered', 'analyzed') to narrow results.
    """
    if status_filter is not None:
        sql = """
            SELECT * FROM codemanager.projects
            WHERE status = $1
            ORDER BY created_at DESC
        """
        async with acquire(pool) as conn:
            rows = await conn.fetch(sql, status_filter)
    else:
        sql = "SELECT * FROM codemanager.projects ORDER BY created_at DESC"
        async with acquire(pool) as conn:
            rows = await conn.fetch(sql)

    return [_project_row(r) for r in rows]


# Columns that callers are allowed to update via update_project.
_UPDATABLE_COLUMNS = frozenset(
    {"summary", "embedding", "status", "status_note", "last_analyzed", "description"}
)


async def update_project(
    pool: asyncpg.Pool,
    project_id: str,
    **fields: Any,
) -> dict | None:
    """
    Dynamically UPDATE only the supplied fields for a project.

    Accepted keyword arguments: summary, embedding, status, status_note,
    last_analyzed, description.  Unknown keys raise ValueError.

    For `embedding`, pass a list[float]; it will be cast to ::vector.

    Returns the updated row as a dict, or None if the project was not found.
    """
    unknown = set(fields) - _UPDATABLE_COLUMNS
    if unknown:
        raise ValueError(f"Unknown field(s) for update_project: {unknown}")

    if not fields:
        # Nothing to update — just return the current row.
        return await get_project(pool, project_id)

    # Build the SET clause positionally.  The embedding column requires a
    # special cast; all others bind directly.
    set_parts: list[str] = []
    values: list[Any] = []
    param_index = 1  # $1 is reserved for project_id at the end

    for col, val in fields.items():
        if col == "embedding" and val is not None:
            # Convert list[float] → '[x,y,…]' and cast in SQL.
            set_parts.append(f"embedding = ${param_index}::vector")
            values.append(_vec_to_str(val))
        else:
            set_parts.append(f"{col} = ${param_index}")
            values.append(val)
        param_index += 1

    # project_id is the final parameter.
    values.append(project_id)
    where_param = f"${param_index}"

    # NOTE: set_parts contains only column names from _UPDATABLE_COLUMNS
    # (validated above) — no user input is interpolated into the SQL structure.
    sql = f"""
        UPDATE codemanager.projects
        SET {', '.join(set_parts)}
        WHERE id = {where_param}
        RETURNING *
    """

    async with acquire(pool) as conn:
        row = await conn.fetchrow(sql, *values)
        return _project_row(row) if row is not None else None


# ── Snapshots ─────────────────────────────────────────────────────────────────

async def create_snapshot(
    pool: asyncpg.Pool,
    project_id: str,
    file_tree: dict | list,
    key_findings: dict | list,
    embedding: list[float] | None = None,
) -> dict:
    """
    Insert a new snapshot for a project and return the full row.

    `file_tree` and `key_findings` are stored as JSONB.  Pass Python
    dicts or lists; they are serialised with json.dumps() internally.

    `embedding` is optional; pass a list[float] if you have one.
    """
    file_tree_json = json.dumps(file_tree)
    key_findings_json = json.dumps(key_findings)

    if embedding is not None:
        sql = """
            INSERT INTO codemanager.snapshots
                (id, project_id, timestamp, file_tree, key_findings, embedding)
            VALUES
                (gen_random_uuid(), $1, NOW(), $2::jsonb, $3::jsonb, $4::vector)
            RETURNING *
        """
        async with acquire(pool) as conn:
            row = await conn.fetchrow(
                sql, project_id, file_tree_json, key_findings_json,
                _vec_to_str(embedding)
            )
    else:
        sql = """
            INSERT INTO codemanager.snapshots
                (id, project_id, timestamp, file_tree, key_findings)
            VALUES
                (gen_random_uuid(), $1, NOW(), $2::jsonb, $3::jsonb)
            RETURNING *
        """
        async with acquire(pool) as conn:
            row = await conn.fetchrow(sql, project_id, file_tree_json, key_findings_json)

    return _snapshot_row(row)


async def get_latest_snapshot(
    pool: asyncpg.Pool,
    project_id: str,
) -> dict | None:
    """
    Return the most recent snapshot for a project, or None if none exist.
    """
    sql = """
        SELECT * FROM codemanager.snapshots
        WHERE project_id = $1
        ORDER BY timestamp DESC
        LIMIT 1
    """
    async with acquire(pool) as conn:
        row = await conn.fetchrow(sql, project_id)
        return _snapshot_row(row) if row is not None else None
