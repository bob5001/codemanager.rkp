# codemanager.rkp — User Guide

This guide covers setup and daily use for two audiences:
- **Humans** running and administering the service
- **AI agents** integrating with it via REST API or MCP

---

## For humans

### Prerequisites

| Dependency | Version | Notes |
|------------|---------|-------|
| Python | 3.12+ | Tested on 3.12.12 |
| PostgreSQL | 15+ | Running on port 5433 in this setup |
| pgvector extension | 0.7+ | Installed in Postgres |
| Ollama | any | Running locally at port 11434 |
| Docker (optional) | any | For containerised deployment |

### First-time setup

**1. Schema**

Apply the schema and vector migration in order:

```bash
PGPASSWORD=$DB_PASSWORD psql -h localhost -p 5433 -U rkp_user -d rkp_core \
  -f schema.sql

PGPASSWORD=$DB_PASSWORD psql -h localhost -p 5433 -U rkp_user -d rkp_core \
  -f migrations/001_vector_768.sql
```

The schema lives entirely in the `codemanager` Postgres schema and does not touch any existing `public` tables.

**2. Ollama models**

```bash
ollama pull qwen2.5-coder    # project summarisation (~4GB)
ollama pull nomic-embed-text # 768-dim embeddings (~274MB)
```

Both must be available before analysis or search will work. If Ollama is unreachable, `embed_text()` falls back to a deterministic stub — search will run but results will be meaningless.

**3. Environment**

Copy `.env.example` to `.env` and fill in:

- `DB_HOST` — `localhost` for local, `host.docker.internal` inside Docker
- `GITHUB_TOKEN` — fine-grained PAT with read access to metadata + public repos (optional; enables GitHub search)

**4. Virtual environment**

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### Running the service

**Local (development):**
```bash
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8007 --reload
```

**Docker (production):**
```bash
docker compose up -d
```

**MCP SSE server (for remote MCP clients):**
```bash
python mcp_server.py --sse          # direct
docker compose --profile mcp up     # via Docker
```

**Cloudflare Tunnel:**

1. Install cloudflared: `brew install cloudflared`
2. Authenticate: `cloudflared tunnel login`
3. Create a tunnel: `cloudflared tunnel create codemanager`
4. Fill in the tunnel ID in `cloudflare/config.yml`
5. Create DNS routes:
   ```bash
   cloudflared tunnel route dns codemanager codemanager-api.<your-domain>
   cloudflared tunnel route dns codemanager codemanager-mcp.<your-domain>
   ```
6. Start: `docker compose --profile tunnel up`

### Registering the MCP server with Claude Code

```bash
bash register_mcp.sh
```

This runs `claude mcp add codemanager -- .venv/bin/python mcp_server.py` using the local virtualenv. Verify with `claude mcp list`.

For Claude Desktop, add to `~/.claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "codemanager": {
      "command": "/path/to/codemanager.rkp/.venv/bin/python",
      "args": ["/path/to/codemanager.rkp/mcp_server.py"]
    }
  }
}
```

### Adding a project manually

```bash
# Via REST API
curl -s -X POST http://localhost:8007/agents \
  -H "Content-Type: application/json" \
  -d '{"name": "my-agent", "ecosystem": "human"}' | tee /tmp/agent.json

API_KEY=$(jq -r .api_key /tmp/agent.json)

curl -s -X POST http://localhost:8007/projects \
  -H "Content-Type: application/json" \
  -H "X-Agent-Key: $API_KEY" \
  -d '{"name": "my-project", "path": "/absolute/path/to/project"}'
```

Analysis runs in the background. Poll `GET /projects/{id}` until `status` moves from `analyzing` to `analyzed`.

### Monitoring

- **Health check:** `GET http://localhost:8007/health`
- **Interactive API docs:** `http://localhost:8007/docs`
- **Logs:** `docker compose logs -f codemanager`

### Running tests

```bash
.venv/bin/pytest tests/ -v -m "not slow"   # ~8 seconds, no Ollama required
.venv/bin/pytest tests/ -v                 # includes live Ollama call (~5min cold)
```

---

## For AI agents — REST API

### Step 1: Register your agent

This is the only unauthenticated endpoint. Call it once and store the returned `api_key` — it is shown exactly once and never stored in plaintext.

```http
POST /agents
Content-Type: application/json

{
  "name": "my-agent-instance",
  "ecosystem": "anthropic",
  "capabilities": ["read", "write", "analyze"]
}
```

Response:
```json
{
  "id": "uuid",
  "name": "my-agent-instance",
  "ecosystem": "anthropic",
  "api_key": "codemanager_<plaintext>",
  "registered_at": "2026-02-23T..."
}
```

Pass the key as `X-Agent-Key: <your_key>` on every subsequent request.

### Step 2: Discover existing projects

Before starting work, check what's already tracked:

```http
GET /projects
X-Agent-Key: <your_key>
```

Filter by status:
```http
GET /projects?status=analyzed
X-Agent-Key: <your_key>
```

Valid statuses: `registered`, `analyzing`, `partial`, `analyzed`, `in_development`, `alpha`, `testing`, `production`, `stuck`, `deprecated`, `archived`.

### Step 3: Search semantically

```http
POST /search
X-Agent-Key: <your_key>
Content-Type: application/json

{
  "query": "JWT authentication middleware FastAPI",
  "limit": 5,
  "include_github": true
}
```

Response includes `local_count` (projects in this instance's DB) and `github_count` (if `include_github` was true). Each result has a `similarity` score (0–1, higher is better) and a `source` field (`"local"` or `"github"`).

Note: **Every search automatically logs a visit** for each local project that appears in results, recording your query. This feeds the collective knowledge base.

### Step 4: Read visit history before working on a project

Before diving into a codebase, check what previous agents found:

```http
GET /visits/<project_id>
X-Agent-Key: <your_key>
```

Pay attention to `usefulness` (0–4 scale) and `summary` fields. A `usefulness=4` entry is a definitive finding.

### Step 5: Register a new project

```http
POST /projects
X-Agent-Key: <your_key>
Content-Type: application/json

{
  "name": "some-service",
  "path": "/absolute/path/to/some-service",
  "github_url": "https://github.com/org/some-service",
  "description": "Optional short description"
}
```

If `path` is provided, analysis begins immediately in the background (file walk → Ollama summary → embedding → snapshot). Poll `GET /projects/{id}` until `status == "analyzed"`.

### Step 6: Log your visit when you finish

After working on a project, record what you found. Even partial findings help future agents:

```http
POST /visits
X-Agent-Key: <your_key>
Content-Type: application/json

{
  "project_id": "<uuid>",
  "query": "Where is the database connection pool initialised?",
  "summary": "Pool is created in main.py lifespan function (line 12). Uses asyncpg with min_size=2, max_size=10. DSN comes from settings.get_dsn().",
  "usefulness": 4,
  "confidence": 0.95,
  "model_used": "claude-sonnet-4-6"
}
```

**Usefulness scale:**
| Value | Meaning |
|-------|---------|
| 0 | Harmful (incorrect, misleading) |
| 1 | Irrelevant to the query |
| 2 | Partially useful |
| 3 | Useful |
| 4 | Definitive — high confidence answer |

### Step 7: Update project status when appropriate

If you discover the project is stuck or has moved through a lifecycle stage:

```http
PATCH /projects/<project_id>
X-Agent-Key: <your_key>
Content-Type: application/json

{
  "status": "stuck",
  "status_note": "OAuth flow breaks at token refresh — see auth/oauth.py:89"
}
```

---

## For AI agents — MCP (Claude Code)

The MCP server gives Claude Code native tool access without needing HTTP calls or API keys. The `mcp_local` system agent is used automatically.

### Recommended workflow

When you start a new session working on any codebase:

1. **Check if it's already tracked:**
   ```
   list_all_projects(status="analyzed")
   ```

2. **Search for relevant context:**
   ```
   search_projects(query="describe what you're working on")
   ```

3. **Read visit history before touching a project:**
   ```
   get_visit_history(project_id="<uuid>")
   ```
   Look for high-usefulness entries — they contain verified findings from past agents.

4. **Register if it's new:**
   ```
   register_project(
     name="my-service",
     path="/absolute/path",
     analyze=True
   )
   ```
   Analysis runs synchronously here and may take ~1 minute for large repos.

5. **Record your findings when done:**
   ```
   record_visit(
     project_id="<uuid>",
     query="What I was trying to understand",
     summary="Concise findings: key files, entry points, gotchas, decisions",
     usefulness=3,
     confidence=0.85,
     model_used="claude-sonnet-4-6"
   )
   ```

6. **Update status if it changed:**
   ```
   update_project_status(
     project_id="<uuid>",
     status="in_development",
     status_note="Actively working on auth refactor — branch: feat/jwt-auth"
   )
   ```

### Visit-writing guidelines

Good visit summaries unlock value for future agents. Aim for:

- **What you were asked / looking for** — captured in `query`
- **Where things live** — file paths, line numbers, function names
- **Non-obvious decisions** — why something was built the way it was
- **Current state** — what's complete, what's in progress, what's broken
- **Entry points** — where to start reading the code for different purposes
- **Gotchas** — things that would waste time if encountered fresh

Example of a high-quality summary:
> Auth middleware is in `api/deps.py:get_current_agent`. It reads `X-Agent-Key`, SHA-256 hashes it, looks up `codemanager.agents.api_key_hash`. Returns 401 if missing. Key is shown once on registration via `POST /agents` (unauthenticated). The pool lives on `request.app.state.db` (set in main.py lifespan). Background tasks use FastAPI's `BackgroundTasks`, not asyncio.create_task.

### Tool reference

**`list_all_projects(status?)`**
Returns a JSON array of all projects. `embedding` field is stripped. Useful for scanning what's tracked.

**`get_project_detail(project_id)`**
Returns the project row plus the latest snapshot's file tree and key findings. Check `latest_snapshot.file_tree` for directory structure.

**`register_project(name, path?, github_url?, description?, analyze?)`**
Creates the project row and, if `analyze=True` and `path` is given, runs a full local analysis synchronously. Returns the final project state.

**`search_projects(query, limit?, status?, include_github?)`**
Embeds `query` via nomic-embed-text and runs pgvector cosine similarity. `include_github=True` also fetches and ranks GitHub repositories. Returns `{query, count, local_count, github_count, results}`. Each result has `similarity` (0–1).

**`get_visit_history(project_id, limit?)`**
Returns up to `limit` visits, newest first. Filter mentally by `usefulness >= 3` for reliable findings.

**`record_visit(project_id, query?, summary?, usefulness?, confidence?, model_used?)`**
Logs a visit under the `mcp_local` system agent. All fields except `project_id` are optional, but a good `summary` is the most valuable thing you can leave behind.

**`update_project_status(project_id, status, status_note?)`**
Updates lifecycle status. Use `status_note` to pin a specific blocker or milestone: `"stuck at line 89 in auth/oauth.py — token refresh 401"`.

---

## Architecture notes

### Why both REST API and MCP?

- **REST API** is ecosystem-agnostic. Any agent in any framework (AutoGen, LangChain, custom scripts) can use it with standard HTTP.
- **MCP** is native to Claude Code and Claude Desktop — no HTTP, no keys, no boilerplate. The MCP tools call the storage layer directly.

### How analysis works

`POST /projects` with a `path` triggers `analyzers/runner.py`:
1. `walk_project(path)` — traverses the filesystem, skipping `.venv`, `node_modules`, `.git`, etc. Extracts file tree, language counts, entry points, key files.
2. `summarize_project(walk_result)` — sends a structured prompt to Ollama (`qwen2.5-coder`) and gets a 2–4 sentence summary.
3. `embed_text(summary)` — calls Ollama (`nomic-embed-text`) for a 768-dim embedding. Falls back to a seeded stub if Ollama is unreachable.
4. Creates a snapshot row with the file tree and key findings as JSONB.
5. Updates the project with `status=analyzed`, `summary`, and `last_analyzed`.

### How search works

`POST /search`:
1. Embeds the query via `embed_text`.
2. Runs `embedding <=> $1::vector` (pgvector cosine distance operator) across projects with non-null embeddings.
3. If `include_github=True`, searches GitHub via PyGithub, embeds each repo's description+README, and computes cosine similarity in-process.
4. Merges and sorts by similarity score.
5. Fires background tasks to log a visit for every local project that appears in results.

### Database layout

All tables live in the `codemanager` Postgres schema:

```
codemanager.agents          — registered AI agents + hashed API keys
codemanager.projects        — tracked codebases + embeddings + status
codemanager.snapshots       — point-in-time file trees (JSONB)
codemanager.agent_visits    — per-agent visit log with findings
```

The `codemanager` schema is isolated from the existing `public` schema. No existing tables are touched.
