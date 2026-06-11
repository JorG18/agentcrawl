# Browser-rendered page example

Use browser rendering only when HTTP extraction cannot see the content because the page depends on JavaScript.

## Install browser support

```bash
pip install "agentcrawl-ai[browser]"
playwright install chromium
```

## Python

```python
from agentcrawl import AgentCrawl, ScrapeDocument

crawler = AgentCrawl({
    "fetcher": "playwright",
    "headless": True,
    "wait_for_selector": "main",
    "block_resources": ["image", "font", "media"],
})

document = crawler.scrape("https://example.com")
assert isinstance(document, ScrapeDocument)
print(document.markdown)
```

The default Docker image does not include Playwright browsers. Keep browser-heavy deployments separate from the lightweight Community image unless you explicitly need rendering.
