from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import sqlite3
import tempfile
import time
import urllib.error
import urllib.request
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from .crawler import AgentCrawl
from .remote_client import AgentCrawlClient
from .serializers import to_jsonable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agentcrawl")
    parser.add_argument(
        "--base-url", default=os.getenv("AGENTCRAWL_BASE_URL", "http://127.0.0.1:8000")
    )
    parser.add_argument("--api-key", default=os.getenv("AGENTCRAWL_API_KEY"))
    parser.add_argument(
        "--remote",
        action="store_true",
        help="Call an AgentCrawl server instead of running locally.",
    )
    parser.add_argument("--fetcher", default=os.getenv("AGENTCRAWL_FETCHER", "http"))

    sub = parser.add_subparsers(dest="command", required=True)

    scrape = sub.add_parser("scrape")
    scrape.add_argument("url")
    scrape.add_argument("--format", action="append", dest="formats", default=None)
    scrape.add_argument("--no-cache", action="store_true")
    scrape.add_argument("--cache-ttl", type=int, default=None)
    scrape.add_argument("--full-page", action="store_true")

    map_cmd = sub.add_parser("map")
    map_cmd.add_argument("url")
    map_cmd.add_argument("--max-urls", type=int, default=None)

    crawl = sub.add_parser("crawl")
    crawl.add_argument("url")
    crawl.add_argument("--max-pages", type=int, default=None)
    crawl.add_argument("--max-depth", type=int, default=None)
    crawl.add_argument("--wait", action="store_true")
    crawl.add_argument("--idempotency-key")

    job = sub.add_parser("job")
    job.add_argument("job_id")
    job.add_argument("--offset", type=int, default=0)
    job.add_argument("--limit", type=int, default=100)

    job_events = sub.add_parser("job-events")
    job_events.add_argument("job_id")
    job_events.add_argument("--event-type")
    job_events.add_argument("--offset", type=int, default=0)
    job_events.add_argument("--limit", type=int, default=100)

    job_cancel = sub.add_parser("job-cancel")
    job_cancel.add_argument("job_id")

    failures = sub.add_parser("failures")
    failures.add_argument("--job-id")
    failures.add_argument("--status", default="open")
    failures.add_argument("--retryable", action="store_true")
    failures.add_argument("--error-type")
    failures.add_argument("--domain")
    failures.add_argument("--offset", type=int, default=0)
    failures.add_argument("--limit", type=int, default=100)

    job_failures = sub.add_parser("job-failures")
    job_failures.add_argument("job_id")
    job_failures.add_argument("--status", default="open")
    job_failures.add_argument("--retryable", action="store_true")
    job_failures.add_argument("--error-type")
    job_failures.add_argument("--offset", type=int, default=0)
    job_failures.add_argument("--limit", type=int, default=100)

    retry_failures = sub.add_parser("retry-failures")
    retry_failures.add_argument("job_id")
    retry_failures.add_argument("--failure-id", action="append", dest="failure_ids")
    retry_failures.add_argument("--url", action="append", dest="urls")
    retry_failures.add_argument("--all", action="store_true", dest="retry_all")

    sub.add_parser("usage")
    sub.add_parser("stats")
    sub.add_parser("doctor")
    sub.add_parser("mcp")

    cache_clear = sub.add_parser("cache-clear")
    cache_clear.add_argument("--domain")
    cache_clear.add_argument("--url")

    backup = sub.add_parser("backup")
    backup.add_argument("--db", default=os.getenv("AGENTCRAWL_DB", "agentcrawl.db"))
    backup.add_argument("--output-dir", required=True)
    backup.add_argument("--env-file")

    restore = sub.add_parser("restore")
    restore.add_argument("--backup-db", required=True)
    restore.add_argument("--db", default=os.getenv("AGENTCRAWL_DB", "agentcrawl.db"))
    restore.add_argument("--force", action="store_true")

    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)

    if args.command == "doctor":
        print(json.dumps(_doctor(), ensure_ascii=False, indent=2))
        return 0
    if args.command == "backup":
        print(json.dumps(_backup(args.db, args.output_dir, args.env_file), ensure_ascii=False, indent=2))
        return 0
    if args.command == "restore":
        print(
            json.dumps(
                _restore(args.backup_db, args.db, force=args.force),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.command == "mcp":
        from .mcp_server import main as mcp_main

        mcp_main()
        return 0
    if args.command == "serve":
        try:
            import uvicorn
        except ImportError as exc:
            raise SystemExit(
                "Server dependencies are missing. Install agentcrawl[server]."
            ) from exc
        uvicorn.run("agentcrawl.server:app", host=args.host, port=args.port)
        return 0

    result = _run_remote(args) if args.remote else _run_local(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _doctor() -> dict[str, Any]:
    try:
        installed_version = version("agentcrawl")
    except PackageNotFoundError:
        installed_version = "source checkout"
    optional_modules = {
        "mcp": "mcp",
        "server": "fastapi",
        "browser": "playwright",
        "llm": "langchain",
    }
    extras = {
        name: importlib.util.find_spec(module) is not None
        for name, module in optional_modules.items()
    }
    checks = {
        "agentcrawl_command": _check_command("agentcrawl"),
        "python": _check_bool((shutil.which("python") or shutil.which("python3")) is not None),
        "local_scrape": _check_local_scrape(),
        "remote_config": _check_remote_config(),
        "remote_health": _check_remote_health(),
    }
    ok = all(check["ok"] for check in checks.values() if not check.get("skipped"))
    return {
        "agentcrawl": installed_version,
        "command": shutil.which("agentcrawl"),
        "python": shutil.which("python") or shutil.which("python3"),
        "default_fetcher": os.getenv("AGENTCRAWL_FETCHER", "http"),
        "extras": extras,
        "checks": checks,
        "summary": {
            "ok": ok,
            "remote_configured": bool(os.getenv("AGENTCRAWL_BASE_URL")),
            "api_key_configured": bool(os.getenv("AGENTCRAWL_API_KEY")),
        },
    }


def _check_bool(ok: bool, detail: str | None = None) -> dict[str, Any]:
    return {"ok": ok, "detail": detail or ("ok" if ok else "missing")}


def _check_command(command: str) -> dict[str, Any]:
    path = shutil.which(command)
    if path:
        return {"ok": True, "detail": path}
    return {
        "ok": True,
        "skipped": True,
        "detail": f"{command} console script not on PATH; source checkout can use python -m agentcrawl",
    }


def _check_local_scrape() -> dict[str, Any]:
    try:
        with tempfile.TemporaryDirectory() as directory:
            page = Path(directory) / "doctor.html"
            page.write_text("<main><h1>AgentCrawl Doctor</h1></main>", encoding="utf-8")
            document = AgentCrawl({"fetcher": os.getenv("AGENTCRAWL_FETCHER", "http")}).scrape(
                str(page)
            )
        markdown = getattr(document, "markdown", "")
        return _check_bool("AgentCrawl Doctor" in markdown)
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def _check_remote_config() -> dict[str, Any]:
    base_url = os.getenv("AGENTCRAWL_BASE_URL")
    if not base_url:
        return {"ok": True, "skipped": True, "detail": "AGENTCRAWL_BASE_URL not set"}
    return {
        "ok": True,
        "detail": {
            "base_url": base_url.rstrip("/"),
            "api_key_configured": bool(os.getenv("AGENTCRAWL_API_KEY")),
        },
    }


def _check_remote_health() -> dict[str, Any]:
    base_url = os.getenv("AGENTCRAWL_BASE_URL")
    if not base_url:
        return {"ok": True, "skipped": True, "detail": "remote health skipped"}
    try:
        request = urllib.request.Request(base_url.rstrip("/") + "/health")
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {"ok": bool(payload.get("ok")), "detail": payload}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "detail": f"HTTP {exc.code}"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}


def _backup(db_path: str, output_dir: str, env_file: str | None = None) -> dict[str, Any]:
    source = Path(db_path).expanduser()
    if not source.exists():
        raise SystemExit(f"Database not found: {source}")
    target_dir = Path(output_dir).expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    db_backup = target_dir / f"agentcrawl-{timestamp}.db"
    manifest_path = target_dir / f"agentcrawl-{timestamp}.manifest.json"

    with sqlite3.connect(source) as source_conn, sqlite3.connect(db_backup) as backup_conn:
        source_conn.backup(backup_conn)
    with sqlite3.connect(db_backup) as conn:
        integrity = str(conn.execute("pragma integrity_check").fetchone()[0])

    copied_env: str | None = None
    if env_file:
        env_source = Path(env_file).expanduser()
        if not env_source.exists():
            raise SystemExit(f"Environment file not found: {env_source}")
        env_target = target_dir / f"agentcrawl-{timestamp}.env"
        shutil.copyfile(env_source, env_target)
        os.chmod(env_target, 0o600)
        copied_env = str(env_target)

    manifest = {
        "ok": integrity == "ok",
        "database": str(db_backup),
        "database_bytes": db_backup.stat().st_size,
        "integrity_check": integrity,
        "env_file": copied_env,
        "manifest": str(manifest_path),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    os.chmod(manifest_path, 0o600)
    return manifest


def _restore(backup_db: str, db_path: str, *, force: bool = False) -> dict[str, Any]:
    source = Path(backup_db).expanduser()
    target = Path(db_path).expanduser()
    if not source.exists():
        raise SystemExit(f"Backup database not found: {source}")
    if target.exists() and not force:
        raise SystemExit(f"Refusing to overwrite existing database without --force: {target}")
    with sqlite3.connect(source) as conn:
        integrity = str(conn.execute("pragma integrity_check").fetchone()[0])
    if integrity != "ok":
        raise SystemExit(f"Backup integrity check failed: {integrity}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    os.chmod(target, 0o600)
    return {
        "ok": True,
        "database": str(target),
        "source": str(source),
        "database_bytes": target.stat().st_size,
        "integrity_check": integrity,
        "overwritten": force,
    }


def _run_local(args: argparse.Namespace) -> Any:
    crawler = AgentCrawl({"fetcher": args.fetcher})
    if args.command == "scrape":
        return to_jsonable(
            crawler.scrape(
                args.url,
                formats=args.formats or ["markdown", "links", "metadata"],
                only_main_content=False if args.full_page else None,
            )
        )
    if args.command == "map":
        return to_jsonable(crawler.map(args.url, max_urls=args.max_urls))
    if args.command == "crawl":
        return to_jsonable(
            crawler.crawl(args.url, max_pages=args.max_pages, max_depth=args.max_depth)
        )
    raise SystemExit(f"{args.command} requires --remote")


def _run_remote(args: argparse.Namespace) -> Any:
    client = AgentCrawlClient(args.base_url, args.api_key)
    if args.command == "scrape":
        return client.scrape(
            args.url,
            formats=args.formats,
            only_main_content=False if args.full_page else None,
            cache=not args.no_cache,
            cache_ttl_seconds=args.cache_ttl,
        )
    if args.command == "map":
        return client.map(args.url, max_urls=args.max_urls)
    if args.command == "crawl":
        return client.crawl(
            args.url,
            max_pages=args.max_pages,
            max_depth=args.max_depth,
            wait=args.wait,
            idempotency_key=args.idempotency_key,
        )
    if args.command == "job":
        return client.job(args.job_id, offset=args.offset, limit=args.limit)
    if args.command == "job-events":
        return client.job_events(
            args.job_id,
            event_type=args.event_type,
            offset=args.offset,
            limit=args.limit,
        )
    if args.command == "job-cancel":
        return client.cancel_job(args.job_id)
    if args.command == "failures":
        return client.failures(
            job_id=args.job_id,
            status=args.status,
            retryable=True if args.retryable else None,
            error_type=args.error_type,
            domain=args.domain,
            offset=args.offset,
            limit=args.limit,
        )
    if args.command == "job-failures":
        return client.job_failures(
            args.job_id,
            status=args.status,
            retryable=True if args.retryable else None,
            error_type=args.error_type,
            offset=args.offset,
            limit=args.limit,
        )
    if args.command == "retry-failures":
        return client.retry_failures(
            args.job_id,
            failure_ids=args.failure_ids,
            urls=args.urls,
            retry_all=args.retry_all,
        )
    if args.command == "usage":
        return client.usage()
    if args.command == "stats":
        return client.stats()
    if args.command == "cache-clear":
        return client.clear_cache(domain=args.domain, url=args.url)
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
