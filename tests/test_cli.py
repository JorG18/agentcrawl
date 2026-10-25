from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

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
    assert payload["checks"]["local_scrape"]["ok"] is True
    assert "api_key_configured" in payload["summary"]


def test_doctor_passes_with_local_files_disabled(capsys, monkeypatch) -> None:
    # The Docker image sets AGENTCRAWL_ALLOW_LOCAL_FILES=false; its smoke test runs doctor.
    monkeypatch.delenv("AGENTCRAWL_BASE_URL", raising=False)
    monkeypatch.setenv("AGENTCRAWL_ALLOW_LOCAL_FILES", "false")

    assert main(["doctor"]) == 0
    assert json.loads(capsys.readouterr().out)["checks"]["local_scrape"]["ok"] is True


def test_doctor_exits_nonzero_when_a_check_fails(capsys, monkeypatch) -> None:
    monkeypatch.delenv("AGENTCRAWL_BASE_URL", raising=False)
    monkeypatch.setattr(
        "agentcrawl.cli._check_local_scrape", lambda: {"ok": False, "detail": "broken"}
    )

    assert main(["doctor"]) == 1
    assert json.loads(capsys.readouterr().out)["summary"]["ok"] is False


def test_cli_version_flag_prints_package_version(capsys) -> None:
    try:
        main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0

    output = capsys.readouterr().out.strip()
    assert output.startswith("agentcrawl ")


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


def test_restore_keeps_a_target_it_cannot_write(tmp_path: Path, monkeypatch) -> None:
    import agentcrawl.cli as cli_module

    backup = tmp_path / "backup.db"
    target = tmp_path / "target.db"
    with sqlite3.connect(backup) as conn:
        conn.execute("create table sample (value text)")
    with sqlite3.connect(target) as conn:
        conn.execute("create table live (value text)")
    real_connect = sqlite3.connect

    class Locked:
        def __init__(self, conn):
            self._conn = conn

        def backup(self, _dst):
            raise sqlite3.OperationalError("database is locked")

        def __getattr__(self, name):
            return getattr(self._conn, name)

    def connect(path, *args, **kwargs):
        conn = real_connect(path, *args, **kwargs)
        return Locked(conn) if Path(path) == backup else conn

    monkeypatch.setattr(cli_module.sqlite3, "connect", connect)

    with pytest.raises(SystemExit, match="Stop the server"):
        cli_module._restore(str(backup), str(target), force=True)

    monkeypatch.setattr(cli_module.sqlite3, "connect", real_connect)
    with sqlite3.connect(target) as conn:  # the live database was not deleted
        assert conn.execute("select name from sqlite_master").fetchone()[0] == "live"


def test_doctor_reads_the_installed_distribution(capsys, monkeypatch) -> None:
    """Regression (2026-09 audit): ``doctor`` looked up the ``agentcrawl``
    distribution instead of ``agentcrawl-ai``, so it reported a stale,
    unrelated version (or "source checkout" on an installed wheel).
    ``__init__`` was fixed in v0.1.2; this call site was missed."""
    requested: list[str] = []

    def fake_version(name: str) -> str:
        requested.append(name)
        return "9.9.9"

    monkeypatch.delenv("AGENTCRAWL_BASE_URL", raising=False)
    monkeypatch.delenv("AGENTCRAWL_API_KEY", raising=False)
    monkeypatch.setattr("agentcrawl.cli.version", fake_version)

    assert main(["doctor"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert requested == ["agentcrawl-ai"]
    assert payload["agentcrawl"] == "9.9.9"


def test_version_fallback_stays_in_sync_with_pyproject() -> None:
    """The source-checkout fallback used to be a hardcoded literal that had
    already drifted several releases behind ``pyproject.toml``."""
    import re

    import agentcrawl

    pyproject = Path(agentcrawl.__file__).resolve().parent.parent / "pyproject.toml"
    expected = re.search(
        r'(?m)^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8")
    ).group(1)

    assert agentcrawl._pyproject_version() == expected


def test_published_skill_and_sdk_versions_match_pyproject() -> None:
    """The ClawHub skill and the TypeScript SDK carry hand-written copies of the
    version; release.yml runs this test, so a bump that misses one fails."""
    import re

    import agentcrawl

    root = Path(agentcrawl.__file__).resolve().parent.parent
    expected = agentcrawl._pyproject_version()
    skill = (root / "skills" / "agentcrawl" / "SKILL.md").read_text(encoding="utf-8")
    sdk = json.loads((root / "sdk" / "typescript" / "package.json").read_text(encoding="utf-8"))

    assert re.search(r'(?m)^version:\s*"([^"]+)"', skill).group(1) == expected
    assert sdk["version"] == expected


def test_doctor_browser_check_without_the_extra_is_skipped() -> None:
    from agentcrawl.cli import _check_browser

    check = _check_browser(False)
    assert check["skipped"] is True
    assert "playwright install chromium" in check["detail"]


def test_backup_and_restore_close_their_connections(tmp_path: Path, monkeypatch, capsys) -> None:
    database = tmp_path / "agentcrawl.db"
    with sqlite3.connect(database) as conn:
        conn.execute("create table sample (value text)")
    opened: list[sqlite3.Connection] = []
    real_connect = sqlite3.connect

    def tracking_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        opened.append(conn)
        return conn

    monkeypatch.setattr(sqlite3, "connect", tracking_connect)
    assert main(["backup", "--db", str(database), "--output-dir", str(tmp_path / "b")]) == 0
    backup_db = json.loads(capsys.readouterr().out)["database"]
    restored = tmp_path / "restored.db"
    assert main(["restore", "--backup-db", backup_db, "--db", str(restored)]) == 0

    assert len(opened) == 6  # restore copies through SQLite's backup API (2 more)
    for conn in opened:
        try:
            conn.execute("select 1")
        except sqlite3.ProgrammingError:
            continue  # closed, as it should be
        raise AssertionError("connection left open")


def test_failures_export_works_in_remote_mode(tmp_path, monkeypatch) -> None:
    from agentcrawl import cli

    monkeypatch.setattr(
        cli.AgentCrawlClient,
        "failures",
        lambda self, **k: {
            "success": True,
            "data": {"failures": [{"id": "f1", "url": "https://e.com/x"}], "returned": 1},
        },
    )
    out = tmp_path / "out.csv"
    assert cli.main(["--remote", "failures", "--export", str(out)]) == 0
    assert "https://e.com/x" in out.read_text(encoding="utf-8")


def test_restore_force_discards_the_stale_wal(tmp_path) -> None:
    import sqlite3
    import subprocess
    import sys

    live, backup = tmp_path / "live.db", tmp_path / "backup.db"
    with sqlite3.connect(backup) as conn:
        conn.execute("create table jobs(id text)")
        conn.execute("insert into jobs values('from-backup')")
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sqlite3,os,sys\n"
            "c=sqlite3.connect(sys.argv[1]); c.execute('pragma journal_mode=wal')\n"
            "c.execute('pragma wal_autocheckpoint=0'); c.execute('create table jobs(id text)')\n"
            "c.commit(); c.execute(\"insert into jobs values('stale-live')\"); c.commit()\n"
            "os._exit(0)",
            str(live),
        ],
        check=True,
    )
    assert Path(f"{live}-wal").exists()
    assert main(["restore", "--backup-db", str(backup), "--db", str(live), "--force"]) == 0
    rows = sqlite3.connect(live).execute("select id from jobs").fetchall()
    assert rows == [("from-backup",)]


def test_restore_force_replaces_a_corrupt_database(tmp_path) -> None:
    import sqlite3

    live, backup = tmp_path / "live.db", tmp_path / "backup.db"
    with sqlite3.connect(backup) as conn:
        conn.execute("create table jobs(id text)")
        conn.execute("insert into jobs values('from-backup')")
    live.write_bytes(b"not a database at all" * 100)

    assert main(["restore", "--backup-db", str(backup), "--db", str(live), "--force"]) == 0
    assert sqlite3.connect(live).execute("select id from jobs").fetchall() == [("from-backup",)]
