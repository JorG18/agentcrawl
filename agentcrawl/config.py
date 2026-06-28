from __future__ import annotations

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
    airgap: bool = False
    allowlist_domains: tuple[str, ...] = field(default_factory=tuple)
    audit: bool = False
    output_format: str = "json"
    chunk_size: int = 8_000
    max_chunks: int = 8
    include_links: bool = True
    include_images: bool = False
    max_input_chars: int = 64_000

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
        normalized = dict(config)
        # Tuple normalization for fields that use tuples by convention.
        for tuple_field in ("allowlist_domains", "browser_fallback_statuses"):
            if tuple_field in normalized and not isinstance(normalized[tuple_field], tuple):
                normalized[tuple_field] = tuple(normalized[tuple_field])
        return cls(**normalized)
