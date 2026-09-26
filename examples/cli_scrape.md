# CLI scrape example

Use this when you already have a URL and want clean Markdown from the terminal.

## Install

```bash
pip install "agentcrawl-ai @ git+https://github.com/JorG18/agentcrawl@v0.4.5"
```

## Scrape one URL

```bash
agentcrawl scrape https://pypi.org/project/agentcrawl-ai/
```

Expected result: Markdown/text from the AgentCrawl PyPI project page.

`https://example.com` is documented separately as a Community boundary case because it commonly returns a Cloudflare client challenge.

## Include specific formats

```bash
agentcrawl scrape https://pypi.org/project/agentcrawl-ai/ --format markdown --format links --format metadata
```

## Local files

```bash
agentcrawl scrape ./notes.md
agentcrawl scrape ./data.json
agentcrawl scrape ./feed.xml
```

For PDFs:

```bash
pip install "agentcrawl-ai[docs] @ git+https://github.com/JorG18/agentcrawl@v0.4.5"
agentcrawl scrape ./report.pdf
```
