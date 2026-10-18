# MCP example

Use this when an agent client supports Model Context Protocol servers.

## Local MCP mode

Local mode does not require an API server:

```bash
pip install "agentcrawl-ai[mcp]"
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

## Remote API-backed MCP mode

Use this when the MCP server should call a self-hosted AgentCrawl API:

```json
{
  "mcpServers": {
    "agentcrawl": {
      "command": "agentcrawl",
      "args": ["mcp"],
      "env": {
        "AGENTCRAWL_BASE_URL": "https://agentcrawl.example.com",
        "AGENTCRAWL_API_KEY": "<protected-token>"
      }
    }
  }
}
```

By default (the `core` profile) the tools are `scrape_url`, `scrape_many`, `map_site`, `crawl_site` and `extract_structured`; `search_web` appears when `AGENTCRAWL_SEARCH_ENGINE` or `AGENTCRAWL_BASE_URL` is set, and `get_job` with `AGENTCRAWL_BASE_URL`. Job controls, failure inspection, usage and cache tools need `AGENTCRAWL_MCP_PROFILE=full`.
