"""
Project summarizer — uses local Ollama for text generation.

summarize_project()  → calls Ollama chat API (qwen2.5-coder by default)
embed_text()         → deterministic stub (unit-length vector seeded by text hash)
                       TODO Step 6: replace with real embedding model
"""
from __future__ import annotations

import hashlib
import random
from pathlib import Path

import httpx

from config import settings

OLLAMA_BASE_URL = settings.ollama_base_url
OLLAMA_MODEL = settings.ollama_model


async def summarize_project(
    walk_result: dict,
    existing_description: str | None = None,
) -> str:
    """
    Send walk_result to a local Ollama model and get a 2-4 sentence summary.
    Uses the Ollama /api/chat endpoint with stream=False.
    """
    root = walk_result.get("root", "unknown")
    total_files = walk_result.get("total_files", 0)
    total_lines = walk_result.get("total_lines", 0)
    languages = walk_result.get("languages", {})
    entry_points = walk_result.get("entry_points", [])
    key_files = walk_result.get("key_files", [])

    lang_summary = ", ".join(
        f"{lang}: {count}"
        for lang, count in sorted(languages.items(), key=lambda x: -x[1])
    )

    readme_snippet = ""
    for candidate in ("README.md", "README.rst"):
        readme_path = Path(root) / candidate
        if readme_path.exists():
            try:
                readme_snippet = readme_path.read_text(
                    encoding="utf-8", errors="replace"
                )[:500]
            except OSError:
                pass
            break

    prompt_parts = [
        "Summarise this software project in 2-4 concise sentences.",
        "",
        f"Root path: {root}",
        f"Total files: {total_files}",
        f"Total lines: {total_lines}",
        f"Languages: {lang_summary or 'unknown'}",
        f"Entry points: {', '.join(entry_points) or 'none detected'}",
        f"Key files: {', '.join(key_files) or 'none detected'}",
    ]
    if existing_description:
        prompt_parts += ["", f"Existing description: {existing_description}"]
    if readme_snippet:
        prompt_parts += ["", "README (first 500 chars):", readme_snippet]

    prompt = "\n".join(prompt_parts)

    async with httpx.AsyncClient(timeout=float(settings.ollama_timeout)) as client:
        response = await client.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json={
                "model": OLLAMA_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "options": {"num_predict": 256},
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["message"]["content"].strip()


async def embed_text(text: str) -> list[float]:
    """
    Produce a 768-dim embedding via Ollama's nomic-embed-text model.
    Calls POST /api/embeddings at the configured Ollama base URL.

    Requires migration 001_vector_768.sql to have been applied (changes
    schema embedding columns from vector(1536) → vector(768)).

    Falls back to a deterministic stub if Ollama is unreachable, so the
    pipeline can still run without embeddings being meaningful.
    """
    try:
        async with httpx.AsyncClient(timeout=float(settings.ollama_timeout)) as client:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/embeddings",
                json={"model": settings.ollama_embed_model, "prompt": text},
            )
            response.raise_for_status()
            return response.json()["embedding"]
    except Exception:
        # Fallback: deterministic unit-length stub (meaningless but safe)
        seed = int(hashlib.md5(text.encode()).hexdigest(), 16)
        rng = random.Random(seed)
        vec = [rng.uniform(-1, 1) for _ in range(768)]
        mag = sum(x * x for x in vec) ** 0.5
        return [x / mag for x in vec]
