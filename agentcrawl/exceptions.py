class AgentCrawlError(Exception):
    """Base exception for AgentCrawl."""


class FetchError(AgentCrawlError):
    """Raised when a URL or local HTML file cannot be fetched.

    ``error_type`` and ``status_code`` are set when the raiser knows them, so
    classification does not have to guess from the message text.
    """

    def __init__(
        self,
        message: str = "",
        *,
        error_type: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.status_code = status_code


class ExtractionError(AgentCrawlError):
    """Raised when LLM extraction or schema validation fails."""
