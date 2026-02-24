# codemanager.rkp — Project Scope v0.1

## What It Is

A **project intelligence broker** — a locally-hosted API service that gives any AI agent,
in any ecosystem, a shared understanding of existing codebases and their current state.

The core premise: agents are stateless by default. Every new session re-reads, re-analyzes,
re-discovers. codemanager.rkp breaks that pattern by maintaining persistent, queryable 
project knowledge that any agent can read from and write to.

---

## The Problem It Solves

When a complex task spawns multiple sub-agents — or when work spans multiple sessions,
tools, or AI ecosystems — there is no shared memory of:

- What projects already exist and what they do
- What has already been tried or decided
- What external code (GitHub etc.) was evaluated and why it was or wasn't used
- What other agents concluded when they looked at the same codebase

codemanager.rkp is the answer to the question every agent should be able to ask:
*"Before I start, what do we already know?"*

---

## Core Capabilities

### 1. Project Ingestion
- Walk a local codebase and extract file tree, key modules, entry points
- Generate a structured summary using Claude API (or local Ollama fallback)
- Write `start.md` — a human and agent-readable orientation document
- Write snapshot files capturing point-in-time state
- Store embeddings in pgvector for semantic search

### 2. Semantic Search
- Accept a natural language query from any agent
- Search local projects first via pgvector similarity
- Expand to GitHub API if local results are insufficient
- Return ranked results with summaries and relevance scores
- Automatically log the search as a visit

### 3. Agent Visit Logging
- Record every agent interaction: who asked, what they asked, what they concluded
- Flag whether the project was found useful
- Make visit history queryable so future agents learn from past agents
- Support multiple agent types: Claude Code, Claude iOS, Ollama, Cursor, custom

### 4. API Surface
All functionality exposed via a clean REST API:
- `GET  /projects` — list all tracked projects
- `GET  /projects/{id}` — project detail + latest summary
- `POST /projects` — register new project, trigger analysis
- `POST /search` — semantic search across local + GitHub
- `GET  /visits/{project_id}` — visit history for a project
- `POST /visits` — log an agent visit manually
- `GET  /health` — service status
- `GET  /docs` — auto-generated interactive API docs (FastAPI built-in)

---

## Architecture

```
codemanager.rkp/
├── main.py                  # FastAPI app, route registration
├── api/
│   └── routes/
│       ├── projects.py      # CRUD for tracked projects
│       ├── search.py        # semantic search endpoint
│       └── visits.py        # agent visit logging
├── analyzers/
│   ├── local.py             # walks local codebases, extracts structure
│   ├── github.py            # GitHub API client, repo search
│   └── summarizer.py        # Claude API / Ollama for summary generation
├── storage/
│   ├── projects.py          # Postgres project + snapshot persistence
│   └── conversations.py     # existing ConversationStore (refactored)
├── docs/                    # generated start.md and snapshots per project
│   └── {project_name}/
│       ├── start.md
│       └── snapshots/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env
```

---

## Data Model

```sql
-- Core project registry
projects (
    id            UUID PRIMARY KEY,
    name          TEXT NOT NULL,
    path          TEXT,                  -- local path if local repo
    github_url    TEXT,                  -- remote URL if github repo
    description   TEXT,
    summary       TEXT,                  -- latest generated summary
    embedding     VECTOR(1536),          -- pgvector embedding of summary
    last_analyzed TIMESTAMPTZ,
    created_at    TIMESTAMPTZ DEFAULT now()
)

-- Point-in-time snapshots
snapshots (
    id            UUID PRIMARY KEY,
    project_id    UUID REFERENCES projects(id),
    timestamp     TIMESTAMPTZ DEFAULT now(),
    file_tree     JSONB,
    key_findings  JSONB,
    embedding     VECTOR(1536)
)

-- Agent visit breadcrumbs
agent_visits (
    id            UUID PRIMARY KEY,
    project_id    UUID REFERENCES projects(id),
    agent_id      TEXT,                  -- e.g. "claude-code", "ollama-llama3"
    query         TEXT,
    summary       TEXT,                  -- what the agent concluded
    useful        BOOLEAN,
    timestamp     TIMESTAMPTZ DEFAULT now()
)
```

---

## Stack

| Layer | Choice | Reason |
|---|---|---|
| API framework | FastAPI | Async, auto-docs, Pydantic validation |
| Server | Uvicorn | Standard FastAPI runtime |
| Database | PostgreSQL | Already running, supports pgvector |
| Vector search | pgvector | Semantic similarity, no new infrastructure |
| Summarization | Anthropic API | Primary; Ollama local as fallback |
| GitHub search | PyGithub | Clean Python client for GitHub REST API |
| Container | Docker + Compose | Consistent with existing homelab stack |
| Routing | NPM + dnsmasq | Already configured; `codemanager.lan` |

---

## Network / Access

- **Local:** `http://codemanager.lan` via dnsmasq + NPM
  - dnsmasq: `codemanager.lan` → `192.168.1.99`
  - NPM: proxy to `192.168.1.189:8000`
- **Remote agents:** Cloudflare Tunnel (planned) — enables iOS app and external agents
  without port forwarding or exposing local network
- **Interactive docs:** `http://codemanager.lan/docs` — always available, always current

---

## Multi-Ecosystem Agent Co-Build (Key Design Principle)

codemanager.rkp is explicitly designed for heterogeneous agent environments.

Any agent — regardless of ecosystem, toolchain, or underlying model — can:
1. Query what exists before starting work
2. Log what it found and concluded
3. Leave breadcrumbs for agents working in parallel or picking up later

This enables patterns that are otherwise impossible:
- Claude Code handles implementation while Claude iOS reviews architecture
- An Ollama agent running locally does bulk file analysis overnight
- A Cursor agent cross-references a GitHub search result against local work
- All of them writing to the same visit log, readable by all the others

The visit log becomes a **shared working memory** across agent ecosystems —
compounding in value with every interaction.

---

## Build Order

1. **Postgres schema** — create tables, enable pgvector extension
2. **Storage layer** — wire projects.py and refactored conversations.py to DB
3. **FastAPI skeleton** — routes returning real data (already scaffolded)
4. **Local analyzer** — file walker + Claude API summarizer
5. **pgvector search** — embed summaries on ingest, similarity search on query
6. **GitHub integration** — PyGithub search, embed and score results
7. **Visit logging** — auto-log on search, manual POST endpoint
8. **Cloudflare Tunnel** — expose for remote agent access with SSL

---

## Out of Scope (v0.1)

- Authentication (local network trust model for now)
- Real-time file watching (manual re-analysis trigger is fine initially)
- Web UI (API + `/docs` is sufficient; UI is a future layer)
- Multi-user support

---

## Success Criteria

An agent in any ecosystem can:
- Ask "what do we have that's relevant to X?" and get a useful answer
- Register a new project and get a `start.md` back
- Read what previous agents concluded about a project
- Leave a record of its own visit and findings

A developer can:
- `docker compose up` and have the service running in under a minute
- Hit `codemanager.lan/docs` and explore the full API interactively
- Add a new project with a single POST request