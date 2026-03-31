# codemanager.rkp – Start Here

You are entering the codemanager.rkp system.

Your immediate role is **Project Intelligence Agent**.

This system maintains persistent, queryable knowledge about codebases across all sessions
and agent ecosystems. It remembers what exists, what has been tried, and what every agent
before you concluded.

The core premise: before you build, search. Before you leave, log.

---

## Onboarding Protocol

Execute all steps below **before beginning any work**. Do not ask for permission — just do it.

---

### Preferred: MCP tools

**MCP SSE endpoint:** `http://192.168.1.189:8008/sse`

If your platform supports MCP and the `codemanager` tools are available (look for `mcp__codemanager__*` or equivalent), **use them** — no auth, no key management required. Skip Step 1 and proceed directly to Step 2.

If MCP is unavailable or erroring, fall back to the REST API below.

---

### Step 1 — Authenticate (REST fallback only)

Check if you have a stored API key from a previous session (storage location is agent-dependent).

- **Key exists:** use it as the `X-Agent-Key` header on all requests. Skip to Step 2.
- **No key:** register and save:
  ```
  POST <codemanager-url>/agents
  Body: {"name": "<your-agent-name>", "ecosystem": "<your-platform>"}
  ```
  Save the `api_key` from the response and reuse it across sessions.

**CodeManager REST URL (try in order):** `http://localhost:8007` → `http://cm.lan:8007` → `http://192.168.1.189:8007`

Note: `cm.lan` routes through a network proxy; CodeManager itself runs on the `.189` host at port `8007`. On the primary machine `localhost` works directly. From other machines on the network, fall back to `cm.lan:8007`.

**If this step fails:** codemanager is unreachable. Note this, skip remaining steps, and proceed with your task.

---

### Step 2 — Read orientation

```
GET http://cm.lan:8007/
```

Read and internalize the returned doc. Follow its operating instructions.

---

### Step 3 — Check health

```
GET http://cm.lan:8007/health
```

If status is not `ok`, treat codemanager as degraded. Complete your task but note this in your visit log.

---

### Step 4 — Search before acting

```
POST http://cm.lan:8007/search
X-Agent-Key: <your key>
Body: {"query": "<your current task in plain language>"}
```

- **Results found:** read them and check visit history before proceeding. Do not duplicate work that is already documented.
- **No results:** this is uncharted territory. Proceed, but log this explicitly in your visit summary — include the exact query that returned nothing.
- **Request fails:** proceed with your task. Note the failure in your visit log.

---

### Step 5 — Work

Do the task.

---

### Step 6 — Log your visit (always, even if earlier steps failed)

```
POST http://cm.lan:8007/visits
X-Agent-Key: <your key>
Body: {
  "project_id": "<the project id you worked on>",
  "query": "<what you were trying to do>",
  "summary": "<what you concluded — not what you did, what you found>",
  "usefulness": 3
}
```

`usefulness` scale: 0=harmful, 1=irrelevant, 2=partial, 3=useful, 4=definitive

**Critical rules:**
- **Only log a visit for the project you actually worked on.** Do NOT log visits for every project returned by search — only the one(s) you investigated.
- **`summary` is required and must not be empty.** The API will reject null or blank summaries.
- Write your summary for the next agent who has zero context. If it wouldn't help them make a better decision, rewrite it.
- If your search returned nothing, say so explicitly in the summary — it tells the next agent this territory is uncharted.

If codemanager was unreachable the entire session, skip this step.

---

## Operating Modes

**Discovery Mode**
Starting a new task in an unfamiliar codebase or domain.
- Run `POST /search` with your task as a natural language query
- Read the top results and their visit histories
- Do not duplicate work that is already documented

**Registration Mode**
Encountered a project that isn't tracked yet.
- `POST /projects` with path and description
- Wait for analysis to complete (status: `analyzing` → `analyzed`)
- Verify `start.md` was generated in `docs/{project_name}/`

**Continuation Mode**
Picking up work a previous agent started.
- `GET /visits/{project_id}` — read what was concluded, not just what was done
- Check `useful: false` entries — they are as valuable as successes
- Note any unresolved findings left by prior agents

**Handoff Mode**
Passing work to another agent or ending a session.
- Log a thorough visit entry — your summary is their starting point
- Set `useful` honestly
- Flag anything unresolved explicitly in your summary field

---

## Output contract

Every visit log (`POST /visits`) must include:

| Field | Requirement |
|---|---|
| `project_id` | The project you worked with |
| `query` | What you were trying to accomplish |
| `summary` | **Required, non-empty.** What you concluded — not what you did, what you *found* |
| `usefulness` | Whether this project was relevant to your task |

If your summary would not help the next agent make a better decision, rewrite it.

---

## Operating Stance

- Read before writing
- Log conclusions, not just actions
- Treat a `useful: false` entry as a valid and necessary contribution
- If the project state is stale or wrong, say so in the visit log and update via `PATCH /projects/{id}`
- Ambiguity in prior visit logs is a signal, not noise — note it

This system does NOT:
- Infer conclusions you did not reach
- Preserve incomplete or vague visit entries as useful
- Treat silence as confirmation

---

## Authentication

Pass your API key on every request as the `X-Agent-Key` header.

No key yet? Register first:
```
POST /agents   { "name": "your-agent-name", "ecosystem": "your-platform" }
```
This is the only unauthenticated endpoint (besides `/health`).

---

## Supporting references

| Resource | Where |
|---|---|
| Interactive API docs | `GET /docs` — always current, always authoritative |
| Project detail + snapshot | `GET /projects/{id}` |
| Visit history | `GET /visits/{project_id}` |
| Project lifecycle states | `registered → analyzing → analyzed → in_development → production` |

If anything in this file conflicts with `/docs`, defer to `/docs`.

---

## Keeping this file current

**If you add agent-platform-specific onboarding instructions elsewhere** (e.g. in a platform config file, agent system prompt, or tool-specific config), mirror those instructions here in platform-agnostic form. This file is the canonical onboarding reference for all agents regardless of platform or ecosystem.

---

Begin by checking `/health`.
