# Examples And Integration Cookbook

This page lists the examples AgentCrawl should make obvious to new users and agent developers.

## Existing Entrypoints

- Python library: `examples/basic.py`
- LLM-backed graph extraction: `examples/graph_extraction.py`
- CLI: `agentcrawl scrape`, `agentcrawl map`, `agentcrawl crawl`
- HTTP API: `/v1/scrape`, `/v1/map`, `/v1/crawl`, `/v1/extract`
- MCP server: `agentcrawl mcp`
- Docker Compose: `docker compose up --build -d`
- Hermes integration: `integrations/hermes/web-agentcrawl/`

## Quick Scenarios

### Scrape One Known URL

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

### Map A Site Before Crawling

Use mapping to discover URLs before spending crawl budget.

```bash
agentcrawl map https://example.com --max-pages 50
```

### Run A Durable Crawl Job

Use the remote API for crawls that should survive retries and be inspected later.

```bash
agentcrawl --remote crawl https://example.com --max-pages 25 --max-depth 2
agentcrawl --remote job JOB_ID --offset 0 --limit 100
```

### Connect An Agent Through MCP

Local mode does not require an API server:

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

### Use Browser Fallback Only When Needed

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

## Examples To Add Next

These examples should be added as files under `examples/` as the public surface grows:

| Example | Purpose |
| --- | --- |
| `examples/http_api.py` | Minimal authenticated scrape through the HTTP API. |
| `examples/mcp_client.md` | Configure and verify MCP in common agent clients. |
| `examples/crawl_job.py` | Start a crawl, poll status, page through results, inspect failures. |
| `examples/structured_extraction.py` | Extract Pydantic-shaped data from one URL. |
| `examples/browser_fallback.py` | Show HTTP failure followed by browser-rendered success. |
| `examples/docker.md` | Run the API with Docker Compose and verify `/health`. |
| `examples/typescript.md` | Use `fetch` from Node or Bun before a dedicated SDK exists. |

## Adoption Checklist

A new user should be able to verify AgentCrawl without private context:

- install from source or PyPI;
- run `agentcrawl doctor`;
- scrape `https://example.com`;
- run the API with auth;
- connect MCP;
- run a small crawl job;
- inspect usage and cache stats;
- back up and restore SQLite state.

If any step requires private instructions, improve the public docs before expanding the feature set.
