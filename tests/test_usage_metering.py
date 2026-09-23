"""Usage metering correctness (2026-09-23 audit).

Usage rows become invoices once AgentCrawl is billed, so who pays and for what
has to be right before that day:

- retrying a job's failures billed the key that pressed "retry" (an owner key
  retrying another key's job paid for it), and the re-run was scheduled under
  that key too;
- ``/v1/extract`` recorded one unit whether or not it spent model requests.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import agentcrawl.server as server_module
from agentcrawl.graph import CrawlGraph
from agentcrawl.config import CrawlConfig
from agentcrawl.server import app, server
from agentcrawl.storage import SQLiteStore

KEY_A = "metering-key-a"
OWNER = "metering-owner"


def _fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _authenticated(tmp_path: Path) -> TestClient:
    server.store = SQLiteStore(tmp_path / "metering.db")
    server.auth_enabled = True
    server.api_keys = {KEY_A, OWNER}
    server.owner_api_keys = {OWNER}
    return TestClient(app)


def test_retry_is_billed_and_scheduled_to_the_job_owner(tmp_path: Path, monkeypatch) -> None:
    client = _authenticated(tmp_path)
    job_id, _created = server.store.create_or_get_job(
        "crawl", {"url": "https://example.com/"}, owner_key=_fingerprint(KEY_A)
    )
    scheduled: list[tuple] = []
    monkeypatch.setattr(server.store, "retry_crawl_failures", lambda *a, **k: [{"id": "f1"}])
    monkeypatch.setattr(server, "schedule_job", lambda *args: scheduled.append(args))

    response = client.post(
        f"/v1/jobs/{job_id}/failures/retry",
        json={"retry_all": True},
        headers={"Authorization": f"Bearer {OWNER}"},
    )

    assert response.status_code == 200
    assert scheduled and scheduled[0][2] == _fingerprint(KEY_A)
    assert server.store.usage_by_endpoint(api_key=_fingerprint(KEY_A)) == {
        "/v1/jobs.failures.retry": 1
    }
    assert server.store.usage_by_endpoint(api_key=_fingerprint(OWNER)) == {}


def test_extract_meters_llm_calls_on_their_own_line(tmp_path: Path, monkeypatch) -> None:
    client = _authenticated(tmp_path)
    result = SimpleNamespace(ok=True, metadata={"llm_calls": 2}, answer={"title": "x"})
    monkeypatch.setattr(server_module.AgentCrawl, "extract", lambda self, *args: result)
    monkeypatch.setattr(server_module, "to_jsonable", lambda value: {"answer": "x"})

    response = client.post(
        "/v1/extract",
        json={"url": "https://example.com/", "prompt": "title"},
        headers={"Authorization": f"Bearer {KEY_A}"},
    )

    assert response.status_code == 200
    assert server.store.usage_by_endpoint(api_key=_fingerprint(KEY_A)) == {
        "/v1/extract": 1,
        "/v1/extract.llm_calls": 2,
    }


def test_graph_reports_llm_calls_including_reattempts(monkeypatch) -> None:
    calls: list[str] = []

    def fake_extract_answer(prompt, chunks, schema, config, previous_error):
        calls.append(prompt)
        return None, None, None  # empty answer -> the default condition reattempts

    monkeypatch.setattr("agentcrawl.graph.extract_answer", fake_extract_answer)
    monkeypatch.setattr(
        "agentcrawl.graph.fetch_source",
        lambda source, config: ("<html><body><p>hello</p></body></html>", {"fetcher": "http"}),
    )
    config = CrawlConfig(auto_reattempt=True, max_attempts=3)

    result = CrawlGraph(config).run("https://example.com/", "title")

    assert result.metadata["llm_calls"] == len(calls) == 3
