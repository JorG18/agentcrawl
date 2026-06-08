from __future__ import annotations

from typing import Any

from .config import CrawlConfig


def get_llm(config: CrawlConfig) -> Any:
    if config.llm is not None:
        return config.llm
    if not config.llm_model:
        raise ValueError("No LLM configured. Set config['llm'] or config['llm_model'].")

    try:
        from langchain.chat_models import init_chat_model
    except ImportError as exc:
        raise ValueError("LangChain model initialization is unavailable.") from exc

    model = config.llm_model
    if config.llm_provider and ":" not in model:
        model = f"{config.llm_provider}:{model}"
    kwargs = {"temperature": config.llm_temperature, **config.llm_kwargs}
    return init_chat_model(model, **kwargs)


def invoke_llm(llm: Any, prompt: str) -> str:
    if callable(llm) and not hasattr(llm, "invoke"):
        response = llm(prompt)
    elif hasattr(llm, "invoke"):
        response = llm.invoke(prompt)
    else:
        raise ValueError("Configured LLM must be callable or expose .invoke().")

    if isinstance(response, str):
        return response
    if hasattr(response, "content"):
        content = response.content
        if isinstance(content, str):
            return content
        return str(content)
    return str(response)
