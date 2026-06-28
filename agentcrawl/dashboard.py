from __future__ import annotations

import html
import json
from typing import Any

from .storage import SQLiteStore


_STATUS_LABELS = {
    "queued": "Queued",
    "running": "Running",
    "completed": "Completed",
    "failed": "Failed",
    "cancelled": "Cancelled",
    "cancelling": "Cancelling",
}


def dashboard_summary(store: SQLiteStore) -> dict[str, Any]:
    """Return a read-only operational snapshot backed by SQLite."""
    store.cleanup_cache()
    jobs = store.job_counts()
    failures = store.crawl_failure_metrics()
    usage_by_endpoint = store.usage_by_endpoint()
    cache_by_domain = store.cache_by_domain()
    cache_entries = store.cache_count()

    return {
        "jobs": jobs,
        "job_events": store.job_event_counts(),
        "crawl_queue": store.crawl_queue_metrics(),
        "crawl_failures": failures,
        "usage_by_endpoint": usage_by_endpoint,
        "cache_entries": cache_entries,
        "cache_by_domain": cache_by_domain,
        "totals": {
            "jobs": sum(jobs.values()),
            "open_failures": failures["by_status"].get("open", 0),
            "retryable_failures": failures["open_retryable"],
            "cache_entries": cache_entries,
            "usage_units": sum(usage_by_endpoint.values()),
        },
    }


def render_dashboard_html(summary: dict[str, Any]) -> str:
    """Render a dependency-free static HTML dashboard."""
    totals = summary["totals"]
    jobs = summary["jobs"]
    queue = summary["crawl_queue"]
    failures = summary["crawl_failures"]
    usage = summary["usage_by_endpoint"]
    cache = summary["cache_by_domain"]

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="30">
  <title>AgentCrawl Dashboard</title>
  <style>
    :root {{ color-scheme: dark; --bg: #0b1020; --card: #121a2f; --muted: #94a3b8; --text: #e2e8f0; --ok: #22c55e; --warn: #f59e0b; --bad: #ef4444; }}
    body {{ margin: 0; padding: 32px; font: 15px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--text); }}
    header {{ display: flex; align-items: baseline; justify-content: space-between; gap: 16px; margin-bottom: 24px; }}
    h1 {{ margin: 0; font-size: 28px; letter-spacing: -0.03em; }}
    h2 {{ margin: 0 0 12px; font-size: 15px; text-transform: uppercase; color: var(--muted); letter-spacing: 0.08em; }}
    a {{ color: #93c5fd; }}
    .muted {{ color: var(--muted); }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; }}
    .card {{ background: var(--card); border: 1px solid rgba(148, 163, 184, 0.18); border-radius: 18px; padding: 18px; box-shadow: 0 20px 60px rgba(0, 0, 0, 0.22); }}
    .metric {{ font-size: 34px; font-weight: 750; letter-spacing: -0.04em; }}
    .ok {{ color: var(--ok); }} .warn {{ color: var(--warn); }} .bad {{ color: var(--bad); }}
    dl {{ margin: 0; }}
    .row {{ display: flex; justify-content: space-between; gap: 16px; padding: 7px 0; border-bottom: 1px solid rgba(148, 163, 184, 0.1); }}
    .row:last-child {{ border-bottom: 0; }}
    dt {{ color: var(--muted); }} dd {{ margin: 0; font-weight: 650; }}
    pre {{ overflow: auto; margin: 0; padding: 14px; border-radius: 12px; background: rgba(15, 23, 42, 0.7); color: #bfdbfe; }}
  </style>
</head>
<body>
  <header>
    <div>
      <h1>AgentCrawl Dashboard</h1>
      <div class="muted">SQLite operational snapshot · auto-refreshes every 30s</div>
    </div>
    <a href="/api/dashboard/summary">/api/dashboard/summary</a>
  </header>

  <section class="grid" aria-label="Summary metrics">
    <article class="card"><h2>Jobs</h2><div class="metric">{totals["jobs"]}</div><div class="muted">total crawl jobs</div></article>
    <article class="card"><h2>Open failures</h2><div class="metric bad">{totals["open_failures"]}</div><div class="muted">{totals["retryable_failures"]} retryable</div></article>
    <article class="card"><h2>Usage</h2><div class="metric ok">{totals["usage_units"]}</div><div class="muted">recorded units</div></article>
    <article class="card"><h2>Cache</h2><div class="metric">{totals["cache_entries"]}</div><div class="muted">active entries</div></article>
  </section>

  <section class="grid" style="margin-top: 16px;" aria-label="Details">
    {_render_mapping_card("Jobs by status", {_STATUS_LABELS.get(key, key.title()): value for key, value in jobs.items()})}
    {_render_mapping_card("Crawl queue", {key.title(): value for key, value in queue.items()})}
    {_render_mapping_card("Open failures by type", failures["open_by_error_type"])}
    {_render_mapping_card("Usage by endpoint", usage)}
    {_render_mapping_card("Cache by domain", cache)}
    <article class="card"><h2>Raw JSON</h2><pre>{html.escape(json.dumps(summary, indent=2, sort_keys=True))}</pre></article>
  </section>
</body>
</html>"""


def _render_mapping_card(title: str, mapping: dict[str, int]) -> str:
    rows = "".join(
        f'<div class="row"><dt>{html.escape(str(key))}</dt><dd>{value}</dd></div>'
        for key, value in mapping.items()
    )
    if not rows:
        rows = '<div class="muted">No data yet.</div>'
    return f'<article class="card"><h2>{html.escape(title)}</h2><dl>{rows}</dl></article>'
