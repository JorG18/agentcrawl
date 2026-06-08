from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from agentcrawl.cli import main
from agentcrawl.config import CrawlConfig


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps({"ok": True, "service": "agentcrawl"}).encode("utf-8")


def test_default_fetcher_is_http() -> None:
    assert CrawlConfig().fetcher == "http"


def test_doctor_reports_installation(capsys, monkeypatch) -> None:
    monkeypatch.delenv("AGENTCRAWL_BASE_URL", raising=False)
    monkeypatch.delenv("AGENTCRAWL_API_KEY", raising=False)

    assert main(["doctor"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["default_fetcher"] == "http"
    assert "mcp" in payload["extras"]
    assert payload["checks"]["local_scrape"]["ok"] is True
    assert payload["checks"]["remote_config"]["skipped"] is True
    assert payload["summary"]["ok"] is True


def test_doctor_checks_remote_health_without_printing_api_key(capsys, monkeypatch) -> None:
    requests = []

    def fake_urlopen(request, timeout):
        requests.append(request)
        return _Response()

    monkeypatch.setenv("AGENTCRAWL_BASE_URL", "http://agentcrawl.test")
    monkeypatch.setenv("AGENTCRAWL_API_KEY", "secret-key")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    assert main(["doctor"]) == 0
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert "secret-key" not in output
    assert requests[0].full_url == "http://agentcrawl.test/health"
    assert payload["checks"]["remote_config"]["detail"] == {
        "base_url": "http://agentcrawl.test",
        "api_key_configured": True,
    }
    assert payload["checks"]["remote_health"]["ok"] is True


def test_cli_scrape_works_with_default_fetcher(tmp_path: Path, capsys) -> None:
    page = tmp_path / "index.html"
    page.write_text("<main><h1>Quick install works</h1></main>", encoding="utf-8")

    assert main(["scrape", str(page)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "Quick install works" in payload["markdown"]


def test_backup_creates_integrity_checked_database_and_manifest(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "agentcrawl.db"
    env_file = tmp_path / "agentcrawl.env"
    backup_dir = tmp_path / "backups"
    env_file.write_text("AGENTCRAWL_API_KEY=secret-value\n", encoding="utf-8")
    with sqlite3.connect(database) as conn:
        conn.execute("create table sample (value text)")
        conn.execute("insert into sample values ('kept')")

    assert (
        main(
            [
                "backup",
                "--db",
                str(database),
                "--output-dir",
                str(backup_dir),
                "--env-file",
                str(env_file),
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    payload = json.loads(output)

    assert "secret-value" not in output
    assert payload["ok"] is True
    assert payload["integrity_check"] == "ok"
    assert Path(payload["database"]).exists()
    assert (
        Path(payload["env_file"]).read_text(encoding="utf-8") == "AGENTCRAWL_API_KEY=secret-value\n"
    )
    assert Path(payload["manifest"]).exists()
    with sqlite3.connect(payload["database"]) as conn:
        assert conn.execute("select value from sample").fetchone()[0] == "kept"


def test_restore_refuses_overwrite_without_force(tmp_path: Path) -> None:
    backup = tmp_path / "backup.db"
    target = tmp_path / "target.db"
    with sqlite3.connect(backup) as conn:
        conn.execute("create table sample (value text)")
    with sqlite3.connect(target) as conn:
        conn.execute("create table existing (value text)")

    try:
        main(["restore", "--backup-db", str(backup), "--db", str(target)])
    except SystemExit as exc:
        assert "without --force" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("restore should refuse overwrite without --force")


def test_restore_copies_integrity_checked_backup_with_force(tmp_path: Path, capsys) -> None:
    backup = tmp_path / "backup.db"
    target = tmp_path / "target.db"
    with sqlite3.connect(backup) as conn:
        conn.execute("create table sample (value text)")
        conn.execute("insert into sample values ('restored')")
    with sqlite3.connect(target) as conn:
        conn.execute("create table existing (value text)")

    assert main(["restore", "--backup-db", str(backup), "--db", str(target), "--force"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True
    assert payload["overwritten"] is True
    assert payload["integrity_check"] == "ok"
    with sqlite3.connect(target) as conn:
        assert conn.execute("select value from sample").fetchone()[0] == "restored"
