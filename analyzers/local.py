import os
from pathlib import Path

SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".pytest_cache", "dist", "build", ".mypy_cache", ".ruff_cache",
    "htmlcov", ".tox", "reference",
}
SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".pyd", ".so", ".dylib", ".dll", ".egg-info",
    ".lock", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf",
    ".zip", ".tar", ".gz", ".whl",
}
MAX_FILE_SIZE_BYTES = 50_000

_ENTRY_POINT_NAMES = {
    "main.py", "app.py", "server.py", "index.js", "index.ts",
    "manage.py", "__main__.py", "Makefile", "docker-compose.yml", "Dockerfile",
}
_KEY_FILE_NAMES = {
    "README.md", "README.rst", "setup.py", "pyproject.toml",
    "package.json", "requirements.txt", "go.mod", "Cargo.toml", ".env.example",
}
_EXT_TO_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".go": "go", ".rs": "rust", ".java": "java", ".rb": "ruby",
    ".sh": "shell", ".sql": "sql", ".md": "markdown",
    ".yml": "yaml", ".yaml": "yaml", ".json": "json",
    ".toml": "toml", ".html": "html", ".css": "css",
}


def walk_project(root: str) -> dict:
    """
    Walk a local project directory and return:
    {
        root, file_tree, entry_points, key_files,
        languages, total_files, total_lines
    }
    """
    root_path = Path(root).resolve()
    file_tree: dict = {}
    entry_points: list = []
    key_files: list = []
    languages: dict = {}
    total_lines = 0

    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]

        for filename in filenames:
            filepath = Path(dirpath) / filename
            suffix = filepath.suffix.lower()

            if suffix in SKIP_EXTENSIONS:
                continue

            try:
                size = filepath.stat().st_size
            except OSError:
                continue

            if size > MAX_FILE_SIZE_BYTES:
                continue

            lines = 0
            try:
                content = filepath.read_text(encoding="utf-8", errors="replace")
                lines = content.count("\n")
            except OSError:
                pass

            rel = str(filepath.relative_to(root_path))
            file_tree[rel] = {"size": size, "lines": lines}
            total_lines += lines

            lang = _EXT_TO_LANG.get(suffix)
            if lang:
                languages[lang] = languages.get(lang, 0) + 1

            if filename in _ENTRY_POINT_NAMES and rel not in entry_points:
                entry_points.append(rel)

            if filename in _KEY_FILE_NAMES and rel not in key_files:
                key_files.append(rel)

    return {
        "root": str(root_path),
        "file_tree": file_tree,
        "entry_points": entry_points,
        "key_files": key_files,
        "languages": languages,
        "total_files": len(file_tree),
        "total_lines": total_lines,
    }
