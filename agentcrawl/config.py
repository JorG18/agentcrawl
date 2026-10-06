from __future__ import annotations

import os

from dataclasses import dataclass, field
import warnings
from typing import Any


def _package_version() -> str:
    try:
        from importlib.metadata import version

        return version("agentcrawl-ai")
    except Exception:
        return "dev"


# An honest, contactable identity: sites (Wikipedia among them) ask crawlers
# for a real URL, and the old "+https://agentcrawl.local" pointed nowhere.
DEFAULT_USER_AGENT = (
    f"Mozilla/5.0 (compatible; AgentCrawl/{_package_version()}; "
    "+https://github.com/JorG18/agentcrawl)"
)


@dataclass(slots=True)
class CrawlConfig:
    """Single configuration object accepted as a dict by AgentCrawler."""

    llm: Any | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_kwargs: dict[str, Any] = field(default_factory=dict)
    # Sent only when set: current Claude models (Opus 5.5, Sonnet 5.5, Fable)
    # reject a non-default temperature.
    llm_temperature: float | None = None
    # Pages one call may send to the LLM for formats=["json"]: each costs the
    # user money, so a batch over this is refused instead of run.
    llm_max_pages: int = 20

    fetcher: str = "http"
    browser_backend: str = "playwright"
    # "patchright" (the ``stealth`` extra) is Playwright patched against
    # automation detection. With it installed, a page the normal browser
    # gets as a challenge or a 403/429 is retried once with it.
    browser_engine: str = "playwright"
    camofox_base_url: str = "http://127.0.0.1:9377"
    camofox_access_key: str | None = None
    camofox_user_id: str = "agentcrawl"
    headless: bool = True
    timeout_ms: int = 30_000
    # Wall-clock budget for one page, shared by every step (HTTP attempts,
    # waiting for a browser, navigation, interstitial, network idle,
    # actions). The steps' own timeouts used to add up to over a minute.
    # 0 turns it off.
    page_budget_ms: int = 45_000
    # Socket timeout for the plain HTTP fetch when the browser fallback can
    # take over (capped by ``timeout_ms``). A server that has not answered in
    # this time is handed to the browser instead of retried at full timeout.
    http_timeout_ms: int = 15_000
    http_retries: int = 2
    http_retry_delay: float = 1.0
    browser_fallback: bool = True
    # 405/421/444 and certificate failures come from edge proxies and servers
    # that answer scripts differently from browsers (web-sample benchmark).
    browser_fallback_statuses: tuple[int, ...] = (403, 405, 421, 429, 444, 500, 502, 503, 504)
    domain_min_delay: float = 0.0
    wait_until: str = "domcontentloaded"
    user_agent: str | None = DEFAULT_USER_AGENT
    # One proxy, or several separated by commas: the browser takes the next
    # one for every page (and for the stealth retry). The HTTP fetch uses the
    # HTTP(S)_PROXY environment variables.
    proxy: str | None = None
    network_idle: bool = True
    # Longest wait for the network to go quiet after load. Pages with
    # analytics or live feeds never go idle; they are read as rendered.
    network_idle_ms: int = 10_000
    # Longest wait for a self-clearing interstitial ("Just a moment...") to
    # finish in the local browser.
    browser_challenge_wait_ms: int = 15_000
    # During that wait, tick a Cloudflare Turnstile checkbox that is still
    # showing, as a person would. Local browser only; no solver services.
    browser_challenge_click: bool = True
    browser_wait_for_selector: str | None = None
    browser_wait_ms: int = 0
    browser_block_resources: tuple[str, ...] = field(default_factory=tuple)
    browser_init_script: str | None = None
    # Bounded steps (click, scroll, type, ...) run before the page is read; see
    # ``browser_actions.py``. Needs the local Playwright backend.
    browser_actions: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    # Name of a saved login (``agentcrawl login``); see ``sessions.py``. Pages
    # are then read in the local browser with that session's cookies.
    browser_session: str | None = None
    # Copy iframe bodies and open shadow roots into the page before reading it
    # (``browser_dom.py``); without this their text never reaches the Markdown.
    browser_iframes: bool = True
    browser_shadow_dom: bool = True
    # Capture a full-page PNG (the ``screenshot`` output format sets this).
    screenshot: bool = False
    # OCR image-only PDF pages (needs the docs extra plus the Tesseract binary).
    ocr: bool = False
    # Conditional-request validators from a previous fetch (``AgentCrawl.diff``
    # sets them): a 304 answer becomes ``error_type='not_modified'``.
    if_none_match: str | None = None
    if_modified_since: str | None = None
    allow_private_network: bool = False
    # Local-file sources (a path instead of a URL). On for the library and the
    # CLI, where a human typed the path; the MCP turns it off (see
    # ``config_from_env``) because an agent's input can come from a hostile
    # page. ``local_files_root`` confines reads to one directory tree.
    allow_local_files: bool = True
    local_files_root: str | None = None
    airgap: bool = False
    # The browser's requests are checked before they leave, then sent by the
    # browser itself. Strict mode replays each one from Playwright instead,
    # which checks every redirect hop before it is sent but gives sites a
    # non-browser TLS fingerprint that Cloudflare blocks. Always on under
    # ``airgap`` and ``audit``; the HTTP API server turns it on by default.
    browser_strict_network: bool = False
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
    # Size of each item of the ``chunks`` output format (estimated tokens).
    chunk_tokens: int = 400
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
        deprecated = _DEPRECATED_KEYS & set(config)
        if deprecated:
            warnings.warn(
                f"Config keys {', '.join(sorted(deprecated))} have no effect and will be "
                "removed in 0.6; drop them from your config.",
                DeprecationWarning,
                stacklevel=2,
            )
            config = {key: value for key, value in config.items() if key not in deprecated}
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        unknown = sorted(set(config) - allowed)
        if unknown:
            raise ValueError(f"Unknown config keys: {', '.join(unknown)}")
        normalized = {key: _validate_config_value(str(key), value) for key, value in config.items()}
        if "fetcher" in normalized:
            normalized["fetcher"] = _validate_fetcher(normalized["fetcher"])
        if normalized.get("browser_engine", "playwright") not in BROWSER_ENGINES:
            raise ValueError(
                f"browser_engine must be one of {', '.join(BROWSER_ENGINES)}, "
                f"got {normalized['browser_engine']!r}"
            )
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
BROWSER_ENGINES = ("playwright", "patchright")
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
# Accepted and ignored until 0.6 so existing configs keep loading: geoip and
# humanize never did anything, and the reattempt rule is fixed to
# "empty answer or validation error" (it was an expression nobody set).
_DEPRECATED_KEYS = frozenset({"geoip", "humanize", "reattempt_condition"})

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
        "browser_strict_network",
        "allow_local_files",
        "network_idle",
        "screenshot",
        "ocr",
        "reasoning",
        "auto_reattempt",
        "verbose",
        "relevance_chunking",
        "browser_iframes",
        "browser_shadow_dom",
        "browser_challenge_click",
    }
)

_STR_FIELDS = frozenset(
    {
        "fetcher",
        "browser_backend",
        "browser_engine",
        "camofox_base_url",
        "camofox_user_id",
        "output_format",
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
        "if_none_match",
        "if_modified_since",
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
    "chunk_tokens": (50, 8_000),
    "parallelism": (1, 256),
    "search_limit": (1, 100),
    "crawl_depth": (0, 1_000),
    "crawl_max_pages": (1, 1_000_000),
    "crawl_url_retries": (0, 20),
    "browser_wait_ms": (0, 3_600_000),
    "http_timeout_ms": (1, 3_600_000),
    "page_budget_ms": (0, 3_600_000),
    "network_idle_ms": (0, 3_600_000),
    "browser_challenge_wait_ms": (0, 600_000),
    "max_attempts": (1, 100),
    "llm_max_pages": (1, 10_000),
}

_FLOAT_RANGES: dict[str, tuple[float, float]] = {
    "http_retry_delay": (0.0, 86_400.0),
    "crawl_retry_delay": (0.0, 86_400.0),
    "crawl_retry_max_delay": (0.0, 86_400.0),
    "domain_min_delay": (0.0, 86_400.0),
    "llm_temperature": (0.0, 2.0),
}

# The only float that means "unset" as None: llm_temperature is sent to the
# model only when set (current Claude models reject any explicit temperature).
# Every other ranged float (retry delays, ...) is a required number — a None
# must fail validation here, not surface later as a TypeError mid-fetch.
_FLOAT_NONE_OK = frozenset({"llm_temperature"})

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
        if value is None and key in _FLOAT_NONE_OK:
            return None
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
    if key == "browser_session":
        if value is None:
            return None
        from .sessions import validate_session_name

        return validate_session_name(value)
    if key == "browser_actions":
        from .browser_actions import validate_actions

        return validate_actions(value)
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
        "browser_strict_network": _env_flag("AGENTCRAWL_BROWSER_STRICT_NETWORK", False),
        "browser_challenge_click": _env_flag("AGENTCRAWL_BROWSER_CHALLENGE_CLICK", True),
        "respect_robots_txt": _env_flag("AGENTCRAWL_RESPECT_ROBOTS_TXT", True),
        "browser_fallback": _env_flag("AGENTCRAWL_BROWSER_FALLBACK", True),
        "ocr": _env_flag("AGENTCRAWL_OCR", False),
        "allow_local_files": _env_flag("AGENTCRAWL_ALLOW_LOCAL_FILES", allow_local_files_default),
        "local_files_root": local_files_root_from_env(),
    }
    # The user's own model, for schema generation and summaries (LangChain id,
    # e.g. "anthropic:claude-haiku-4-5" with its API key in the environment).
    for variable, key in (
        ("AGENTCRAWL_LLM_MODEL", "llm_model"),
        ("AGENTCRAWL_LLM_PROVIDER", "llm_provider"),
    ):
        value = os.getenv(variable, "").strip()
        if value:
            config[key] = value
    engine = os.getenv("AGENTCRAWL_BROWSER_ENGINE", "").strip()
    if engine:
        config["browser_engine"] = engine
    backend = os.getenv("AGENTCRAWL_BROWSER_BACKEND", "").strip()
    if backend:
        config["browser_backend"] = backend
    browser_session = os.getenv("AGENTCRAWL_BROWSER_SESSION", "").strip()
    if browser_session:
        config["browser_session"] = browser_session
    user_agent = os.getenv("AGENTCRAWL_USER_AGENT", "").strip()
    if user_agent:
        config["user_agent"] = user_agent
    # Web search is opt-in: a query leaves the machine for a third-party engine.
    search_engine = os.getenv("AGENTCRAWL_SEARCH_ENGINE", "").strip().lower()
    if search_engine:
        config["search_engine"] = search_engine
    page_budget_ms = os.getenv("AGENTCRAWL_PAGE_BUDGET_MS", "").strip()
    if page_budget_ms.isdigit():
        config["page_budget_ms"] = int(page_budget_ms)
    llm_max_pages = os.getenv("AGENTCRAWL_LLM_MAX_PAGES", "").strip()
    if llm_max_pages.isdigit():
        config["llm_max_pages"] = int(llm_max_pages)
    timeout_ms = os.getenv("AGENTCRAWL_TIMEOUT_MS", "").strip()
    if timeout_ms.isdigit():
        config["timeout_ms"] = int(timeout_ms)
    return config
