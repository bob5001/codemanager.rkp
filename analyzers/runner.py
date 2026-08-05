from __future__ import annotations

from datetime import datetime, timezone

import asyncpg

from analyzers.local import walk_project
from analyzers.summarizer import embed_text, summarize_project
from storage.projects import create_snapshot, get_project, update_project

# Statuses owned by the analysis pipeline. Anything else (in_development, alpha,
# testing, production, deprecated, archived) is a curated lifecycle status set by
# a human or an agent, and analysis must never overwrite it — re-analysing a
# project is a data refresh, not a statement about where the build stands.
_PIPELINE_STATUSES = frozenset(
    {"registered", "analyzing", "partial", "analyzed", "stuck"}
)

# Marker for the analyzer's own line in status_note. It is stripped and rewritten
# on every run so repeated failures never accumulate, and a human-written note
# above it is always preserved.
_STAMP = "[analysis]"

# Notes written by the pre-stamp version of this module, which overwrote the
# whole field. Treated as analyzer-generated so they get cleaned up.
_LEGACY_PREFIX = "Analysis failed:"


def _strip_stamp(note: str | None) -> str:
    """Return `note` with any analyzer-generated text removed."""
    if not note:
        return ""
    if note.startswith(_LEGACY_PREFIX) or note.startswith(_STAMP):
        return ""
    marker = note.rfind(f"\n\n{_STAMP}")
    return note[:marker].rstrip() if marker != -1 else note


def _with_stamp(note: str, message: str) -> str:
    """Append a dated analyzer stamp beneath any human-written note."""
    today = datetime.now(timezone.utc).date().isoformat()
    stamp = f"{_STAMP} {today} {message}"
    return f"{note}\n\n{stamp}" if note else stamp


async def analyze_project(pool: asyncpg.Pool, project_id: str, path: str) -> None:
    """
    Full analysis pipeline:
    1. Walk the local filesystem
    2. Summarise with Ollama
    3. Embed the summary
    4. Save snapshot (file_tree, key_findings)
    5. Update project (summary, embedding, last_analyzed)
    6. On any error: record the failure in status_note

    Status handling: a project sitting on a pipeline status is moved
    'analyzing' → 'analyzed' (or 'stuck' on failure). A project on a curated
    lifecycle status keeps that status throughout — it is never set to
    'analyzing', so a crash mid-run cannot strand it either.
    """
    project = await get_project(pool, project_id)
    prior_status = (project or {}).get("status") or "registered"
    prior_note = (project or {}).get("status_note") or ""

    # Human-written portion of the note, minus any stamp from a previous run.
    base_note = _strip_stamp(prior_note)
    pipeline_owned = prior_status in _PIPELINE_STATUSES

    try:
        if pipeline_owned:
            await update_project(pool, project_id, status="analyzing")

        walk = walk_project(path)
        summary = await summarize_project(walk)
        embedding = await embed_text(summary)

        key_findings = {
            "total_files": walk["total_files"],
            "total_lines": walk["total_lines"],
            "languages": walk["languages"],
            "entry_points": walk["entry_points"],
            "key_files": walk["key_files"],
        }

        await create_snapshot(
            pool,
            project_id=project_id,
            file_tree=walk["file_tree"],
            key_findings=key_findings,
            embedding=embedding,
        )

        updates: dict = {
            "summary": summary,
            "embedding": embedding,
            "last_analyzed": datetime.now(timezone.utc),
        }
        if pipeline_owned:
            updates["status"] = "analyzed"
        # Only touch status_note if a stale stamp needs clearing.
        if base_note != prior_note:
            updates["status_note"] = base_note

        await update_project(pool, project_id, **updates)

    except Exception as exc:
        updates = {"status_note": _with_stamp(base_note, f"failed: {str(exc)[:200]}")}
        if pipeline_owned:
            updates["status"] = "stuck"
        await update_project(pool, project_id, **updates)
