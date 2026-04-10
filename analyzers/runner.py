from __future__ import annotations

from datetime import datetime, timezone

import asyncpg

from analyzers.local import walk_project
from analyzers.summarizer import embed_text, summarize_project
from storage.projects import create_snapshot, update_project


async def analyze_project(pool: asyncpg.Pool, project_id: str, path: str) -> None:
    """
    Full analysis pipeline:
    1. Set status → 'analyzing'
    2. Walk the local filesystem
    3. Summarise with Ollama
    4. Embed the summary (stub until Step 6)
    5. Save snapshot (file_tree, key_findings)
    6. Update project (summary, embedding, status → 'analyzed', last_analyzed)
    7. On any error: set status → 'stuck', status_note → error message
    """
    try:
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

        await update_project(
            pool,
            project_id,
            summary=summary,
            embedding=embedding,
            status="analyzed",
            status_note="",
            last_analyzed=datetime.now(timezone.utc),
        )

    except Exception as exc:
        await update_project(
            pool,
            project_id,
            status="stuck",
            status_note=f"Analysis failed: {str(exc)[:200]}",
        )
