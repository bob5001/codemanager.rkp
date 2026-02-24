"""
FastAPI dependencies for request-level authentication.
"""

import hashlib

from fastapi import Header, HTTPException, Request

from storage.agents import get_agent_by_key_hash
from storage.database import get_pool


async def get_current_agent(
    request: Request,
    x_agent_key: str | None = Header(default=None),
) -> dict:
    """
    Authenticate the calling agent via the X-Agent-Key header.

    1. Read the raw key from the header.
    2. If missing → 401.
    3. SHA-256 hash the key.
    4. Look up the agent by hash (also updates last_seen).
    5. If not found → 401.
    6. Return the agent dict.
    """
    if not x_agent_key:
        raise HTTPException(status_code=401, detail="Missing X-Agent-Key header")

    key_hash = hashlib.sha256(x_agent_key.encode()).hexdigest()
    pool = get_pool(request)
    agent = await get_agent_by_key_hash(pool, key_hash)

    if agent is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return agent
