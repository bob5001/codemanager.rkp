"""
Integration tests for the storage layer.

These tests hit the real PostgreSQL database at localhost:5433/rkp_core.
Run with:  pytest tests/test_storage.py -v

A fresh asyncpg pool is created per test function.  All tests clean up
their own rows in try/finally blocks so the DB stays tidy.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import uuid

import asyncpg
import pytest
import pytest_asyncio

# ── sys.path fix so storage.* imports work when running from repo root ─────────
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from storage.agents import (
    create_agent,
    get_agent_by_id,
    get_agent_by_key_hash,
    list_agents,
)
from storage.projects import (
    create_project,
    create_snapshot,
    get_latest_snapshot,
    get_project,
    list_projects,
    update_project,
)
from storage.visits import get_visits, log_visit

# ── Constants ─────────────────────────────────────────────────────────────────

DB_DSN = (
    f"postgresql://{os.getenv('DB_USER', 'rkp_user')}:{os.getenv('DB_PASSWORD', '')}"
    f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5433')}/{os.getenv('DB_NAME', 'rkp_core')}"
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture()
async def pool():
    """Create a fresh connection pool for each test, then close it."""
    p = await asyncpg.create_pool(DB_DSN)
    yield p
    await p.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fake_hash(value: str) -> str:
    """Return a deterministic SHA-256 hex digest for test API keys."""
    return hashlib.sha256(value.encode()).hexdigest()


async def _delete_agent(pool: asyncpg.Pool, agent_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM codemanager.agents WHERE id = $1", agent_id
        )


async def _delete_project(pool: asyncpg.Pool, project_id: str) -> None:
    """Delete a project and its cascading snapshots and visits."""
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM codemanager.agent_visits WHERE project_id = $1",
            project_id,
        )
        await conn.execute(
            "DELETE FROM codemanager.snapshots WHERE project_id = $1",
            project_id,
        )
        await conn.execute(
            "DELETE FROM codemanager.projects WHERE id = $1", project_id
        )


# ── Agent tests ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_and_get_agent_by_key_hash(pool):
    """create_agent followed by get_agent_by_key_hash should round-trip."""
    key_hash = _fake_hash(f"test-agent-{uuid.uuid4()}")
    agent = await create_agent(
        pool,
        name="test-agent",
        ecosystem="python",
        api_key_hash=key_hash,
        capabilities=["read", "write"],
    )
    agent_id = str(agent["id"])

    try:
        assert agent["name"] == "test-agent"
        assert agent["ecosystem"] == "python"
        assert agent["api_key_hash"] == key_hash
        assert agent["capabilities"] == ["read", "write"]
        assert agent["id"] is not None

        fetched = await get_agent_by_key_hash(pool, key_hash)
        assert fetched is not None
        assert str(fetched["id"]) == agent_id
        assert fetched["name"] == "test-agent"
        # last_seen should have been refreshed by get_agent_by_key_hash
        assert fetched["last_seen"] is not None
    finally:
        await _delete_agent(pool, agent_id)


@pytest.mark.asyncio
async def test_get_agent_by_key_hash_missing(pool):
    """get_agent_by_key_hash returns None for an unknown hash."""
    result = await get_agent_by_key_hash(pool, "nonexistent-hash-xyz")
    assert result is None


@pytest.mark.asyncio
async def test_get_agent_by_id(pool):
    """create_agent then retrieve by UUID."""
    key_hash = _fake_hash(f"test-agent-id-{uuid.uuid4()}")
    agent = await create_agent(
        pool,
        name="agent-by-id",
        ecosystem="rust",
        api_key_hash=key_hash,
        capabilities=[],
    )
    agent_id = str(agent["id"])

    try:
        fetched = await get_agent_by_id(pool, agent_id)
        assert fetched is not None
        assert str(fetched["id"]) == agent_id
        assert fetched["ecosystem"] == "rust"
    finally:
        await _delete_agent(pool, agent_id)


@pytest.mark.asyncio
async def test_list_agents_includes_new_agent(pool):
    """list_agents should contain a freshly inserted agent."""
    key_hash = _fake_hash(f"list-agent-{uuid.uuid4()}")
    agent = await create_agent(
        pool,
        name="list-test-agent",
        ecosystem="go",
        api_key_hash=key_hash,
        capabilities=["read"],
    )
    agent_id = str(agent["id"])

    try:
        agents = await list_agents(pool)
        ids = [str(a["id"]) for a in agents]
        assert agent_id in ids
    finally:
        await _delete_agent(pool, agent_id)


# ── Project tests ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_and_get_project(pool):
    """create_project followed by get_project should round-trip."""
    project = await create_project(
        pool,
        name="test-project",
        path="/tmp/test-project",
        github_url="https://github.com/example/test-project",
        description="A test project.",
    )
    project_id = str(project["id"])

    try:
        assert project["name"] == "test-project"
        assert project["status"] == "registered"
        assert project["path"] == "/tmp/test-project"

        fetched = await get_project(pool, project_id)
        assert fetched is not None
        assert str(fetched["id"]) == project_id
        assert fetched["github_url"] == "https://github.com/example/test-project"
        # No snapshot yet
        assert fetched["latest_snapshot_id"] is None
    finally:
        await _delete_project(pool, project_id)


@pytest.mark.asyncio
async def test_get_project_missing(pool):
    """get_project returns None for an unknown UUID."""
    result = await get_project(pool, str(uuid.uuid4()))
    assert result is None


@pytest.mark.asyncio
async def test_list_projects_with_status_filter(pool):
    """list_projects with status_filter should only return matching rows."""
    project = await create_project(pool, name="filter-test-project")
    project_id = str(project["id"])

    try:
        registered = await list_projects(pool, status_filter="registered")
        ids = [str(p["id"]) for p in registered]
        assert project_id in ids

        analyzed = await list_projects(pool, status_filter="analyzed")
        ids_analyzed = [str(p["id"]) for p in analyzed]
        assert project_id not in ids_analyzed
    finally:
        await _delete_project(pool, project_id)


@pytest.mark.asyncio
async def test_update_project_status_and_note(pool):
    """update_project should change only the specified fields."""
    project = await create_project(pool, name="update-test-project")
    project_id = str(project["id"])

    try:
        updated = await update_project(
            pool,
            project_id,
            status="analyzed",
            status_note="Analysis complete.",
        )
        assert updated is not None
        assert updated["status"] == "analyzed"
        assert updated["status_note"] == "Analysis complete."
        # Name should be untouched
        assert updated["name"] == "update-test-project"
    finally:
        await _delete_project(pool, project_id)


@pytest.mark.asyncio
async def test_update_project_unknown_field_raises(pool):
    """update_project should raise ValueError for unknown field names."""
    project = await create_project(pool, name="bad-field-project")
    project_id = str(project["id"])

    try:
        with pytest.raises(ValueError, match="Unknown field"):
            await update_project(pool, project_id, nonexistent_column="oops")
    finally:
        await _delete_project(pool, project_id)


# ── Snapshot tests ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_snapshot_and_get_latest(pool):
    """create_snapshot followed by get_latest_snapshot should round-trip."""
    project = await create_project(pool, name="snapshot-test-project")
    project_id = str(project["id"])

    try:
        file_tree = {"src": ["main.py", "utils.py"], "tests": ["test_main.py"]}
        key_findings = {"lines_of_code": 500, "languages": ["python"]}

        snapshot = await create_snapshot(
            pool,
            project_id=project_id,
            file_tree=file_tree,
            key_findings=key_findings,
        )
        snapshot_id = str(snapshot["id"])

        assert str(snapshot["project_id"]) == project_id
        assert snapshot["file_tree"] == file_tree
        assert snapshot["key_findings"] == key_findings

        latest = await get_latest_snapshot(pool, project_id)
        assert latest is not None
        assert str(latest["id"]) == snapshot_id

        # get_project should now expose the snapshot
        fetched = await get_project(pool, project_id)
        assert fetched is not None
        assert str(fetched["latest_snapshot_id"]) == snapshot_id
    finally:
        await _delete_project(pool, project_id)


@pytest.mark.asyncio
async def test_get_latest_snapshot_returns_newest(pool):
    """get_latest_snapshot should return the most recent of multiple snapshots."""
    project = await create_project(pool, name="multi-snapshot-project")
    project_id = str(project["id"])

    try:
        await create_snapshot(
            pool, project_id=project_id,
            file_tree={"v": 1}, key_findings={}
        )
        # Small sleep to guarantee distinct timestamps
        await asyncio.sleep(0.05)
        snap2 = await create_snapshot(
            pool, project_id=project_id,
            file_tree={"v": 2}, key_findings={}
        )

        latest = await get_latest_snapshot(pool, project_id)
        assert latest is not None
        assert str(latest["id"]) == str(snap2["id"])
    finally:
        await _delete_project(pool, project_id)


@pytest.mark.asyncio
async def test_get_latest_snapshot_no_snapshots(pool):
    """get_latest_snapshot returns None when a project has no snapshots."""
    project = await create_project(pool, name="no-snapshot-project")
    project_id = str(project["id"])

    try:
        result = await get_latest_snapshot(pool, project_id)
        assert result is None
    finally:
        await _delete_project(pool, project_id)


# ── Visit tests ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_log_visit_and_get_visits(pool):
    """log_visit followed by get_visits should round-trip."""
    key_hash = _fake_hash(f"visit-agent-{uuid.uuid4()}")
    agent = await create_agent(
        pool,
        name="visit-agent",
        ecosystem="python",
        api_key_hash=key_hash,
        capabilities=[],
    )
    agent_id = str(agent["id"])

    project = await create_project(pool, name="visit-test-project")
    project_id = str(project["id"])

    try:
        visit = await log_visit(
            pool,
            project_id=project_id,
            agent_id=agent_id,
            query="What does this repo do?",
            summary="It manages code.",
            usefulness=4,
            confidence=0.9,
            model_used="claude-opus-4-6",
        )
        assert str(visit["project_id"]) == project_id
        assert str(visit["agent_id"]) == agent_id
        assert visit["query"] == "What does this repo do?"
        assert visit["usefulness"] == 4
        assert abs(visit["confidence"] - 0.9) < 1e-6
        assert visit["model_used"] == "claude-opus-4-6"

        visits = await get_visits(pool, project_id)
        assert len(visits) == 1
        assert str(visits[0]["id"]) == str(visit["id"])
    finally:
        await _delete_project(pool, project_id)
        await _delete_agent(pool, agent_id)


@pytest.mark.asyncio
async def test_get_visits_respects_limit(pool):
    """get_visits should honour the limit parameter."""
    key_hash = _fake_hash(f"limit-agent-{uuid.uuid4()}")
    agent = await create_agent(
        pool,
        name="limit-agent",
        ecosystem="python",
        api_key_hash=key_hash,
        capabilities=[],
    )
    agent_id = str(agent["id"])

    project = await create_project(pool, name="limit-visit-project")
    project_id = str(project["id"])

    try:
        for i in range(5):
            await log_visit(
                pool,
                project_id=project_id,
                agent_id=agent_id,
                query=f"query-{i}",
            )

        all_visits = await get_visits(pool, project_id, limit=50)
        assert len(all_visits) == 5

        limited = await get_visits(pool, project_id, limit=2)
        assert len(limited) == 2

        # Most recent should come first
        assert limited[0]["timestamp"] >= limited[1]["timestamp"]
    finally:
        await _delete_project(pool, project_id)
        await _delete_agent(pool, agent_id)


@pytest.mark.asyncio
async def test_log_visit_minimal(pool):
    """log_visit with only required args should insert nulls for optional cols."""
    key_hash = _fake_hash(f"minimal-agent-{uuid.uuid4()}")
    agent = await create_agent(
        pool,
        name="minimal-agent",
        ecosystem="python",
        api_key_hash=key_hash,
        capabilities=[],
    )
    agent_id = str(agent["id"])

    project = await create_project(pool, name="minimal-visit-project")
    project_id = str(project["id"])

    try:
        visit = await log_visit(pool, project_id=project_id, agent_id=agent_id)
        assert visit["query"] is None
        assert visit["summary"] is None
        assert visit["usefulness"] is None
        assert visit["confidence"] is None
        assert visit["model_used"] is None
    finally:
        await _delete_project(pool, project_id)
        await _delete_agent(pool, agent_id)
