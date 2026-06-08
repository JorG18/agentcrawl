class AgentCrawlError(Exception):
    """Base exception for AgentCrawl."""


class FetchError(AgentCrawlError):
    """Raised when a URL or local HTML file cannot be fetched."""


class ExtractionError(AgentCrawlError):
    """Raised when LLM extraction or schema validation fails."""
