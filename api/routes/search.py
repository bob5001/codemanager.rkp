"""
Semantic search route.

POST /search — embeds the query via Ollama nomic-embed-text, runs a
               pgvector cosine similarity search across codemanager.projects,
               and optionally merges ranked GitHub repository results.

Each call automatically logs a visit for every local project returned in
results, recording the query and the calling agent.  This lets future agents
see which codebases past agents found relevant for a given query.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from pydantic import BaseModel

from analyzers.github import search_github
from analyzers.summarizer import embed_text
from api.deps import get_current_agent
from storage.database import get_pool
from storage.visits import log_visit

router = APIRouter()


class SearchRequest(BaseModel):
    query: str
    limit: int = 10
    status_filter: str | None = None   # narrow local results to a status
    include_github: bool = False        # also search GitHub and merge results


@router.post("/search", status_code=200)
async def search(
    body: SearchRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    agent: dict = Depends(get_current_agent),
) -> dict:
    """
    Semantic search across tracked local projects + optionally GitHub.

    Local results: pgvector cosine similarity on project embeddings.
    GitHub results: PyGithub search → embed description+README → rank by
                    cosine similarity to the same query vector, merged and
                    sorted with local results by score.

    Projects/repos with no embedding are excluded from local results.
    """
    pool = get_pool(request)

    # Embed the query — both local and GitHub use the same vector
    query_vec = await embed_text(body.query)
    vec_str = "[" + ",".join(str(x) for x in query_vec) + "]"

    # ── Local pgvector search ────────────────────────────────────────────────
    if body.status_filter:
        sql = """
            SELECT
                id, name, path, github_url, description, summary,
                status, status_note, last_analyzed, created_at,
                1 - (embedding <=> $1::vector) AS similarity
            FROM codemanager.projects
            WHERE embedding IS NOT NULL
              AND status = $3
            ORDER BY embedding <=> $1::vector
            LIMIT $2
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, vec_str, body.limit, body.status_filter)
    else:
        sql = """
            SELECT
                id, name, path, github_url, description, summary,
                status, status_note, last_analyzed, created_at,
                1 - (embedding <=> $1::vector) AS similarity
            FROM codemanager.projects
            WHERE embedding IS NOT NULL
            ORDER BY embedding <=> $1::vector
            LIMIT $2
        """
        async with pool.acquire() as conn:
            rows = await conn.fetch(sql, vec_str, body.limit)

    local_results = []
    for row in rows:
        r = {"source": "local"}
        for key, value in dict(row).items():
            if hasattr(value, "isoformat"):
                r[key] = value.isoformat()
            elif hasattr(value, "hex"):
                r[key] = str(value)
            elif isinstance(value, float):
                r[key] = round(value, 4)
            else:
                r[key] = value
        local_results.append(r)

    # ── GitHub search (optional) ────────────────────────────────────────────
    github_results = []
    if body.include_github and body.query.strip():
        try:
            gh_repos = await search_github(body.query, limit=body.limit)

            # Embed each repo's description+README in parallel, then score
            async def _score_repo(repo: dict) -> dict:
                try:
                    repo_vec = await embed_text(repo["_embed_input"])
                    # Cosine similarity = dot product of unit vectors
                    dot = sum(a * b for a, b in zip(query_vec, repo_vec))
                    norm_q = sum(x * x for x in query_vec) ** 0.5
                    norm_r = sum(x * x for x in repo_vec) ** 0.5
                    similarity = dot / (norm_q * norm_r) if norm_q and norm_r else 0.0
                    repo["similarity"] = round(similarity, 4)
                except Exception:
                    repo["similarity"] = 0.0
                repo.pop("_embed_input", None)
                return repo

            github_results = await asyncio.gather(*[_score_repo(r) for r in gh_repos])
        except Exception as e:
            github_results = [{"source": "github", "error": str(e)}]

    # ── Merge and sort ───────────────────────────────────────────────────────
    all_results = local_results + list(github_results)
    all_results.sort(key=lambda r: r.get("similarity", 0.0), reverse=True)

    # ── Auto-log a visit for each local project in results ───────────────────
    # Runs in the background so it doesn't delay the response.
    if local_results:
        agent_id = str(agent["id"])

        async def _log_search_visits() -> None:
            for result in local_results:
                project_id = result.get("id")
                if not project_id:
                    continue
                try:
                    await log_visit(
                        pool,
                        project_id=str(project_id),
                        agent_id=agent_id,
                        query=body.query,
                    )
                except Exception:
                    pass  # never fail the search response due to visit logging

        background_tasks.add_task(_log_search_visits)

    return {
        "query": body.query,
        "count": len(all_results),
        "local_count": len(local_results),
        "github_count": len(github_results),
        "results": all_results,
    }
