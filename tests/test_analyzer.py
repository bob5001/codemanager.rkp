"""
Tests for the local filesystem analyzer and Ollama summarizer.

Tests 1-5 are pure unit tests (no DB, no Ollama).
Test 6 calls Ollama live (requires localhost:11434 to be running).
Test 7 is a DB integration test with Ollama mocked.

Run all:          pytest tests/test_analyzer.py -v
Skip Ollama:      pytest tests/test_analyzer.py -v -k "not ollama and not analyze"
"""
from __future__ import annotations

import math
import os
import sys
import uuid
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest
import pytest_asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DB_HOST", "localhost")

from analyzers.local import walk_project
from analyzers.summarizer import embed_text, summarize_project

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DSN = "postgresql://rkp_user:rkp_password@localhost:5433/rkp_core"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture()
async def pool():
    p = await asyncpg.create_pool(DB_DSN)
    yield p
    await p.close()


# ── walk_project tests ────────────────────────────────────────────────────────

def test_walk_project_on_itself():
    result = walk_project(PROJECT_ROOT)
    assert result["total_files"] > 0
    assert "python" in result["languages"]
    assert isinstance(result["entry_points"], list)
    assert isinstance(result["file_tree"], dict)
    assert result["total_lines"] > 0


def test_walk_project_skips_venv():
    result = walk_project(PROJECT_ROOT)
    for rel_path in result["file_tree"]:
        first = rel_path.split(os.sep)[0]
        assert first not in (".venv", "venv"), f"venv leaked into file_tree: {rel_path}"


def test_walk_project_key_files():
    result = walk_project(PROJECT_ROOT)
    assert any("requirements.txt" in kf for kf in result["key_files"])


# ── embed_text tests ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_embed_text_returns_768_floats():
    """embed_text returns 768-dim floats (Ollama nomic-embed-text or fallback stub)."""
    vec = await embed_text("hello world")
    assert len(vec) == 768
    assert all(isinstance(x, float) for x in vec)
    assert math.sqrt(sum(x * x for x in vec)) > 0.0


@pytest.mark.asyncio
async def test_embed_text_deterministic():
    """Same input → same vector (true for both Ollama and stub)."""
    assert await embed_text("hello") == await embed_text("hello")


# ── Ollama live test ──────────────────────────────────────────────────────────

@pytest.mark.slow
@pytest.mark.asyncio
async def test_summarize_project_via_ollama():
    """Calls Ollama live — requires a warm qwen2.5-coder model at localhost:11434.
    Run with: pytest -m slow tests/test_analyzer.py::test_summarize_project_via_ollama
    """
    walk = walk_project(PROJECT_ROOT)
    summary = await summarize_project(walk)
    assert isinstance(summary, str)
    assert len(summary) > 20, f"Summary too short: {summary!r}"
    print(f"\nOllama summary: {summary}")


# ── Full pipeline integration test (Ollama mocked) ───────────────────────────

@pytest.mark.asyncio
async def test_analyze_project_success(pool):
    """Runs the full pipeline with summarize_project mocked; verifies DB state."""
    from analyzers.runner import analyze_project
    from storage.projects import create_project, get_latest_snapshot, get_project

    project_name = f"test-analyzer-{uuid.uuid4()}"
    project = await create_project(pool, name=project_name, path=PROJECT_ROOT)
    project_id = str(project["id"])

    try:
        mock_summary = "A test summary of codemanager.rkp."
        # Also mock embed_text so the test is independent of DB column width
        # (migration 001 changes vector(1536)→vector(768); until applied, use None)
        with patch(
            "analyzers.runner.summarize_project",
            new=AsyncMock(return_value=mock_summary),
        ), patch(
            "analyzers.runner.embed_text",
            new=AsyncMock(return_value=None),
        ):
            await analyze_project(pool, project_id, PROJECT_ROOT)

        updated = await get_project(pool, project_id)
        assert updated["status"] == "analyzed", f"Got status: {updated['status']}"
        assert updated["summary"] == mock_summary
        assert updated["last_analyzed"] is not None

        snapshot = await get_latest_snapshot(pool, project_id)
        assert snapshot is not None
        assert "total_files" in snapshot["key_findings"]
        assert snapshot["key_findings"]["total_files"] > 0
    finally:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM codemanager.snapshots WHERE project_id = $1", project_id
            )
            await conn.execute(
                "DELETE FROM codemanager.projects WHERE id = $1", project_id
            )
