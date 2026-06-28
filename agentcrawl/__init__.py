"""AgentCrawl public API."""

from importlib.metadata import PackageNotFoundError, version

from .client import AgentCrawler
from .config import CrawlConfig
from .graphs import ExtractionGraph, MultiExtractionGraph, SearchGraph
from .crawler import AgentCrawl
from .models import CrawlResult, CrawlRun, MapResult, ScrapeDocument, SearchResult
from .remote_client import AgentCrawlClient

try:
    __version__ = version("agentcrawl-ai")
except PackageNotFoundError:
    __version__ = "0.1.2"

__all__ = [
    "AgentCrawlClient",
    "AgentCrawler",
    "CrawlConfig",
    "CrawlRun",
    "CrawlResult",
    "AgentCrawl",
    "MapResult",
    "ScrapeDocument",
    "SearchGraph",
    "SearchResult",
    "ExtractionGraph",
    "MultiExtractionGraph",
]
