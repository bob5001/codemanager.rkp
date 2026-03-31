"""
GET /dashboard — human-readable HTML view of system state.
No auth required. Read-only.
"""

from __future__ import annotations

import math

from fastapi import APIRouter, Query, Request
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


def _pagination_controls(current_page: int, total_pages: int, base_url: str) -> str:
    if total_pages <= 1:
        return ""
    parts = []
    if current_page > 1:
        parts.append(
            f'<a href="{base_url}?page={current_page - 1}" class="page-btn">‹ Prev</a>'
        )
    parts.append(
        f'<span class="page-info">Page {current_page} of {total_pages}</span>'
    )
    if current_page < total_pages:
        parts.append(
            f'<a href="{base_url}?page={current_page + 1}" class="page-btn">Next ›</a>'
        )
    return f'<div class="pagination">{"".join(parts)}</div>'


def _render(projects, agents, visits, current_page: int = 1, total_pages: int = 1, total_visits: int = 0) -> str:
    projects_rows = ""
    for p in projects:
        desc = p.get('description') or ''
        if len(desc) > 100:
            desc_cell = (
                f'<details><summary class="summary-preview">{desc[:100].rstrip()}…</summary>'
                f'<span class="summary-full">{desc}</span></details>'
            )
        else:
            desc_cell = _fmt(desc or None)
        projects_rows += f"""
        <tr>
          <td>{_fmt(p.get('name'))}</td>
          <td>{_status_badge(p.get('status',''))}</td>
          <td style="max-width:300px">{desc_cell}</td>
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
        if not summary:
            summary_cell = _fmt(None)
        elif len(summary) > 120:
            short = summary[:120].rstrip() + '…'
            summary_cell = (
                f'<details><summary class="summary-preview">{short}</summary>'
                f'<span class="summary-full">{summary}</span></details>'
            )
        else:
            summary_cell = summary

        visits_rows += f"""
        <tr>
          <td>{_fmt(v.get('agent_name'))}</td>
          <td>{_fmt(v.get('project_name'))}</td>
          <td style="max-width:380px">{summary_cell}</td>
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
    details {{ cursor: pointer; }}
    details summary {{ list-style: none; }}
    details summary::-webkit-details-marker {{ display: none; }}
    .summary-preview {{ color: #374151; }}
    .summary-preview::after {{ content: " ▾"; color: #9ca3af; font-size: 0.7rem; }}
    details[open] .summary-preview::after {{ content: " ▴"; }}
    .summary-full {{ display: block; margin-top: 0.4rem; color: #374151;
                     white-space: pre-wrap; line-height: 1.5; }}
    .pagination {{ display: flex; align-items: center; gap: 0.75rem;
                   margin-top: 0.75rem; justify-content: flex-end; }}
    .page-btn {{ display: inline-block; padding: 0.35rem 0.85rem;
                 background: #fff; border: 1px solid #d1d5db; border-radius: 6px;
                 font-size: 0.8rem; color: #374151; text-decoration: none; }}
    .page-btn:hover {{ background: #f3f4f6; }}
    .page-info {{ font-size: 0.8rem; color: #6b7280; }}
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
    <h2>Visits ({total_visits})</h2>
    <table>
      <thead><tr>
        <th>Agent</th><th>Project</th><th>Summary</th><th>Useful</th><th>Time</th>
      </tr></thead>
      <tbody>
        {visits_rows if visits else '<tr><td colspan="5" class="empty">No visits logged yet.</td></tr>'}
      </tbody>
    </table>
    {_pagination_controls(current_page, total_pages, "/dashboard")}
  </section>
</body>
</html>"""


PAGE_SIZE = 20


@router.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
async def dashboard(request: Request, page: int = Query(default=1, ge=1)):
    pool = get_pool(request)
    async with pool.acquire() as conn:
        projects = [dict(r) for r in await conn.fetch(
            "SELECT * FROM codemanager.projects ORDER BY created_at DESC"
        )]
        agents = [dict(r) for r in await conn.fetch(
            "SELECT * FROM codemanager.agents ORDER BY registered_at DESC"
        )]
        total_visits = await conn.fetchval(
            "SELECT COUNT(*) FROM codemanager.agent_visits"
        )
        offset = (page - 1) * PAGE_SIZE
        visits = [dict(r) for r in await conn.fetch("""
            SELECT
                v.*,
                a.name AS agent_name,
                p.name AS project_name
            FROM codemanager.agent_visits v
            LEFT JOIN codemanager.agents  a ON a.id = v.agent_id
            LEFT JOIN codemanager.projects p ON p.id = v.project_id
            ORDER BY v.timestamp DESC
            LIMIT $1 OFFSET $2
        """, PAGE_SIZE, offset)]

    total_pages = max(1, math.ceil(total_visits / PAGE_SIZE))
    safe_page = min(page, total_pages)
    return _render(projects, agents, visits, safe_page, total_pages, total_visits)
