# Roadmap

AgentCrawl focuses on one job first: help AI agents read and crawl web content reliably from infrastructure you control.

This roadmap is **directional**. Public performance or quality claims need reproducible tests before they appear in docs or marketing. No benchmark numbers in marketing copy.

## Current community status

AgentCrawl Community is a serious self-hosted alpha surface. The core paths work:

- **CLI, Python library, HTTP API, Docker/GHCR image, and MCP server** — pick whichever interface fits the agent.
- **HTTP-first scraping** with optional browser/Camofox fallback when a page needs it.
- **Durable crawl jobs** with checkpoints, retries, cancellation, pagination, event history, and selective failure retries.
- **SQLite-backed local state** for cache, usage, jobs, events, failures, and extracted documents — plus a read-only local dashboard.
- **Safer server defaults**: bearer auth, `robots.txt` support, SSRF protections, unsafe redirect blocking, and private-network controls.
- **Quality extraction baseline**: checked-in fixtures, quality report, provenance metadata, JSON-LD/Product extraction, Markdown table + code preservation, noisy-layout handling.
- **Distribution readiness**: wheel/sdist checks, clean install smoke tests, CI, lightweight Docker image, and GHCR publication.

## Completed

### Safety & privacy
- Text normalization, IDNA domain normalization, safer URL validation, unsafe redirect blocking.
- Sliding-window per-key API rate limiting.
- SSRF and private-network controls.
- PDF safety limits (size + page count).
- Clearer scrape error behavior on partial / malformed pages.

### Extraction quality
- Main-content extraction for semantic containers and text-rich fallback blocks.
- Boilerplate reduction: page chrome, cookie banners, sidebars, related posts, hidden content, unsafe tags.
- Cookie-consent node-level filter (0.1.1) — drops text-only consent blocks inside generic containers, preserves documentation that mentions cookies as a feature.
- Markdown table + fenced-code-block preservation with language tags.
- Quality extraction hardening: 19 → 20 checked-in fixtures, browser-rendered SPA coverage, score thresholds, JSON report, richer provenance, JSON-LD/Product schema + rating extraction, hidden-class filtering.

### Crawler & ops
- Sitemap index expansion and `robots.txt` discovery.
- Local document ingestion: Markdown, text, JSON, XML/RSS/Atom, PDF-to-Markdown through the optional `docs` extra.
- Opt-in browser fallback on 200-OK challenge pages (0.1.1) — `client_challenge` is honest failure, not a disguised content scrape.
- Pagination, job events, and failure inspection end-to-end.

### Observability & packaging
- Local SQLite-backed cache, usage, jobs, events, failures, and extracted documents.
- Read-only local dashboard: `agentcrawl dashboard` + FastAPI `GET /dashboard` and `GET /api/dashboard/summary`.
- Failure alert hook: `agentcrawl crawl ... --alert-on-failure --cmd "..."` runs a local command with failures JSON on stdin.
- Audit trail end-to-end into `ScrapeDocument.metadata` when `audit=True`.
- Token Efficiency pillar: `estimated_tokens`, `raw_html_tokens_estimate`, `raw_html_bytes` on every document + `--token-stats` CLI flag.

### Reliability fixes (2026-06-28 audit, shipped in v0.1.3)
- SQLite-backed scheduling lease cross-process — multi-worker uvicorn no longer enqueues the same job twice.
- Audit trail now reaches the document on terminal fetch failure, not just on success.
- Blocked-page heuristic strips HTML chrome before matching — fewer false negatives on `nginx default 403` and friends.
- `CrawlConfig.__post_init__` warns when `llm` is a dict (Enhanced-pool shape) instead of silently accepting it.
- `list_crawl_failures` domain filter uses 3-pattern LIKE in SQL (no Python post-filter).
- `_pop_ready_item` no busy-spins when nothing is ready yet.
- `SQLiteStore._migrated_paths` per-process cache, `:memory:`-aware.
- `_export_failures_csv` skips `mkdir` when there are no rows to write.

### Release hygiene
- `agentcrawl doctor`, `agentcrawl --version`, package build verification, clean install smoke tests.
- Lightweight Docker image on GHCR (`ghcr.io/jorg18/agentcrawl:latest`).
- PyPI publication pipeline (`pip install agentcrawl-ai`).
- CI badge in README + PyPI version badge.

**Audit context:** `~/Proyectos/agentcrawl-private-docs/archive/2026-06-28/CODE_AUDIT_2026-06-28.md` (the audit, with fixes-applied appendix).

## Next community priorities

We're working on, in roughly this order:

1. **Keep the public benchmark lane honest.** Accessible public docs, API references, blogs, RFC pages, non-protected ecommerce/product pages only. We don't compete with paid infrastructure and we don't pretend to.
2. **Maintained release smoke targets.** Smoke-tested paths stay tested. They don't grow into unsupported "vs X" comparison copy.
3. **Lighter document ingestion.** More file types only when the dependency cost stays small enough to remain an optional extra.
4. **Verified examples.** Every example in `examples/` runs against a real public site we actually tested. Anything else gets removed.

These are scope keepers, not feature promises. Each only lands when its verification criteria are met.

## Public launch readiness

Public marketing and visibility copy (Show HN, Reddit, blog, demo assets) is drafted in private planning docs and only rolls out after:

1. The `agentcrawl-ai` package installs cleanly in a fresh venv and reports the version we tagged.
2. `pytest` + `ruff check` + `ruff format --check` are clean on the release commit.
3. The GitHub Release for the tagged version has explicit release notes (not auto-generated).
4. The GHCR workflow for the tag is green.
5. No `from agentcrawl.enhanced` import exists in any public source file.

Autonomous work stops at "ready to ship". Showing drafts for review is a user-driven step — see `agentcrawl-private-docs/MARKETING_DRAFTS.md` for the current draft pack and the guardrails around competitive claims.
