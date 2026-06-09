# Examples And Integration Cookbook

AgentCrawl works as a local Python library, CLI tool, HTTP API, Docker service, or MCP server. Pick the entrypoint that fits your agent workflow and start with the smallest thing that works.

## Entrypoints 🧩

- Python library: `examples/basic.py`
- LLM-backed graph extraction: `examples/graph_extraction.py`
- CLI: `agentcrawl scrape`, `agentcrawl map`, `agentcrawl crawl`
- HTTP API: `/v1/scrape`, `/v1/map`, `/v1/crawl`, `/v1/extract`
- MCP server: `agentcrawl mcp`
- Docker Compose: `docker compose up --build -d`
- Hermes integration: `integrations/hermes/web-agentcrawl/`

## Scrape One Known URL 🎯

Use this when an agent already has a URL and needs clean Markdown.

```bash
agentcrawl scrape https://example.com
```

Python:

```python
from agentcrawl import AgentCrawl

crawler = AgentCrawl({"fetcher": "http"})
document = crawler.scrape("https://example.com")
print(document.markdown)
```

## Scrape Local Documents 📄

Use this when an agent needs local files converted into Markdown without a hosted parser.

```bash
agentcrawl scrape ./notes.md
agentcrawl scrape ./data.json
agentcrawl scrape ./feed.xml
python -m pip install -e ".[docs]"
agentcrawl scrape ./report.pdf
```

Python:

```python
from agentcrawl import AgentCrawl

document = AgentCrawl({"fetcher": "http"}).scrape("./report.pdf")
print(document.markdown)
print(document.metadata)
```

Supported Community inputs: HTML, Markdown, text, JSON, XML/RSS/Atom, and local PDFs with the `docs` extra.

## Map A Site Before Crawling

Use mapping to discover URLs before spending crawl budget.

```bash
agentcrawl map https://example.com --max-pages 50
```

## Run A Durable Crawl Job 🧭

Use the remote API for crawls designed to survive retries and remain inspectable later.

```bash
agentcrawl --remote crawl https://example.com --max-pages 25 --max-depth 2
agentcrawl --remote job JOB_ID --offset 0 --limit 100
```

HTTP clients can use an idempotency key to avoid duplicate jobs during retries:

```bash
curl http://127.0.0.1:8000/v1/crawl \
  -H "authorization: Bearer $AGENTCRAWL_API_KEY" \
  -H "content-type: application/json" \
  -H "Idempotency-Key: docs-crawl-2026-06-06" \
  -d '{"url":"https://example.com","max_pages":25,"max_depth":2}'
```

## Connect An Agent Through MCP 🤖

Local MCP mode does not require an API server:

```bash
agentcrawl mcp
```

Generic MCP configuration:

```json
{
  "mcpServers": {
    "agentcrawl": {
      "command": "agentcrawl",
      "args": ["mcp"]
    }
  }
}
```

Remote API mode:

```json
{
  "mcpServers": {
    "agentcrawl": {
      "command": "agentcrawl",
      "args": ["mcp"],
      "env": {
        "AGENTCRAWL_BASE_URL": "https://agentcrawl.example.com",
        "AGENTCRAWL_API_KEY": "<secret>"
      }
    }
  }
}
```

## Extraction Quality Notes 🧹

AgentCrawl's Community extractor currently protects the output shape agents care about most:

- main content is selected from semantic containers or text-rich fallback blocks;
- noisy chrome is removed, including nav/footer/header/sidebar/cookie/share/related blocks and hidden or unsafe tags;
- tables stay as readable Markdown tables;
- code blocks stay fenced and preserve common language classes (`language-python`, `lang-js`, etc.).

Small local HTML fixtures are the safest way to verify output without network flakiness:

```python
from agentcrawl import AgentCrawl

html_path = "./fixtures/page.html"
document = AgentCrawl({"fetcher": "http"}).scrape(html_path)
print(document.markdown)
```

## Use Browser Fallback Only When Needed

Start with HTTP extraction. Add browser support only when the target page needs JavaScript rendering.

```bash
python -m pip install -e ".[browser]"
playwright install chromium
```

```python
from agentcrawl import AgentCrawl

crawler = AgentCrawl({"fetcher": "playwright"})
document = crawler.scrape("https://example.com/app")
print(document.markdown)
```

## Run The API With Docker

```bash
cp .env.example .env
# Set AGENTCRAWL_API_KEYS and AGENTCRAWL_API_KEY in .env before exposure.
docker compose up --build -d
curl http://127.0.0.1:8000/health
```

## Verify A New Installation

A complete installation check covers:

- `agentcrawl doctor`;
- `agentcrawl scrape https://example.com`;
- authenticated `/health` or `/v1/stats` when the API is running;
- MCP tool discovery;
- a small crawl job;
- usage and cache stats;
- backup and restore commands for persistent deployments.

## Additional Examples On The Roadmap

The public roadmap includes more dedicated examples for:

| Example | Purpose |
| --- | --- |
| `examples/http_api.py` | Minimal authenticated scrape through the HTTP API. |
| `examples/mcp_client.md` | Configure and verify MCP in common agent clients. |
| `examples/crawl_job.py` | Start a crawl, poll status, page through results, inspect failures. |
| `examples/structured_extraction.py` | Extract Pydantic-shaped data from one URL. |
| `examples/browser_fallback.py` | Show HTTP failure followed by browser-rendered success. |
| `examples/docker.md` | Run the API with Docker Compose and verify `/health`. |
| `examples/typescript.md` | Use `fetch` from Node or Bun before a dedicated SDK exists. |
