from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI

from config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage asyncpg connection pool for the lifetime of the service."""
    app.state.db = await asyncpg.create_pool(
        dsn=settings.get_dsn(),
        min_size=2,
        max_size=10,
    )
    yield
    await app.state.db.close()


app = FastAPI(
    title="codemanager.rkp",
    version="0.1.0",
    description="Locally-hosted REST API + MCP server giving AI agents shared knowledge of codebases.",
    lifespan=lifespan,
)


# -- Core routes ---------------------------------------------------------------

@app.get("/health", tags=["meta"])
async def health():
    """Liveness check - confirms the service is up and the DB pool is open."""
    return {"status": "ok", "service": "codemanager.rkp"}


# -- Agent routes --------------------------------------------------------------

from api.routes import agents as agents_router  # noqa: E402

app.include_router(agents_router.router, prefix="/agents", tags=["agents"])


# -- Project routes ------------------------------------------------------------

from api.routes import projects as projects_router  # noqa: E402

app.include_router(projects_router.router, prefix="/projects", tags=["projects"])


# -- Visit routes --------------------------------------------------------------

from api.routes import visits as visits_router  # noqa: E402

app.include_router(visits_router.router, prefix="/visits", tags=["visits"])


# -- Search routes -------------------------------------------------------------

from api.routes import search as search_router  # noqa: E402

app.include_router(search_router.router, tags=["search"])
