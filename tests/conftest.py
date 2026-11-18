"""Shared test setup.

1. Load .env / .env.local into os.environ before the test modules build their DSNs.
   The test modules read DB_* straight from os.environ, but pydantic-settings reads
   .env files without exporting them, so without this DB_PASSWORD is empty and every
   DB-backed test fails with InvalidPasswordError. .env.local wins over .env (it
   points host-side processes at localhost:5433 instead of the compose-network
   `db:5432`), and anything already exported in the shell wins over both.

2. Sweep test agents when the session ends. The suite runs against the live
   rkp_core database and several tests register agents through POST /agents
   without deleting them. Leftovers piled up (76 by 2026-11-18, 19 of them stuck in
   the dashboard's pending-approval list). At session end we delete agents that
   (a) did not exist when the session started AND (b) have a test-style name.
   Both conditions are required, so a real agent that registers while the
   tests are running is never touched.

   Convention: name any agent a test creates with "test" as a hyphen-separated
   word (e.g. "test-agent", "route-test-<uuid>", "remote-test-agent").
"""
import asyncio
import os
import re
from pathlib import Path

import pytest
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(_ROOT / ".env.local", override=False)
load_dotenv(_ROOT / ".env", override=False)

TEST_AGENT_NAME = re.compile(r"(^|-)test(-|$)")


def _dsn() -> str:
    return (
        f"postgresql://{os.getenv('DB_USER', 'rkp_user')}:{os.getenv('DB_PASSWORD', '')}"
        f"@{os.getenv('DB_HOST', 'localhost')}:{os.getenv('DB_PORT', '5433')}"
        f"/{os.getenv('DB_NAME', 'rkp_core')}"
    )


async def _agent_ids() -> set[str] | None:
    import asyncpg

    try:
        conn = await asyncpg.connect(_dsn(), timeout=5)
    except Exception:
        return None  # DB unreachable — DB tests will fail loudly on their own
    try:
        rows = await conn.fetch("SELECT id FROM codemanager.agents")
        return {str(r["id"]) for r in rows}
    finally:
        await conn.close()


async def _delete_new_test_agents(before: set[str]) -> int:
    import asyncpg

    conn = await asyncpg.connect(_dsn(), timeout=5)
    try:
        rows = await conn.fetch("SELECT id, name FROM codemanager.agents")
        doomed = [
            r["id"] for r in rows
            if str(r["id"]) not in before and TEST_AGENT_NAME.search(r["name"] or "")
        ]
        if doomed:
            await conn.execute(
                "DELETE FROM codemanager.agents WHERE id = ANY($1::uuid[])", doomed
            )
        return len(doomed)
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def _sweep_test_agents():
    before = asyncio.run(_agent_ids())
    yield
    if before is not None:
        asyncio.run(_delete_new_test_agents(before))
