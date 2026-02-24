from contextlib import asynccontextmanager

import asyncpg
from fastapi import Request


def get_pool(request: Request) -> asyncpg.Pool:
    """Extract the asyncpg pool from FastAPI app state."""
    return request.app.state.db


@asynccontextmanager
async def acquire(pool: asyncpg.Pool):
    """Async context manager that yields a connection from the pool."""
    async with pool.acquire() as conn:
        yield conn
