"""LangChain loader and LlamaIndex reader over local pages."""

from __future__ import annotations

import importlib
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

PAGE = """<html><head><title>Refund Policy</title></head><body><main>
<h1>Refund Policy</h1><p>Refunds are issued within five business days of a request.</p>
<h2>Exceptions</h2><p>Gift cards cannot be refunded once they have been redeemed.</p>
</main></body></html>"""


@dataclass
class FakeDocument:
    page_content: str = ""
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class FakeBase:
    def load(self):
        return list(self.lazy_load())


def _fake(monkeypatch, names: dict[str, dict[str, Any]]) -> None:
    for name, attrs in names.items():
        module = types.ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)


def _load(module: str, monkeypatch, real: str):
    try:
        importlib.import_module(real)
    except ImportError:
        if module.endswith("langchain"):
            _fake(
                monkeypatch,
                {
                    "langchain_core": {},
                    "langchain_core.document_loaders": {"BaseLoader": FakeBase},
                    "langchain_core.documents": {"Document": FakeDocument},
                },
            )
        else:
            _fake(
                monkeypatch,
                {
                    "llama_index": {},
                    "llama_index.core": {},
                    "llama_index.core.readers": {},
                    "llama_index.core.readers.base": {"BaseReader": object},
                    "llama_index.core.schema": {"Document": FakeDocument},
                },
            )
    sys.modules.pop(module, None)
    return importlib.import_module(module)


@pytest.fixture
def pages(tmp_path: Path) -> list[str]:
    page = tmp_path / "refunds.html"
    page.write_text(PAGE, encoding="utf-8")
    return [str(page), str(tmp_path / "missing.html")]


def test_langchain_loader_yields_pages_and_reports_failures(monkeypatch, pages) -> None:
    module = _load("agentcrawl.integrations.langchain", monkeypatch, "langchain_core")

    loader = module.AgentCrawlLoader(pages)
    docs = loader.load()

    assert len(docs) == 1
    assert docs[0].page_content.startswith("# Refund Policy")
    assert docs[0].metadata["source"] == pages[0]
    assert docs[0].metadata["title"] == "Refund Policy"
    assert len(loader.errors) == 1 and "missing.html" in loader.errors[0]


def test_llama_index_reader_yields_citable_chunks(monkeypatch, pages) -> None:
    module = _load("agentcrawl.integrations.llama_index", monkeypatch, "llama_index.core")

    documents = module.AgentCrawlReader(chunk_tokens=20, query="gift cards").load_data(pages[0])

    assert len(documents) >= 2
    exceptions = next(d for d in documents if "Gift cards" in d.text)
    assert exceptions.metadata["heading"] == "Refund Policy > Exceptions"
    assert exceptions.metadata["cite_url"].endswith("refunds.html")
    assert exceptions.metadata["score"] > 0


def test_mode_is_checked(monkeypatch, pages) -> None:
    from agentcrawl.integrations._records import iter_records

    with pytest.raises(ValueError, match="mode must be"):
        list(iter_records(pages, mode="spider"))
