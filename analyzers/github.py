"""
GitHub repository search and metadata fetching via PyGithub.

search_github(query, limit)  → list of enriched repo dicts
get_repo_detail(full_name)   → single repo dict with README snippet
"""
from __future__ import annotations

import asyncio
import base64

from github import Github, GithubException
from config import settings


def _get_client() -> Github:
    return Github(settings.github_token) if settings.github_token else Github()


def _fetch_search(query: str, limit: int) -> list[dict]:
    """Blocking: run in thread via asyncio.to_thread."""
    g = _get_client()
    results = []
    try:
        repos = g.search_repositories(query=query, sort="stars", order="desc")
        for repo in repos[:limit]:
            readme_snippet = ""
            try:
                readme = repo.get_readme()
                content = base64.b64decode(readme.content).decode("utf-8", errors="replace")
                readme_snippet = content[:500]
            except GithubException:
                pass

            results.append({
                "source": "github",
                "full_name": repo.full_name,
                "name": repo.name,
                "description": repo.description or "",
                "html_url": repo.html_url,
                "stars": repo.stargazers_count,
                "language": repo.language,
                "topics": repo.get_topics(),
                "readme_snippet": readme_snippet,
                # embed_text input: combine description + readme for ranking
                "_embed_input": f"{repo.name} {repo.description or ''} {readme_snippet}",
            })
    except GithubException as e:
        raise RuntimeError(f"GitHub search failed: {e}") from e
    return results


async def search_github(query: str, limit: int = 10) -> list[dict]:
    """Async wrapper — runs the blocking PyGithub calls in a thread."""
    return await asyncio.to_thread(_fetch_search, query, limit)


def _fetch_repo(full_name: str) -> dict:
    g = _get_client()
    try:
        repo = g.get_repo(full_name)
    except GithubException as e:
        raise RuntimeError(f"Repo not found: {full_name}") from e

    readme_snippet = ""
    try:
        readme = repo.get_readme()
        content = base64.b64decode(readme.content).decode("utf-8", errors="replace")
        readme_snippet = content[:1000]
    except GithubException:
        pass

    return {
        "source": "github",
        "full_name": repo.full_name,
        "name": repo.name,
        "description": repo.description or "",
        "html_url": repo.html_url,
        "stars": repo.stargazers_count,
        "language": repo.language,
        "topics": repo.get_topics(),
        "readme_snippet": readme_snippet,
    }


async def get_repo_detail(full_name: str) -> dict:
    """Async wrapper for fetching a single repo's details."""
    return await asyncio.to_thread(_fetch_repo, full_name)
