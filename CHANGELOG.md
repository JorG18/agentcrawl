# Changelog

All notable changes to AgentCrawl Community are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Each entry gives a one-line "what changed" up front, then the engineering detail for anyone who wants to verify the fix landed.

## 0.2.1 - 2026-09-24

A security patch, plus honest error reporting. **Upgrade if an agent uses the local MCP server.**

### Security

- **C1 (high): the local MCP engine read arbitrary files.**
  *What this means:* with `AGENTCRAWL_BASE_URL` unset, `scrape_url("/etc/passwd")` or `scrape_url("~/.pypirc")` returned the file as Markdown, and so did `scrape_many`, `extract_structured`, `map_site` and `crawl_site`. A prompt injection on any page the agent read could turn that into credential theft. The API server gated local files, but the MCP did not.
  *Detail:* the gate now lives in `fetchers.fetch_source`, so every entrance goes through one check (`security.check_local_source`, also used by the API server). New config keys `allow_local_files` and `local_files_root`. `config_from_env()`, which the MCP uses, refuses local files unless `AGENTCRAWL_ALLOW_LOCAL_FILES=true`; `AGENTCRAWL_LOCAL_FILES_ROOT` confines reads to the real path of one directory (`..`, symlink escapes and `/root-evil` siblings are refused). The check runs before the file is touched, so a refusal does not reveal whether the path exists. Refusals come back as an honest `error_type` (`local_files_disabled` or `local_file_outside_root`). The API server also passes its setting into the engine, so the gate holds even on a path that skips request validation.
  **Breaking (MCP only):** an MCP setup that read local files must now set `AGENTCRAWL_ALLOW_LOCAL_FILES=true` (and should set a root). The Python library and the CLI keep reading local files by default; `AGENTCRAWL_ALLOW_LOCAL_FILES=false` turns them off there too.

### Fixed

- **S2: errors say what happened.**
  *What this means:* a failed page carried only `error_type`, and that type was guessed from the message wording. "Unknown fetcher: browser" became `browser_error`, and a DNS failure inside a Playwright fetch could be labelled a browser problem. The API server and the crawl loop also re-guessed the type from the message, so a `client_challenge` page reached callers as `fetch_error` and the crawl retried it.
  *Detail:* failed documents now carry `error_message` (one line, control characters stripped, at most 300 characters) and `status_code` when an HTTP status is known. `errors.classify_exception` classifies by the explicit type on the error first, then the HTTP status, then the exception cause chain (timeout, TLS, DNS/connection), and only then by message wording. The API server and `crawl()` keep the engine's `error_type` instead of overwriting it. `FetchError` accepts `error_type=` and `status_code=`.
  **Breaking (config):** `fetcher` is validated in `CrawlConfig.from_dict` (`http`, `playwright`, `camofox`; `browser` is accepted as an alias of `playwright`), and so is `browser_backend`. An unknown name is now a `ValueError` (HTTP 400 on the API) instead of a failed page.

## 0.2.0 - 2026-09-23

The 2026-09 hardening release, plus a first set of new extraction capabilities. It is the first release after 0.1.4.

**Security and honesty.** Four audit passes closed one recurring failure mode everywhere it appeared — **the data was dropped, miscounted or let through and nothing said so** — which is exactly what the project's pillars promise never happens. The privacy guarantees now hold end to end: the airgap and the audit trail cover page fetches, search, robots.txt/sitemap discovery *and* every request the local Playwright browser makes (the Camofox backend, which cannot be guarded, is refused under airgap); HTTP connections are DNS-pinned against rebinding; jobs, cache and every aggregate are scoped per API key.

**New capabilities.** Batch scraping (`scrape_many`) on every surface, deterministic CSS-schema extraction without an LLM, query-aware (BM25) selection when a page exceeds the budget, CSV/TSV ingestion, CLI parity with MCP configuration, and a reproducible offline comparison benchmark.

Deliberate API behavior changes are called out with **Breaking** below; everything else is invisible to existing callers.

### Fixed

- **SEC-11 (high) — `airgap` and `audit` now cover robots.txt and sitemap discovery.**
  *What this means:* with `airgap=True`, `map()` still fetched a sitemap that a site's `robots.txt` announced on *another host* — reproduced with two local servers, the second host received `GET /sitemap.xml`. And discovery requests never appeared in the audit trail: a crawl that fetched `robots.txt` plus one page reported one request. Discovery had its own opener with the SSRF guard only.
  *Detail:* `_guarded_urlopen` now goes through `fetchers._safe_urlopen` (SSRF guard on every hop, DNS pinning, airgap allowlist). Each `map()`/`crawl()` run carries a discovery audit trail, exposed as `metadata.discovery_audit` on `MapResult` / `CrawlRun` (same shape as a document's audit fields; page documents keep their own trails). A sitemap the airgap refuses is skipped and listed in `metadata.airgap_skipped` instead of failing the map. robots.txt is fetched at most once per run.

- **SEC-8b (low) — the last global aggregates are scoped per key.**
  *What this means:* SEC-8 (below) scoped the cache and most aggregates per key, but `/v1/stats` → `job_events` and the dashboard's `job_events` and `crawl_queue` still counted every key's jobs. They now follow the caller like the rest (owner keys and auth-disabled servers keep the global view).

- **BUG (medium) — the CLI's local mode ignored the `AGENTCRAWL_*` environment.**
  *What this means:* `cli._run_local` built the engine from `AGENTCRAWL_FETCHER` alone, so `AGENTCRAWL_ALLOW_PRIVATE_NETWORK=true agentcrawl scrape http://127.0.0.1:3000/` was still refused (while MCP honoured the same variable), and airgap/audit/robots/timeout could not be set from the CLI at all. One mapping, `config.config_from_env()`, now serves CLI local mode, MCP local mode and `doctor`; the CLI adds `--allow-private-network`, `--airgap`, `--allowlist`, `--audit`, `--timeout-ms`, `--no-robots`, `--no-browser-fallback` (flags beat env). The private-network refusal now says how to allow it on purpose.

- **SEC-9 (high) — the browser fetcher is guarded request by request.**
  *What this means:* with Playwright, only the final `page.url` was validated, after every redirect hop, iframe, image, `fetch()`/XHR and WebSocket had already left the machine. A public page could make the browser reach `127.0.0.1`, cloud metadata or any third-party host, and `airgap=True` kept nothing off the network on this path. Verified with real Chromium: an unguarded test page leaked 5 requests to a non-allowed host; the guarded one leaks 0.
  *Detail:* new `agentcrawl.browser_guard.BrowserNetworkGuard`, installed as a context-level route (popups/iframes included) plus a WebSocket route; service workers are blocked while guarding. Because a Playwright route only sees the first URL of a redirect chain, the guard performs each request itself with `route.fetch(max_redirects=0)`: sub-resource chains are walked and validated hop by hop inside the guard; main-frame redirects become a fresh, re-routed `goto`. Blocked requests are recorded in the audit trail (`blocked: true`), and browser fetches now carry audit metadata when `audit=True`. Known limit, documented in SECURITY.md: the browser resolves DNS itself, so there is no IP pinning on this path.

- **SEC-10 (medium) — DNS pinning on the HTTP path.**
  *What this means:* the SSRF check resolved the host, then urllib resolved it again to connect; a DNS server answering "public" first and "127.0.0.1" second (DNS rebinding) got past the guard. Connections now resolve once, validate those addresses and connect to exactly them.
  *Detail:* `security.resolve_public_addresses()` + `PinnedHTTPHandler` / `PinnedHTTPSHandler` (SNI and certificate checks still use the hostname), active whenever `allow_private_network` is false, for page fetches, search and robots/sitemap discovery. Proxied requests are not pinned (the proxy resolves).

- **BUG (medium) — `AgentCrawl({"llm": client}).extract()` crashed for real LLM clients.**
  *What this means:* the config was copied with `dataclasses.asdict`, which deep-copies every field — including the caller's LLM client, whose locks/connections cannot be copied (`TypeError: cannot pickle '_thread.RLock' object`). The config object is now passed as-is (`AgentCrawler.markdown()` uses a shallow `replace`).

- **BUG (medium) — airgapped `search_then_scrape` let the search engine choose the hosts.**
  *What this means:* each scrape trusts its own target host, so under `airgap=True` every result URL was contacted whatever its host. Result hosts must now match `allowlist_domains`; the rest are listed in `airgap_skipped` instead of fetched.

- **BUG (low) — usage metering is attributed to the right key.**
  *What this means:* retrying a job's failures billed (and scheduled the re-run under) the key that pressed retry — an owner key retrying another key's job paid for it. The job owner is now billed. `/v1/extract` also records model requests on their own line (`/v1/extract.llm_calls`, reattempts included; `CrawlResult.metadata.llm_calls`).

- **SEC-1 (high) — robots.txt and sitemap discovery now honor SSRF guard rails.**
  *What this means:* `AgentCrawl.map()` fetched `robots.txt`, `Sitemap:` entries, and sitemap-index `<loc>` targets through raw `urllib.request.urlopen` with no URL validation and no redirect protection. On a network-exposed deployment, a remote site's robots file could point discovery at `169.254.169.254` or internal services. Discovery now goes through `_guarded_urlopen`, which applies `validate_remote_url` and `_SafeRedirectHandler` to the initial URL and every redirect hop, and `Sitemap:` entries are re-validated before fetch.

- **SEC-2 (medium) — bounded sitemap-index recursion.**
  *What this means:* a self-referencing or deeply-nested sitemap index could recurse until the Python stack blew up. Depth is now capped (`_SITEMAP_MAX_DEPTH = 4`) and total `<loc>` entries are budgeted (`_SITEMAP_MAX_URLS = 100_000`); hitting a limit returns the URLs collected so far instead of failing discovery.

- **SEC-3 (medium) — optional local-file jail for the HTTP API.**
  *What this means:* with `AGENTCRAWL_ALLOW_LOCAL_FILES=true`, any API key could read any world-readable file as the server user (`/etc/passwd`, config volumes, mounted secrets). Setting the new `AGENTCRAWL_LOCAL_FILES_ROOT` env var confines local-file scrapes to that directory tree (symlink- and `..`-safe via `os.path.realpath`). Unset keeps the old behavior; the default config keeps local files disabled anyway.

- **BUG (high) — delayed retries no longer strand in `queued` until restart.**
  *What this means:* after a transient page failure the worker requeued the job and tried to re-schedule it, but `acquire_schedule_lease` only granted the lease when it was null or expired — and the 300s lease taken at claim time was still live. The re-schedule silently failed and the job waited for a process restart. The lease is now re-entrant for the same owner, so the worker that requeued the job can renew its own lease; other instances still cannot steal it.

- **BUG (medium) — restart recovery no longer deletes documents of unrelated cancelled jobs.**
  *What this means:* `prepare_restart_recovery()` ran `delete from crawl_documents where job_id in (select id from jobs where status = 'cancelled')`, wiping documents from every cancelled job in history, not just the jobs it transitioned in that pass. Cleanup now targets only the `cancelling → cancelled` jobs finalized by the recovery itself (same contract as a live cancellation; recovered jobs keep their documents so the resumed run does not duplicate pages).

- **QUAL (medium) — HTTP bodies are decoded with the declared charset.**
  *What this means:* `_fetch_http` always decoded bytes as UTF-8 with replacement, silently mojibake-ing latin-1 / windows-1252 pages, and the corrupted text was cached and stored. Decoding now prefers the `Content-Type` charset, honors UTF-8/UTF-16 BOMs over a lying declaration, falls back to strict UTF-8, then latin-1 (lossless). `Retry-After` values are also clamped to `[0, 30]` so a hostile header can neither stall the crawl nor crash `time.sleep`.

- **SEC-4 (high) — crawl jobs are scoped to the API key that created them.**
  *What this means:* every `/v1/jobs/{job_id}` route authenticated the caller but never checked ownership, so any valid key could read another key's job, its extracted documents, its event history, and its failures — and could cancel or requeue it. Keys now only reach their own jobs and only list their own crawl failures; a foreign job returns `404` (not `403`) so a key cannot probe which job ids exist either. `AGENTCRAWL_OWNER_API_KEYS` keeps working as the operator set: owner keys already bypass rate limiting and are the only keys that see every job.

- **SEC-5 (medium) — the HTTP dashboard follows the API auth setting.**
  *What this means:* `GET /dashboard` and `GET /api/dashboard/summary` were served without authentication while the equivalent `GET /v1/stats` required a key, so a network-exposed deployment leaked job counts, cache domains, open failures, and usage units to anyone who could reach the port. Both now follow `AGENTCRAWL_AUTH_ENABLED`, with the new `AGENTCRAWL_DASHBOARD_PUBLIC=true` opt-out for single-operator hosts that want the header-less browser view. Auth disabled (local/dev) keeps the old open behavior.

- **BUG (high) — a missing browser backend no longer masks the real error.**
  *What this means:* with the default `browser_fallback=true` and no `[browser]` extra installed, every 403/fetch failure was reported as `error_type=browser_error` with "Playwright is not installed. Install agentcrawl[browser]..." instead of the honest `blocked` + HTTP 403. Worse, `browser_error` is in `crawl_retry_error_types`, so crawls spent their whole retry budget on pages that were never going to work. The fallback is now only attempted when the configured backend can actually run; otherwise the original error is preserved. When the fallback *is* attempted and also fails, the reported error stays the honest HTTP one and the reason is attached as `metadata.browser_fallback_error`.

- **BUG (medium) — deterministic refusals are no longer retried.**
  *What this means:* `_fetch_http`'s catch-all retry treated an airgap violation or an SSRF refusal from a redirect hop like a transient transport fault: it retried three times with backoff (0.9s in the reproduced case) and then reported the denial wrapped as a generic `fetch_error`, burying the actual reason. Policy denials now fail immediately with their real message; timeouts, DNS, TLS, and connection faults remain retryable.

- **BUG (medium) — `agentcrawl doctor` reports the installed version.**
  *What this means:* `_doctor()` looked up the `agentcrawl` distribution while the package publishes as `agentcrawl-ai`, so a correct install reported a stale, unrelated version (`0.1.0` here) or “source checkout”. `__init__` was fixed for this in v0.1.2; the CLI call site was missed.

- **BUG (low) — the API reports its real version.**
  *What this means:* `FastAPI(version="0.1.0")` was hardcoded, so `/openapi.json` and `/docs` advertised 0.1.0 no matter which release was running. It now uses the package version. The source-checkout fallback in `__init__` also no longer hardcodes a literal (`0.1.2`, five releases behind): it reads `pyproject.toml`, so it cannot drift again.

- **SEC-6 (medium) — fetched bodies are bounded.**
  *What this means:* `_fetch_http` called `response.read()` with no ceiling, and `urlopen` gives no size guarantee — a server can omit `Content-Length` or lie about it and stream indefinitely. Extraction only keeps `max_input_chars` (64 KB by default), so a hostile or oversized page was fully materialized in memory first (measured: 41 MB read / 79 MB peak for a 64 KB budget). Bodies are now read in chunks up to the new `max_response_bytes` (10 MB, settable per request via the API `config`), and an oversized body fails with a clear error instead of exhausting memory. `robots.txt` and sitemap discovery get their own 50 MB ceiling — the sitemap protocol's own uncompressed limit — so legitimate large sitemaps still work while a hostile one cannot stream without end.

- **SEC-7 (medium) — local documents have a size ceiling.**
  *What this means:* PDFs were capped at 50 MB per file and 500 pages, but every other local document type (`.txt`, `.md`, `.json`, `.xml`, and unknown suffixes) was read whole with no limit — a 60 MB `.txt` read back in full with no error, and the API can be pointed at local paths when `AGENTCRAWL_ALLOW_LOCAL_FILES` is enabled. All local documents now share the 50 MB ceiling and fail with a clear message beyond it.

- **QUAL (low) — the server's per-domain bookkeeping is bounded.**
  *What this means:* `_domain_last_seen` and `_domain_semaphores` kept one entry per domain for the lifetime of the process, so a long-running server that scraped many domains grew without limit. State is now trimmed past 1024 domains, oldest pacing entry first, and a domain semaphore is only dropped while no request holds it — so trimming can never loosen an in-flight per-domain limit.

- **QUAL (low) — Playwright sessions release their resources.**
  *What this means:* `_fetch_playwright` closed the browser twice — the second close happened after the Playwright driver had already stopped, so it failed silently and leaked browser processes on error paths — while the `context` was never closed at all. Both now close exactly once inside the Playwright session, including when navigation fails.

- **BUG (high) — the extractor no longer truncates the page text in silence.**
  *What this means:* `html_to_markdown` ended with `[: max_input_chars]` and `chunk_text` discarded everything past `chunk_size * max_chunks` (64 000 chars with the defaults). A 152 047-char page reached the model as 64 005 chars — 58% gone — so facts past the cut came back as `null` and looked like a model failure, while the prompt says "use only evidence in the page text". The cut is now a reported decision, not an accident: a 278 935-char page still yields 64 000 chars of markdown, but the document now carries `metadata.markdown_truncated`, `chars_omitted`, `markdown_chars_full`, `chunks_kept`, and `chunks_total`, and library callers get the same numbers from `apply_output_budget()` and `chunk_text_stats()`. `chunk_text` keeps its signature.

- **BUG (high) — `audit=true` no longer turns the airgap on behind your back.**
  *What this means:* `_safe_urlopen` attached the airgap handler whenever an audit trail was present (`if airgap or audit_trail is not None`), so a flag you read as observe-only changed which traffic was allowed. With the default empty allowlist the allowlist reduces to the target host, so `audit=true, airgap=false` refused every cross-host redirect — `example.com` → `www.example.com` is the most common redirect on the web — and `_is_policy_denial` classified it as a deterministic refusal, so it was not even retried: the scrape failed outright. An audit trail is measurement; enforcement is now explicit through `_AirgapHandler(enforce=...)`, and audit alone never blocks a request.

- **BUG (medium) — the audit trail counts each request once.**
  *What this means:* the airgap handler recorded a request and `_fetch_http` recorded the same one again. One real HTTP hit produced `audit_request_count: 2` with a duplicate record (`status: None, bytes: 0`) alongside the real one — and in the blocked case a request that never left the machine was counted as a third-party request. Records are now written once, by `_fetch_http`, with the real status and byte count; blocked hops are marked `blocked: true` and excluded from the third-party count. If you document "1 request, 0 third parties", the trail now proves it.

- **BUG (medium) — `search_web` goes through the same guard rails as a scrape.**
  *What this means:* the search backend called `urllib.request.urlopen` directly and read the body with `response.read()` and no ceiling, so an `airgap=true` install still issued the request, the hop never appeared in `audit_records`, and a hostile response could stream into memory. Search now honours airgap (and fails with an honest refusal when the search host is not allowlisted), records the hop under `audit=true`, and respects `max_response_bytes`.

- **BUG (medium) — a bad `include`/`exclude` regex can no longer fail a request or a crawl.**
  *What this means:* `POST /v1/map` with `include: ["docs("]` answered `500`, and a catastrophic pattern could hang the crawler against attacker-chosen URLs (ReDoS). Patterns are validated up front — `validate_url_patterns` names the offending pattern and the API answers `400` — and `url_allowed` is now total: an uncompilable pattern counts as "does not match" instead of raising in the middle of a crawl.

- **BUG (medium) — client `config` values are validated by type and range, not just by name.**
  *What this means:* the allowlist check added earlier in this release only verified key names. `max_input_chars: "x"` and `http_retries: "2"` answered `500` on a client error; `timeout_ms: "abc"` returned `200` with `error_type: fetch_error` (blaming the network for a caller typo, and polluting the failure ledger); `headless: "false"`, `respect_robots_txt: "false"`, and `browser_fallback: "false"` were accepted and treated as `True`, silently inverting a privacy-relevant intent; `browser_fallback_statuses: "403"` became `('4','0','3')`, enabling the browser for every 4xx; `crawl_max_pages: 0` was a silent no-op. Validation now lives in `CrawlConfig.from_dict`, one source of truth for the library, the CLI, and the API, and the server translates the resulting `ValueError` into the `400` the caller deserves.

- **SEC-8 (medium) — the scrape cache and the aggregate stats are scoped per key too.**
  *What this means:* the per-key scoping of SEC-4/SEC-5 covered jobs and failures but not the cache: `GET /v1/stats` and `GET /api/dashboard/summary` exposed another key's `cache_by_domain`, `jobs` queue counts, and `usage_by_endpoint`, and `DELETE /v1/cache` with no filters ran `delete from scrape_cache` — a regular key could clear every key's cache. `scrape_cache` now has an `owner_key` column (auto-migrated in place, and the owner also enters the cache-key hash so two keys can never overwrite each other's row), the aggregates filter by caller, and an unfiltered cache delete only clears your own rows. Owner keys keep the global view. Trade-off, as designed: two keys scraping the same URL no longer share one upstream fetch.

- **BUG (low) — challenge-page detection keeps line boundaries.**
  *What this means:* `_blocked_page_reason` concatenated the stripped text, so unrelated fragments could merge across block boundaries and register as a challenge page. Block tags now become line breaks and plain text keeps its own lines; a cookie-consent notice no longer reads as "Access Denied".

- **BUG (low) — transport errors are classified before the browser bucket.**
  *What this means:* `"browser"` is a broad substring, so a DNS or certificate failure inside a browser fetch was classified `browser_error` and lost its real cause (and `browser_error` is retryable, so the retry budget went to the wrong class). TLS and network patterns are now checked first.

### Changed

- **Breaking — CLI exit codes.** `scrape`, `scrape-many`, `extract-css`, `map` and `crawl` exit **1** when the result carries errors (`success: false` in remote mode) and **2** on usage errors; the JSON is still printed. They always exited 0 before, so scripts and CI could not tell a failed scrape from a good one.
- **Breaking — `/v1/crawl` with `wait=true` is capped** at `AGENTCRAWL_SYNC_CRAWL_MAX_PAGES` (default 25) and charges one rate-limit unit per page; larger crawls get `400` asking for a durable job (`wait=false`). It used to run up to 10 000 pages inside the request thread for one unit.
- **Breaking — `/v1/extract` bounds its input:** `prompt` 1-8 000 chars, `schema` ≤ 64 KiB (`422` otherwise).
- **Breaking — total body read deadline** of 3 × `timeout_ms` (minimum 1 s). `timeout_ms` is per socket operation, so a server dripping bytes could hold a worker indefinitely; reads now use `read1` and stop at the deadline with an honest error.
- **Breaking — the API only routes through Camofox when the operator configured it** (`AGENTCRAWL_FETCHER` or `AGENTCRAWL_BROWSER_BACKEND` = `camofox`); a caller override selecting it is `400`. Camofox also refuses to run under `airgap=True`: its browser lives in another process and cannot be guarded from here.
- Scrape cache keys now include `query`; entries written by earlier builds simply miss once.

- **Breaking (HTTP API) — unsupported `config` overrides are rejected with `400`.**
  *What this means:* `POST /v1/scrape`, `/v1/map`, and `/v1/crawl` used to silently drop any `config` key outside the server-controlled allowlist. A caller asking for `airgap=true` (or mistyping `fetcher`) got a request that looked accepted but had no effect — dangerous for the privacy settings. Unknown or server-controlled keys now fail the request with the offending names. `POST /v1/crawl` validates before creating the job, so a bad override does not leave a failed durable job behind.

- **Breaking (library) — `MapRequest.max_urls` is bounded and `browser_fallback_statuses` is typed.**
  *What this means:* `max_urls` now takes `1..10_000`, matching `CrawlRequest.max_pages`. `-1` used to return an empty map with no warning, and `10**9` let one request carry up to the 100 000-URL sitemap ceiling. `browser_fallback_statuses` accepts a scalar or a list of ints; a bare string used to be iterated into `('4','0','3')`, which enabled the browser fallback for *every* 4xx instead of the one status the caller named.

- **Breaking (library) — the graph adapters read `loader_kwargs["timeout"]` as seconds.**
  *What this means:* `normalize_graph_config` assigned it straight to `timeout_ms`, so the documented `{"timeout": 30}` became a 30 ms timeout — every fetch failed instantly — and `{"timeout": 0.5}` became `int(0.5) == 0`. It is now converted to milliseconds, rejects non-positive and non-numeric values by name, and an explicit `timeout_ms` still wins.

- MCP `scrape_url` documents that `use_cache` and `cache_ttl_seconds` are server-mode only; the local engine keeps no cache.

### Added

- **`scrape_many` everywhere** — `AgentCrawl.scrape_many()`, `POST /v1/scrape_many` (1-100 URLs), MCP `scrape_many`, CLI `agentcrawl scrape-many URL... [--file urls.txt]`.
  *What this means:* an agent reads N known pages in one call instead of N sequential calls. Every URL goes through the single-scrape path (validation, per-key cache, per-domain politeness, usage), results keep input order, and a bad URL fails its own item, not the batch. The library bounds concurrency overall (`parallelism`) and per host (`per_host_concurrency=2`); the server uses `AGENTCRAWL_SCRAPE_MANY_CONCURRENCY` (default 8) and charges one rate-limit unit per URL.

- **Deterministic CSS-schema extraction** — `AgentCrawl.extract_css()`, `POST /v1/extract_css`, MCP `extract_structured`, CLI `agentcrawl extract-css URL --schema schema.json`.
  *What this means:* write a `baseSelector` + `fields` schema once and extract structured JSON from any number of similar pages with no LLM, no tokens and the same answer every run. Field types `text`/`attribute`/`html`/`regex`/`nested`/`list`, transforms `strip`/`lower`/`upper`/`number`/`url`, `multiple`, `default`. Stdlib only (no new dependency), with a documented selector subset; unsupported selector syntax and malformed schemas are rejected up front (`422` on the API) instead of silently matching nothing.

- **Query-aware budgets (BM25)** — `scrape(..., query=...)` (API/MCP/CLI `query`), and the extraction prompt is now the query for LLM chunk selection.
  *What this means:* when a page is larger than the budget, the truncation fix below reports the cut honestly but would still keep the *head* of the page. With a query, the budget goes to the passages that match it, in document order, with their section headings. If the query matches nothing, the head is kept exactly as before. New metadata: `markdown_selection` (`bm25`/`head`), `markdown_blocks_kept/total`, `chunking.chunk_selection`. Disable with `relevance_chunking=false`.

- **CSV/TSV ingestion** — local `.csv`/`.tsv` files and URLs served as `text/csv` become Markdown tables, with `row_count`, `column_count`, `columns`, `csv_delimiter` (sniffed) and `csv_rows_omitted` (render cap 5 000 rows, reported, never silent).

- **`benchmarks/compare.py`** — reproducible, offline comparison lane over the committed quality fixtures: text recall, style-neutral structure recall, fence-language recall, noise leakage, tokens, latency. Adapters for AgentCrawl, an html2text baseline, trafilatura and Crawl4AI (raw and `fit_markdown`) run when installed; missing tools are reported as skipped. It prints tool versions, commit and environment. The fixtures were written by this project, so results are a regression signal, not a neutral market claim (see docs/QUALITY_BENCHMARKS.md).

- `AGENTCRAWL_LOCAL_FILES_ROOT` env var (optional local-file jail for the API server).
- `max_response_bytes` config option (default 10 MB) capping a single fetched body.
- `AGENTCRAWL_DASHBOARD_PUBLIC` env var (opt out of dashboard authentication on a trusted host).
- `SQLiteStore.job_owner_key(job_id)` and `list_crawl_failures(owner_key=...)` for owner-scoped authorization without exposing the fingerprint in responses.
- Regression tests: lease re-entrancy, restart-recovery scope, charset decoding, `Retry-After` clamping, sitemap depth/budget caps, private-target sitemap rejection, guarded-urlopen SSRF refusal, local-file jail traversal, per-key job scoping (including the global failure listing), dashboard auth plus its opt-out, config-override rejection, honest 403 without the browser extra, non-retry of policy denials, retry of transient faults, Playwright resource cleanup, `doctor` distribution lookup, the `pyproject.toml` version fallback, bounded response reads (chunked, capped, and terminating for readers without `amt`), the local-document size ceiling, and bounded per-domain server state.
- **The owner-key requirement is documented.** `AGENTCRAWL_OWNER_API_KEYS` elevates a key that must already be accepted by `AGENTCRAWL_API_KEYS` — authentication runs first — so a key listed only as an owner was refused with `403` and nothing said why. `.env.example` and `docs/OPERATIONS.md` now state it (and describe the per-key cache scoping).
- **MCP local mode reads the same configuration the CLI does.** `AGENTCRAWL_AUDIT`, `AGENTCRAWL_AIRGAP` / `AGENTCRAWL_AIRGAP_ALLOWLIST`, `AGENTCRAWL_ALLOW_PRIVATE_NETWORK`, `AGENTCRAWL_RESPECT_ROBOTS_TXT`, `AGENTCRAWL_BROWSER_FALLBACK`, and `AGENTCRAWL_TIMEOUT_MS` are now honoured by the MCP engine, which previously read only `AGENTCRAWL_FETCHER` — so `airgap` and `audit`, Community's headline guarantees, were unreachable from an agent. `crawl_site`'s `wait` is documented as server-mode only (the local engine always runs inline).
- **Token-efficiency metrics ride on browser-retried documents too.** A page rescued through the browser fallback now reports `estimated_tokens`, `raw_html_bytes`, and `raw_html_tokens_estimate` alongside the truncation fields, matching the contract that every document carries them.
- **Test hermeticity (internal) — the suite no longer needs real DNS.**
  *What this means:* `validate_remote_url` resolves every hostname with `socket.getaddrinfo` before fetching, so the fetcher/audit tests silently depended on documentation hosts (e.g. `example.com`) resolving. In CI or containers with filtered DNS those 8 tests failed even though the code was correct. A conftest autouse fixture now stubs DNS for documentation hosts only (unknown hosts still fall through to the real resolver and keep the honest `Unable to resolve target host` failure path). The suite drops from ~139s to ~12s and passes in offline sandboxes. No production code changed.
- Regression tests for the deep sweep: silent markdown/chunk truncation reporting (including the browser-retry path and its token metrics), audit-vs-airgap semantics with a real cross-host redirect (audit alone must not enforce, the airgap must), one-record-per-request and blocked-hop accounting, search under airgap/audit, `config` type/range rejection with `400`, regex validation and the total `url_allowed`, `max_urls` bounds, per-key cache/aggregate scoping plus the legacy-database migration, `loader_kwargs` timeout units, line-oriented blocked-page detection, and error-classification ordering.

### Known issues

- `SQLiteStore.prepare_restart_recovery()` requeues **every** `running` job when a server process starts. With `uvicorn --workers > 1`, a worker that restarts can requeue — and re-run — a job another live worker is still processing. A proper fix needs a per-job heartbeat and a migration; tracked for the next release. Until then run one server process and scale with `AGENTCRAWL_WORKERS` threads.
- The per-domain concurrency slot is still keyed by the requested host, not re-keyed after a cross-domain redirect.

### Verification

- `pytest`: 373 passed (292 after the first three audit passes; +81 regression and feature cases in 12 modules for the 2026-09-23 pass).
- `ruff check` and `ruff format --check`: clean. `benchmarks.quality_report`: 20/20 fixtures, average 100.0.
- Every finding was reproduced against a local HTTP server or a direct call before the fix and re-run after it; the evidence lives in the `tests/test_*.py` modules. The browser guard was additionally exercised against real Chromium (Playwright 1.61) with redirect chains, a hostile redirect, an iframe, an image redirect, XHR and a WebSocket: 5 leaked requests without the guard, 0 with it.

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
