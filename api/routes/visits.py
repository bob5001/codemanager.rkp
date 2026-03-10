"""
Visit routes.

GET  /visits/{project_id} - list visits for a project
POST /visits              - log a new agent visit
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, field_validator

from api.deps import get_current_agent
from storage.database import get_pool
from storage.visits import get_visits, log_visit

router = APIRouter()


class VisitCreateRequest(BaseModel):
    project_id: str
    query: Optional[str] = None
    summary: str
    usefulness: Optional[int] = None
    confidence: Optional[float] = None
    model_used: Optional[str] = None

    @field_validator("summary")
    @classmethod
    def summary_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError(
                "summary is required and must not be empty. "
                "Write what you concluded — not what you did, but what you found. "
                "Only log a visit for a project you actually worked on."
            )
        return v


def _serialize_visit(visit: dict) -> dict:
    result = {}
    for key, value in visit.items():
        if hasattr(value, "isoformat"):
            result[key] = value.isoformat()
        elif hasattr(value, "hex"):
            result[key] = str(value)
        else:
            result[key] = value
    return result


@router.get("/{project_id}", status_code=200)
async def list_visits(
    project_id: str,
    request: Request,
    limit: int = 50,
    agent: dict = Depends(get_current_agent),
) -> list[dict]:
    """Return up to limit visits for a project, newest first."""
    pool = get_pool(request)
    visits = await get_visits(pool, project_id, limit)
    return [_serialize_visit(v) for v in visits]


@router.post("", status_code=200)
async def create_visit(
    body: VisitCreateRequest,
    request: Request,
    agent: dict = Depends(get_current_agent),
) -> dict:
    """Log a visit for the authenticated agent."""
    pool = get_pool(request)
    agent_id = str(agent["id"])
    visit = await log_visit(
        pool,
        project_id=body.project_id,
        agent_id=agent_id,
        query=body.query,
        summary=body.summary,
        usefulness=body.usefulness,
        confidence=body.confidence,
        model_used=body.model_used,
    )
    return _serialize_visit(visit)
