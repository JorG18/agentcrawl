from __future__ import annotations

import json
import subprocess

from agentcrawl.cli import _run_failure_alert


def test_run_failure_alert_sends_terminal_failures_to_command(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(cmd, *, input, text, shell, check):
        calls.append(
            {
                "cmd": cmd,
                "input": input,
                "text": text,
                "shell": shell,
                "check": check,
            }
        )
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = {
        "source": "https://example.com",
        "metadata": {
            "terminal_failures": [
                {
                    "url": "https://example.com/a",
                    "attempts": 2,
                    "error_type": "timeout",
                    "message": "Request timed out",
                    "retryable": True,
                }
            ]
        },
    }

    assert _run_failure_alert(result, "notify-send AgentCrawl") is True

    assert len(calls) == 1
    assert calls[0]["cmd"] == "notify-send AgentCrawl"
    assert calls[0]["text"] is True
    assert calls[0]["shell"] is True
    assert calls[0]["check"] is False
    payload = json.loads(str(calls[0]["input"]))
    assert payload == {
        "source": "https://example.com",
        "failure_count": 1,
        "failures": result["metadata"]["terminal_failures"],
    }


def test_run_failure_alert_skips_when_no_terminal_failures(monkeypatch) -> None:
    def fail_run(*args, **kwargs):  # pragma: no cover - should not be called
        raise AssertionError("alert command should not run")

    monkeypatch.setattr(subprocess, "run", fail_run)

    assert _run_failure_alert({"metadata": {"terminal_failures": []}}, "notify-send nope") is False
