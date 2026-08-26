"""
FastAPI dependencies for request-level authentication.
"""

import hashlib
import socket
import struct

from fastapi import Header, HTTPException, Request

from storage.agents import get_agent_by_key_hash
from storage.database import get_pool

_LOOPBACK_ADDRS = {"127.0.0.1", "::1"}


def _read_default_gateway() -> str | None:
    """
    Best-effort: the container's default gateway, read from /proc/net/route.

    Docker (and OrbStack) NAT-hairpin loopback connections: when the host
    machine curls its own published port (e.g. `curl localhost:8007`), the
    request arrives here with a source IP of the bridge gateway, not
    127.0.0.1 — confirmed via docker logs (source showed as the network's
    Gateway address, not loopback). Treating that gateway address as local
    lets same-machine callers through; a genuine LAN or remote client's
    source IP won't match it. Linux-only; returns None if unreadable (host
    networking, non-Linux runtime), and callers fall back to loopback-only.
    """
    try:
        with open("/proc/net/route") as f:
            for line in f.readlines()[1:]:
                fields = line.split()
                if len(fields) >= 3 and fields[1] == "00000000":
                    return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
    except (OSError, ValueError):
        pass
    return None


_DOCKER_GATEWAY = _read_default_gateway()


def is_local_request(request: Request) -> bool:
    """
    True if the request originated from this machine.

    Matches true loopback (127.0.0.1 / ::1 — bare-metal installs, or an
    ASGI test client) plus the Docker-NAT gateway address that
    host-originated localhost traffic gets rewritten to (see
    _read_default_gateway). Used to let local callers skip agent-approval
    gating even when ADMIN_KEY is set — the gate exists for LAN/remote
    callers, not for the machine running the service.
    """
    host = request.client.host if request.client else None
    if host is None:
        return False
    return host in _LOOPBACK_ADDRS or (host == _DOCKER_GATEWAY and _DOCKER_GATEWAY is not None)


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

    if agent.get("status") == "pending":
        raise HTTPException(status_code=403, detail="Registration pending admin approval")

    return agent
