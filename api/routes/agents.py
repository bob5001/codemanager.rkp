"""
Agent registration and profile routes.

POST /agents       — unauthenticated, registers a new agent and returns a one-time api_key
GET  /agents/me    — authenticated via X-Agent-Key header, returns calling agent's profile
"""

from __future__ import annotations

import hashlib
import secrets

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from api.deps import get_current_agent
from storage.agents import create_agent
from storage.database import get_pool

router = APIRouter()


# ── Pydantic models ────────────────────────────────────────────────────────────

class AgentRegisterRequest(BaseModel):
    name: str
    ecosystem: str
    capabilities: list[str] = []


class AgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    ecosystem: str
    capabilities: list[str]
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

    pool = get_pool(request)
    agent = await create_agent(
        pool,
        name=body.name,
        ecosystem=body.ecosystem,
        api_key_hash=key_hash,
        capabilities=body.capabilities,
    )

    data = _serialize_agent(agent, api_key=plaintext_key)
    return AgentRegisterResponse(**data)


@router.get("/me", response_model=AgentResponse)
async def get_me(agent: dict = Depends(get_current_agent)) -> AgentResponse:
    """
    Return the authenticated agent's profile.

    Authentication is performed by the get_current_agent dependency via the
    X-Agent-Key header.  The api_key_hash is never included in the response.
    """
    data = _serialize_agent(agent)
    return AgentResponse(**data)
