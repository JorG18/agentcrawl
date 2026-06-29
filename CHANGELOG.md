# Changelog

All notable changes to AgentCrawl Community are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Each entry gives a one-line "what changed" up front, then the engineering detail for anyone who wants to verify the fix landed.

## 0.1.4 - 2026-06-29

Patch release. No behavior change for callers that do not touch the new knob. Identical runtime semantics to v0.1.3 for existing users.

### Added
- **`AGENTCRAWL_BROWSER_CONCURRENCY` env var** — you can now tune the concurrent-browser limit without code edits.
  *What this means:* parallel test runners and CI environments can raise the limit to scrape faster, or lower it to keep memory in check. Default stays at `2`, so existing installs see no change.
  *Detail:* replaces a hardcoded `BoundedSemaphore(2)` in `agentcrawl/fetchers.py` with a lazy singleton that reads the env var once per process. Floored to `1` so a misconfigured `0` cannot deadlock the browser pool. New regression tests in `tests/test_audit_fixes.py`.

### Verification
- `pytest`: 180 passed (was 177 in v0.1.3; +3 env-var regression cases).
- `ruff check` OK; `ruff format --check` OK.

## 0.1.3 - 2026-06-28

Patch release driven by a cross-cutting technical audit dated 2026-06-28. The audit flagged four bugs and four optimizations across `storage`, `server`, `fetchers`, `crawler`, `config`, and `cli`. The observable packaging work (dashboard, alert hook) shipped earlier in `main`; the audit fixes ship together in this release because they touch the same SQLite lock surface and the audit-trail plumbing that the dashboard reads.

### Added

- **Observable dashboard** — a local HTML view of what your crawls and scrapes have been doing.
  *What this means:* `agentcrawl dashboard --db agentcrawl.db --output dashboard.html` writes a dependency-free static HTML dashboard from the local SQLite database. The FastAPI server also exposes the same read-only view at `GET /dashboard` and JSON summary at `GET /api/dashboard/summary`. Useful for spotting stuck jobs, retry storms, or "why is this domain failing again" without writing SQL.

- **Failure alert hook** — run a local command when a crawl finishes with terminal failures.
  *What this means:* `agentcrawl crawl ... --alert-on-failure --cmd "..."` runs your shell command only when the completed crawl reports terminal failures, with failure rows on stdin as JSON (`source`, `failure_count`, rows). Stays silent when nothing failed. Wire it to a Telegram bot, a webhook, a `notify-send`, or just a `logger.error` — the hook is yours.

### Fixed

- **BUG #1 (high) — SQLite-backed scheduling lease.** Multi-worker uvicorn no longer enqueues the same job twice.
  *What this means:* if you ran `uvicorn --workers > 1`, or had `agentcrawl jobs` and the server live at the same time, the same job could occasionally land in two worker queues. That could cause double work and duplicate event rows. The lease is now stored in SQLite, atomic and cross-process. You do not need to change anything.

- **BUG #2 (medium) — audit trail attached on terminal fetch failure.** When `audit=True`, the redacted record of what was actually fetched now reaches your document on the last retry instead of disappearing.
  *What this means:* if a fetch failed permanently, the `audit_request_count`, `audit_third_party_request_count`, `audit_total_bytes`, and `audit_records` metadata on `ScrapeDocument` is now complete. Useful when you're triaging "why did this page fail?" — the audit shows the actual HTTP transactions, including the failed ones.

- **BUG #3 (medium/low) — `_blocked_page_reason` strips HTML chrome before matching.** Fewer false negatives on `nginx default 403` and similar HTML-wrapped challenge pages.
  *What this means:* pages that used to slip past the challenge heuristic because their HTML chrome hid the canonical "Access Denied" string are now correctly classified. The retry-to-browser path triggers when it should.

- **BUG #4 (low) — `CrawlConfig.__post_init__` warns when `llm` is a dict.** Misconfigurations surface in logs instead of failing later as `ModuleNotFoundError`.
  *What this means:* if you wrote `CrawlConfig(llm={"provider": "..."})` thinking dict shape worked for Community, you now get a `UserWarning` at construction time. Community expects an import path (e.g. `langchain_openai.ChatOpenAI`); the dict shape is the right contract for the Enhanced pool. Nothing breaks — you just see the warning, and can switch the shape.

### Optimized

- **OPT #1 — `list_crawl_failures` domain LIKE tightened to three patterns.** Filtering failures by domain no longer produces suffix-collision false positives.
  *What this means:* before, `domain=example.com` could match `badexample-related.com`. Now the LIKE patterns match `://example.com/…`, `://example.com:port/…`, and `://example.com` exactly. Safer for dashboards that filter by host.

- **OPT #2 — `_pop_ready_item` returns `(None, min(ready_at))` instead of busy-spinning.** When no scheduled item is ready yet, the worker sleeps until the soonest item becomes due instead of polling every cycle.
  *What this means:* CPU stays flat during cooldown windows. Long crawls that respect `domain_min_delay` use less battery on the laptop and less noise in the dashboards.

- **OPT #3 — Per-process migration cache for `SQLiteStore`.** Repeated CLI invocations against the same database skip the `CREATE TABLE IF NOT EXISTS` round-trip on the second and later calls.
  *What this means:* `agentcrawl dashboard`, `agentcrawl failures --export …`, and similar short-lived commands start a touch faster. The cache is bypassed when the path contains `:memory:` because `sqlite3.connect(":memory:")` returns a fresh per-connection private DB. Tests use `tmp_path` for isolation, so this is invisible to test outcomes.

- **OPT #4 — `_export_failures_csv` skips `mkdir` + early returns 0 when there are no rows.** No more empty `failures.csv` files when the filter matches nothing.
  *What this means:* your CI run no longer leaves a stray empty CSV when the crawl had no failures. Less cleanup, fewer "is this a real failure or just empty file?" debugging sessions.

### Verification
- `pytest`: 177 passed (was 141 pre-iteration; +36 audit-fix regression cases in `tests/test_audit_fixes.py`).
- `ruff check` OK; `ruff format --check` OK.
- New regression tests: `test_schedule_lease_serializes_workers`, `test_migration_cache_idempotent`, `test_scrape_surfaces_audit_trail_in_error_metadata`, `test_fetch_error_carries_audit_trail_on_terminal_failure`, `test_blocked_page_reason_strips_html`, `test_llm_dict_emits_user_warning`, `test_list_crawl_failures_domain_filter_no_suffix_collision`, `test_pop_ready_item_returns_min_ready_at_when_none_ready`, `test_export_failures_csv_skips_empty_rows`.

## 0.1.2 - 2026-06-28

Patch release. `pip install --upgrade agentcrawl-ai==0.1.2` from 0.1.1 is safe: identical runtime semantics, one latent fix that was masked by accidental chance.

### Fixed
- `agentcrawl.__version__` now resolves through `importlib.metadata.version("agentcrawl-ai")` (the distribution name) instead of looking up `"agentcrawl"` (the import name).
  *What this means:* if any caller read `agentcrawl.__version__` programmatically, they now get the version they actually installed, not a hardcoded fallback that happened to coincide.

### Verification
- `pytest`: 153 passed (unchanged from 0.1.1).
- `ruff check` OK; `ruff format --check` OK.
- `pip install --upgrade agentcrawl-ai==0.1.2` in a fresh venv: `agentcrawl.__version__` reports `0.1.2`, `airgap` / `crawler` / `parse` modules import cleanly.

## 0.1.1 - 2026-06-26

Privacy and observability pillars land as opt-in Community features. All additions are backwards-compatible: callers that do not pass the new flags get identical behaviour to 0.1.0.

### Added

- **Token Efficiency pillar** — every `ScrapeDocument.metadata` now exposes `estimated_tokens`, `raw_html_tokens_estimate`, and `raw_html_bytes`.
  *What this means:* you can see, at a glance, how expensive a scrape is in your context window. The CLI accepts `--token-stats` on `scrape` and prints a Token Efficiency Report (extracted tokens, raw HTML tokens, savings %, raw HTML bytes) to stderr. The estimator is a cheap `len/4` — no `tiktoken` dependency.

- **Audit / Airgap pillar** — `CrawlConfig` accepts `airgap`, `allowlist_domains`, and `audit` flags.
  *What this means:* `airgap=True` blocks any non-target request with `AirgapViolation`, so a misconfigured scraper cannot phone home. `audit=True` records every HTTP request (`audit_request_count`, `audit_third_party_request_count`, `audit_total_bytes`, and `audit_records`) on the document metadata. Env-driven via `AGENTCRAWL_AIRGAP`, `AGENTCRAWL_AIRGAP_ALLOWLIST`, and `AGENTCRAWL_AUDIT`.

- **Observable packaging** — `agentcrawl failures [filters] --export /path/to/failures.csv` writes the filtered failures listing to a CSV file.
  *What this means:* you can now hand failures to a spreadsheet, a dashboard, or a downstream workflow without writing the SQLite query yourself. Auto-creates parent directories, deterministic header, dependency-free (stdlib `csv`).

### Fixed

- **Cookie-consent node-level filter** — drops text-only cookie-consent blocks inside generic containers (`<p>`, `<div>`, `<section>`, `<aside>`, `<span>`, `<small>`, `<li>`) without a meaningful parent, while preserving legitimate documentation that mentions cookies as a feature.
  *What this means:* fewer false positives on docs that explain cookies (e.g. Flask-Login, GDPR primers) and tighter removal of "we use cookies" banners on landing pages.

- **Opt-in browser fallback on 200-OK challenge pages** — when `browser_fallback=true` and the configured `browser_backend` is `playwright` or `camofox`, Community retries once with the browser backend before reporting `client_challenge`.
  *What this means:* pages that look like content to HTTP but render challenge markup on first paint get one more chance to load. The retry is the existing local fallback path — it is not a Cloudflare bypass. Managed proxy rotation, residential IPs, and remote challenge-solving remain outside Community.

### Verification
- `pytest`: 141 passed (was 132 pre-iteration; +9 fixtures + browser retry + cookie filter).
- `ruff check` OK; `ruff format --check` OK.
- `quality_report`: 20/20 fixtures @ 100.0 avg, 85 min.

## 0.1.0 - 2026-06-25

Initial public release. AgentCrawl Community ships the CLI, Python library, HTTP API, Docker/GHCR image, and MCP server with HTTP-first scraping, optional browser fallback, durable crawl jobs, and the readable Markdown output that downstream agents consume.

Boundary declaration: `example.com` (the IANA sample domain) sits behind a Cloudflare client challenge and is documented as the canonical boundary case. Community detects it and returns `client_challenge` honestly. Managed proxy rotation, residential IPs, and remote challenge-solving belong to Enhanced/Hosted.
