"""
Integration tests for agent auth middleware and registration routes.

These tests use httpx.AsyncClient with ASGITransport and asgi_lifespan
to exercise the real FastAPI application (including its asyncpg lifespan)
without starting an actual HTTP server.

Run with:  pytest tests/test_auth.py -v
"""

from __future__ import annotations

import os
import sys

# Ensure repo root is on sys.path so `from main import app` resolves correctly.
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Override DB_HOST before importing main/config so the app connects to
# localhost rather than host.docker.internal (which only resolves inside
# Docker but not when running pytest directly on the host).
os.environ.setdefault("DB_HOST", "localhost")

import httpx
import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager

from main import app


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture()
async def client():
    """
    Async httpx client backed by ASGITransport.

    LifespanManager triggers FastAPI's lifespan startup (creates the asyncpg
    pool) on entry and shutdown (closes the pool) on exit, so all tests share
    a live database connection.
    """
    async with LifespanManager(app) as manager:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app),
            base_url="http://test",
        ) as c:
            yield c


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_register_agent(client: httpx.AsyncClient):
    """POST /agents returns 200 with id, name, and a one-time api_key."""
    response = await client.post(
        "/agents",
        json={"name": "test-agent", "ecosystem": "python", "capabilities": ["read"]},
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert "id" in body
    assert "api_key" in body
    assert body["name"] == "test-agent"
    assert body["ecosystem"] == "python"
    assert body["capabilities"] == ["read"]
    assert "registered_at" in body
    # api_key_hash must never be exposed
    assert "api_key_hash" not in body


@pytest.mark.asyncio
async def test_get_me_authenticated(client: httpx.AsyncClient):
    """Registering an agent and then calling GET /agents/me returns the correct profile."""
    reg = await client.post(
        "/agents",
        json={"name": "auth-test-agent", "ecosystem": "typescript"},
    )
    assert reg.status_code == 200, reg.text
    api_key = reg.json()["api_key"]
    agent_name = reg.json()["name"]

    me = await client.get("/agents/me", headers={"X-Agent-Key": api_key})
    assert me.status_code == 200, me.text

    body = me.json()
    assert body["name"] == agent_name
    assert body["ecosystem"] == "typescript"
    assert "api_key_hash" not in body
    assert "api_key" not in body


@pytest.mark.asyncio
async def test_get_me_no_key(client: httpx.AsyncClient):
    """GET /agents/me without any X-Agent-Key header returns 401."""
    response = await client.get("/agents/me")
    assert response.status_code == 401
    assert "Missing X-Agent-Key header" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_me_bad_key(client: httpx.AsyncClient):
    """GET /agents/me with a key that does not match any agent returns 401."""
    response = await client.get(
        "/agents/me",
        headers={"X-Agent-Key": "this-key-does-not-exist-in-db"},
    )
    assert response.status_code == 401
    assert "Invalid API key" in response.json()["detail"]


@pytest.mark.asyncio
async def test_health_no_auth(client: httpx.AsyncClient):
    """GET /health is publicly accessible and returns status ok."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
