from __future__ import annotations

try:
    from .provider import AgentCrawlWebProvider
except ImportError:  # Direct test execution outside a package context.
    from provider import AgentCrawlWebProvider


def register(ctx) -> None:
    ctx.register_web_search_provider(AgentCrawlWebProvider())
