from agentcrawl import AgentCrawl, ScrapeDocument

crawler = AgentCrawl({"fetcher": "http"})
document = crawler.scrape("tests/fixtures/quality/documentation.html")

assert isinstance(document, ScrapeDocument)
assert "Agent SDK Guide" in document.markdown

print(document.markdown)
