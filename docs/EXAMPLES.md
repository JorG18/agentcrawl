# Examples And Integration Cookbook

AgentCrawl works as a local Python library, CLI tool, HTTP API, Docker service, or MCP server. Start with the smallest entrypoint that fits your agent workflow.

## Copy-paste examples

| Example | Use when |
| --- | --- |
| [CLI scrape](../examples/cli_scrape.md) | You have one URL and want Markdown from the terminal. |
| [Python scrape](../examples/python_scrape.py) | You want to call AgentCrawl from Python code. |
| [HTTP API scrape](../examples/api_scrape.md) | You run AgentCrawl as a self-hosted API. |
| [Docker](../examples/docker.md) | You want the API through the published GHCR image. |
| [MCP](../examples/mcp.md) | You want an agent client to use AgentCrawl tools. |
| [Browser-rendered pages](../examples/browser_rendered.md) | A page needs JavaScript rendering. |

## Quick start by interface

### CLI

```bash
pip install agentcrawl
agentcrawl scrape https://example.com
```

For deterministic local testing from a repository checkout:

```bash
AGENTCRAWL_ALLOW_LOCAL_FILES=true agentcrawl scrape tests/fixtures/quality/documentation.html
```

### Python

```python
from agentcrawl import AgentCrawl, ScrapeDocument

crawler = AgentCrawl({"fetcher": "http"})
document = crawler.scrape("https://example.com")
assert isinstance(document, ScrapeDocument)
print(document.markdown)
```

### HTTP API

```bash
export AGENTCRAWL_API_KEYS="exampl...-key"
pip install "agentcrawl[server]"
agentcrawl serve --host 127.0.0.1 --port 8000
```

```bash
curl http://127.0.0.1:8000/v1/scrape \
  -H "authorization: Bearer exampl...key" \
  -H "content-type: application/json" \
  -d '{"url":"https://example.com","formats":["markdown","links","metadata"]}'
```

### Docker

```bash
docker run --rm -p 8000:8000 \
  -e AGENTCRAWL_API_KEYS="exampl...-key" \
  ghcr.io/jorg18/agentcrawl:latest
```

### MCP

```bash
pip install "agentcrawl[mcp]"
agentcrawl doctor
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

## Durable crawl job

Use the remote API for crawls designed to survive retries and remain inspectable later.

```bash
agentcrawl --remote crawl https://example.com --max-pages 25 --max-depth 2
agentcrawl --remote job JOB_ID --offset 0 --limit 100
```

HTTP clients can use an idempotency key to avoid duplicate jobs during retries:

```bash
curl http://127.0.0.1:8000/v1/crawl \
  -H "authorization: Bearer exampl...key" \
  -H "content-type: application/json" \
  -H "Idempotency-Key: docs-crawl-2026-06-06" \
  -d '{"url":"https://example.com","max_pages":25,"max_depth":2}'
```

## Local documents

```bash
agentcrawl scrape ./notes.md
agentcrawl scrape ./data.json
agentcrawl scrape ./feed.xml
pip install "agentcrawl[docs]"
agentcrawl scrape ./report.pdf
```

Supported Community inputs: HTML, Markdown, text, JSON, XML/RSS/Atom, and local PDFs with the `docs` extra.

## Extraction quality notes

AgentCrawl's Community extractor protects the output shape agents care about most:

- main content is selected from semantic containers or text-rich fallback blocks;
- noisy chrome is removed, including nav/footer/header/sidebar/cookie/share/related blocks and hidden or unsafe tags;
- tables stay as readable Markdown tables;
- code blocks stay fenced and preserve common language classes;
- provenance metadata records source/final URLs, selected content hints, score, content hash, and structure metrics.

Run the fixture report:

```bash
python benchmarks/quality_report.py
```
