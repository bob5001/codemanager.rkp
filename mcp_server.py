"""
Standalone MCP server for codemanager.rkp.

Exposes project-intelligence tools directly to Claude Code and other MCP
clients without going through the REST API — it reads the database directly.

Run modes:
  python mcp_server.py           # stdio transport (Claude Code / Claude Desktop)
  python mcp_server.py --sse     # SSE transport on port 8008 (remote / Cloudflare)

Register with Claude Code (stdio):
  claude mcp add codemanager -- python /path/to/codemanager.rkp/mcp_server.py

Or add to ~/.claude/claude_desktop_config.json:
  {
    "mcpServers": {
      "codemanager": {
        "command": "python",
        "args": ["/path/to/codemanager.rkp/mcp_server.py"]
      }
    }
  }
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import sys
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from mcp.server.fastmcp import FastMCP

# Ensure project root is importable when invoked directly
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import settings  # noqa: E402
from storage.projects import (  # noqa: E402
    create_project,
    get_latest_snapshot,
    get_project,
    list_projects,
    update_project,
)
from storage.visits import get_visits, log_visit  # noqa: E402

MCP_AGENT_NAME = "mcp_local"
MCP_SSE_PORT = 8008

# Module-level pool + agent id (set during lifespan, valid for process lifetime)
_pool: asyncpg.Pool | None = None
_mcp_agent_id: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pool_required() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised — lifespan not started")
    return _pool


def _serialize(obj: Any) -> Any:
    """Recursively convert non-JSON-serialisable values to primitives."""
    if obj is None:
        return None
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "hex") and not isinstance(obj, (str, bytes)):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    return obj


async def _ensure_mcp_agent(pool: asyncpg.Pool) -> str:
    """Find or create the dedicated 'mcp_local' system agent. Returns UUID."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM codemanager.agents WHERE name = $1", MCP_AGENT_NAME
        )
        if row:
            return str(row["id"])
        key_hash = hashlib.sha256(secrets.token_hex(32).encode()).hexdigest()
        row = await conn.fetchrow(
            """
            INSERT INTO codemanager.agents (id, name, ecosystem, api_key_hash, capabilities)
            VALUES (gen_random_uuid(), $1, 'mcp', $2, $3::jsonb)
            RETURNING id
            """,
            MCP_AGENT_NAME,
            key_hash,
            json.dumps(["read", "write", "analyze", "search"]),
        )
        return str(row["id"])


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(server: FastMCP):
    global _pool, _mcp_agent_id
    _pool = await asyncpg.create_pool(dsn=settings.get_dsn(), min_size=1, max_size=5)
    _mcp_agent_id = await _ensure_mcp_agent(_pool)
    yield
    await _pool.close()
    _pool = None


# ── FastMCP server ────────────────────────────────────────────────────────────

mcp = FastMCP(
    "codemanager",
    instructions=(
        "Project intelligence broker. Use list_all_projects to discover what's "
        "tracked, search_projects to find relevant codebases by natural language, "
        "get_project_detail for deep info, and record_visit to share your findings."
    ),
    lifespan=lifespan,
    host="0.0.0.0",
    port=MCP_SSE_PORT,
)


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool()
async def list_all_projects(
    status: str | None = None,
) -> str:
    """
    List all tracked projects.

    Args:
        status: Optional status filter. One of: registered, analyzing, partial,
                analyzed, in_development, alpha, testing, production, stuck,
                deprecated, archived.
    """
    pool = _pool_required()
    rows = await list_projects(pool, status_filter=status)
    for r in rows:
        r.pop("embedding", None)
    return json.dumps(_serialize(rows), indent=2)


@mcp.tool()
async def get_project_detail(project_id: str) -> str:
    """
    Get full detail for a project including its latest file-tree snapshot.

    Args:
        project_id: UUID of the project.
    """
    pool = _pool_required()
    row = await get_project(pool, project_id)
    if row is None:
        return json.dumps({"error": f"Project '{project_id}' not found"})
    row.pop("embedding", None)
    snapshot = await get_latest_snapshot(pool, project_id)
    if snapshot:
        snapshot.pop("embedding", None)
        row["latest_snapshot"] = _serialize(snapshot)
    return json.dumps(_serialize(row), indent=2)


@mcp.tool()
async def register_project(
    name: str,
    path: str | None = None,
    github_url: str | None = None,
    description: str | None = None,
    analyze: bool = True,
) -> str:
    """
    Register a new project and optionally trigger local filesystem analysis.

    Analysis walks the path, summarises with Ollama, stores an embedding and
    snapshot.  It runs synchronously (unlike the REST API which backgrounds it),
    so it may take up to a minute for large repos.

    Args:
        name: Human-readable project name.
        path: Absolute filesystem path to the project root (required for analysis).
        github_url: GitHub URL (optional).
        description: Short description (optional; analysis will fill this in).
        analyze: Run analysis immediately if path is provided (default True).
    """
    pool = _pool_required()
    project = await create_project(
        pool, name=name, path=path, github_url=github_url, description=description
    )
    project_id = str(project["id"])

    if analyze and path:
        from analyzers.runner import analyze_project  # noqa: PLC0415
        try:
            await analyze_project(pool, project_id, path)
            project = await get_project(pool, project_id) or project
        except Exception as exc:
            project["analysis_error"] = str(exc)

    project.pop("embedding", None)
    return json.dumps(_serialize(project), indent=2)


@mcp.tool()
async def search_projects(
    query: str,
    limit: int = 10,
    status: str | None = None,
    include_github: bool = False,
) -> str:
    """
    Semantic similarity search across registered projects (and optionally GitHub).

    Embeds the query via nomic-embed-text (Ollama), runs pgvector cosine
    similarity across project embeddings, and optionally merges GitHub results.

    Args:
        query: Natural language query, e.g. "FastAPI auth with JWT tokens".
        limit: Maximum results to return (default 10).
        status: Optional status filter (e.g. "analyzed", "in_development").
        include_github: Also search GitHub and merge ranked results.
    """
    from analyzers.summarizer import embed_text  # noqa: PLC0415

    pool = _pool_required()
    query_vec = await embed_text(query)
    vec_str = "[" + ",".join(str(x) for x in query_vec) + "]"

    if status:
        sql = """
            SELECT
                id, name, path, github_url, description, summary,
                status, status_note, last_analyzed, created_at,
                1 - (embedding <=> $1::vector) AS similarity
            FROM codemanager.projects
            WHERE embedding IS NOT NULL AND status = $3
            ORDER BY embedding <=> $1::vector
            LIMIT $2
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, vec_str, limit, status)
    else:
        sql = """
            SELECT
                id, name, path, github_url, description, summary,
                status, status_note, last_analyzed, created_at,
                1 - (embedding <=> $1::vector) AS similarity
            FROM codemanager.projects
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> $1::vector
            LIMIT $2
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, vec_str, limit)

    local_results = []
    for row in rows:
        r: dict[str, Any] = {"source": "local"}
        for key, value in dict(row).items():
            if hasattr(value, "isoformat"):
                r[key] = value.isoformat()
            elif hasattr(value, "hex") and not isinstance(value, (str, bytes)):
                r[key] = str(value)
            elif isinstance(value, float):
                r[key] = round(value, 4)
            else:
                r[key] = value
        local_results.append(r)

    github_results: list[dict] = []
    if include_github and query.strip():
        from analyzers.github import search_github  # noqa: PLC0415

        async def _score_repo(repo: dict) -> dict:
            try:
                repo_vec = await embed_text(repo["_embed_input"])
                dot = sum(a * b for a, b in zip(query_vec, repo_vec))
                norm_q = sum(x * x for x in query_vec) ** 0.5
                norm_r = sum(x * x for x in repo_vec) ** 0.5
                repo["similarity"] = (
                    round(dot / (norm_q * norm_r), 4) if norm_q and norm_r else 0.0
                )
            except Exception:
                repo["similarity"] = 0.0
            repo.pop("_embed_input", None)
            return repo

        try:
            gh_repos = await search_github(query, limit=limit)
            github_results = list(await asyncio.gather(*[_score_repo(r) for r in gh_repos]))
        except Exception as exc:
            github_results = [{"source": "github", "error": str(exc)}]

    all_results = local_results + github_results
    all_results.sort(key=lambda r: r.get("similarity", 0.0), reverse=True)

    return json.dumps(
        _serialize({
            "query": query,
            "count": len(all_results),
            "local_count": len(local_results),
            "github_count": len(github_results),
            "results": all_results,
        }),
        indent=2,
    )


@mcp.tool()
async def get_visit_history(
    project_id: str,
    limit: int = 20,
) -> str:
    """
    Retrieve past agent visits for a project — prior queries and conclusions.

    Read this before starting work on a project to learn what other agents
    have already discovered.

    Args:
        project_id: UUID of the project.
        limit: Maximum visits to return, newest first (default 20).
    """
    pool = _pool_required()
    visits = await get_visits(pool, project_id, limit=limit)
    return json.dumps(_serialize(visits), indent=2)


@mcp.tool()
async def record_visit(
    project_id: str,
    query: str | None = None,
    summary: str | None = None,
    usefulness: int | None = None,
    confidence: float | None = None,
    model_used: str | None = None,
) -> str:
    """
    Record your visit to a project with findings for future agents.

    Call this after finishing work on a project — even partial findings are
    valuable for the next agent that opens this codebase.

    Args:
        project_id: UUID of the project you visited.
        query: What you were trying to find or do.
        summary: Key architectural insights, entry points, gotchas, conclusions.
        usefulness: 0=harmful 1=irrelevant 2=partial 3=useful 4=definitive
        confidence: Self-reported confidence 0.0–1.0
        model_used: Model that did the analysis, e.g. "claude-sonnet-4-6"
    """
    pool = _pool_required()
    if _mcp_agent_id is None:
        return json.dumps({"error": "MCP system agent not initialised"})
    visit = await log_visit(
        pool,
        project_id=project_id,
        agent_id=_mcp_agent_id,
        query=query,
        summary=summary,
        usefulness=usefulness,
        confidence=confidence,
        model_used=model_used,
    )
    return json.dumps(_serialize(visit), indent=2)


@mcp.tool()
async def update_project_status(
    project_id: str,
    status: str,
    status_note: str | None = None,
) -> str:
    """
    Update the lifecycle status of a project.

    Valid statuses: registered | analyzing | partial | analyzed |
                    in_development | alpha | testing | production |
                    stuck | deprecated | archived

    Args:
        project_id: UUID of the project.
        status: New status value.
        status_note: Optional note, e.g. "stuck at OAuth middleware in auth.py:142".
    """
    pool = _pool_required()
    fields: dict[str, Any] = {"status": status}
    if status_note is not None:
        fields["status_note"] = status_note
    row = await update_project(pool, project_id, **fields)
    if row is None:
        return json.dumps({"error": f"Project '{project_id}' not found"})
    row.pop("embedding", None)
    return json.dumps(_serialize(row), indent=2)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="codemanager MCP server")
    parser.add_argument(
        "--sse",
        action="store_true",
        help=f"SSE transport (HTTP on 0.0.0.0:{MCP_SSE_PORT}) instead of stdio",
    )
    args = parser.parse_args()
    mcp.run(transport="sse" if args.sse else "stdio")
