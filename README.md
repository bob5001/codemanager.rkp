# codemanager.rkp

A locally-hosted project intelligence broker. Any AI agent — Claude, Cursor, AutoGen, Ollama, or anything else — can register codebases, run semantic search across them, and read/write a shared visit log. The knowledge accumulates across sessions and agents.

```
┌─────────────────────────────────────────────────────┐
│                  codemanager.rkp                    │
│                                                     │
│   REST API (port 8007)   MCP server (port 8008)     │
│         │                       │                   │
│         └──────────┬────────────┘                   │
│                    │                                │
│            asyncpg pool                             │
│                    │                                │
│         Postgres · codemanager schema               │
│         (agents · projects · snapshots · visits)    │
│                    │                                │
│         pgvector · nomic-embed-text (768d)          │
│         Ollama  · qwen2.5-coder (summaries)         │
└─────────────────────────────────────────────────────┘
```

## Quick start

**Prerequisites:** Docker, Postgres (port 5433), Ollama running locally.

```bash
# 1. Clone and enter
git clone <repo> && cd codemanager.rkp

# 2. Create virtualenv and install
python -m venv .venv && .venv/bin/pip install -r requirements.txt

# 3. Copy and configure env
cp .env.example .env   # edit DB_HOST, tokens as needed

# 4. Apply schema to your Postgres instance
PGPASSWORD=$DB_PASSWORD psql -h localhost -p 5433 -U rkp_user -d rkp_core -f schema.sql

# 5. Apply the vector-dimension migration (768d for nomic-embed-text)
PGPASSWORD=$DB_PASSWORD psql -h localhost -p 5433 -U rkp_user -d rkp_core -f migrations/001_vector_768.sql

# 6. Pull the Ollama models
ollama pull qwen2.5-coder
ollama pull nomic-embed-text

# 7. Start the REST API
.venv/bin/uvicorn main:app --port 8007 --reload

# 8. Register the MCP server with Claude Code
bash register_mcp.sh
```

Or run everything via Docker:

```bash
docker compose up                          # REST API only
docker compose --profile mcp up           # + MCP SSE server
docker compose --profile tunnel up        # + Cloudflare Tunnel
```

## API overview

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/agents` | none | Register an agent, get API key |
| GET | `/agents/me` | key | Your agent profile |
| GET | `/projects` | key | List projects (optional `?status=`) |
| POST | `/projects` | key | Register project, trigger analysis |
| GET | `/projects/{id}` | key | Project detail + latest snapshot |
| PATCH | `/projects/{id}` | key | Update status / status_note |
| POST | `/search` | key | Semantic search (local + optional GitHub) |
| GET | `/visits/{project_id}` | key | Visit history for a project |
| POST | `/visits` | key | Log a visit manually |
| GET | `/health` | none | Liveness check |

Pass your API key as the `X-Agent-Key` header. Interactive docs at `http://localhost:8007/docs`.

## MCP tools (Claude Code)

After `bash register_mcp.sh`, these tools are available inside any Claude Code session:

| Tool | What it does |
|------|-------------|
| `list_all_projects` | List tracked codebases, optionally filtered by status |
| `get_project_detail` | Full project info + latest file-tree snapshot |
| `register_project` | Add and analyse a new project |
| `search_projects` | Semantic similarity search (local + optional GitHub) |
| `get_visit_history` | Past agent findings for a project |
| `record_visit` | Share your findings for future agents |
| `update_project_status` | Set lifecycle status + optional note |

## Configuration

All settings load from `.env` via pydantic-settings. Key variables:

| Variable | Default | Notes |
|----------|---------|-------|
| `DB_HOST` | `localhost` | Use `host.docker.internal` inside Docker |
| `DB_PORT` | `5433` | |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | |
| `OLLAMA_MODEL` | `qwen2.5-coder:latest` | For summaries |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | 768-dim embeddings |
| `OLLAMA_TIMEOUT` | `300` | Seconds; first model load is slow |
| `GITHUB_TOKEN` | `` | Fine-grained PAT for GitHub search |
| `APP_PORT` | `8007` | REST API port |

## Project status lifecycle

```
registered → analyzing → partial → analyzed
                                      ↓
                              in_development → alpha → testing → production
                                      ↓
                               stuck / deprecated / archived
```

## Running tests

```bash
.venv/bin/pytest tests/ -v -m "not slow"   # fast suite (~8s)
.venv/bin/pytest tests/ -v                 # includes live Ollama test
```

## Remote access (Cloudflare Tunnel)

See [cloudflare/config.yml](cloudflare/config.yml) for setup. Once configured:

```bash
docker compose --profile tunnel up
```

Exposes the REST API and MCP SSE endpoint over HTTPS via your Cloudflare domain.

## Testing and troubleshooting the MCP

The MCP server runs as a stdio subprocess launched by Claude Code — it's separate from the Docker REST API container.

**Verify the MCP is registered:**
```bash
claude mcp list
# Should show: codemanager → /path/to/.venv/bin/python mcp_server.py
```

**Test a tool call directly** (no Claude needed):
```bash
.venv/bin/python - <<'EOF'
import asyncio, mcp_server
async def test():
    result = await mcp_server.list_all_projects()
    print(result)
asyncio.run(test())
EOF
```

**MCP not showing up in Claude Code?** Re-register it:
```bash
bash register_mcp.sh
# Then restart Claude Code for the new registration to take effect
```

**Tools failing with a DB auth error?**
The MCP subprocess loads `.env` at startup. If you rotated `DB_PASSWORD` mid-session, the running subprocess has stale creds. Restart Claude Code to reload.

**SSE transport (port 8008) vs stdio:**
- Claude Code uses **stdio** — `mcp_server.py` runs as a direct subprocess, no HTTP.
- The `--profile mcp` Docker service exposes **SSE on port 8008** for other MCP clients (Cursor, etc.).
- Restarting the Docker MCP container does not affect the Claude Code stdio connection.

**Check the SSE container:**
```bash
docker compose --profile mcp logs mcp --tail=20
curl http://localhost:8008/health
```

**Password change checklist** — after rotating `DB_PASSWORD`:
1. Update `.env`
2. `ALTER USER rkp_user PASSWORD '...'` in Postgres
3. `docker compose restart codemanager` (REST API)
4. `docker compose up -d --force-recreate mcp` (SSE container)
5. Restart Claude Code (stdio MCP subprocess)

## Detailed guide

See [docs/guide.md](docs/guide.md) for full human + agent documentation.

