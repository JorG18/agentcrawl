"""AgentCrawl public API."""

import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from .client import AgentCrawler
from .config import CrawlConfig
from .graphs import ExtractionGraph, MultiExtractionGraph, SearchGraph
from .crawler import AgentCrawl
from .models import CrawlResult, CrawlRun, MapResult, ScrapeDocument, SearchResult
from .remote_client import AgentCrawlClient


def _pyproject_version() -> str:
    """Version declared by the source checkout's ``pyproject.toml``.

    Only consulted when the installed distribution metadata is missing (an
    uninstalled source checkout). Deriving it from the file keeps the
    fallback from drifting — the previous hardcoded literal still said
    ``0.1.2`` several releases later.
    """
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        contents = pyproject.read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', contents)
    return match.group(1) if match else ""


try:
    __version__ = version("agentcrawl-ai")
except PackageNotFoundError:
    __version__ = _pyproject_version() or "0.0.0+unknown"

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
