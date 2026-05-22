"""
Agent registration and profile routes.

POST /agents       — unauthenticated, registers a new agent and returns a one-time api_key
GET  /agents/me    — authenticated via X-Agent-Key header, returns calling agent's profile
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from api.deps import get_current_agent
from config import settings
from storage.agents import approve_agent, create_agent
from storage.database import get_pool

router = APIRouter()


# ── Pydantic models ────────────────────────────────────────────────────────────

class AgentRegisterRequest(BaseModel):
    name: str
    ecosystem: str
    capabilities: list[str] = Field(default_factory=list)


class AgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    ecosystem: str
    capabilities: list[str]
    status: str
    registered_at: str   # ISO 8601 string
    last_seen: str | None = None


class AgentRegisterResponse(AgentResponse):
    api_key: str   # plaintext — shown exactly once, never stored


# ── Helper ─────────────────────────────────────────────────────────────────────

def _serialize_agent(agent: dict, *, api_key: str | None = None) -> dict:
    """
    Convert a storage-layer agent dict to a JSON-serialisable dict.

    - Converts datetime objects to ISO 8601 strings.
    - Converts UUID objects to strings.
    - Excludes api_key_hash (never returned to callers).
    - Optionally injects the plaintext api_key (registration response only).
    """
    result = {
        "id": str(agent["id"]),
        "name": agent["name"],
        "ecosystem": agent["ecosystem"],
        "capabilities": agent["capabilities"],
        "status": agent.get("status", "active"),
        "registered_at": agent["registered_at"].isoformat(),
        "last_seen": agent["last_seen"].isoformat() if agent.get("last_seen") else None,
    }
    if api_key is not None:
        result["api_key"] = api_key
    return result


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.post("", response_model=AgentRegisterResponse, status_code=200)
async def register_agent(body: AgentRegisterRequest, request: Request) -> AgentRegisterResponse:
    """
    Register a new agent.

    Generates a cryptographically random API key, hashes it for storage,
    and returns the plaintext key once.  The key cannot be recovered after
    this response — the caller must store it securely.
    """
    plaintext_key = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(plaintext_key.encode()).hexdigest()
    status = "pending" if settings.admin_key else "active"

    pool = get_pool(request)
    agent = await create_agent(
        pool,
        name=body.name,
        ecosystem=body.ecosystem,
        api_key_hash=key_hash,
        capabilities=body.capabilities,
        status=status,
    )

    data = _serialize_agent(agent, api_key=plaintext_key)
    return AgentRegisterResponse(**data)


@router.post("/{agent_id}/approve", response_model=AgentResponse, status_code=200)
async def approve_agent_registration(
    agent_id: UUID,
    request: Request,
    x_admin_key: str | None = Header(default=None),
) -> AgentResponse:
    """
    Approve a pending agent registration.

    Requires the X-Admin-Key header to match the ADMIN_KEY environment variable.
    Once approved, the agent can authenticate normally with its API key.
    """
    if not settings.admin_key:
        raise HTTPException(status_code=403, detail="Admin approval is not configured (ADMIN_KEY not set)")
    if not x_admin_key or not hmac.compare_digest(x_admin_key, settings.admin_key):
        raise HTTPException(status_code=403, detail="Invalid admin key")

    pool = get_pool(request)
    agent = await approve_agent(pool, str(agent_id))
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")

    data = _serialize_agent(agent)
    return AgentResponse(**data)


@router.get("/me", response_model=AgentResponse)
async def get_me(agent: dict = Depends(get_current_agent)) -> AgentResponse:
    """
    Return the authenticated agent's profile.

    Authentication is performed by the get_current_agent dependency via the
    X-Agent-Key header.  The api_key_hash is never included in the response.
    """
    data = _serialize_agent(agent)
    return AgentResponse(**data)
