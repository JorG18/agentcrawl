# Changelog

All notable changes to AgentCrawl Community are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

Each entry gives a one-line "what changed" up front, then the engineering detail for anyone who wants to verify the fix landed.

## 0.5.0 - 2026-09-29

AgentCrawl now reads more of the open web than Crawl4AI on a 400-page random sample, from a datacenter network: the browser no longer gives itself away to Cloudflare, stays open between pages and works to one time budget per page. Agents can read long pages in parts, Firecrawl v2 code runs against an AgentCrawl server, and errors say what to try next.

### Added

- **Read long pages in parts.** `formats=["outline"]` lists a page's sections (`id`, heading path, level, and the tokens that section returns, subsections included); `section="s4"` or heading text returns only that section, chosen before the output budget applies; `max_tokens` caps the Markdown returned. `scrape(max_age=...)` reuses this process's fetch of the page, and the MCP does so for 10 minutes by default, so reading the outline and then sections downloads the page once. A section that does not exist fails as `section_not_found` with the list of sections. Library, API (`section`, `max_tokens` on `/v1/scrape`) and MCP `scrape_url`.
- **Firecrawl v2-compatible API.** `/v2/scrape`, `/v2/map`, `/v2/search`, `/v2/crawl` (+ status, cancel) and `/v2/batch/scrape` (+ status, cancel) answer what Firecrawl's SDKs send, in the shape they read; checked with the official `firecrawl-py` 4.45 against a local server. Requests go through the `/v1` handlers, so authentication, SSRF checks, cache, politeness, limits and usage are unchanged. Options AgentCrawl cannot honour (stealth proxies, location, mobile, tag filters, `json` format, webhooks, `ignoreRobotsTxt`) are refused with `400` and the reason, never dropped; `skipTlsVerification`, which the SDK always sends, is accepted and certificates are still verified. Batch scrape is a durable job saved page by page.
  *Legal note:* only the interface follows Firecrawl's public API reference; no Firecrawl code is used and nothing is sent to Firecrawl.
- **Local stealth.** The `stealth` extra (Patchright) retries once a page the browser got as a challenge or a 403/429, within the page budget, and `browser_engine="patchright"` (`AGENTCRAWL_BROWSER_ENGINE`) uses it for every page. `proxy` takes a comma-separated list, rotated per browser page (the stealth retry takes the next one). Nothing is solved: an interactive CAPTCHA still ends as `client_challenge`.
- **`next_step` on every error**: what to try instead of retrying as is (a proxy or saved login for a challenge, `map_site` for a missing page, a larger `page_budget_ms` for a timeout...).
- **LLM-written CSS schemas.** `generate_css_schema(url, "each product: name, price")` has the configured LLM write a schema from the page's markup (scripts and styles removed); it is checked by running it on the page, and an error or an empty result goes back to the model (three attempts). The returned schema then runs with `extract_css` on similar pages at no token cost. MCP `extract_structured` takes `describe` instead of a schema. `AGENTCRAWL_LLM_MODEL` / `AGENTCRAWL_LLM_PROVIDER` configure the model for the MCP and the server.
- **Summaries.** `formats=["summary"]` (library, API, `/v2`) adds a summary by the configured LLM; a failure is noted in `summary_error` and the page is still returned.
- HTTP documents carry `status_code` and `content_type`.

### Changed

- **Browsers stay open between pages.** Each of four worker threads (`AGENTCRAWL_BROWSER_CONCURRENCY`, was 2) keeps a browser open and gives every page a fresh, isolated context; Chromium used to be launched and closed for every page. On the 400-page benchmark the median time of browser-rendered pages fell from 34.8 s to 13.4 s, most of it queueing. On a small machine the parallel browsers compete with HTML parsing for CPU (plain HTTP pages took a median 2.0 s instead of 1.2 s on the benchmark runner); lower `AGENTCRAWL_BROWSER_CONCURRENCY` there.
- **One time budget per page.** `page_budget_ms` (default 45 s, `AGENTCRAWL_PAGE_BUDGET_MS`, API override) is shared by every step: HTTP attempts and retries, waiting for a browser, navigation, the interstitial, network idle, actions and every later browser operation, including the browser retry after a challenge. The steps' own limits used to add up to over a minute (one page took 164 s). A spent budget is a `timeout`.
- **Heading permalinks** (Sphinx and MkDocs "¶") are dropped from headings.

- **The browser no longer tells Cloudflare it is a bot.** Every request the browser made was replayed by the SSRF guard from Playwright's own HTTP client, so sites saw a Chrome user agent over a non-browser TLS fingerprint, and the browser also announced itself as `AgentCrawl/<version>` (or `HeadlessChrome`). Cloudflare and CDNs answered all three with a challenge or a 403, so the browser fallback got the refusal the HTTP fetch got. Now the guard checks each request before it leaves and the browser sends it itself; the browser presents the Chrome version it is, with `navigator.webdriver` unset and an `en-US` locale. Headless runs use the full Chromium instead of `chromium-headless-shell`, which sent `Sec-CH-UA: "HeadlessChrome"` with every request whatever the user agent said (the shell is still used when it is the only one installed). On the benchmark pages Crawl4AI read and AgentCrawl did not (apnews.com, tracker.gg, fsmb.org, princeton.edu events), all four now load. Nothing is solved or clicked: an interactive Turnstile still ends as `client_challenge`. The HTTP fetch keeps its `AgentCrawl/<version>` identity, and a user agent you set is used as is.
  *Detail:* redirect hops of the main navigation are checked once the page has loaded and the page is refused if one was not allowed. `browser_strict_network=true` (`AGENTCRAWL_BROWSER_STRICT_NETWORK`) keeps the old behaviour, which checks every hop before it is sent; it is always on under `airgap` and `audit`, and on by default in the HTTP API server, where remote callers choose the URLs.
- **Bare domains with another name's certificate.** gamepass.com, tbank.ru and many other bare domains serve a certificate that does not match and only redirect to the real site. AgentCrawl now reads just the redirect (status line and `Location`, never the body) and fetches its `https` target on another host with full verification; the document says `redirected_past_invalid_certificate`. A host that serves content under a bad certificate still fails as `tls_error` (Crawl4AI accepts any certificate). Not done under `airgap`.
- **Self-clearing interstitials.** When the browser lands on a page titled "Just a moment…", "Checking your browser" or similar, it waits up to `browser_challenge_wait_ms` (default 15 s) for the page to replace itself. Nothing is solved or bypassed; a page that does not clear is still reported as `client_challenge`, and the crawler no longer runs a second browser attempt on it. `challenge_waited_ms` is in the metadata.
- **Short extractions are rendered.** A page with scripts whose HTML extracts to under 500 characters is opened in the browser (`fallback_reason: thin_extraction`). If the render adds nothing, the HTTP result is kept (`browser_render_no_gain`).
- **Slow servers.** With a browser available, the HTTP request gives up after `http_timeout_ms` (default 15 s) and hands a timeout or TLS failure straight to the browser instead of retrying. Without a browser the old timeout and retries apply.
- **Busy networks.** Waiting for the network to go idle is capped at `network_idle_ms` (default 10 s); a page that never goes idle is kept and marked `network_idle_timeout` instead of failing.
- **Extraction.** Collapsed FAQ and accordion panels (`aria-hidden`) are kept unless they are mostly links. Elements named banner, promo, sidebar, sticky, modal or rail are dropped only when they are mostly links. Page-state classes on `<body>` and utility-CSS tokens no longer make a whole page boilerplate, and an ASP.NET form wrapping the page is read as the page.

### Fixed

- **PyPI publishing.** The release workflow used only trusted publishing, which PyPI refused (`invalid-publisher`: the project does not list this workflow). It now uploads with the `PYPI_API_TOKEN` secret and falls back to trusted publishing without it.
- **Error pages from the browser.** When the browser fallback gets an error status (a CDN's 403 "Access Denied"), the original HTTP error is reported as `blocked` with `browser_fallback_error`, instead of returning the error page as content.
- **Browser queue.** A browser fetch waited only `timeout_ms` for a free browser and failed with "concurrency limit reached" when others were busy. It now waits as long as one browser run can take.
- **Incomplete certificate chains.** A server that sends its certificate without the intermediate (browsers fill it in, Python did not) now works: the intermediate is downloaded from the address in the server's certificate and the chain must still reach a trusted root. Not done under `airgap`.
- **Failed navigations** now say why the request failed instead of Chromium's bare `net::ERR_FAILED`.

### Benchmark

- The hard-to-scrape block of the web sample was always empty: one shared deadline was spent on the first category. Each category now gets its own share of time, Common Crawl lookups retry with backoff and stop once the index is down, and category sitemaps are read first.
- Same 400 pages as the earlier run, local tools rerun (run 36288315800): AgentCrawl returned content on 223 pages (was 211), consensus recall 74.0% (was 65%), median 1.5 s per page. Crawl4AI in the same run: 225 pages, 79.6%, 2.1 s. Firecrawl's earlier result on these pages was 265 pages and 77.4%.
- `--keep-html` stores the fetched HTML with AgentCrawl's results for offline diagnosis. A run reusing an earlier sample reports the earlier results of tools it did not rerun, and says so.

## 0.4.5 - 2026-09-26

Pages behind a login, infinite and virtualized lists, iframes and web components can now be read in the local browser. A neutral 12-page corpus of real sites found several extraction defects, which are fixed here.

### Added

- **Saved logins.** `agentcrawl login URL --session NAME` opens a visible browser; you sign in by hand and the session's cookies and storage are saved under `~/.agentcrawl/sessions` (`AGENTCRAWL_SESSIONS_DIR`) with owner-only permissions. `--session NAME` in the CLI, `browser_session` in the library and `session` in the MCP `scrape_url` tool read pages with it, and refreshed cookies are written back. Sessions are addressed by name, never by path. `agentcrawl sessions` lists them (domains only) and `agentcrawl logout --session NAME` deletes one. The HTTP API does not accept a session per request; an operator can set `AGENTCRAWL_BROWSER_SESSION` for the whole server.
- **Scrolling.** Two browser actions: `scroll_to_end` scrolls until the page stops growing (at most `max_scrolls`, default 20), and `virtual_scroll` scrolls a recycled list one screen at a time and keeps every row it saw, since such lists only ever hold the visible rows. Both report what they did in `browser_actions_log`.
- **Shadow DOM and iframes.** In the browser, open shadow roots are copied into their hosts (slots filled) and visible iframes are inlined before the page is read, with scripts and inline handlers removed. Tracking pixels, ad and captcha frames are skipped; at most 10 frames. `browser_shadow_dom` and `browser_iframes` turn them off; `browser_dom` in the metadata says what was done.
- **Browser CI job** running these features against a local site in a real Chromium.

### Fixed

- **Code block languages.** Languages were matched to fences by counting every `<code>` element, so a single inline code span before the first block moved every language to the wrong fence (on the Rust book, all of them). They are now counted per `<pre>`, and Sphinx (`highlight-python3`) and MDN (`brush: js`) classes are read.
- **Code indentation.** Code inside fences kept html2text's four-space indent and surrounding blank lines.
- **Emphasis spacing.** `**web crawler** , sometimes` is now `**web crawler**, sometimes`.
- **RFC appendices.** Every `appendix-*` section was dropped as if it were the index, losing acknowledgements and authors. Only a section headed "Index" is dropped now.
- **GitHub Docs parameter tables** lost their header row, which is marked screen-reader-only on the page.
- **Main content selection.** A page's own `<main>` / `role="main"` wins over the `<body>` around it when it holds most of the text, so sidebars such as "Report a bug / Show source" on the Python docs no longer leak. "Jump to content" links are removed like "Skip to content".
- **Live smoke test.** PyPI was replaced by the Django docs: PyPI serves a Fastly challenge or the real page depending on the network, so it made the test flaky. The 0.4.0 entry below no longer credits request headers for PyPI content.

### Benchmark

- `benchmarks/corpus/neutral.json` now has reviewed signals for its 12 pages, written from each snapshot's HTML. The benchmark workflow runs on pull requests that touch it, installs Crawl4AI's browser, and uploads the snapshots and every tool's Markdown for review (`compare --dump-dir`, `--include-unreviewed`).
- Text signals are matched on the output with inline Markdown removed, so a tool that keeps links or inline code is not penalised against one that drops them.
- First results, in `docs/QUALITY_BENCHMARKS.md`: AgentCrawl and Crawl4AI both keep every checked sentence; AgentCrawl's output is about 40% smaller than Crawl4AI's `fit_markdown` with similar noise. The signals were written by this project and the same review drove the fixes above, so read it as a first data point, not a ranking.

## 0.4.0 - 2026-09-26

AgentCrawl now reads the real pages it used to get wrong: its own GitHub page and JavaScript-rendered sites, checked on every change against 16 live public sites. Output is about a third smaller on table-heavy pages, the MCP costs agents ~1.5k tokens of context instead of ~4k, and install instructions point at GitHub while PyPI publishing is being set up.

### Fixed

- **Challenge detection no longer throws away real pages.** Any page whose text mentioned "client challenge" or "disable any ad blockers" (this project's own GitHub page, articles about bots) came back empty as `client_challenge`, while HTTP-200 Cloudflare, DataDome, PerimeterX and Fastly interstitials came back as content.
  *Detail:* `agentcrawl/challenge.py` decides by page shape: an interstitial `<title>` ("Just a moment...", "Client Challenge") is enough; otherwise the page must be short and carry challenge wording on a very short page, or two kinds of evidence (title, vendor script, wording). The document lists what fired in `metadata.challenge_signals`.
- **JavaScript-only pages are rendered.** An HTTP 200 that is only a script mount point used to scrape to a menu and a footer. With `browser_fallback` (default) and a browser installed it is rendered once (`metadata.fallback_reason: "javascript_required"`); without a browser the document says `javascript_required` instead of pretending.
- **A refused proxy tunnel is `network_error`**, not `blocked`: "Tunnel connection failed: 403" is the local proxy, not the site.
- **`AgentCrawler` has `scrape`, `map` and `crawl`**, the same as `AgentCrawl`; the two names were easy to mix up.
- **`agentcrawl doctor` checks that the browser can start** and prints the fix (`python -m playwright install chromium`) when the installed Playwright has no matching browser.
- **Install instructions work.** The agent guide installed the browser extra but not `mcp` (so `agentcrawl mcp` failed), the API example installed `browser` instead of `server`, and the verification step expected content from a URL the same guide called a challenge.

### Changed

- **Compact Markdown.** Tables are not padded with spaces; a column that repeats its neighbour or holds no data is dropped; link tooltips, image-only links (`[](url)`), skip links and runs of blank lines are removed; links and images are absolute URLs. On the GitHub repository page the output shrinks from 37k to 25k characters with the same content; offline quality fixtures still score 100%.
- **HTTP transfer.** Requests send `Accept` and `Accept-Encoding: gzip, deflate` and inflate under the same `max_response_bytes` ceiling (a decompression bomb is refused). Pages that declare their charset only in `<meta>` decode correctly, and latin-1 labels decode as windows-1252 like browsers do. (PyPI answers the HTTP fetcher with real pages from some networks and a Fastly challenge from others; AgentCrawl reports the challenge either way.)
- **User-Agent** is `Mozilla/5.0 (compatible; AgentCrawl/<version>; +https://github.com/JorG18/agentcrawl)` instead of `AgentCrawl/0.1` with a `.local` address.
- **Lean MCP by default.** The core profile exposes `scrape_url`, `scrape_many`, `map_site`, `crawl_site` and `extract_structured`, plus `search_web` when a search engine is configured and `get_job` when a server is. `AGENTCRAWL_MCP_PROFILE=full` restores the operator tools (`check_changes`, `job_events`, `cancel_job`, `inspect_failures`, `retry_failures`, `usage`, `cache_stats`, `clear_cache`). Tool descriptions were shortened.
- **Install from GitHub.** README, agent guide and examples install `agentcrawl-ai @ git+https://github.com/JorG18/agentcrawl@v0.4.0`; the PyPI badge is gone for now. The release workflow publishes to PyPI only when the repository variable `PUBLISH_PYPI` is `true`.

### Added

- **Live smoke workflow** (`.github/workflows/live-smoke.yml`, `benchmarks/live_smoke.py`, `benchmarks/corpus/live_smoke.json`): on engine changes, weekly and on demand, scrapes 16 real public pages (docs, RFC, Wikipedia, GitHub, Hacker News, PyPI, a JavaScript-rendered page) with the browser extra and fails when content goes missing. The first run on 0.3.0 passed 14 of 16; 0.4.0 passes 16 of 16.

## 0.3.0 - 2026-09-26

Agents can now go from a question to cited pages (web search, `llms.txt`, citable `chunks`), act on pages before reading them (bounded browser actions, screenshots), crawl by relevance, track what changed, read Office files and scanned PDFs, and get schema-checked LLM extraction. New LangChain and LlamaIndex adapters, a TypeScript client, and releases cut from tags.

### Added

- **Web search on every surface.** `AgentCrawl.search()`, `POST /v1/search`, MCP `search_web`, CLI `agentcrawl search QUERY [--limit N] [--no-scrape]`.
  *What this means:* an agent can go from a question to cited pages without a second tool. Each result carries title, URL and snippet and, by default, the page's Markdown with the query used as the relevance query, so long pages keep their best passages.
  *Detail:* opt-in through `AGENTCRAWL_SEARCH_ENGINE` (`duckduckgo`, or `serper` with `SERPER_API_KEY`); with none set, every surface says how to enable it instead of returning an empty list. On the API the engine is operator-only and result pages go through the `/v1/scrape_many` path (SSRF checks, cache, politeness, metering). Under `airgap`, only allowlisted result hosts are scraped and the rest are listed in `airgap_skipped`.

- **llms.txt.** `map()` (and `map_site`, `/v1/map`) reads the site's `/llms.txt` and adds the pages it lists, reported as `metadata.llms_txt`; `AgentCrawl.llms_txt()` and `agentcrawl llms-txt URL [--output FILE]` generate one from a bounded crawl.
  *Detail:* the file is fetched like robots.txt (guarded, bounded, audited); a missing file, an error or an HTML soft-404 contributes nothing. Generated files list only pages that were read; failed pages go to `errors`.

- **`chunks` output format.** `formats=["chunks"]` on scrape, scrape_many, search and MCP returns pieces of at most `chunk_tokens` estimated tokens (config, default 400, 50-8000; also an API override).
  *Detail:* chunks follow sections and keep whole Markdown blocks (tables and code fences are cut only when one alone exceeds the budget, and then at line boundaries). Each carries `id`, `heading` (path like `Guide > Limits`), `url`, `cite_url` (a `#:~:text=` link to its first words, so no element ids are needed) and `estimated_tokens`; with `query` also a BM25 `score`, keeping document order.

- **Bounded browser actions and screenshots.** `browser_actions` (config, API override, MCP `scrape_url`) runs up to 25 steps before the page is read: `click`, `type`, `press`, `scroll`, `wait`, `wait_for`. `formats=["screenshot"]` returns a full-page PNG (base64) as its own field.
  *Detail:* every step shares the fetch timeout and has its own limits; unknown keys are rejected with the step named. A failing step fails the scrape as `browser_error` naming the step, instead of returning a half-loaded page; `metadata.browser_actions_log` lists what ran. Requesting either switches that fetch to the local Playwright backend; the Camofox backend refuses them with a clear `config_error`.

- **Adaptive crawl.** `crawl(query=..., stop_after_irrelevant=3)` (API, MCP `crawl_site`, CLI `crawl --query`) visits the links most related to the query first and stops after that many irrelevant pages in a row.
  *Detail:* each page gets `metadata.query_relevance` (share of query terms present); links are scored by the terms in their URL plus the relevance of the page they were found on. The run reports `query` and `stopped_early`; the streak survives checkpoints. Without `query` the crawl order is unchanged.

- **Change tracking.** Pages carry `markdown_sha256`, `etag` and `last_modified`. `AgentCrawl.diff(url, previous)`, CLI `agentcrawl diff URL [--previous FILE] [--save FILE]` and MCP `check_changes` send `If-None-Match`/`If-Modified-Since`, treat a 304 as unchanged without downloading, and otherwise return a unified diff with added and removed line counts. `crawl(previous_hashes={url: sha256})` marks each page `new`, `changed` or `unchanged` and counts them in the run metadata.

- **DOCX, XLSX and PPTX to Markdown** with no new dependency (zip plus XML from the standard library): headings, lists and tables from Word, one table per sheet, one section per slide in presentation order. Archives are size-checked before inflating and parts declaring a DOCTYPE are refused.

- **PDF and Office files from URLs** are converted instead of decoded as text, detected by content type or, for generic binary responses, by extension.

- **OCR for scanned PDFs (opt-in).** A PDF with no text layer now says so in `metadata.warning`; `ocr=true` (API override, `--ocr`, `AGENTCRAWL_OCR`) reads those pages through PyMuPDF with Tesseract and lists them in `metadata.ocr_pages`.

- **Schema-checked LLM extraction.** A JSON Schema object (what the API sends) is now validated: types, required fields, enums, bounds, `anyOf`/`oneOf`, local `$ref`. A wrong answer goes back to the model with the failing path. New `ollama` extra for local models.

- **LangChain and LlamaIndex adapters.** `agentcrawl.integrations.langchain.AgentCrawlLoader` and `agentcrawl.integrations.llama_index.AgentCrawlReader` load pages (scrape or crawl), optionally as citable chunks with `heading` and `cite_url` metadata; failed pages are listed in `.errors`, never returned as empty documents.

- **TypeScript client** (`sdk/typescript`): zero dependencies, `fetch`-based, covers scrape, batch, search, map, crawl, jobs and both extraction endpoints; errors throw `AgentCrawlError` with status and body.

- **Quality fixtures** for tabbed docs, a Q&A thread and an infinite-scroll feed (23 fixtures, all at 100).

- **Neutral benchmark corpus.** `benchmarks/corpus/neutral.json` lists 12 accessible public pages; `python -m benchmarks.snapshot` freezes them with SHA-256 hashes and `python -m benchmarks.compare --corpus ...` scores every tool on the same bytes, printing the hashes. Snapshots are never committed; pages without reviewed signals are reported as `unscored`.

### Fixed

- **`/v1/extract` with a schema** always failed validation, because Pydantic cannot adapt a JSON Schema dict. Fixed by the validator above.
- **PDF install hint** named the wrong package (`agentcrawl[docs]` instead of `agentcrawl-ai[docs]`).

### Changed

- **Hidden tab panels are kept.** Inactive `role="tabpanel"` content (the "Linux" and "Windows" tabs of install docs) was dropped as hidden; it is real content and is now extracted.
- **Public benchmark workflow.** `benchmark.yml` (run by hand) snapshots the neutral corpus on a GitHub runner and scores every installed extractor on it and on the fixtures, uploading the results.

- **`examples/graph_extraction.py`** reads a real public page and takes its model from `AGENTCRAWL_LLM_MODEL` instead of a hard-coded one.
- **Dev extra** pins `httpx2` for the Starlette test client, so the test suite runs without deprecation warnings.
- **Releases are automated:** pushing a `v*` tag builds the package, publishes it to PyPI and creates the GitHub Release from this changelog.

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
  *What this means:* if you wrote `CrawlConfig(llm={"provider": "..."})` thinking dict shape worked for Community, you now get a `UserWarning` at construction time. Community expects an import path (e.g. `langchain_openai.ChatOpenAI`). Nothing breaks — you just see the warning, and can switch the shape.

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
