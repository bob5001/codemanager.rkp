"""
Tests for the MCP server tools.

These tests call the tool functions directly (bypassing MCP transport) by
temporarily initialising the module-level pool + agent_id that the tools use.

Run with:  pytest tests/test_mcp_server.py -v
"""
from __future__ import annotations

import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DB_HOST", "localhost")

import asyncpg
import pytest
import pytest_asyncio

import mcp_server  # noqa: E402 — sets up module-level globals

DB_DSN = "postgresql://rkp_user:rkp_password@localhost:5433/rkp_core"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture()
async def pool():
    p = await asyncpg.create_pool(DB_DSN)
    # Wire the MCP server module globals so tool functions can run
    mcp_server._pool = p
    mcp_server._mcp_agent_id = await mcp_server._ensure_mcp_agent(p)
    yield p
    # Cleanup
    mcp_server._pool = None
    await p.close()


# ── Cleanup helpers ───────────────────────────────────────────────────────────

async def _delete_project(pool: asyncpg.Pool, project_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM codemanager.agent_visits WHERE project_id = $1", project_id
        )
        await conn.execute(
            "DELETE FROM codemanager.snapshots WHERE project_id = $1", project_id
        )
        await conn.execute(
            "DELETE FROM codemanager.projects WHERE id = $1", project_id
        )


# ── Tests ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_all_projects_returns_json_list(pool):
    result = await mcp_server.list_all_projects()
    data = json.loads(result)
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_list_all_projects_status_filter(pool):
    result = await mcp_server.list_all_projects(status="registered")
    data = json.loads(result)
    assert isinstance(data, list)
    for item in data:
        assert item["status"] == "registered"


@pytest.mark.asyncio
async def test_get_project_detail_not_found(pool):
    result = await mcp_server.get_project_detail(str(uuid.uuid4()))
    data = json.loads(result)
    assert "error" in data


@pytest.mark.asyncio
async def test_register_and_get_project(pool):
    project_id = None
    try:
        result = await mcp_server.register_project(
            name=f"mcp-test-{uuid.uuid4()}",
            analyze=False,  # skip analysis to keep the test fast
        )
        data = json.loads(result)
        assert "id" in data
        assert data["status"] == "registered"
        project_id = data["id"]

        detail_result = await mcp_server.get_project_detail(project_id)
        detail = json.loads(detail_result)
        assert detail["id"] == project_id
    finally:
        if project_id:
            await _delete_project(pool, project_id)


@pytest.mark.asyncio
async def test_update_project_status(pool):
    project_id = None
    try:
        reg = await mcp_server.register_project(
            name=f"mcp-status-test-{uuid.uuid4()}", analyze=False
        )
        project_id = json.loads(reg)["id"]

        result = await mcp_server.update_project_status(
            project_id=project_id,
            status="in_development",
            status_note="MCP test note",
        )
        data = json.loads(result)
        assert data["status"] == "in_development"
        assert data["status_note"] == "MCP test note"
    finally:
        if project_id:
            await _delete_project(pool, project_id)


@pytest.mark.asyncio
async def test_update_project_status_not_found(pool):
    result = await mcp_server.update_project_status(
        project_id=str(uuid.uuid4()), status="analyzed"
    )
    data = json.loads(result)
    assert "error" in data


@pytest.mark.asyncio
async def test_record_and_get_visit(pool):
    project_id = None
    try:
        reg = await mcp_server.register_project(
            name=f"mcp-visit-test-{uuid.uuid4()}", analyze=False
        )
        project_id = json.loads(reg)["id"]

        visit_result = await mcp_server.record_visit(
            project_id=project_id,
            query="Where is the auth middleware?",
            summary="Found in api/deps.py, uses SHA-256 key hash.",
            usefulness=3,
            confidence=0.9,
            model_used="claude-sonnet-4-6",
        )
        visit = json.loads(visit_result)
        assert visit["project_id"] == project_id
        assert visit["query"] == "Where is the auth middleware?"

        history_result = await mcp_server.get_visit_history(project_id)
        history = json.loads(history_result)
        assert isinstance(history, list)
        ids = [v["id"] for v in history]
        assert visit["id"] in ids
    finally:
        if project_id:
            await _delete_project(pool, project_id)


@pytest.mark.asyncio
async def test_search_projects_returns_valid_shape(pool):
    result = await mcp_server.search_projects(query="authentication middleware", limit=5)
    data = json.loads(result)
    assert "query" in data
    assert "results" in data
    assert "local_count" in data
    assert data["query"] == "authentication middleware"


@pytest.mark.asyncio
async def test_mcp_agent_created_once(pool):
    """Calling _ensure_mcp_agent twice should return the same UUID."""
    id1 = await mcp_server._ensure_mcp_agent(pool)
    id2 = await mcp_server._ensure_mcp_agent(pool)
    assert id1 == id2
