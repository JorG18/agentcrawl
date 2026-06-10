# CLI scrape example

Use this when you already have a URL and want clean Markdown from the terminal.

## Install

```bash
pip install agentcrawl
```

## Scrape one URL

```bash
agentcrawl scrape https://example.com
```

Expected result: Markdown/text containing `Example Domain`.

## Include specific formats

```bash
agentcrawl scrape https://example.com --format markdown --format links --format metadata
```

## Local files

```bash
agentcrawl scrape ./notes.md
agentcrawl scrape ./data.json
agentcrawl scrape ./feed.xml
```

For PDFs:

```bash
pip install "agentcrawl[docs]"
agentcrawl scrape ./report.pdf
```
