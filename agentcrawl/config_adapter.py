from __future__ import annotations

from typing import Any


def normalize_graph_config(config: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize nested graph configuration for AgentCrawl."""

    if not config:
        return {}

    normalized = dict(config)
    llm = normalized.pop("llm", None)
    if isinstance(llm, dict):
        model = llm.get("model")
        if model:
            provider, _, model_name = str(model).partition("/")
            if model_name:
                normalized["llm_provider"] = _provider_alias(provider)
                normalized["llm_model"] = model_name
            else:
                normalized["llm_model"] = str(model)
        if "temperature" in llm:
            normalized["llm_temperature"] = llm["temperature"]
        llm_kwargs = {
            key: value
            for key, value in llm.items()
            if key not in {"model", "temperature", "format", "model_tokens", "api_key"}
        }
        if llm_kwargs:
            normalized["llm_kwargs"] = llm_kwargs
    elif llm is not None:
        normalized["llm"] = llm

    if "loader_kwargs" in normalized:
        loader_kwargs = normalized.pop("loader_kwargs") or {}
        if "timeout" in loader_kwargs:
            normalized["timeout_ms"] = int(loader_kwargs["timeout"])

    return normalized


def _provider_alias(provider: str) -> str:
    aliases = {
        "ollama": "ollama",
        "openai": "openai",
        "anthropic": "anthropic",
        "azure": "azure_openai",
        "gemini": "google_genai",
        "google": "google_genai",
    }
    return aliases.get(provider, provider)
