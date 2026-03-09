"""
GET /dashboard — human-readable HTML view of system state.
No auth required. Read-only.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from storage.database import get_pool

router = APIRouter()

STATUS_COLORS = {
    "registered": "#6b7280",
    "analyzing": "#d97706",
    "partial": "#d97706",
    "analyzed": "#2563eb",
    "in_development": "#7c3aed",
    "alpha": "#7c3aed",
    "testing": "#0891b2",
    "production": "#16a34a",
    "stuck": "#dc2626",
    "deprecated": "#9ca3af",
    "archived": "#9ca3af",
}


def _status_badge(status: str) -> str:
    color = STATUS_COLORS.get(status, "#6b7280")
    return (
        f'<span style="background:{color};color:#fff;padding:2px 8px;'
        f'border-radius:4px;font-size:0.75rem;font-weight:600;">{status}</span>'
    )


def _fmt(value) -> str:
    if value is None:
        return '<span style="color:#9ca3af">—</span>'
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


def _render(projects, agents, visits) -> str:
    projects_rows = ""
    for p in projects:
        projects_rows += f"""
        <tr>
          <td>{_fmt(p.get('name'))}</td>
          <td>{_status_badge(p.get('status',''))}</td>
          <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{_fmt(p.get('description'))}</td>
          <td style="font-size:0.8rem;color:#6b7280">{_fmt(p.get('path') or p.get('github_url'))}</td>
          <td>{_fmt(p.get('last_analyzed'))}</td>
          <td>{_fmt(p.get('created_at'))}</td>
        </tr>"""

    agents_rows = ""
    for a in agents:
        agents_rows += f"""
        <tr>
          <td>{_fmt(a.get('name'))}</td>
          <td>{_fmt(a.get('ecosystem'))}</td>
          <td>{_fmt(a.get('registered_at'))}</td>
          <td>{_fmt(a.get('last_seen'))}</td>
        </tr>"""

    visits_rows = ""
    for v in visits:
        useful = v.get('usefulness')
        if useful is None:
            useful_cell = _fmt(None)
        elif useful >= 3:
            useful_cell = '<span style="color:#16a34a">✓</span>'
        else:
            useful_cell = '<span style="color:#dc2626">✗</span>'

        summary = v.get('summary') or ''
        summary_short = (summary[:120] + '…') if len(summary) > 120 else summary

        visits_rows += f"""
        <tr>
          <td>{_fmt(v.get('agent_name'))}</td>
          <td>{_fmt(v.get('project_name'))}</td>
          <td style="max-width:320px">{summary_short or _fmt(None)}</td>
          <td style="text-align:center">{useful_cell}</td>
          <td>{_fmt(v.get('timestamp'))}</td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>codemanager.rkp</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
            background: #f9fafb; color: #111827; padding: 2rem; }}
    h1 {{ font-size: 1.4rem; font-weight: 700; margin-bottom: 0.25rem; }}
    .subtitle {{ color: #6b7280; font-size: 0.875rem; margin-bottom: 2rem; }}
    section {{ margin-bottom: 2.5rem; }}
    h2 {{ font-size: 1rem; font-weight: 600; margin-bottom: 0.75rem;
          padding-bottom: 0.4rem; border-bottom: 1px solid #e5e7eb; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff;
             border-radius: 8px; overflow: hidden;
             box-shadow: 0 1px 3px rgba(0,0,0,0.07); }}
    th {{ background: #f3f4f6; text-align: left; font-size: 0.75rem;
          font-weight: 600; color: #6b7280; text-transform: uppercase;
          letter-spacing: 0.05em; padding: 0.6rem 1rem; }}
    td {{ padding: 0.65rem 1rem; font-size: 0.875rem;
          border-top: 1px solid #f3f4f6; vertical-align: top; }}
    tr:hover td {{ background: #f9fafb; }}
    .empty {{ color: #9ca3af; font-size: 0.875rem; padding: 1rem; }}
  </style>
</head>
<body>
  <h1>codemanager.rkp</h1>
  <p class="subtitle">System dashboard — auto-refresh on load</p>

  <section>
    <h2>Projects ({len(projects)})</h2>
    <table>
      <thead><tr>
        <th>Name</th><th>Status</th><th>Description</th>
        <th>Path / URL</th><th>Last Analyzed</th><th>Created</th>
      </tr></thead>
      <tbody>
        {projects_rows if projects else '<tr><td colspan="6" class="empty">No projects registered yet.</td></tr>'}
      </tbody>
    </table>
  </section>

  <section>
    <h2>Agents ({len(agents)})</h2>
    <table>
      <thead><tr>
        <th>Name</th><th>Ecosystem</th><th>Registered</th><th>Last Seen</th>
      </tr></thead>
      <tbody>
        {agents_rows if agents else '<tr><td colspan="4" class="empty">No agents registered yet.</td></tr>'}
      </tbody>
    </table>
  </section>

  <section>
    <h2>Recent Visits (last 20)</h2>
    <table>
      <thead><tr>
        <th>Agent</th><th>Project</th><th>Summary</th><th>Useful</th><th>Time</th>
      </tr></thead>
      <tbody>
        {visits_rows if visits else '<tr><td colspan="5" class="empty">No visits logged yet.</td></tr>'}
      </tbody>
    </table>
  </section>
</body>
</html>"""


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard(request: Request):
    pool = get_pool(request)
    async with pool.acquire() as conn:
        projects = [dict(r) for r in await conn.fetch(
            "SELECT * FROM codemanager.projects ORDER BY created_at DESC"
        )]
        agents = [dict(r) for r in await conn.fetch(
            "SELECT * FROM codemanager.agents ORDER BY registered_at DESC"
        )]
        visits = [dict(r) for r in await conn.fetch("""
            SELECT
                v.*,
                a.name AS agent_name,
                p.name AS project_name
            FROM codemanager.agent_visits v
            LEFT JOIN codemanager.agents  a ON a.id = v.agent_id
            LEFT JOIN codemanager.projects p ON p.id = v.project_id
            ORDER BY v.timestamp DESC
            LIMIT 20
        """)]

    return _render(projects, agents, visits)
