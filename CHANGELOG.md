# Changelog

All notable changes to AgentCrawl Community will be documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 0.1.4 - 2026-06-29

Patch release. Carries the audit-2026-06-28 `OBS#5` follow-up onto the
public package. No behavior change for callers that do not change their
default browser environment. Identical runtime semantics to v0.1.3
for existing users.

### Added

- **`AGENTCRAWL_BROWSER_CONCURRENCY` env var** — replaces the hardcoded
  `BoundedSemaphore(2)` in `agentcrawl/fetchers.py` with a configurable
  process-wide semaphore. The limit defaults to `2` (preserving v0.1.3
  behavior) and is floored to `1` to avoid a zero-value deadlock when
  misconfigured. Lets parallel-test runners and CI environments tune
  Playwright concurrency without touching code. Affects
  `agentcrawl/fetchers.py`, `tests/test_audit_fixes.py`
  (3 new regression tests: default, override, floor).

### Verification

- `pytest`: 180 passed (was 177 in v0.1.3; +3 env-var regression cases).
- `ruff check` OK; `ruff format --check` OK.

## 0.1.3 - 2026-06-28

Patch release driven by the cross-cutting technical audit dated
2026-06-28 (`Proyectos/agentcrawl-private-docs/archive/2026-06-28/CODE_AUDIT_2026-06-28.md`).
Four bugs and four optimizations were identified across
`storage`, `server`, `fetchers`, `crawler`, `config`, and `cli`.
Adds the observable dashboard and `--alert-on-failure` hook
landed earlier in `main`; the audit fixes ship together in this
release since they touch the same SQLite lock surface and the
audit-trail plumbing that the dashboard reads.

### Added

- **Observable dashboard** — `agentcrawl dashboard --db agentcrawl.db --output dashboard.html` renders a dependency-free static HTML dashboard from the local SQLite database. The FastAPI server also exposes the same read-only view at `GET /dashboard` and JSON summary at `GET /api/dashboard/summary`.
- **Failure alert hook** — `agentcrawl crawl ... --alert-on-failure --cmd "..."` runs a local shell command only when the completed crawl reports terminal failures. The command receives JSON on stdin with `source`, `failure_count`, and the failure rows.

### Fixed

- **BUG #1 (high) — SQLite-backed scheduling lease.** `server.schedule_job` previously relied on an in-memory boolean guard that is invisible to other worker processes (uvicorn `--workers >1`, separate `agentcrawl jobs` invocations, the `job_threads` background loops). Two workers could observe the same `ready_at` and enqueue the same job twice. Adds `jobs.schedule_lock INTEGER` and `jobs.schedule_lock_expires_at REAL` columns (with a backfill migration) plus `SQLiteStore.acquire_schedule_lease(job_id, ttl)` and `release_schedule_lease(job_id)` methods that use a single atomic `UPDATE ... WHERE schedule_lock IS NULL OR schedule_lock_expires_at < ?` round-trip. `server.schedule_job` now calls `acquire_schedule_lease` before touching `_job_queue` and releases it on failure paths; `_enqueue_job` clears `_queued_jobs` and pops `_job_threads` under the same lock so FIFO order is preserved. Affects `agentcrawl/storage.py`, `agentcrawl/server.py`.
- **BUG #2 (medium) — audit trail attached on terminal fetch failure.** When `config.audit=True`, `_fetch_http` raised `FetchError` without copying the in-progress `AuditTrail` so callers lost the redacted record (status, hostname, bytes) of their actual request. Now attaches `FetchError.audit_trail` on terminal failure (no dead `try/finally`) and `AgentCrawl.scrape` surfaces it via `ScrapeDocument.metadata["audit_trail"]`. Regression test `test_scrape_surfaces_audit_trail_in_error_metadata` patches `agentcrawl.fetchers._fetch_http` to raise a crafted `FetchError(..., audit_trail=AuditTrail(...))` so the contract is exercised directly. Affects `agentcrawl/fetchers.py`, `agentcrawl/crawler.py`.
- **BUG #3 (medium/low) — HTML inside blocked-page markup leaked through `_blocked_page_reason`.** The heuristic compared raw response bytes against a regex designed for plain text; pages like `nginx default 403` with HTML chrome produced false negatives. Adds `agentcrawl/parsing.py::_html_to_plain_text` and routes `_blocked_page_reason` through it before matching. Affects `agentcrawl/crawler.py`, `agentcrawl/parsing.py`.
- **BUG #4 (low) — `CrawlConfig.__post_init__` silently accepted a dict for `llm`.** Community treats `llm` as an import path (e.g. `langchain_openai.ChatOpenAI`); a dict was the right shape for Enhanced's pool configuration. Emit `UserWarning` so misconfigurations surface in logs instead of failing later as a `ModuleNotFoundError`. Affects `agentcrawl/config.py`.

### Optimized

- **OPT #1 — `list_crawl_failures` domain LIKE tightened to three patterns** (`://%/`, `://%:%`, `://%`) to avoid suffix-collisions like `badexample-related.com` matching `example.com`. Affects `agentcrawl/storage.py`.
- **OPT #2 — `_pop_ready_item` returns `(None, min(ready_at))` instead of busy-spinning.** When no scheduled item is ready yet, the worker now sleeps until the soonest item becomes due instead of polling every iteration. CPU savings are modest but the behaviour is observable in long crawls. Affects `agentcrawl/server.py`.
- **OPT #3 — Per-process migration cache for `SQLiteStore`.** A class-level `_migrated_paths: set[str]` skips re-running `CREATE TABLE IF NOT EXISTS` against the same file path on every instantiation. The cache is **bypassed** when the path contains `:memory:` because `sqlite3.connect(":memory:")` returns a fresh per-connection private DB, which is intentionally incompatible with our `self._connect()`-per-call design (see the design decision in the audit doc). Tests use `tmp_path` instead of `:memory:` for true cross-test isolation. Affects `agentcrawl/storage.py`, `tests/test_audit_fixes.py`, `tests/test_server.py`.
- **OPT #4 — `_export_failures_csv` skips `mkdir` + early returns 0 when the filtered row set is empty.** Avoids creating empty `<dest>.csv` files that downstream tooling treats as a successful zero-failure scrape. Affects `agentcrawl/cli.py`, `tests/test_csv_export.py`.

### Verification

- `pytest`: 177 passed (was 141 pre-iteration; +36 new regression cases in `tests/test_audit_fixes.py` covering the four bugs and four optimizations).
- `ruff check` OK; `ruff format --check` OK.
- New regression tests for the SQLite lease (`test_schedule_lease_serializes_workers`), the per-process migration cache (`test_migration_cache_idempotent`), audit-trail surfacing (`test_scrape_surfaces_audit_trail_in_error_metadata`, `test_fetch_error_carries_audit_trail_on_terminal_failure`), blocked-page heuristic (`test_blocked_page_reason_strips_html`), `__post_init__` warning (`test_llm_dict_emits_user_warning`), the three-pattern LIKE (`test_list_crawl_failures_domain_filter_no_suffix_collision`), `_pop_ready_item` timing (`test_pop_ready_item_returns_min_ready_at_when_none_ready`), `_export_failures_csv` empty-row early return (`test_export_failures_csv_skips_empty_rows`).

## 0.1.2 - 2026-06-28

Patch release. `pip install --upgrade agentcrawl-ai==0.1.2` from
0.1.1 is safe: identical runtime semantics, two latent fixes that
were masked by accidental chance.

### Fixed

- `agentcrawl.__version__` now resolves through
  `importlib.metadata.version("agentcrawl-ai")` (the distribution
  name) instead of looking up `"agentcrawl"` (the import name). The
  old code worked because the `except PackageNotFoundError` branch
  returned the fallback literal by coincidence, which would have
  reported a stale version if the fallback had ever been tweaked.
  Affects `agentcrawl --version` and any caller reading
  `agentcrawl.__version__` programmatically.

### Verification

- `pytest`: 153 passed (unchanged from 0.1.1).
- `ruff check` OK; `ruff format --check` OK.
- `pip install --upgrade agentcrawl-ai==0.1.2` in a fresh venv:
  `agentcrawl.__version__` reports `0.1.2`, airgap/crawler/parse
  modules import cleanly.

## 0.1.1 - 2026-06-26

Privacy and observability pillars land as opt-in Community
features. All additions are backwards-compatible: existing
callers that do not pass the new flags get identical behaviour
to 0.1.0.

### Added

- **Token Efficiency pillar** — every `ScrapeDocument.metadata`
  now exposes `estimated_tokens`, `raw_html_tokens_estimate`, and
  `raw_html_bytes`. The CLI accepts `--token-stats` on `scrape` and
  prints a Token Efficiency Report (extracted tokens, raw HTML
  tokens, savings %, raw HTML bytes) to stderr. Cheap, dependency-free
  `len/4` estimator; deliberately avoids tiktoken.
- **Audit / Airgap pillar** — `CrawlConfig` accepts `airgap`,
  `allowlist_domains`, and `audit` flags. The HTTP fetcher installs a
  urllib opener handler that validates every request against the
  target host (+ explicit comma-separated allowlist with `*.foo.com`
  wildcard support). `airgap=True` blocks any non-target request with
  `AirgapViolation`. `audit=True` records
  `metadata.audit_request_count`,
  `metadata.audit_third_party_request_count`,
  `metadata.audit_total_bytes`, and `metadata.audit_records` on the
  document. Env-driven via `AGENTCRAWL_AIRGAP`,
  `AGENTCRAWL_AIRGAP_ALLOWLIST`, and `AGENTCRAWL_AUDIT`.
- **Observable packaging** — `agentcrawl failures [filters]
  --export /path/to/failures.csv` writes the filtered failures
  listing to a CSV file (auto-creates parent dirs, deterministic
  header). Dependency-free: stdlib `csv`.

### Fixed

- Cookie-consent text inside generic containers (`<p>`, `<div>`,
  `<section>`, `<aside>`, `<span>`, `<small>`, `<li>` without a
  `cookie`/`consent` class) is now dropped at the node level before
  Markdown conversion. Legitimate documentation that discusses
  cookies as a feature (e.g. FastAPI's "Cookie Sessions") is
  preserved.
  Added fixture `tests/fixtures/quality/cookie_consent.html` and
  regression tests in `tests/test_parsing.py`.
- When `browser_fallback=true` and the configured `browser_backend`
  is `playwright` or `camofox`, `scrape()` retries once with the
  browser backend on a fresh 200-OK challenge page before reporting
  `client_challenge`. The retry is opt-in (gated on the user flag)
  and falls back silently to the original error if the browser
  itself is challenged or raises. The retry path is the existing
  local fallback and does NOT make Community a Cloudflare bypass;
  managed proxy rotation, residential IPs, and stealth
  challenge-solving remain in Enhanced/Hosted.
  Added `agentcrawl/browser_retry.py` and regression tests in
  `tests/test_browser_retry.py` covering 6 paths (no retry when
  source is non-remote, opt-out preserved, retry success, browser
  also challenged, browser raises, fetcher already a browser).

### Documentation

- `INSTALL_FOR_AGENTS.md`: replaced the `example.com` smoke test
  with `pypi.org/project/agentcrawl-ai` plus `agentcrawl doctor`,
  and documented `example.com` explicitly as the canonical
  Community boundary case (Cloudflare client challenge, `ok=False`,
  `error_type=client_challenge`).
- `README.md`: canonical quickstart now points at
  `https://pypi.org/project/agentcrawl-ai/`, with an explicit
  "Edge case: `example.com` returns a Cloudflare client challenge"
  block that explains the boundary without smoothing it over.
- `ROADMAP.md`: marked the cookie-consent filter, the opt-in
  browser retry, and the doc swaps as Completed; updated "Next
  community priorities" to name the three differentiation pillars.

### Verification

- `pytest`: 153 passed (127 original + 26 new), 1 non-blocking
  Starlette/httpx warning.
- `ruff check` OK; `ruff format --check` OK.
- `quality_report.py`: 20/20 fixtures @ 100.0 avg, 85 min.
- `agentcrawl doctor`: `local_scrape`, `agentcrawl_command`,
  `python` all `ok=true`. `remote_health` is intentionally off
  (no daemon, on-demand).

## 0.1.0 - Unreleased

- Initial AgentCrawl Community release candidate.
- CLI, Python library, HTTP API, Docker/GHCR image, and MCP integration.
- Local and HTTP scraping with optional browser/Camofox fallback.
- Main-content Markdown extraction with semantic container selection, text-rich fallback blocks, and boilerplate reduction.
- Markdown table preservation and fenced code blocks with language tags from common HTML classes.
- Local document ingestion for Markdown, text, JSON, XML/RSS/Atom, and PDF-to-Markdown through the optional `docs` extra.
- Mapping, crawling, persistent jobs, progress, cancellation, event history, crawl failures, and selective failure retries.
- Cache controls, usage reporting, operational stats, backup, and restore.
- Authentication, SSRF protections, unsafe redirect blocking, private-network controls, and safe server defaults.
- Safety baseline fixes for text normalization, sitemap discovery, PDF limits, scrape error behavior, URL validation, and crawl failure filtering.
- Package version export, wheel/sdist build verification, `twine check`, and clean install smoke tests for base/server/MCP/docs extras.
- Lightweight default Docker image based on `python:3.12-slim`, published through GHCR as `ghcr.io/jorg18/agentcrawl:latest`.
- GitHub Actions Docker workflow builds, smoke-tests, and publishes GHCR images for `main`, tags, and commit SHAs.
- README quickstart refreshed around CLI, Python, MCP, Docker/API, and Community scope.
- Quality report baseline: 19 checked-in fixtures, minimum score threshold 85, current local average 100.0, richer provenance metadata, JSON-LD/Product schema extraction, Markdown structure metrics, and noisy-layout coverage.
- Phase 2 hardening: protected/challenge pages are classified as honest failures instead of scraped content, and technical reference extraction avoids generated index/TOC candidates when selecting main content.
