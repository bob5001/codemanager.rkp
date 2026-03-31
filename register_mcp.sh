#!/usr/bin/env bash
# Register codemanager as an MCP server in Claude Code (stdio transport).
#
# Run this once:  bash register_mcp.sh
#
# The MCP server connects directly to the local Postgres DB — no REST API
# or HTTP server required when using stdio transport.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${SCRIPT_DIR}/.venv/bin/python"

if [[ ! -x "$PYTHON" ]]; then
  echo "ERROR: virtualenv not found at .venv — run: python -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

claude mcp add -s user codemanager -- "$PYTHON" "${SCRIPT_DIR}/mcp_server.py"

echo ""
echo "Done. Verify with:  claude mcp list"
echo ""
echo "Tools available in Claude Code:"
echo "  - list_all_projects     — list tracked codebases"
echo "  - get_project_detail    — full project info + latest snapshot"
echo "  - register_project      — add + analyse a new project"
echo "  - search_projects       — semantic similarity search"
echo "  - get_visit_history     — past agent findings for a project"
echo "  - record_visit          — share your findings for future agents"
echo "  - update_project_status — set lifecycle status + notes"
