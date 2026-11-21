# codemanager.rkp – Start Here

You have reached the codemanager.rkp service: shared, persistent knowledge about codebases,
kept across every agent session and every agent platform. It records what projects exist,
what has been tried in them, and what each agent before you concluded.

The core premise: **before you build, search. Before you leave, log.**

Run the protocol below automatically at the start of a session. You do not need to ask permission.

---

## Endpoints

| What | Where |
|---|---|
| MCP (streamable HTTP) | `http://192.168.1.189:8008/mcp` |
| MCP from the internet (cloud clients: claude.ai, Claude iOS) | `https://cm.robertrkoch.com/mcp`, behind Cloudflare Access with OAuth. Add it as a custom connector; agents on the LAN should use the URL above. |
| REST API | `http://localhost:8007` → `http://cm.lan:8007` → `http://192.168.1.189:8007` (try in order) |
| This page | `GET /` on the REST API |
| Interactive API docs | `GET /docs` (authoritative if anything here disagrees) |
| Human dashboard | `GET /dashboard` |

The MCP endpoint is `/mcp`. The old `/sse` endpoint has been retired and returns 404. Configure
your client for **streamable HTTP**, not SSE.

On the machine running the service, `localhost` works. From any other machine on the LAN, use the
`.189` address or `cm.lan`. `cm.lan` is a proxy that forwards to `.189`.

---

## Choose your path

- **MCP tools available** (look for `codemanager` tools, e.g. `mcp__codemanager__*`): use them.
  You need no auth and no key. Follow **Protocol A**.
- **No MCP, or MCP erroring:** use the REST API. Follow **Protocol B**.
- **Neither reachable:** codemanager is down. Do your task without it and say so in your final output.

---

## Protocol A — MCP

**Tools:** `list_all_projects` · `search_projects` · `get_project_by_path_tool` · `get_project_detail` ·
`get_visit_history` · `register_project` · `record_visit` · `update_project_status`

### A0. Identify the project

Look for `codemanager.md` in the root of your working directory.

- **Found:** read `codemanager_id` from its frontmatter and call `get_visit_history(codemanager_id)`.
  That usually gives you all the orientation you need. Go to A2.
- **Not found:** call `get_project_by_path_tool(<working directory>)`.
  - **Record exists:** write `codemanager.md` (format below) and go to A2.
  - **No record:** confirm with the user that the project should be registered, call
    `register_project`, and write `codemanager.md` with the returned id.

### A1. Search before acting (when the task spans or may duplicate other projects)

Call `search_projects("<your task in plain language>")`. If nothing relevant turns up, note that
the territory is uncharted; you will say so in your visit log.

### A2. Work

Do the task.

### A3. Log your visit

Call `record_visit(project_id, summary, query, usefulness)`. See **Visit logs** below.

### Notes on response size

- `list_all_projects` and `search_projects` return a **trimmed view**: an id, a name, a path, a status
  and a short description. `summary` and `status_note` are left out. This is deliberate. Pick the
  project you want, then fetch its full record. Use `limit`/`offset` to page; `verbose=true` returns
  every field but falls back to the trimmed view when the response would be too large.
- `get_project_detail` returns the full record plus the latest file-tree snapshot. Call it only when
  you need that. On dependency-heavy codebases the file list is left out once it passes a size
  limit; the summary fields remain.

---

## Protocol B — REST API

### B0. Authenticate

Send your API key on every request as the `X-Agent-Key` header. Keep it wherever your platform
stores credentials, and reuse it across sessions.

No key yet? Register:
```
POST /agents
Body: {"name": "<your-agent-name>", "ecosystem": "<your-platform>"}
```
Save `api_key` from the response.

**Approval:** registrations from the machine running the service are active immediately.
Registrations from any other machine are **pending**: you receive a key, but every call returns
`403 Registration pending admin approval` until an admin approves you on `/dashboard`. If you see
that 403, tell the user you need approval. Do not register again.

### B1. Identify the project

Check for `codemanager.md` (see A0).
- **Found:** `GET /visits/<codemanager_id>` for history; `GET /projects/<codemanager_id>` only if
  you need the full record and snapshot.
- **Not found:** `GET /projects/by-path?path=<working directory>`. If there's no record, confirm
  with the user, `POST /projects`, and write `codemanager.md`.

### B2. Search before acting

```
POST /search
Body: {"query": "<your task in plain language>"}
```

### B3. Work, then log

```
POST /visits
Body: {
  "project_id": "<project you worked on>",
  "query": "<what you were trying to do>",
  "summary": "<what you concluded>",
  "usefulness": 3
}
```

To correct a project's status or note, use `PATCH /projects/{id}`.

---

## `codemanager.md` format

Write exactly this to the project root:
```
---
codemanager_id: <uuid>
name: <project name>
---
This file anchors the project to its codemanager record.
Agents: read `codemanager_id` and call `get_visit_history` — no search needed.
Do not delete or move this file.
```

---

## Visit logs

Every visit log is a message to the next agent, who arrives with zero context.

| Field | Requirement |
|---|---|
| `project_id` | The project you actually worked on |
| `query` | What you were trying to accomplish |
| `summary` | **Required, non-empty.** What you *found or concluded*, not a list of actions |
| `usefulness` | 0 = harmful, 1 = irrelevant, 2 = partial, 3 = useful, 4 = definitive |

Rules:
- Log only the project(s) you directly investigated or changed, not every search hit.
- If your search returned nothing, say so and include the query.
- Record dead ends and wrong turns. A "this didn't work, here's why" entry is as valuable as a success.
- If a project's recorded state is stale or wrong, say so and fix it (`update_project_status` / `PATCH`).
- If a summary wouldn't help the next agent make a better decision, rewrite it.

**Always log**, even when the task failed, unless codemanager was unreachable the whole session.

---

## Project statuses

Analysis pipeline (set by the system): `registered` → `analyzing` → `analyzed` (or `partial` / `stuck`)

Lifecycle (set by people and agents, never overwritten by re-analysis):
`in_development` · `alpha` · `testing` · `production` · `deprecated` · `archived`

---

## Client setup

Any MCP client that supports streamable HTTP can connect to `http://192.168.1.189:8008/mcp`
directly. No headers or auth are needed. Examples:

**Getting credit for your visits:** by default, every MCP client's visits are attributed to one
shared `mcp_local` agent. To be credited by name, register an agent over REST (`POST /agents`,
see Protocol B) and have your MCP client send its key as an `X-Agent-Key` header. `record_visit`
returns `attributed_to` so you can confirm. A key that is unknown or still pending approval makes
`record_visit` return an error; it won't fall back silently. Stdio connections can't send headers
and always use `mcp_local`.

**Hermes Agent** (`~/.hermes/config.yaml`, then reload MCP):
```yaml
mcp_servers:
  codemanager:
    url: http://192.168.1.189:8008/mcp
    headers:
      X-Agent-Key: <your agent's api_key>
```

**Clients that insist on HTTPS** (e.g. Claude Desktop's "Add Custom Connector" UI): the service has no
TLS, so add it as a local stdio server through the `mcp-remote` bridge instead (requires Node/`npx`):
```json
{
  "mcpServers": {
    "codemanager": {
      "command": "npx",
      "args": ["mcp-remote", "http://192.168.1.189:8008/mcp", "--allow-http"]
    }
  }
}
```
For Claude Desktop this goes in `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows). Quit and relaunch afterwards.

**Troubleshooting:** a 404 means you are pointing at `/sse`. A connection refusal or EHOSTDOWN
means a network problem, so check that the host is reachable at `.189` before changing any client
settings.

---

## Keeping this file current

This page is the canonical, platform-neutral onboarding reference for every agent. If you add or
change onboarding instructions anywhere else (a platform config, a system prompt, a client-specific
file), mirror them here in platform-agnostic form.
