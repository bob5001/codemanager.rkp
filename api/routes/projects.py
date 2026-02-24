"""
Project routes.

GET  /projects               - list all projects (optional ?status= filter)
GET  /projects/{project_id}  - fetch a single project by UUID
POST /projects               - create a new project
PATCH /projects/{project_id} - update status / status_note / description
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel

from analyzers.runner import analyze_project
from api.deps import get_current_agent
from storage.database import get_pool
from storage.projects import (
    create_project,
    get_project,
    list_projects,
    update_project,
)

router = APIRouter()


class ProjectCreateRequest(BaseModel):
    name: str
    path: Optional[str] = None
    github_url: Optional[str] = None
    description: Optional[str] = None


class ProjectPatchRequest(BaseModel):
    status: Optional[str] = None
    status_note: Optional[str] = None
    description: Optional[str] = None


def _serialize_project(project: dict) -> dict:
    result = {}
    for key, value in project.items():
        if hasattr(value, "isoformat"):
            result[key] = value.isoformat()
        elif hasattr(value, "hex"):
            result[key] = str(value)
        else:
            result[key] = value
    return result


@router.get("", status_code=200)
async def list_all_projects(
    request: Request,
    status: Optional[str] = None,
    agent: dict = Depends(get_current_agent),
) -> list[dict]:
    pool = get_pool(request)
    projects = await list_projects(pool, status_filter=status)
    return [_serialize_project(p) for p in projects]


@router.get("/{project_id}", status_code=200)
async def get_one_project(
    project_id: str,
    request: Request,
    agent: dict = Depends(get_current_agent),
) -> dict:
    pool = get_pool(request)
    project = await get_project(pool, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return _serialize_project(project)


@router.post("", status_code=201)
async def create_new_project(
    body: ProjectCreateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    agent: dict = Depends(get_current_agent),
) -> dict:
    pool = get_pool(request)
    project = await create_project(
        pool,
        name=body.name,
        path=body.path,
        github_url=body.github_url,
        description=body.description,
    )
    project_id = str(project["id"])

    if project.get("path"):
        background_tasks.add_task(analyze_project, pool, project_id, project["path"])

    return _serialize_project(project)


@router.patch("/{project_id}", status_code=200)
async def patch_project(
    project_id: str,
    body: ProjectPatchRequest,
    request: Request,
    agent: dict = Depends(get_current_agent),
) -> dict:
    pool = get_pool(request)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    updated = await update_project(pool, project_id, **fields)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return _serialize_project(updated)
