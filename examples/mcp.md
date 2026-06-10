# MCP example

Use this when an agent client supports Model Context Protocol servers.

## Local MCP mode

Local mode does not require an API server:

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

Expected tools include `scrape_url`, `map_site`, `crawl_site`, job controls, failure inspection, usage, and cache tools.
