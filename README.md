# codemanager.rkp

A locally-hosted project intelligence broker. Any AI agent — Claude, Cursor, AutoGen, or anything else — can register codebases, run semantic search across them, and read/write a shared visit log. The knowledge accumulates across sessions and agents.

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

---

## Quick start

**Prerequisites:** Docker, a Postgres instance (port 5433), Ollama running locally.

```bash
# 1. Clone
git clone <repo> && cd codemanager.rkp

# 2. Configure environment
cp .env.example .env
# Edit .env — required: DB_PASSWORD
# Optional: ADMIN_KEY (see "Agent approval" below), GITHUB_TOKEN

# 3. Pull Ollama models
ollama pull qwen2.5-coder      # project summarisation (~4 GB)
ollama pull nomic-embed-text   # semantic embeddings (~274 MB)

# 4. Start the service
docker compose up -d

# 5. Apply the schema (first time only)
PGPASSWORD=$DB_PASSWORD psql -h localhost -p 5433 -U rkp_user -d rkp_core \
  -f schema.sql

# 6. Register yourself as an agent and save your API key
curl -s -X POST http://localhost:8007/agents \
  -H "Content-Type: application/json" \
  -d '{"name": "me", "ecosystem": "human"}' | tee /tmp/agent.json

export CM_KEY=$(python3 -c "import json,sys; print(json.load(open('/tmp/agent.json'))['api_key'])")

# If ADMIN_KEY is set, approve yourself first (see "Agent approval" below)

# 7. Register Claude Code's MCP server
bash register_mcp.sh
```

Dashboard: `http://localhost:8007/dashboard`
Interactive API docs: `http://localhost:8007/docs`

**Running without Docker:**
```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8007 --reload
```

**Upgrading an existing install:**
```bash
# Run any migrations you haven't applied yet, in order:
PGPASSWORD=$DB_PASSWORD psql -h localhost -p 5433 -U rkp_user -d rkp_core \
  -f migrations/001_vector_768.sql   # changes embedding columns to 768-dim

PGPASSWORD=$DB_PASSWORD psql -h localhost -p 5433 -U rkp_user -d rkp_core \
  -f migrations/002_agent_status.sql # adds pending/active approval flow
```

---

## Agent registration & approval

By default, `POST /agents` is open — anyone who can reach the service can register and get an API key. For private or shared deployments, set `ADMIN_KEY` in `.env` to enable an approval gate.

**Generating a key:**
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

**With `ADMIN_KEY` set:**
- New agents register with `status: pending` and receive their API key immediately, but all API calls return `403 Registration pending admin approval` until approved.
- Open `http://localhost:8007/dashboard` — a **Pending Registrations** section appears at the top with an **Approve** button for each waiting agent. Click it, enter the admin key, done.
- Or approve via CLI:
  ```bash
  curl -s -X POST http://localhost:8007/agents/<agent_id>/approve \
    -H "X-Admin-Key: <your_admin_key>"
  ```

**Without `ADMIN_KEY`:** all registrations are auto-approved (good for personal/trusted-LAN installs).

---

## Projects volume mount

The analyzer walks your local filesystem to generate project summaries. Tell Docker where your projects live by setting `PROJECTS_HOST_PATH` in `.env`:

```bash
# .env
PROJECTS_HOST_PATH=/Users/yourname/Projects
```

The directory is mounted read-only into the container at the same path, so filesystem paths stored in the database resolve correctly inside and outside Docker.

---

## API overview

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| `POST` | `/agents` | none | Register an agent, get API key |
| `POST` | `/agents/{id}/approve` | admin key | Approve a pending agent |
| `GET` | `/agents/me` | key | Your agent profile |
| `GET` | `/projects` | key | List all projects (optional `?status=`) |
| `POST` | `/projects` | key | Register a project, trigger analysis |
| `GET` | `/projects/{id}` | key | Project detail + snapshot metadata |
| `PATCH` | `/projects/{id}` | key | Update status / description / status_note |
| `DELETE` | `/projects/{id}` | key | Remove a project and its history |
| `POST` | `/projects/{id}/analyze` | key | Re-trigger analysis |
| `POST` | `/search` | key | Semantic search (local + optional GitHub) |
| `GET` | `/visits/{project_id}` | key | Visit history for a project |
| `GET` | `/visits/recent?since=` | key | Visits across all projects since a timestamp |
| `POST` | `/visits` | key | Log a visit |
| `GET` | `/health` | none | Liveness check |
| `GET` | `/dashboard` | none | Human-readable system dashboard |

Pass your API key as `X-Agent-Key: <key>` on every authenticated request.

---

## MCP tools (Claude Code)

After `bash register_mcp.sh`, these tools are available inside any Claude Code session:

| Tool | What it does |
|------|-------------|
| `list_all_projects` | List tracked codebases, optionally filtered by status |
| `get_project_detail` | Full project info + latest file-tree snapshot |
| `get_project_by_path_tool` | Look up a project by filesystem path |
| `register_project` | Add and analyse a new codebase |
| `search_projects` | Semantic similarity search (local + optional GitHub) |
| `get_visit_history` | Past agent findings for a project |
| `record_visit` | Share your findings for future agents |
| `update_project_status` | Set lifecycle status + optional note |

The MCP server connects directly to the database — no HTTP calls or API keys required in Claude Code sessions.

---

## Configuration

All settings load from `.env` via pydantic-settings. See `.env.example` for the full list.

| Variable | Default | Notes |
|----------|---------|-------|
| `DB_HOST` | `localhost` | Use `host.docker.internal` inside Docker |
| `DB_PORT` | `5433` | |
| `DB_PASSWORD` | — | **Required** |
| `ADMIN_KEY` | `` | Set to enable agent registration approval. Leave empty for open registration. |
| `PROJECTS_HOST_PATH` | `~/Projects` | Host path mounted into the container for local analysis |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Use `http://host.docker.internal:11434` inside Docker |
| `OLLAMA_MODEL` | `qwen2.5-coder:latest` | For project summarisation |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text` | 768-dim embeddings |
| `OLLAMA_TIMEOUT` | `300` | Seconds — first model load can be slow |
| `GITHUB_TOKEN` | `` | Fine-grained PAT for GitHub search (optional) |
| `APP_PORT` | `8007` | REST API port |

---

## Project status lifecycle

```
registered → analyzing → partial → analyzed
                                      ↓
                              in_development → alpha → testing → production
                                      ↓
                               stuck / deprecated / archived
```

When Ollama is unreachable during analysis, the project moves to `stuck` with a `status_note` explaining the error. Re-trigger with `POST /projects/{id}/analyze` once Ollama is back up.

---

## Running tests

```bash
.venv/bin/pytest tests/ -v -m "not slow"   # fast suite, no Ollama required (~8s)
.venv/bin/pytest tests/ -v                 # includes live Ollama call (~5 min cold start)
```

Tests require a running Postgres instance with the schema applied and `DB_PASSWORD` set in `.env`.

---

## MCP troubleshooting

The MCP server runs as a stdio subprocess launched by Claude Code — separate from the Docker REST API container.

**Verify registration:**
```bash
claude mcp list
# Should show: codemanager → /path/to/.venv/bin/python mcp_server.py
```

**Not showing up in Claude Code?**
```bash
bash register_mcp.sh
# Restart Claude Code for the registration to take effect
```

**Tools failing with a DB error?**
The MCP subprocess loads `.env` at startup. If you changed `DB_PASSWORD` mid-session, restart Claude Code to reload credentials.

**Claude Desktop setup:**
Add to `~/.claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "codemanager": {
      "command": "/absolute/path/to/codemanager.rkp/.venv/bin/python",
      "args": ["/absolute/path/to/codemanager.rkp/mcp_server.py"]
    }
  }
}
```

**Check the Docker MCP container (SSE transport for non-Claude clients):**
```bash
docker compose --profile mcp logs mcp --tail=20
curl http://localhost:8008/health
```

**Password rotation checklist:**
1. Update `.env`
2. `ALTER USER rkp_user PASSWORD '...'` in Postgres
3. `docker compose restart codemanager`
4. `docker compose --profile mcp up -d --force-recreate mcp`
5. Restart Claude Code (reloads the stdio subprocess)

---

## Remote access (Cloudflare Tunnel)

See [docs/guide.md](docs/guide.md) for full setup. Quick version:

```bash
brew install cloudflared
cloudflared tunnel login
cloudflared tunnel create codemanager
# Fill tunnel ID into cloudflare/config.yml
docker compose --profile tunnel up
```

---

## Detailed guide

[docs/guide.md](docs/guide.md) — full documentation for humans and agents, including visit-writing guidelines, architecture notes, and the database layout.
