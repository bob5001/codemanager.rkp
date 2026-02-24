"""
Integration tests for project, visit, and search routes.

Tests use httpx.AsyncClient with ASGITransport and asgi_lifespan to
exercise the real FastAPI application against a live database without
starting an HTTP server.

Run with:  pytest tests/test_routes.py -v
"""

from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DB_HOST", "localhost")

import asyncpg
import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager

from main import app

DB_DSN = "postgresql://rkp_user:rkp_password@localhost:5433/rkp_core"


# -- Fixtures -----------------------------------------------------------------

@pytest_asyncio.fixture()
async def client():
    async with LifespanManager(app) as manager:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app),
            base_url="http://test",
        ) as c:
            yield c


@pytest_asyncio.fixture()
async def pool():
    p = await asyncpg.create_pool(DB_DSN)
    yield p
    await p.close()


@pytest_asyncio.fixture()
async def auth(client):
    """Register a fresh agent and return (client, api_key, agent_id)."""
    reg = await client.post(
        "/agents",
        json={"name": f"route-test-{uuid.uuid4()}", "ecosystem": "python"},
    )
    assert reg.status_code == 200, reg.text
    body = reg.json()
    return client, body["api_key"], body["id"]


# -- Cleanup helpers ----------------------------------------------------------

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


async def _delete_agent(pool: asyncpg.Pool, agent_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM codemanager.agents WHERE id = $1", agent_id
        )


# -- Tests --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_project(client, auth, pool):
    c, api_key, agent_id = auth
    project_id = None
    try:
        resp = await c.post(
            "/projects",
            json={"name": "test-create-project"},
            headers={"X-Agent-Key": api_key},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "test-create-project"
        assert "id" in body
        project_id = body["id"]
    finally:
        if project_id:
            await _delete_project(pool, project_id)
        await _delete_agent(pool, agent_id)


@pytest.mark.asyncio
async def test_list_projects(client, auth, pool):
    c, api_key, agent_id = auth
    project_id = None
    try:
        create = await c.post(
            "/projects",
            json={"name": "test-list-project"},
            headers={"X-Agent-Key": api_key},
        )
        assert create.status_code == 201, create.text
        project_id = create.json()["id"]

        resp = await c.get("/projects", headers={"X-Agent-Key": api_key})
        assert resp.status_code == 200, resp.text
        ids = [p["id"] for p in resp.json()]
        assert project_id in ids
    finally:
        if project_id:
            await _delete_project(pool, project_id)
        await _delete_agent(pool, agent_id)


@pytest.mark.asyncio
async def test_list_projects_status_filter(client, auth, pool):
    c, api_key, agent_id = auth
    project_id = None
    try:
        create = await c.post(
            "/projects",
            json={"name": "test-filter-project"},
            headers={"X-Agent-Key": api_key},
        )
        assert create.status_code == 201, create.text
        project_id = create.json()["id"]

        # The project starts as 'registered' but the background task may have
        # already transitioned it to 'analyzing' by the time we query.
        # Check that it appears under at least one of those two statuses.
        resp_reg = await c.get(
            "/projects?status=registered",
            headers={"X-Agent-Key": api_key},
        )
        assert resp_reg.status_code == 200
        resp_ana_bg = await c.get(
            "/projects?status=analyzing",
            headers={"X-Agent-Key": api_key},
        )
        assert resp_ana_bg.status_code == 200
        ids_registered_or_analyzing = (
            [p["id"] for p in resp_reg.json()]
            + [p["id"] for p in resp_ana_bg.json()]
        )
        assert project_id in ids_registered_or_analyzing, (
            "Project should appear under registered or analyzing status"
        )

        # Should NOT appear under analyzed
        resp_ana = await c.get(
            "/projects?status=analyzed",
            headers={"X-Agent-Key": api_key},
        )
        assert resp_ana.status_code == 200
        ids_ana = [p["id"] for p in resp_ana.json()]
        assert project_id not in ids_ana
    finally:
        if project_id:
            await _delete_project(pool, project_id)
        await _delete_agent(pool, agent_id)


@pytest.mark.asyncio
async def test_get_project(client, auth, pool):
    c, api_key, agent_id = auth
    project_id = None
    try:
        create = await c.post(
            "/projects",
            json={"name": "test-get-project"},
            headers={"X-Agent-Key": api_key},
        )
        assert create.status_code == 201, create.text
        project_id = create.json()["id"]

        resp = await c.get(f"/projects/{project_id}", headers={"X-Agent-Key": api_key})
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"] == project_id
    finally:
        if project_id:
            await _delete_project(pool, project_id)
        await _delete_agent(pool, agent_id)


@pytest.mark.asyncio
async def test_get_project_not_found(client, auth, pool):
    c, api_key, agent_id = auth
    try:
        random_id = str(uuid.uuid4())
        resp = await c.get(f"/projects/{random_id}", headers={"X-Agent-Key": api_key})
        assert resp.status_code == 404
    finally:
        await _delete_agent(pool, agent_id)


@pytest.mark.asyncio
async def test_patch_project(client, auth, pool):
    c, api_key, agent_id = auth
    project_id = None
    try:
        create = await c.post(
            "/projects",
            json={"name": "test-patch-project"},
            headers={"X-Agent-Key": api_key},
        )
        assert create.status_code == 201, create.text
        project_id = create.json()["id"]

        resp = await c.patch(
            f"/projects/{project_id}",
            json={"status": "in_development"},
            headers={"X-Agent-Key": api_key},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "in_development"
    finally:
        if project_id:
            await _delete_project(pool, project_id)
        await _delete_agent(pool, agent_id)


@pytest.mark.asyncio
async def test_log_visit(client, auth, pool):
    c, api_key, agent_id = auth
    project_id = None
    try:
        create = await c.post(
            "/projects",
            json={"name": "test-visit-project"},
            headers={"X-Agent-Key": api_key},
        )
        assert create.status_code == 201, create.text
        project_id = create.json()["id"]

        resp = await c.post(
            "/visits",
            json={"project_id": project_id, "query": "What does this do?"},
            headers={"X-Agent-Key": api_key},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["project_id"] == project_id
    finally:
        if project_id:
            await _delete_project(pool, project_id)
        await _delete_agent(pool, agent_id)


@pytest.mark.asyncio
async def test_get_visits(client, auth, pool):
    c, api_key, agent_id = auth
    project_id = None
    try:
        create = await c.post(
            "/projects",
            json={"name": "test-get-visits-project"},
            headers={"X-Agent-Key": api_key},
        )
        assert create.status_code == 201, create.text
        project_id = create.json()["id"]

        post_visit = await c.post(
            "/visits",
            json={"project_id": project_id, "summary": "Looks good."},
            headers={"X-Agent-Key": api_key},
        )
        assert post_visit.status_code == 200, post_visit.text
        visit_id = post_visit.json()["id"]

        resp = await c.get(
            f"/visits/{project_id}",
            headers={"X-Agent-Key": api_key},
        )
        assert resp.status_code == 200, resp.text
        visit_ids = [v["id"] for v in resp.json()]
        assert visit_id in visit_ids
    finally:
        if project_id:
            await _delete_project(pool, project_id)
        await _delete_agent(pool, agent_id)


@pytest.mark.asyncio
async def test_search_placeholder(client, auth, pool):
    c, api_key, agent_id = auth
    try:
        resp = await c.post(
            "/search",
            json={"query": "find authentication logic"},
            headers={"X-Agent-Key": api_key},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["query"] == "find authentication logic"
        assert "results" in body
        assert "count" in body
        # No local projects have embeddings yet (migration pending), so local results are empty
        assert body["local_count"] == 0
    finally:
        await _delete_agent(pool, agent_id)


@pytest.mark.asyncio
async def test_unauthenticated_returns_401(client):
    resp = await client.get("/projects")
    assert resp.status_code == 401
