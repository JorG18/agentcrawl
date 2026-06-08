from pydantic import BaseModel

from agentcrawl import AgentCrawler


class PageSummary(BaseModel):
    title: str | None = None
    summary: str
    links: list[str] = []


crawler = AgentCrawler(
    {
        "llm_provider": "openai",
        "llm_model": "gpt-4.1-mini",
        "fetcher": "playwright",
        "headless": True,
    }
)

result = crawler.extract(
    "https://example.com",
    "Summarize the page and collect important links.",
    schema=PageSummary,
)

print(result.answer)
