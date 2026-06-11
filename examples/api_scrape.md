# HTTP API scrape example

Use this when AgentCrawl is running as a self-hosted API.

## Start the API locally

```bash
export AGENTCRAWL_API_KEYS="example-development-key"
pip install "agentcrawl-ai[server]"
agentcrawl serve --host 127.0.0.1 --port 8000
```

## Health check

```bash
curl http://127.0.0.1:8000/health
```

## Scrape through the API

```bash
curl http://127.0.0.1:8000/v1/scrape \
  -H "authorization: Bearer example-development-key" \
  -H "content-type: application/json" \
  -d '{"url":"https://example.com","formats":["markdown","links","metadata"]}'
```

Expected result: JSON containing Markdown with `Example Domain`.

Do not expose the API publicly without TLS, authentication, request limits, and network controls.
