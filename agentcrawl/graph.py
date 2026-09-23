from __future__ import annotations

from collections.abc import Callable

try:
    from simpleeval import simple_eval
except ImportError:
    simple_eval = None

from .config import CrawlConfig
from .documents import markdown_from_fetched_content
from .extraction import extract_answer
from .fetchers import fetch_source
from .models import CrawlResult
from .parsing import apply_output_budget, chunk_text_stats, html_to_markdown
from .state import CrawlState
from .utils import is_empty_answer, log

Node = Callable[[CrawlState], CrawlState]


class CrawlGraph:
    def __init__(self, config: CrawlConfig):
        self.config = config
        self.nodes: list[tuple[str, Node]] = [
            ("fetch", self.fetch),
            ("parse", self.parse),
            ("chunk", self.chunk),
            ("extract", self.extract),
        ]

    def run(self, source: str, prompt: str, schema: object | None = None) -> CrawlResult:
        state: CrawlState = {
            "source": source,
            "prompt": prompt,
            "schema": schema,
            "config": self.config,
            "attempt": 0,
            "errors": [],
            "metadata": {},
        }
        for name, node in self.nodes:
            log(self.config.verbose, f"{name}")
            state = node(state)
        state = self.reattempt_if_needed(state)
        return CrawlResult(
            source=source,
            prompt=prompt,
            answer=state.get("answer"),
            markdown=state.get("markdown", ""),
            raw_html=state.get("html", ""),
            chunks=state.get("chunks", []),
            metadata={
                **state.get("metadata", {}),
                "chunking": state.get("chunk_stats", {}),
                "truncation": state.get("truncation", {}),
                # What this run cost in model requests (auto-reattempts
                # included); the API meters it separately from the page.
                "llm_calls": int(state.get("llm_calls", 0)),
            },
            errors=state.get("errors", []),
        )

    def fetch(self, state: CrawlState) -> CrawlState:
        html, metadata = fetch_source(state["source"], self.config)
        state["html"] = html
        state["metadata"] = {**state.get("metadata", {}), **metadata}
        return state

    def parse(self, state: CrawlState) -> CrawlState:
        markdown = markdown_from_fetched_content(state.get("html", ""), state.get("metadata", {}))
        markdown = markdown or html_to_markdown(state.get("html", ""), self.config)
        # Apply the output budget here and record it: the extraction prompt
        # claims to answer from "the page text", so a silent cut would make an
        # incomplete answer look like a model failure.
        markdown_chars_full = len(markdown)
        markdown, chars_omitted = apply_output_budget(markdown, self.config.max_input_chars)
        state["markdown"] = markdown
        state["truncation"] = {
            "markdown_chars": len(markdown),
            "markdown_chars_full": markdown_chars_full,
            "markdown_truncated": chars_omitted > 0,
            "chars_omitted": chars_omitted,
        }
        return state

    def chunk(self, state: CrawlState) -> CrawlState:
        # The prompt is the query: when the page exceeds the chunk budget, the
        # model sees the passages that answer it, not just the first ones.
        chunks, stats = chunk_text_stats(
            state.get("markdown", ""), self.config, query=state.get("prompt")
        )
        state["chunks"] = chunks
        state["chunk_stats"] = stats
        return state

    def extract(self, state: CrawlState) -> CrawlState:
        # Counted before the call: a provider error still cost a request.
        state["llm_calls"] = int(state.get("llm_calls", 0)) + 1
        answer, validation_error, reasoning = extract_answer(
            state["prompt"],
            state.get("chunks", []),
            state.get("schema"),
            self.config,
            state.get("validation_error"),
        )
        state["answer"] = answer
        state["validation_error"] = validation_error
        state["reasoning"] = reasoning
        state["empty"] = is_empty_answer(answer)
        if validation_error:
            state.setdefault("errors", []).append(validation_error)
        return state

    def reattempt_if_needed(self, state: CrawlState) -> CrawlState:
        if not self.config.auto_reattempt:
            return state
        while state.get("attempt", 0) + 1 < self.config.max_attempts and self._should_reattempt(
            state
        ):
            state["attempt"] = state.get("attempt", 0) + 1
            state["errors"] = []
            state = self.extract(state)
        return state

    def _should_reattempt(self, state: CrawlState) -> bool:
        names = {
            "empty": bool(state.get("empty")),
            "validation_error": bool(state.get("validation_error")),
            "attempt": int(state.get("attempt", 0)),
        }
        if simple_eval is None:
            return bool(state.get("empty") or state.get("validation_error"))
        try:
            return bool(simple_eval(self.config.reattempt_condition, names=names))
        except Exception:
            return bool(state.get("empty") or state.get("validation_error"))
