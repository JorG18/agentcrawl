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
        # Imported here: errors.py imports this module.
        from .errors import redact_credentials

        super().__init__(redact_credentials(message))
        self.error_type = error_type
        self.status_code = status_code
