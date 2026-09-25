from __future__ import annotations

import os

from dataclasses import dataclass, field
import warnings
from typing import Any


@dataclass(slots=True)
class CrawlConfig:
    """Single configuration object accepted as a dict by AgentCrawler."""

    llm: Any | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_kwargs: dict[str, Any] = field(default_factory=dict)
    llm_temperature: float = 0.0

    fetcher: str = "http"
    browser_backend: str = "playwright"
    camofox_base_url: str = "http://127.0.0.1:9377"
    camofox_access_key: str | None = None
    camofox_user_id: str = "agentcrawl"
    headless: bool = True
    timeout_ms: int = 30_000
    http_retries: int = 2
    http_retry_delay: float = 1.0
    browser_fallback: bool = True
    browser_fallback_statuses: tuple[int, ...] = (403, 429, 500, 502, 503, 504)
    domain_min_delay: float = 0.0
    wait_until: str = "domcontentloaded"
    user_agent: str | None = "Mozilla/5.0 (compatible; AgentCrawl/0.1; +https://agentcrawl.local)"
    proxy: str | None = None
    geoip: bool = False
    humanize: bool = False
    network_idle: bool = True
    browser_wait_for_selector: str | None = None
    browser_wait_ms: int = 0
    browser_block_resources: tuple[str, ...] = field(default_factory=tuple)
    browser_init_script: str | None = None
    allow_private_network: bool = False
    # Local-file sources (a path instead of a URL). On for the library and the
    # CLI, where a human typed the path; the MCP turns it off (see
    # ``config_from_env``) because an agent's input can come from a hostile
    # page. ``local_files_root`` confines reads to one directory tree.
    allow_local_files: bool = True
    local_files_root: str | None = None
    airgap: bool = False
    allowlist_domains: tuple[str, ...] = field(default_factory=tuple)
    audit: bool = False
    # Hard ceiling on a single fetched body. ``urlopen`` offers no size
    # guarantee — a server may omit Content-Length or lie about it and stream
    # indefinitely — and extraction only keeps ``max_input_chars`` of it, so an
    # unbounded read let one hostile or oversized page exhaust memory before
    # the surplus could be discarded.
    max_response_bytes: int = 10_000_000
    output_format: str = "json"
    chunk_size: int = 8_000
    max_chunks: int = 8
    include_links: bool = True
    include_images: bool = False
    max_input_chars: int = 64_000
    # With a query (the extraction prompt, or ``scrape(query=...)``), spend the
    # chunk/char budget on the most relevant passages (BM25) instead of the
    # head of the document. No effect when the document fits the budget.
    relevance_chunking: bool = True

    reasoning: bool = False
    auto_reattempt: bool = True
    max_attempts: int = 2
    reattempt_condition: str = "empty or validation_error"

    parallelism: int = 4
    search_engine: str = "none"
    search_limit: int = 5
    serper_api_key: str | None = None

    crawl_depth: int = 1
    crawl_max_pages: int = 25
    crawl_same_domain: bool = True
    crawl_url_retries: int = 2
    crawl_retry_delay: float = 2.0
    crawl_retry_max_delay: float = 60.0
    crawl_retry_error_types: tuple[str, ...] = (
        "rate_limited",
        "timeout",
        "network_error",
        "browser_error",
        "fetch_error",
    )
    respect_robots_txt: bool = True
    crawl_include: list[str] = field(default_factory=list)
    crawl_exclude: list[str] = field(default_factory=list)

    verbose: bool = False

    def __post_init__(self) -> None:
        # Catch the silent footgun where users do ``CrawlConfig({"airgap": True})``
        # expecting to pass a config dict. Because ``llm`` is the first positional
        # field with a default, Python assigns the dict to ``self.llm`` and the
        # rest of the fields stay at defaults. The intended API is
        # ``CrawlConfig.from_dict(...)`` or simply ``AgentCrawl({"airgap": True})``
        # which routes through ``from_dict``.
        if isinstance(self.llm, dict):
            warnings.warn(
                "CrawlConfig received a dict in its first positional 'llm' field. "
                "Use CrawlConfig.from_dict(...) or wrap the dict in AgentCrawl(...) so "
                "the other config keys actually apply.",
                UserWarning,
                stacklevel=2,
            )

    @classmethod
    def from_dict(cls, config: dict[str, Any] | "CrawlConfig" | None) -> "CrawlConfig":
        if isinstance(config, cls):
            return config
        if config is None:
            return cls()
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        unknown = sorted(set(config) - allowed)
        if unknown:
            raise ValueError(f"Unknown config keys: {', '.join(unknown)}")
        normalized = {key: _validate_config_value(str(key), value) for key, value in config.items()}
        if "fetcher" in normalized:
            normalized["fetcher"] = _validate_fetcher(normalized["fetcher"])
        if "browser_backend" in normalized:
            backend = normalized["browser_backend"]
            if backend not in BROWSER_BACKENDS:
                raise ValueError(
                    f"browser_backend must be one of {', '.join(BROWSER_BACKENDS)}, got {backend!r}"
                )
        # Tuple normalization for fields that use tuples by convention.
        for tuple_field in ("allowlist_domains", "browser_fallback_statuses"):
            if tuple_field in normalized and not isinstance(normalized[tuple_field], tuple):
                normalized[tuple_field] = tuple(normalized[tuple_field])
        return cls(**normalized)


BROWSER_BACKENDS = ("playwright", "camofox")
FETCHERS = ("http", *BROWSER_BACKENDS)
# "browser" is what people type; the engine name is "playwright". An unknown
# name used to reach the fetcher and come back as "Unknown fetcher: browser",
# which the substring classifier then labelled ``browser_error``.
_FETCHER_ALIASES = {"browser": "playwright"}


def _validate_fetcher(value: str) -> str:
    name = _FETCHER_ALIASES.get(value.strip().lower(), value.strip().lower())
    if name not in FETCHERS:
        raise ValueError(
            f"fetcher must be one of {', '.join(FETCHERS)} (or 'browser'), got {value!r}"
        )
    return name


# ---------------------------------------------------------------------------
# Value validation
# ---------------------------------------------------------------------------
# ``from_dict`` used to accept anything the dataclass did not reject, and the
# consequences were felt much later: a string where a number belonged raised a
# TypeError deep inside the fetcher (reported to the caller as a *network*
# ``fetch_error``), ``max_input_chars="x"`` became an HTTP 500, and a string
# like ``"false"`` for a boolean field is truthy — so the caller got the
# opposite of what they asked for with no warning at all.
#
# The table lives here so the library, the CLI and the API all share one
# contract, and the API can turn the ``ValueError`` into a 400.
_BOOL_FIELDS = frozenset(
    {
        "headless",
        "browser_fallback",
        "include_links",
        "include_images",
        "crawl_same_domain",
        "respect_robots_txt",
        "airgap",
        "audit",
        "allow_private_network",
        "allow_local_files",
        "geoip",
        "humanize",
        "network_idle",
        "reasoning",
        "auto_reattempt",
        "verbose",
        "relevance_chunking",
    }
)

_STR_FIELDS = frozenset(
    {
        "fetcher",
        "browser_backend",
        "camofox_base_url",
        "camofox_user_id",
        "output_format",
        "reattempt_condition",
        "search_engine",
    }
)

_STR_OR_NONE_FIELDS = frozenset(
    {
        "user_agent",
        "llm_provider",
        "llm_model",
        "camofox_access_key",
        "proxy",
        "browser_wait_for_selector",
        "browser_init_script",
        "wait_until",
        "serper_api_key",
        "local_files_root",
    }
)

# Inclusive (minimum, maximum). Deliberately generous: this is meant to catch
# nonsense (a string, a negative budget), not to police legitimate tuning.
_INT_RANGES: dict[str, tuple[int, int]] = {
    "timeout_ms": (1, 3_600_000),
    "http_retries": (0, 20),
    "max_input_chars": (100, 50_000_000),
    "max_response_bytes": (1_024, 1_000_000_000),
    "chunk_size": (1, 1_000_000),
    "max_chunks": (1, 100_000),
    "parallelism": (1, 256),
    "search_limit": (1, 100),
    "crawl_depth": (0, 1_000),
    "crawl_max_pages": (1, 1_000_000),
    "crawl_url_retries": (0, 20),
    "browser_wait_ms": (0, 3_600_000),
    "max_attempts": (1, 100),
}

_FLOAT_RANGES: dict[str, tuple[float, float]] = {
    "http_retry_delay": (0.0, 86_400.0),
    "crawl_retry_delay": (0.0, 86_400.0),
    "crawl_retry_max_delay": (0.0, 86_400.0),
    "domain_min_delay": (0.0, 86_400.0),
    "llm_temperature": (0.0, 2.0),
}

# Status codes are the one sequence that arrives as a bare scalar often enough
# to matter: ``"403"`` used to be iterated into ``('4', '0', '3')``.
_INT_SEQUENCE_FIELDS = frozenset({"browser_fallback_statuses"})
_STR_SEQUENCE_FIELDS = frozenset(
    {
        "allowlist_domains",
        "browser_block_resources",
        "crawl_include",
        "crawl_exclude",
        "crawl_retry_error_types",
    }
)


def _validate_config_value(key: str, value: Any) -> Any:
    """Validate and normalize one config value, raising ``ValueError`` if wrong."""
    if key in _BOOL_FIELDS:
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be true or false, got {type(value).__name__} ({value!r})")
        return value
    if key in _INT_RANGES:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} must be an integer, got {type(value).__name__} ({value!r})")
        low, high = _INT_RANGES[key]
        if not low <= value <= high:
            raise ValueError(f"{key} must be between {low} and {high}, got {value}")
        return value
    if key in _FLOAT_RANGES:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be a number, got {type(value).__name__} ({value!r})")
        low, high = _FLOAT_RANGES[key]
        numeric = float(value)
        if not low <= numeric <= high:
            raise ValueError(f"{key} must be between {low} and {high}, got {value}")
        return numeric
    if key in _STR_FIELDS:
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string, got {type(value).__name__} ({value!r})")
        return value
    if key in _STR_OR_NONE_FIELDS:
        if value is not None and not isinstance(value, str):
            raise ValueError(
                f"{key} must be a string or null, got {type(value).__name__} ({value!r})"
            )
        return value
    if key in _INT_SEQUENCE_FIELDS:
        return _validate_int_sequence(key, value)
    if key in _STR_SEQUENCE_FIELDS:
        return _validate_str_sequence(key, value)
    return value


def _validate_int_sequence(key: str, value: Any) -> tuple[int, ...]:
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a list of HTTP status codes, got a boolean")
    if isinstance(value, int):
        items: list[Any] = [value]
    elif isinstance(value, str):
        raise ValueError(
            f"{key} must be a list of HTTP status codes, got the string {value!r}. "
            f"Write [{value}] instead of {value!r}."
        )
    elif isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    else:
        raise ValueError(f"{key} must be a list of HTTP status codes, got {type(value).__name__}")
    checked: list[int] = []
    for item in items:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError(
                f"{key} entries must be integers, got {type(item).__name__} ({item!r})"
            )
        if not 100 <= item <= 599:
            raise ValueError(f"{key} entries must be HTTP status codes (100-599), got {item}")
        checked.append(item)
    return tuple(checked)


def _validate_str_sequence(key: str, value: Any) -> list[str] | tuple[str, ...]:
    if isinstance(value, str):
        raise ValueError(f"{key} must be a list of strings, got the single string {value!r}")
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError(f"{key} must be a list of strings, got {type(value).__name__}")
    checked: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{key} entries must be strings, got {type(item).__name__} ({item!r})")
        checked.append(item)
    # Preserve the container kind so a list stays a list (callers append to it)
    # while the tuple-typed fields keep their convention.
    return checked if isinstance(value, list) else tuple(checked)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def local_files_root_from_env() -> str | None:
    """``AGENTCRAWL_LOCAL_FILES_ROOT`` resolved to a real path, or ``None``."""
    raw = os.getenv("AGENTCRAWL_LOCAL_FILES_ROOT", "").strip()
    return os.path.realpath(os.path.expanduser(raw)) if raw else None


def config_from_env(*, allow_local_files_default: bool = False) -> dict[str, Any]:
    """Engine config from the documented ``AGENTCRAWL_*`` variables.

    Local-file sources are refused unless ``AGENTCRAWL_ALLOW_LOCAL_FILES`` is
    set, because the MCP (an agent-driven entrance) uses this mapping. The CLI
    passes ``allow_local_files_default=True``: there a human typed the path.
    ``AGENTCRAWL_LOCAL_FILES_ROOT`` confines reads either way. Both names are
    shared with the API server.

    One mapping for every local entrance (MCP local mode, CLI local mode,
    ``doctor``). The CLI used to read only ``AGENTCRAWL_FETCHER``, so
    ``AGENTCRAWL_ALLOW_PRIVATE_NETWORK=true agentcrawl scrape http://127.0.0.1:…``
    still refused the target while the MCP server honoured the same variable.
    """
    from .airgap import airgap_from_env

    airgap, allowlist = airgap_from_env()
    config: dict[str, Any] = {
        "fetcher": os.getenv("AGENTCRAWL_FETCHER", "http"),
        "airgap": airgap,
        "allowlist_domains": list(allowlist),
        "audit": _env_flag("AGENTCRAWL_AUDIT", False),
        "allow_private_network": _env_flag("AGENTCRAWL_ALLOW_PRIVATE_NETWORK", False),
        "respect_robots_txt": _env_flag("AGENTCRAWL_RESPECT_ROBOTS_TXT", True),
        "browser_fallback": _env_flag("AGENTCRAWL_BROWSER_FALLBACK", True),
        "allow_local_files": _env_flag("AGENTCRAWL_ALLOW_LOCAL_FILES", allow_local_files_default),
        "local_files_root": local_files_root_from_env(),
    }
    backend = os.getenv("AGENTCRAWL_BROWSER_BACKEND", "").strip()
    if backend:
        config["browser_backend"] = backend
    user_agent = os.getenv("AGENTCRAWL_USER_AGENT", "").strip()
    if user_agent:
        config["user_agent"] = user_agent
    timeout_ms = os.getenv("AGENTCRAWL_TIMEOUT_MS", "").strip()
    if timeout_ms.isdigit():
        config["timeout_ms"] = int(timeout_ms)
    return config
