"""
Tests for the MCP server tools.

These tests call the tool functions directly (bypassing MCP transport) by
temporarily initialising the module-level pool + agent_id that the tools use.

Run with:  pytest tests/test_mcp_server.py -v
"""
from __future__ import annotations

import hashlib
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
from storage.projects import create_snapshot  # noqa: E402

DB_DSN = (
    f"postgresql://{os.getenv('DB_USER', 'rkp_user')}:{os.getenv('DB_PASSWORD', '')}"
    f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5433')}/{os.getenv('DB_NAME', 'rkp_core')}"
)


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
async def test_list_all_projects_returns_envelope(pool):
    result = await mcp_server.list_all_projects()
    data = json.loads(result)
    assert isinstance(data, dict)
    assert isinstance(data["projects"], list)
    assert data["total"] >= data["count"] == len(data["projects"])
    assert data["offset"] == 0


@pytest.mark.asyncio
async def test_list_all_projects_status_filter(pool):
    result = await mcp_server.list_all_projects(status="registered")
    data = json.loads(result)
    for item in data["projects"]:
        assert item["status"] == "registered"


# ── Response trimming (see MAX_LIST_DESCRIPTION_CHARS) ───────────────────────

def test_truncate_at_word_breaks_on_word_boundary():
    assert mcp_server._truncate_at_word("alpha beta gamma delta", 12) == "alpha beta\u2026"
    # trailing punctuation is stripped before the ellipsis
    assert mcp_server._truncate_at_word("alpha beta, gamma", 12) == "alpha beta\u2026"
    # a single token longer than the limit still gets cut
    assert mcp_server._truncate_at_word("x" * 50, 10) == "x" * 10 + "\u2026"
    # short enough is returned untouched, with no ellipsis
    assert mcp_server._truncate_at_word("alpha beta", 40) == "alpha beta"
    assert mcp_server._truncate_at_word(None, 40) is None
    assert mcp_server._truncate_at_word("", 40) == ""


def test_slim_project_drops_prose_keeps_identity():
    row = {
        "id": "abc", "name": "proj", "path": "/tmp/proj", "status": "analyzed",
        "description": "Purpose-first sentence. " * 40,
        "summary": "This software project is a WordPress-based application...",
        "status_note": "operational detail",
    }
    slim = mcp_server._slim_project(row)
    assert "summary" not in slim
    assert "status_note" not in slim
    assert {"id", "name", "path", "status"} <= slim.keys()
    assert len(slim["description"]) <= mcp_server.MAX_LIST_DESCRIPTION_CHARS + 1
    # rows without a description (e.g. GitHub hits) pass through unharmed
    assert mcp_server._slim_project({"source": "github", "similarity": 0.5})["similarity"] == 0.5


@pytest.mark.asyncio
async def test_list_all_projects_trims_prose_by_default(pool):
    """The whole point of the trim: 49 projects of full prose serialized to
    63KB and blew the calling agent's tool-output limit."""
    data = json.loads(await mcp_server.list_all_projects())
    assert data["verbose"] is False
    for item in data["projects"]:
        assert "summary" not in item
        assert "status_note" not in item
        if item.get("description"):
            assert len(item["description"]) <= mcp_server.MAX_LIST_DESCRIPTION_CHARS + 1


@pytest.mark.asyncio
async def test_list_all_projects_verbose_keeps_prose(pool):
    """verbose=True on a small page returns the untrimmed fields."""
    project_id = None
    try:
        reg = await mcp_server.register_project(
            name=f"mcp-verbose-test-{uuid.uuid4()}", analyze=False
        )
        project_id = json.loads(reg)["id"]

        data = json.loads(await mcp_server.list_all_projects(limit=1, verbose=True))
        assert data["verbose"] is True
        assert "summary" in data["projects"][0]
        assert "status_note" in data["projects"][0]
    finally:
        if project_id:
            await _delete_project(pool, project_id)


@pytest.mark.asyncio
async def test_list_all_projects_verbose_downgrades_when_oversized(pool, monkeypatch):
    """A verbose response past the byte cap falls back to the trimmed view
    with an explanatory note, rather than returning something unusable."""
    monkeypatch.setattr(mcp_server, "MAX_LIST_RESPONSE_BYTES", 200)
    data = json.loads(await mcp_server.list_all_projects(verbose=True))
    assert data["verbose"] is False
    assert "verbose_downgraded" in data
    for item in data["projects"]:
        assert "summary" not in item


@pytest.mark.asyncio
async def test_list_all_projects_paging(pool):
    first = json.loads(await mcp_server.list_all_projects(limit=1, offset=0))
    if first["total"] < 2:
        pytest.skip("needs at least 2 registered projects")
    second = json.loads(await mcp_server.list_all_projects(limit=1, offset=1))
    assert first["count"] == second["count"] == 1
    assert first["truncated"] is True
    assert second["offset"] == 1
    assert first["projects"][0]["id"] != second["projects"][0]["id"]


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
async def test_get_project_detail_omits_oversized_file_tree(pool):
    """A snapshot whose file_tree serializes past MAX_FILE_TREE_BYTES should
    have the tree omitted (with a note) rather than returned raw — this is
    the fix for the case that motivated it: a WordPress project's vendor-heavy
    plugin tree serialized to 2.4MB and the tool call failed outright."""
    project_id = None
    try:
        reg = await mcp_server.register_project(
            name=f"mcp-oversized-tree-test-{uuid.uuid4()}", analyze=False
        )
        project_id = json.loads(reg)["id"]

        # One giant file_tree entry, well past MAX_FILE_TREE_BYTES on its own.
        huge_file_tree = {
            f"vendor/pkg/file_{i}.php": {"size": 100, "lines": 5}
            for i in range(6000)
        }
        await create_snapshot(
            pool, project_id, file_tree=huge_file_tree, key_findings={}
        )

        detail_result = await mcp_server.get_project_detail(project_id)
        detail = json.loads(detail_result)
        snapshot = detail["latest_snapshot"]

        assert snapshot["file_tree"] is None
        assert "file_tree_omitted" in snapshot
        assert str(len(huge_file_tree)) in snapshot["file_tree_omitted"]
    finally:
        if project_id:
            await _delete_project(pool, project_id)


@pytest.mark.asyncio
async def test_get_project_detail_keeps_small_file_tree(pool):
    """A file_tree under the threshold should be returned as-is, unchanged."""
    project_id = None
    try:
        reg = await mcp_server.register_project(
            name=f"mcp-small-tree-test-{uuid.uuid4()}", analyze=False
        )
        project_id = json.loads(reg)["id"]

        small_file_tree = {"app.py": {"size": 100, "lines": 5}}
        await create_snapshot(
            pool, project_id, file_tree=small_file_tree, key_findings={}
        )

        detail_result = await mcp_server.get_project_detail(project_id)
        detail = json.loads(detail_result)
        snapshot = detail["latest_snapshot"]

        assert snapshot["file_tree"] == small_file_tree
        assert "file_tree_omitted" not in snapshot
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
async def test_search_projects_trims_prose_by_default(pool):
    result = await mcp_server.search_projects(query="authentication middleware", limit=5)
    data = json.loads(result)
    assert data["verbose"] is False
    for item in data["results"]:
        assert "summary" not in item
        assert "status_note" not in item
        if item.get("description"):
            assert len(item["description"]) <= mcp_server.MAX_LIST_DESCRIPTION_CHARS + 1


@pytest.mark.asyncio
async def test_mcp_agent_created_once(pool):
    """Calling _ensure_mcp_agent twice should return the same UUID."""
    id1 = await mcp_server._ensure_mcp_agent(pool)
    id2 = await mcp_server._ensure_mcp_agent(pool)
    assert id1 == id2


# ── Per-client attribution via X-Agent-Key ────────────────────────────────────

from types import SimpleNamespace  # noqa: E402

from storage.agents import create_agent  # noqa: E402


def _ctx_with_headers(headers: dict | None):
    """Stand-in for FastMCP's Context: only .request_context.request.headers is read."""
    request = SimpleNamespace(headers=headers) if headers is not None else None
    return SimpleNamespace(request_context=SimpleNamespace(request=request))


async def _make_agent(pool, status: str = "active") -> tuple[str, str, str]:
    """Create a test agent; return (agent_id, name, plaintext key)."""
    key = f"mcp-attr-test-key-{uuid.uuid4()}"
    name = f"mcp-attr-test-{uuid.uuid4().hex[:8]}"
    agent = await create_agent(
        pool, name=name, ecosystem="pytest",
        api_key_hash=hashlib.sha256(key.encode()).hexdigest(),
        capabilities=[], status=status,
    )
    return str(agent["id"]), name, key


async def _visit_with_ctx(ctx):
    reg = await mcp_server.register_project(name=f"mcp-attr-test-{uuid.uuid4()}", analyze=False)
    project_id = json.loads(reg)["id"]
    result = json.loads(await mcp_server.record_visit(
        project_id=project_id, summary="attribution test", ctx=ctx,
    ))
    return project_id, result


@pytest.mark.asyncio
@pytest.mark.parametrize("ctx", [None, _ctx_with_headers(None), _ctx_with_headers({})])
async def test_record_visit_without_key_uses_mcp_local(pool, ctx):
    project_id, visit = await _visit_with_ctx(ctx)
    try:
        assert visit["agent_id"] == mcp_server._mcp_agent_id
        assert visit["attributed_to"] == mcp_server.MCP_AGENT_NAME
    finally:
        await _delete_project(pool, project_id)


@pytest.mark.asyncio
async def test_record_visit_with_key_credits_that_agent(pool):
    agent_id, name, key = await _make_agent(pool)
    project_id, visit = await _visit_with_ctx(_ctx_with_headers({"x-agent-key": key}))
    try:
        assert visit["agent_id"] == agent_id
        assert visit["attributed_to"] == name
    finally:
        await _delete_project(pool, project_id)


@pytest.mark.asyncio
async def test_record_visit_unknown_key_is_rejected(pool):
    project_id, visit = await _visit_with_ctx(_ctx_with_headers({"x-agent-key": "no-such-key"}))
    try:
        assert "error" in visit
        assert "does not match any registered agent" in visit["error"]
    finally:
        await _delete_project(pool, project_id)


@pytest.mark.asyncio
async def test_record_visit_pending_agent_is_rejected(pool):
    _, _, key = await _make_agent(pool, status="pending")
    project_id, visit = await _visit_with_ctx(_ctx_with_headers({"x-agent-key": key}))
    try:
        assert "error" in visit
        assert "pending admin approval" in visit["error"]
    finally:
        await _delete_project(pool, project_id)
