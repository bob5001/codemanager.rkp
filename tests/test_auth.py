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

from api.deps import is_local_request
from config import settings
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


# ── Local-request gating (is_local_request / registration bypass) ─────────────
#
# ASGITransport defaults its fake client to ("127.0.0.1", 123), which is why
# `client` above (and every test using it) registers as an active agent even
# when ADMIN_KEY is set in this repo's .env — it looks like a local caller.
# These tests target that behavior directly.

def _fake_request(client_host: str | None, headers: dict[str, str] | None = None):
    """Minimal Starlette Request exposing `.client` and `.headers` for is_local_request."""
    from starlette.requests import Request as StarletteRequest

    scope = {
        "type": "http",
        "client": (client_host, 12345) if client_host else None,
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
    }
    return StarletteRequest(scope)


def test_is_local_request_cloudflare_tunnel_is_remote(monkeypatch):
    """Tunnel traffic arrives from a local peer (cloudflared runs on this box)
    but carries Cf-Connecting-IP — it must not bypass the approval gate."""
    monkeypatch.setattr("api.deps._DOCKER_GATEWAY", "192.168.147.1")
    cf = {"Cf-Connecting-IP": "203.0.113.9"}
    assert is_local_request(_fake_request("127.0.0.1", cf)) is False
    assert is_local_request(_fake_request("192.168.147.1", cf)) is False


def test_is_local_request_loopback():
    assert is_local_request(_fake_request("127.0.0.1")) is True
    assert is_local_request(_fake_request("::1")) is True


def test_is_local_request_remote():
    assert is_local_request(_fake_request("203.0.113.5")) is False


def test_is_local_request_no_client():
    assert is_local_request(_fake_request(None)) is False


def test_is_local_request_docker_gateway(monkeypatch):
    """The Docker-NAT gateway address (see api.deps._read_default_gateway) counts
    as local too — it's what host-originated loopback traffic arrives as."""
    monkeypatch.setattr("api.deps._DOCKER_GATEWAY", "192.168.147.1")
    assert is_local_request(_fake_request("192.168.147.1")) is True
    assert is_local_request(_fake_request("192.168.1.50")) is False


@pytest.mark.skipif(not settings.admin_key, reason="ADMIN_KEY not set — registration is open regardless of origin")
@pytest.mark.asyncio
async def test_register_agent_remote_lands_pending():
    """A registration whose source IP isn't local lands in 'pending' and its
    key is rejected with 403 until an admin approves it — the gate the local
    bypass is meant to preserve for genuine LAN/remote callers."""
    async with LifespanManager(app) as manager:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=manager.app, client=("203.0.113.5", 12345)),
            base_url="http://test",
        ) as c:
            reg = await c.post(
                "/agents",
                json={"name": "remote-test-agent", "ecosystem": "python"},
            )
            assert reg.status_code == 200, reg.text
            body = reg.json()
            assert body["status"] == "pending"

            me = await c.get("/agents/me", headers={"X-Agent-Key": body["api_key"]})
            assert me.status_code == 403
            assert "pending admin approval" in me.json()["detail"]


@pytest.mark.skipif(not settings.admin_key, reason="ADMIN_KEY not set — registration is open regardless of origin")
@pytest.mark.asyncio
async def test_register_agent_local_lands_active(client: httpx.AsyncClient):
    """A registration from this machine (ASGITransport's default fake client
    is 127.0.0.1) is active immediately, even with ADMIN_KEY set."""
    reg = await client.post(
        "/agents",
        json={"name": "local-bypass-test-agent", "ecosystem": "python"},
    )
    assert reg.status_code == 200, reg.text
    assert reg.json()["status"] == "active"
