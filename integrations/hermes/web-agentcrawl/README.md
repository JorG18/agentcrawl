# Hermes Web Provider

This plugin routes Hermes native `web_extract` calls through a self-hosted AgentCrawl API.
It is extraction-only, so Hermes can keep a separate provider for `web_search`.

## Install

Copy `web-agentcrawl` to `~/.hermes/plugins/web/agentcrawl`, then configure:

```yaml
plugins:
  enabled:
    - web/agentcrawl
web:
  extract_backend: agentcrawl
```

Set these in `~/.hermes/.env`:

```text
AGENTCRAWL_BASE_URL=http://YOUR_AGENTCRAWL_HOST:8000
AGENTCRAWL_API_KEY=YOUR_API_KEY
```

Restart the Hermes gateway after changing the environment.

## Compatibility Note

Some Hermes builds only recognize bundled web providers. Those builds need support for resolving configured third-party providers through the plugin registry before this backend can be selected.
