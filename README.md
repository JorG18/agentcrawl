# AgentCrawl

[![CI](https://github.com/JorG18/agentcrawl/actions/workflows/ci.yml/badge.svg)](https://github.com/JorG18/agentcrawl/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![ClawHub](https://img.shields.io/badge/ClawHub-agentcrawl-darkred)](https://clawhub.ai/JorG18/agentcrawl)

![AgentCrawl README hero](assets/readme-hero.png)

🕷️ **A small self-hosted crawler for AI agents.**

AgentCrawl gives agents a simple way to read normal web pages without pasting raw HTML into chat or routing every URL through a hosted scraper. It turns pages and local documents into Markdown, text, links, metadata, JSON-LD, and crawl results. It runs from the CLI, Python, Docker/API, or as an MCP server.

The project is early, intentionally modest, and being worked on steadily: accessible pages first, clean output, local state, honest failures.

```bash
pip install agentcrawl-ai
agentcrawl scrape https://docs.python.org/3/library/json.html
```

Pages that only render with JavaScript need the browser extra (`[browser]`, then
`python -m playwright install chromium`); `agentcrawl doctor` tells you whether
the browser can start. The browser presents itself as the Chrome it is and waits
out self-clearing checks such as Cloudflare's "Just a moment…", ticking the
Turnstile checkbox when one is still showing, as a person would; the optional
`[stealth]` extra retries a refused page once with Patchright. A page that still
asks for a CAPTCHA comes back as `error_type: "client_challenge"` with the
signals seen and a `next_step`, never as content.

Every change is checked against real public sites (docs, Wikipedia, GitHub,
Hacker News, Django docs, a JavaScript-rendered page) by the
[live smoke workflow](.github/workflows/live-smoke.yml).

## Pick your path 🚀

### Agents: MCP 🤖

```bash
python -m pip install "agentcrawl-ai[mcp]"
agentcrawl doctor
agentcrawl mcp
```

By default the MCP exposes the core tools an agent needs: `scrape_url`, `scrape_many`, `map_site`, `crawl_site` and `extract_structured`, plus `search_web` when a search engine is configured. Set `AGENTCRAWL_MCP_PROFILE=full` for the operator tools (job history, cancellation, failure inspection, retries, usage, cache, change checks). Coding agents should follow [INSTALL_FOR_AGENTS.md](INSTALL_FOR_AGENTS.md).

The MCP only fetches URLs by default: local file paths are refused, because an agent can be steered by the pages it reads. To let it read a docs folder, set `AGENTCRAWL_ALLOW_LOCAL_FILES=true` and `AGENTCRAWL_LOCAL_FILES_ROOT=/path/to/docs`. The Python library and the CLI still read local files by default.

### Agents: skill 🧩

An agent skill (Claude Code, Codex, Cursor, OpenClaw and other clients that read `SKILL.md`) teaches the agent when and how to use AgentCrawl, including reading long pages section by section:

```bash
npx skills add JorG18/agentcrawl
```

The skill lives in [skills/agentcrawl/SKILL.md](skills/agentcrawl/SKILL.md) and is also on [ClawHub](https://clawhub.ai/JorG18/agentcrawl) for OpenClaw. ClawHub publishes every skill under MIT-0; that covers the skill text only, the code stays Apache-2.0.

### Developers: Python + CLI 🧪

```bash
pip install agentcrawl-ai
agentcrawl scrape https://docs.python.org/3/library/json.html
```

```python
from agentcrawl import AgentCrawl

crawler = AgentCrawl({"fetcher": "http"})
document = crawler.scrape("https://docs.python.org/3/library/json.html")

print(document.markdown)
print(document.metadata)
```

### Servers: Docker + API 🐳

```bash
docker run --rm -p 8000:8000 \
  -e AGENTCRAWL_API_KEYS="replace-with-a-long-random-key" \
  ghcr.io/jorg18/agentcrawl:latest

curl http://127.0.0.1:8000/health
# Read-only local dashboard. It follows the API auth setting, so send the key:
# curl -H "authorization: Bearer replace-with-a-long-random-key" \
#   http://127.0.0.1:8000/api/dashboard/summary
# Header-less browser view (single-operator hosts only):
#   -e AGENTCRAWL_DASHBOARD_PUBLIC=true  →  open http://127.0.0.1:8000/dashboard
```

Or with Compose:

```bash
cp .env.example .env
# Replace AGENTCRAWL_API_KEYS and AGENTCRAWL_API_KEY in .env
docker compose up -d
curl http://127.0.0.1:8000/health
```

## Why AgentCrawl? 🕸️

Agents often need web context, but raw HTML is a mess. A useful page can arrive mixed with navigation, cookie text, related links, footer links, scripts, and layout junk.

AgentCrawl is a small local layer for that. Give it a URL, get something an agent can read, and keep the cache, jobs, and failures in your own environment.

What works today:

- 🧹 Known URL in, clean Markdown out: main-content extraction, tables, code blocks, links, metadata, and provenance.
- ⚡ HTTP first: fast default extraction without starting a browser.
- 🧱 Durable crawls: SQLite jobs with checkpoints, pagination, cancellation, events, retries, and failure inspection.
- 📦 Local state: cache, usage, jobs, events, crawl failures, and extracted documents stay with you.
- 📊 Read-only dashboard: generate static HTML from SQLite with `agentcrawl dashboard` or open `/dashboard` on the API server (it follows the API auth setting).
- 🔒 Safer API defaults: bearer auth, `robots.txt` support, SSRF protections, unsafe redirect blocking, and private-network controls.
- 🤖 Agent-facing interfaces: CLI, Python, HTTP API, Docker, and MCP.

## What Community includes 🧰

AgentCrawl Community is the self-hosted trust layer:

| Included | Notes |
| --- | --- |
| CLI | Scrape, crawl, inspect jobs, manage cache, backup, restore. |
| Python library | Local use from scripts and agent runtimes. |
| HTTP API | FastAPI server for self-hosted deployments. |
| MCP | Standards-based stdio MCP server for agent clients. |
| Docker / GHCR | Public image built and smoke-tested by GitHub Actions. |
| Durable crawls | SQLite jobs, events, checkpoints, retries, and failure records. |
| Local dashboard | Read-only static HTML over SQLite via `agentcrawl dashboard` and `/dashboard`; the HTTP view follows the API auth setting. |
| Quality extraction | Markdown, links, metadata, JSON-LD/provenance, tables, code blocks. |
| Web search | `search` in the library, API (`/v1/search`), MCP (`search_web`) and CLI: search, then read the top results with the query as the relevance query. Opt-in with `AGENTCRAWL_SEARCH_ENGINE=duckduckgo` (or `serper` + `SERPER_API_KEY`). |
| llms.txt | `map` reads a site's `/llms.txt` links; `agentcrawl llms-txt URL` generates one from a bounded crawl. |
| Citable chunks | `formats=["chunks"]`: pieces of about `chunk_tokens` (default 400) that keep tables and code whole, with the heading path, a `cite_url` text-fragment link and, with `query`, a BM25 score. |
| Browser actions and screenshots | `browser_actions` (click, type, press, scroll, scroll_to_end, virtual_scroll, wait, wait_for; at most 25 bounded steps) run before the page is read, and `formats=["screenshot"]` returns a full-page PNG. Local Playwright only; a failed step fails the scrape with the step named. |
| Logged-in pages | `agentcrawl login URL --session NAME` opens a browser where you sign in by hand; `--session NAME` (library `browser_session`, MCP `session`) then reads pages with that login. Sessions are stored locally with owner-only permissions. |
| Web components and iframes | In the browser, text inside open Shadow DOM and visible iframes is copied into the page before it is read (`browser_shadow_dom`, `browser_iframes`, both on). |
| Adaptive crawl | `crawl(query=...)` (API/MCP/CLI `--query`) visits the most relevant links first and stops after `stop_after_irrelevant` pages in a row that do not match. |
| Change tracking | Every page carries `markdown_sha256`, `etag` and `last_modified`; `diff()` / `agentcrawl diff URL --previous FILE` / MCP `check_changes` send conditional requests and return a unified diff; `crawl(previous_hashes=...)` marks pages `new`, `changed` or `unchanged`. |
| Batch scraping | `scrape_many` in the library, API (`/v1/scrape_many`), MCP and CLI (`scrape-many`). |
| Framework adapters | `agentcrawl.integrations.langchain.AgentCrawlLoader` and `agentcrawl.integrations.llama_index.AgentCrawlReader` yield pages or citable chunks. A zero-dependency TypeScript client for the HTTP API lives in [`sdk/typescript`](sdk/typescript). |
| Structured extraction without an LLM | CSS schemas (`extract-css`, `/v1/extract_css`, MCP `extract_structured`): deterministic, zero tokens. `generate_css_schema(url, "what to extract")` (MCP `describe=`) has your LLM write the schema once; reuse it for free. |
| Reading long pages in parts | `formats=["outline"]` lists sections with the tokens each returns; `section="s4"` (or heading text) returns one; `max_tokens` caps the output. The MCP keeps a page for 10 minutes, so outline then sections is one download. |
| Firecrawl-compatible API | `/v2/scrape`, `/v2/map`, `/v2/search`, `/v2/crawl` and `/v2/batch/scrape` answer Firecrawl's v2 SDKs: point `api_url` at your server. Options AgentCrawl cannot honour are refused with the reason. |
| Local stealth and proxies | Real Chrome identity, native browser TLS, the `[stealth]` extra (Patchright retry), and `proxy` as a comma-separated list rotated per page. |
| Summaries | `formats=["summary"]` with your own LLM (`AGENTCRAWL_LLM_MODEL`); a failure never fails the page. |
| Query-aware budgets | `query=` keeps the passages that matter (BM25) when a page is larger than the output budget. |
| Browser fallback | Optional local browser (kept open between pages, four at a time) or Camofox, not required for the default image. One time budget per page (`page_budget_ms`, 45 s) covers every step. |
| Lightweight docs | Install, examples, operations, release, quality notes. |

Community is self-hosted: everything above runs on your machine, with your browser, your proxies and your LLM.

## Community boundary 🚧

Community is everything that runs on your own machine: the extraction engine, the local browser with its stealth retry, proxies you bring, your own LLM. It never returns a challenge page as content: what it cannot read is reported with the reason and a `next_step`.

What costs money to operate belongs to AgentCrawl Enhanced, a hosted API (planned): managed residential and mobile proxies, geolocation, a managed browser fleet, CAPTCHA solving, schedules, webhooks, retained datasets, teams and billing.

## Extraction quality 🧹

The Community engine focuses on stable, agent-ready Markdown before benchmark claims:

- selects semantic content from `<main>`, `<article>`, documentation/content containers, or text-rich fallback blocks;
- removes unsafe and noisy page chrome such as scripts, styles, hidden content, nav, footer, cookie banners, sidebars, and related-post blocks;
- preserves Markdown tables with headers and cell values;
- preserves fenced code blocks and language tags from common classes such as `language-python` and `lang-javascript`;
- attaches extraction provenance such as source/final URL, selected content hint, selection score, candidate count, content hash, extraction strategy, JSON-LD/schema fields, Product offer/rating fields, and output size/structure metadata;
- validates extraction quality against checked-in fixtures with a minimum score threshold and Markdown structure checks.

Run the report locally:

```bash
python -m benchmarks.quality_report
```

## HTTP API 🌐

Authentication is enabled by default. Configure at least one API key before exposing the server:

```bash
export AGENTCRAWL_API_KEYS="replace-with-a-long-random-key"
python -m pip install "agentcrawl-ai[server]"
agentcrawl serve --host 0.0.0.0 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Code written for Firecrawl's v2 API works against the same server (the SDK's
default `skipTlsVerification` is accepted; certificates are still verified):

```python
from firecrawl import Firecrawl

app = Firecrawl(api_key="replace-with-a-long-random-key", api_url="http://127.0.0.1:8000")
doc = app.scrape("https://docs.python.org/3/library/json.html", formats=["markdown"])
```

Scrape a URL:

```bash
curl http://127.0.0.1:8000/v1/scrape \
  -H "authorization: Bearer replace-with-a-long-random-key" \
  -H "content-type: application/json" \
  -d '{"url":"https://example.com","formats":["markdown","links","metadata"]}'
```

Main endpoints (everything except `/health` requires the bearer key; the dashboard follows the same setting unless `AGENTCRAWL_DASHBOARD_PUBLIC=true`):

```text
GET    /health                     (unauthenticated)
GET    /dashboard                  (see AGENTCRAWL_DASHBOARD_PUBLIC)
GET    /api/dashboard/summary      (see AGENTCRAWL_DASHBOARD_PUBLIC)
POST   /v1/scrape
POST   /v1/scrape_many
POST   /v1/search
POST   /v1/map
POST   /v1/crawl
GET    /v1/jobs/{job_id}
GET    /v1/jobs/{job_id}/events
DELETE /v1/jobs/{job_id}
GET    /v1/failures
GET    /v1/jobs/{job_id}/failures
POST   /v1/jobs/{job_id}/failures/retry
POST   /v1/extract
POST   /v1/extract_css
GET    /v1/usage
GET    /v1/stats
DELETE /v1/cache
```

Several pages at once, and structured data without an LLM:

```bash
agentcrawl scrape-many https://example.com/a https://example.com/b
AGENTCRAWL_SEARCH_ENGINE=duckduckgo agentcrawl search "fastapi dependency injection" --limit 3
agentcrawl scrape https://example.com/docs/faq --query "refund policy"

cat > products.json <<'JSON'
{"baseSelector": "div.product",
 "fields": [{"name": "title", "selector": "h2"},
            {"name": "price", "selector": ".price", "transform": "number"},
            {"name": "url", "selector": "a", "type": "attribute", "attribute": "href", "transform": "url"}]}
JSON
agentcrawl extract-css https://shop.example.com/catalog --schema products.json
```

Scraping a local dev server (localhost/127.x) is refused by default by the SSRF guard. Allow it on purpose with `--allow-private-network` or `AGENTCRAWL_ALLOW_PRIVATE_NETWORK=true`; the CLI's local mode reads the same `AGENTCRAWL_*` variables as MCP (`--airgap`, `--allowlist`, `--audit`, `--timeout-ms`, `--no-robots`, `--no-browser-fallback` override them). `scrape`, `scrape-many`, `extract-css`, `map` and `crawl` exit 1 when the result has errors.

The CSS schema supports `text`, `attribute`, `html`, `regex`, `nested` and `list` fields; the selector subset is documented in `agentcrawl/css_extract.py`, and anything outside it is rejected instead of silently matching nothing.

OpenAPI docs are available at `/docs` when the server is running.

## Local dashboard 📊

Generate a dependency-free static HTML snapshot from any AgentCrawl SQLite database:

```bash
agentcrawl dashboard --db agentcrawl.db --output dashboard.html
```

When the API server is running, the same read-only view is available at `/dashboard` (HTML) and `/api/dashboard/summary` (JSON). The dashboard reports job status, crawl queue, open failures, cache domains, and usage units without sending data to any hosted service.

Because that is the same operational data `GET /v1/stats` protects, the HTTP dashboard follows `AGENTCRAWL_AUTH_ENABLED`: with auth on it needs the bearer key, and with auth off (local/dev) it stays open. A browser cannot send a bearer header, so a single-operator host that wants the header-less view sets `AGENTCRAWL_DASHBOARD_PUBLIC=true`. The offline path needs no server at all:

```bash
agentcrawl dashboard --db agentcrawl.db --output dashboard.html
```

## Crawl jobs 🧭

Start an asynchronous crawl:

```bash
agentcrawl --remote crawl https://example.com --max-pages 25 --max-depth 2
```

HTTP clients can attach an idempotency key so retries return the original job instead of starting a duplicate:

```bash
curl http://127.0.0.1:8000/v1/crawl \
  -H "authorization: Bearer replace-with-a-long-random-key" \
  -H "content-type: application/json" \
  -H "Idempotency-Key: docs-crawl-2026-06-06" \
  -d '{"url":"https://example.com","max_pages":25,"max_depth":2}'
```

Running jobs checkpoint their queue, visited URLs, retry attempts, progress, and extracted documents in SQLite. Transient page failures use persisted exponential backoff without occupying a crawl worker. They are reclaimed after a service restart.

Read completed documents page by page:

```bash
agentcrawl --remote job JOB_ID --offset 0 --limit 100
```

Run a local command when a crawl finishes with terminal failures:

```bash
agentcrawl crawl https://example.com \
  --alert-on-failure \
  --cmd 'python notify.py'
```

The command receives JSON on stdin with `source`, `failure_count`, and `failures`.

Inspect or cancel a job:

```bash
agentcrawl --remote job JOB_ID
agentcrawl --remote job-cancel JOB_ID
```

`/v1/stats` reports queue readiness, delayed retries, running and cancelling jobs, crawl failures by status, open retryable failures, and open failures by error type.

## Local documents 📄

Community supports local document ingestion without sending file contents to a hosted parser:

```bash
agentcrawl scrape ./notes.md
agentcrawl scrape ./data.json
agentcrawl scrape ./feed.xml
agentcrawl scrape ./report.docx      # also .xlsx and .pptx, no extra needed
python -m pip install "agentcrawl-ai[docs]"
agentcrawl scrape ./report.pdf
agentcrawl scrape ./scanned.pdf --ocr # needs the Tesseract binary
```

Current document support:

| Input | Support |
| --- | --- |
| HTML | Main-content Markdown extraction. |
| Markdown | Passed through as Markdown. |
| Text | Passed through as plain Markdown text. |
| JSON | Pretty-printed inside a fenced `json` block. |
| XML/RSS/Atom | Preserved inside a fenced `xml` block. |
| CSV/TSV | Rendered as a Markdown table (delimiter sniffed); also for URLs served as `text/csv`. Shape in metadata; rows beyond 5 000 are reported as omitted. |
| DOCX / XLSX / PPTX | Read with the standard library: headings, lists and tables from Word; one Markdown table per sheet; one section per slide in presentation order. Size-checked before unzipping. |
| PDF | Extracted page-by-page to Markdown with the optional `docs` extra. Enforces size/page safety limits and rejects encrypted PDFs. A PDF with no text layer says so in `metadata.warning`; `ocr=true` (`--ocr`, `AGENTCRAWL_OCR`) reads it with Tesseract. |

PDF and Office files fetched from URLs are converted the same way, detected by content type or, for generic binary responses, by file extension.

## Browser rendering

The default package and default Docker image use HTTP extraction. Add browser rendering only when a site needs JavaScript:

```bash
python -m pip install "agentcrawl-ai[browser]"
python -m playwright install chromium
```

Pages behind a login: sign in once in a visible browser, then read as that user.
Passwords and 2FA never pass through AgentCrawl; only the resulting cookies are
saved, under `~/.agentcrawl/sessions` (or `AGENTCRAWL_SESSIONS_DIR`), readable
only by you.

```bash
agentcrawl login https://wiki.example.org --session work
agentcrawl scrape https://wiki.example.org/team/roadmap --session work
agentcrawl sessions            # list; agentcrawl logout --session work deletes
```

Infinite feeds and virtualized lists (only the visible rows exist in the page):

```python
from agentcrawl import AgentCrawl

feed = AgentCrawl(
    {
        "fetcher": "browser",
        "browser_actions": [
            {"type": "scroll_to_end", "max_scrolls": 20},
            # or, for a recycled list: {"type": "virtual_scroll", "selector": "#rows"},
        ],
    }
).scrape("https://example.org/feed")
```

Sites that challenge automated browsers: the `stealth` extra retries a page the
browser got as a challenge or a 403/429 once with Patchright, on the next proxy
if you gave several. Both browsers wait out "Just a moment…" pages and tick a
Cloudflare Turnstile checkbox that is still showing, in your own browser
(`AGENTCRAWL_BROWSER_CHALLENGE_CLICK=false` turns that off). No solver service is
called and no image CAPTCHA is attempted.

```bash
python -m pip install "agentcrawl-ai[stealth]"
python -m patchright install chromium
export AGENTCRAWL_BROWSER_ENGINE=patchright   # optional: use it for every page
```

```python
AgentCrawl({"proxy": "http://user:pass@p1:8080, http://user:pass@p2:8080"})
```

AgentCrawl also supports an optional external Camofox REST backend:

```bash
export AGENTCRAWL_BROWSER_BACKEND=camofox
export AGENTCRAWL_CAMOFOX_URL=http://127.0.0.1:9377
export AGENTCRAWL_CAMOFOX_ACCESS_KEY=replace-if-access-control-is-enabled
```

## Cache ⚡

Disable cache for one scrape or choose a TTL of up to 30 days:

```json
{"url":"https://example.com","cache":false}
```

```json
{"url":"https://example.com","cache_ttl_seconds":3600}
```

Clear all cache entries or filter by domain or exact URL:

```bash
agentcrawl --remote cache-clear
agentcrawl --remote cache-clear --domain example.com
agentcrawl --remote cache-clear --url https://example.com/page
```

## Backups 💾

Use SQLite online backup before deployment or migration:

```bash
agentcrawl backup --db agentcrawl.db --output-dir ./backups
```

Pass `--env-file` to copy a protected environment file into the backup directory without printing secret values. Restore refuses to overwrite an existing database unless `--force` is provided and verifies the backup before copying:

```bash
agentcrawl restore --backup-db ./backups/agentcrawl-YYYYMMDD-HHMMSS.db --db agentcrawl.db --force
```

## Security defaults

The HTTP server rejects local file paths, localhost, private networks, non-HTTP schemes, embedded URL credentials, and redirects to non-global addresses. Local files remain available through the Python library.

Do not expose the API without authentication, TLS, request limits, and network controls. See [SECURITY.md](SECURITY.md) and [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Docs you'll actually use 📚

- [Install for agents](INSTALL_FOR_AGENTS.md): canonical setup flow for coding agents.
- [Docs index](docs/README.md): map of the public documentation set.
- [Examples](docs/EXAMPLES.md): copy-paste workflows for CLI, Python, HTTP, MCP, Docker, and agents.
- [Quality benchmarks](docs/QUALITY_BENCHMARKS.md): how extraction quality is measured and reported.
- [Operations](docs/OPERATIONS.md): deployment, backup, restore, and production checks.
- [Release checklist](docs/RELEASE.md): PyPI/GHCR release validation and smoke tests.
- [Comparison](docs/COMPARISON.md): choose between AgentCrawl, Firecrawl, Crawl4AI, ScrapeGraphAI, Jina Reader, Crawlee, and Stagehand.

## Optional LLM extraction

AgentCrawl Community does not require an LLM for scraping, crawling, API, Docker, or MCP usage. Prompt-driven `extract()` (and `POST /v1/extract`) is optional: install `agentcrawl-ai[llm]` and configure `llm` or `llm_model`, or `agentcrawl-ai[ollama]` with `llm_provider="ollama"` to keep extraction on your machine. Pass a Pydantic model or a JSON Schema object as the schema: the answer is validated against it, and a wrong shape is sent back to the model with the failing path (`$.price: expected number, got string`) instead of being returned. With a model configured (`AGENTCRAWL_LLM_MODEL`, e.g. `anthropic:claude-haiku-4-5`), `generate_css_schema(url, "each product: name, price")` writes a CSS schema once, checked by running it on the page, and `formats=["summary"]` adds a short summary. Built-in web search is disabled by default; keep search in your agent/provider layer unless you explicitly configure a search backend.

### Your model, your cost: pages as JSON for an agent

Your agent can run on a large model while a small one you choose reads the pages. With `formats=["json"]` and a JSON Schema, the page goes to the configured model and only the validated JSON comes back, so the page never enters the agent's context. In MCP, pass `schema` to `scrape_many` (one URL is fine); in the API, `json_options: {"schema": ..., "prompt": ...}`; on `/v2`, Firecrawl's `{"type": "json", "schema": ...}` format.

```bash
# Any LangChain model id; any OpenAI-compatible endpoint works, e.g. OpenRouter:
export AGENTCRAWL_LLM_MODEL=openai:openai/gpt-4o-mini
export OPENAI_API_KEY=<your OpenRouter key>
export OPENAI_BASE_URL=https://openrouter.ai/api/v1
export AGENTCRAWL_LLM_MAX_PAGES=20   # pages one call may send to the model (default 20)
```

The model, its key and endpoint come only from your configuration, never from a request, so a caller cannot switch you to an expensive model or send your key elsewhere. A batch over `AGENTCRAWL_LLM_MAX_PAGES` is refused before any call, and crawls do not take `json` (each page would be a call). With no model configured, `metadata.json_error` says so and the agent reads the Markdown instead.

## Development

```bash
pip install -e ".[server,mcp,llm,dev]"
pytest -q
ruff check agentcrawl tests examples benchmarks
```

## Roadmap

See [ROADMAP.md](ROADMAP.md).

## License

AgentCrawl Community is licensed under Apache License 2.0. Commercial modules and hosted services are separate products and are not included in this repository.
