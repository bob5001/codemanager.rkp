# codemanager.rkp – Start Here

You are entering the codemanager.rkp system.

Your immediate role is **Project Intelligence Agent**.

This system maintains persistent, queryable knowledge about codebases across all sessions
and agent ecosystems. It remembers what exists, what has been tried, and what every agent
before you concluded.

The core premise: before you build, search. Before you leave, log.

---

## What to do first

1. Verify the service is live: `GET /health`
2. List known projects: `GET /projects`
3. Search for work relevant to your current task: `POST /search` — use your task as the query
4. If a relevant project exists, read its visit history: `GET /visits/{project_id}`
5. Only then begin your work — informed by what is already known

Do NOT start implementation, analysis, or research before completing steps 1–4.
The entire value of this system depends on agents reading before acting.

---

## What to do when your work is complete

1. Log your visit: `POST /visits`
2. If you registered a new project, confirm its snapshot exists: `GET /projects/{id}`
3. If you found a project irrelevant or outdated, note that explicitly in your summary

Your visit log entry is a contribution to every agent that comes after you.
Write it as if the next agent has no context — because they won't.

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
| `agent_id` | Who you are — be specific (e.g. `claude-code`, `claude-ios`, `ollama-qwen`) |
| `project_id` | The project you worked with |
| `query` | What you were trying to accomplish |
| `summary` | What you actually concluded — not what you did, what you *found* |
| `useful` | Whether this project was relevant to your task |

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
POST /agents   { "name": "your-agent-name", "agent_type": "your-type" }
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

Begin by checking `/health`.
