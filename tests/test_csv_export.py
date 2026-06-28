from __future__ import annotations

import csv
from pathlib import Path

from agentcrawl.cli import _export_failures_csv


def test_export_failures_csv_writes_header_and_rows(tmp_path: Path) -> None:
    rows = [
        {"failure_id": "f1", "url": "https://example.com/", "error_type": "client_challenge"},
        {"failure_id": "f2", "url": "https://other.test/", "error_type": "timeout"},
    ]
    dest = tmp_path / "failures.csv"
    written = _export_failures_csv(rows, str(dest))
    assert written == 2
    assert dest.exists()
    with dest.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows_out = list(reader)
    assert len(rows_out) == 2
    assert {row["failure_id"] for row in rows_out} == {"f1", "f2"}
    for row in rows_out:
        assert "error_type" in row


def test_export_failures_csv_creates_parent_dirs(tmp_path: Path) -> None:
    rows = [{"failure_id": "x", "url": "https://a.test/", "error_type": "fetch_error"}]
    dest = tmp_path / "nested" / "sub" / "failures.csv"
    written = _export_failures_csv(rows, str(dest))
    assert written == 1
    assert dest.exists()


def test_export_failures_csv_handles_empty_list(tmp_path: Path) -> None:
    dest = tmp_path / "nested" / "empty.csv"
    # Empty rows must short-circuit _before_ mkdir runs so neither the
    # parent directory nor the destination file is materialized on disk.
    written = _export_failures_csv([], str(dest))
    assert written == 0
    assert not dest.exists()
    assert not (tmp_path / "nested").exists()


def test_export_failures_csv_uses_union_of_keys(tmp_path: Path) -> None:
    rows = [
        {"failure_id": "f1", "url": "https://a.test/", "error_type": "fetch_error"},
        {"failure_id": "f2", "url": "https://b.test/", "domain": "b.test"},
    ]
    dest = tmp_path / "failures.csv"
    written = _export_failures_csv(rows, str(dest))
    assert written == 2
    with dest.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        _ = list(reader)
    # Header should include all union keys
    fld = reader.fieldnames or []
    assert "failure_id" in fld
    assert "url" in fld
    assert "error_type" in fld
    assert "domain" in fld
