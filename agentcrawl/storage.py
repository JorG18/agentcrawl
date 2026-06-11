from __future__ import annotations

import json
import sqlite3
import time
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

from .html_tools import normalize_url


def _cache_domain(value: str) -> str:
    candidate = value.strip().lower()
    if "://" not in candidate:
        candidate = "//" + candidate
    parsed = urllib.parse.urlsplit(candidate)
    hostname = (parsed.hostname or parsed.netloc or candidate).rstrip(".").lower()
    try:
        return hostname.encode("idna").decode("ascii")
    except UnicodeError:
        return hostname


class SQLiteStore:
    def __init__(self, path: str | Path = "agentcrawl.db"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("pragma busy_timeout = 30000")
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute("pragma journal_mode = wal")
            conn.execute("pragma synchronous = normal")
            conn.executescript(
                """
                create table if not exists jobs (
                    id text primary key,
                    type text not null,
                    status text not null,
                    request_json text not null,
                    result_json text,
                    error text,
                    progress_json text,
                    checkpoint_json text,
                    owner_key text not null default '',
                    idempotency_key text,
                    available_at real not null default 0,
                    cancel_requested integer not null default 0,
                    created_at real not null,
                    updated_at real not null
                );
                create table if not exists crawl_documents (
                    job_id text not null,
                    url text not null,
                    document_json text not null,
                    created_at real not null,
                    primary key (job_id, url)
                );
                create table if not exists crawl_failures (
                    id text primary key,
                    job_id text not null,
                    url text not null,
                    attempts integer not null,
                    error_type text not null,
                    message text not null,
                    retryable integer not null default 0,
                    status text not null default 'open',
                    failed_at real not null,
                    retried_at real,
                    resolved_at real,
                    created_at real not null,
                    updated_at real not null
                );
                create index if not exists idx_crawl_failures_job_status
                    on crawl_failures(job_id, status);
                create index if not exists idx_crawl_failures_url
                    on crawl_failures(url);
                create table if not exists job_events (
                    id text primary key,
                    job_id text not null,
                    event_type text not null,
                    payload_json text not null,
                    created_at real not null
                );
                create index if not exists idx_job_events_job_created
                    on job_events(job_id, created_at);
                create index if not exists idx_job_events_type_created
                    on job_events(event_type, created_at);
                create table if not exists usage_events (
                    id text primary key,
                    api_key text,
                    endpoint text not null,
                    units integer not null,
                    created_at real not null
                );
                create table if not exists scrape_cache (
                    cache_key text primary key,
                    url text not null,
                    response_json text not null,
                    created_at real not null,
                    expires_at real not null
                );
                create index if not exists idx_scrape_cache_expires_at on scrape_cache(expires_at);
                """
            )
            columns = {
                str(row["name"]) for row in conn.execute("pragma table_info(jobs)").fetchall()
            }
            if "progress_json" not in columns:
                conn.execute("alter table jobs add column progress_json text")
            if "cancel_requested" not in columns:
                conn.execute(
                    "alter table jobs add column cancel_requested integer not null default 0"
                )
            if "checkpoint_json" not in columns:
                conn.execute("alter table jobs add column checkpoint_json text")
            if "owner_key" not in columns:
                conn.execute("alter table jobs add column owner_key text not null default ''")
            if "idempotency_key" not in columns:
                conn.execute("alter table jobs add column idempotency_key text")
            if "available_at" not in columns:
                conn.execute("alter table jobs add column available_at real not null default 0")
            conn.execute(
                """
                create unique index if not exists idx_jobs_idempotency
                on jobs(type, owner_key, idempotency_key)
                where idempotency_key is not null
                """
            )

    def prepare_restart_recovery(self) -> int:
        now = time.time()
        with self._connect() as conn:
            running = conn.execute(
                """
                update jobs
                set status = 'queued',
                    error = 'Recovered interrupted job after server restart.',
                    updated_at = ?
                where status = 'running'
                """,
                (now,),
            ).rowcount
            conn.execute(
                """
                update jobs
                set status = 'cancelled',
                    error = 'Cancellation completed during server restart.',
                    checkpoint_json = null,
                    updated_at = ?
                where status = 'cancelling'
                """,
                (now,),
            )
            conn.execute(
                "delete from crawl_documents where job_id in (select id from jobs where status = 'cancelled')"
            )
        return int(running or 0)

    @staticmethod
    def _insert_job_event(
        conn: sqlite3.Connection,
        job_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        created_at: float | None = None,
    ) -> None:
        conn.execute(
            "insert into job_events values (?, ?, ?, ?, ?)",
            (
                uuid.uuid4().hex,
                job_id,
                event_type,
                json.dumps(payload or {}),
                created_at if created_at is not None else time.time(),
            ),
        )

    def create_job(
        self,
        job_type: str,
        request: dict[str, Any],
        *,
        owner_key: str = "",
        idempotency_key: str | None = None,
    ) -> str:
        job_id, _ = self.create_or_get_job(
            job_type,
            request,
            owner_key=owner_key,
            idempotency_key=idempotency_key,
        )
        return job_id

    def create_or_get_job(
        self,
        job_type: str,
        request: dict[str, Any],
        *,
        owner_key: str = "",
        idempotency_key: str | None = None,
    ) -> tuple[str, bool]:
        job_id = uuid.uuid4().hex
        now = time.time()
        progress = {"visited": 0, "pending": 1, "failed": 0, "discovered": 1}
        with self._connect() as conn:
            if idempotency_key:
                existing = conn.execute(
                    """
                    select id from jobs
                    where type = ? and owner_key = ? and idempotency_key = ?
                    """,
                    (job_type, owner_key, idempotency_key),
                ).fetchone()
                if existing:
                    self._validate_idempotent_request(
                        conn,
                        str(existing["id"]),
                        request,
                    )
                    return str(existing["id"]), False
            try:
                conn.execute(
                    """
                    insert into jobs (
                        id, type, status, request_json, result_json, error,
                        progress_json, checkpoint_json, owner_key, idempotency_key,
                        available_at, cancel_requested, created_at, updated_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        job_type,
                        "queued",
                        json.dumps(request),
                        None,
                        None,
                        json.dumps(progress),
                        None,
                        owner_key,
                        idempotency_key,
                        0,
                        0,
                        now,
                        now,
                    ),
                )
                self._insert_job_event(
                    conn,
                    job_id,
                    "created",
                    {"type": job_type, "idempotency_key": idempotency_key is not None},
                    now,
                )
            except sqlite3.IntegrityError:
                existing = conn.execute(
                    """
                    select id from jobs
                    where type = ? and owner_key = ? and idempotency_key = ?
                    """,
                    (job_type, owner_key, idempotency_key),
                ).fetchone()
                if existing:
                    self._validate_idempotent_request(
                        conn,
                        str(existing["id"]),
                        request,
                    )
                    return str(existing["id"]), False
                raise
        return job_id, True

    @staticmethod
    def _validate_idempotent_request(
        conn: sqlite3.Connection,
        job_id: str,
        request: dict[str, Any],
    ) -> None:
        row = conn.execute(
            "select request_json from jobs where id = ?",
            (job_id,),
        ).fetchone()
        if row is None or json.loads(row["request_json"]) != request:
            raise ValueError("Idempotency-Key was already used for a different request.")

    def claim_job(self, job_id: str) -> bool:
        with self._connect() as conn:
            cursor = conn.execute(
                """
                update jobs
                set status = 'running', error = null, updated_at = ?
                where id = ? and status = 'queued' and cancel_requested = 0
                  and available_at <= ?
                """,
                (time.time(), job_id, time.time()),
            )
            claimed = bool(cursor.rowcount)
            if claimed:
                self._insert_job_event(conn, job_id, "claimed", {})
            return claimed

    def update_job(
        self,
        job_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        now = time.time()
        stored_result = result
        documents: list[dict[str, Any]] = []
        if result is not None:
            documents = list(result.get("documents") or [])
            stored_result = {**result, "documents": []}
            stored_result.setdefault("metadata", {})["document_count"] = len(documents)
        with self._connect() as conn:
            for document in documents:
                conn.execute(
                    """
                    insert or replace into crawl_documents (
                        job_id, url, document_json, created_at
                    ) values (?, ?, ?, ?)
                    """,
                    (job_id, str(document["url"]), json.dumps(document), now),
                )
                self._mark_failure_resolved(conn, job_id, str(document["url"]), now)
            if result is not None:
                self._record_crawl_failures(
                    conn,
                    job_id,
                    list(result.get("metadata", {}).get("terminal_failures") or []),
                    now,
                )
            conn.execute(
                """
                update jobs
                set status = ?, result_json = ?, error = ?, checkpoint_json = null,
                    available_at = 0, updated_at = ?
                where id = ?
                """,
                (
                    status,
                    json.dumps(stored_result) if stored_result is not None else None,
                    error,
                    now,
                    job_id,
                ),
            )
            if status == "cancelled" and result is None:
                conn.execute("delete from crawl_documents where job_id = ?", (job_id,))
            self._insert_job_event(
                conn,
                job_id,
                status,
                {
                    "error": error,
                    "document_count": len(documents),
                    "terminal_failures": len(
                        list(result.get("metadata", {}).get("terminal_failures") or [])
                    )
                    if result is not None
                    else 0,
                },
                now,
            )

    @staticmethod
    def _mark_failure_resolved(
        conn: sqlite3.Connection,
        job_id: str,
        url: str,
        now: float,
    ) -> None:
        conn.execute(
            """
            update crawl_failures
            set status = 'resolved', resolved_at = ?, updated_at = ?
            where job_id = ? and url = ? and status in ('open', 'retried')
            """,
            (now, now, job_id, url),
        )

    @staticmethod
    def _record_crawl_failures(
        conn: sqlite3.Connection,
        job_id: str,
        failures: list[dict[str, Any]],
        now: float,
    ) -> None:
        for failure in failures:
            url = str(failure.get("url") or "")
            if not url:
                continue
            existing = conn.execute(
                """
                select id from crawl_failures
                where job_id = ? and url = ? and status in ('open', 'retried')
                """,
                (job_id, url),
            ).fetchone()
            payload = (
                int(failure.get("attempts") or 1),
                str(failure.get("error_type") or "fetch_error"),
                str(failure.get("message") or ""),
                1 if failure.get("retryable") else 0,
                float(failure.get("failed_at") or now),
                now,
            )
            if existing:
                conn.execute(
                    """
                    update crawl_failures
                    set attempts = ?, error_type = ?, message = ?, retryable = ?,
                        failed_at = ?, updated_at = ?
                    where id = ?
                    """,
                    (*payload, str(existing["id"])),
                )
                continue
            conn.execute(
                """
                insert into crawl_failures (
                    id, job_id, url, attempts, error_type, message, retryable,
                    status, failed_at, retried_at, resolved_at, created_at, updated_at
                ) values (?, ?, ?, ?, ?, ?, ?, 'open', ?, null, null, ?, ?)
                """,
                (uuid.uuid4().hex, job_id, url, *payload, now),
            )

    def requeue_job(
        self,
        job_id: str,
        available_at: float,
        *,
        event_type: str = "requeued",
        payload: dict[str, Any] | None = None,
    ) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                update jobs
                set status = 'queued', available_at = ?, error = null, updated_at = ?
                where id = ? and cancel_requested = 0
                """,
                (available_at, now, job_id),
            )
            self._insert_job_event(
                conn,
                job_id,
                event_type,
                {"available_at": available_at, **(payload or {})},
                now,
            )

    def update_job_progress(self, job_id: str, progress: dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "update jobs set progress_json = ?, updated_at = ? where id = ?",
                (json.dumps(progress), time.time(), job_id),
            )

    def save_job_checkpoint(
        self,
        job_id: str,
        checkpoint: dict[str, Any],
        progress: dict[str, Any],
        document: dict[str, Any] | None = None,
    ) -> None:
        now = time.time()
        with self._connect() as conn:
            if document is not None:
                document_url = str(document["url"])
                conn.execute(
                    """
                    insert or replace into crawl_documents (
                        job_id, url, document_json, created_at
                    ) values (?, ?, ?, ?)
                    """,
                    (job_id, document_url, json.dumps(document), now),
                )
                self._mark_failure_resolved(conn, job_id, document_url, now)
            conn.execute(
                """
                update jobs
                set checkpoint_json = ?, progress_json = ?, updated_at = ?
                where id = ?
                """,
                (json.dumps(checkpoint), json.dumps(progress), now, job_id),
            )

    def get_job_documents(
        self,
        job_id: str,
        *,
        offset: int = 0,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            select document_json from crawl_documents
            where job_id = ? order by created_at, url
        """
        params: tuple[Any, ...] = (job_id,)
        if limit is not None:
            query += " limit ? offset ?"
            params = (job_id, limit, offset)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [json.loads(row["document_json"]) for row in rows]

    def job_document_count(self, job_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "select count(*) as total from crawl_documents where job_id = ?",
                (job_id,),
            ).fetchone()
        return int(row["total"])

    def get_job_checkpoint(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "select checkpoint_json from jobs where id = ?",
                (job_id,),
            ).fetchone()
        if row is None or not row["checkpoint_json"]:
            return None
        return json.loads(row["checkpoint_json"])

    def recoverable_jobs(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                select id, request_json, owner_key, available_at
                from jobs
                where type = 'crawl' and status = 'queued' and cancel_requested = 0
                order by created_at
                """
            ).fetchall()
        return [
            {
                "id": str(row["id"]),
                "request": json.loads(row["request_json"]),
                "owner_key": str(row["owner_key"]),
                "available_at": float(row["available_at"]),
            }
            for row in rows
        ]

    def request_job_cancel(self, job_id: str) -> bool:
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                update jobs
                set cancel_requested = 1, status = 'cancelling', updated_at = ?
                where id = ? and status in ('queued', 'running')
                """,
                (now, job_id),
            )
            requested = bool(cursor.rowcount)
            if requested:
                self._insert_job_event(conn, job_id, "cancel_requested", {}, now)
            return requested

    def job_cancel_requested(self, job_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "select cancel_requested from jobs where id = ?", (job_id,)
            ).fetchone()
        return bool(row and row["cancel_requested"])

    def record_job_event(
        self,
        job_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "insert into job_events values (?, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex,
                    job_id,
                    event_type,
                    json.dumps(payload or {}),
                    time.time(),
                ),
            )

    def list_job_events(
        self,
        job_id: str,
        *,
        event_type: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = ["job_id = ?"]
        params: list[Any] = [job_id]
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        params.extend([limit, offset])
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select * from job_events
                where {" and ".join(clauses)}
                order by created_at, id
                limit ? offset ?
                """,
                tuple(params),
            ).fetchall()
        return [self._event_row_to_dict(row) for row in rows]

    def job_event_counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "select event_type, count(*) as total from job_events group by event_type"
            ).fetchall()
        return {str(row["event_type"]): int(row["total"]) for row in rows}

    @staticmethod
    def _event_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "job_id": str(row["job_id"]),
            "event_type": str(row["event_type"]),
            "payload": json.loads(row["payload_json"]),
            "created_at": float(row["created_at"]),
        }

    def list_crawl_failures(
        self,
        *,
        job_id: str | None = None,
        status: str | None = "open",
        retryable: bool | None = None,
        error_type: str | None = None,
        domain: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if job_id:
            clauses.append("job_id = ?")
            params.append(job_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        if retryable is not None:
            clauses.append("retryable = ?")
            params.append(1 if retryable else 0)
        if error_type:
            clauses.append("error_type = ?")
            params.append(error_type)
        where = " where " + " and ".join(clauses) if clauses else ""
        query = f"""
            select * from crawl_failures{where}
            order by failed_at desc, url
        """
        query_params = tuple(params)
        if not domain:
            query += " limit ? offset ?"
            params.extend([limit, offset])
            query_params = tuple(params)
        with self._connect() as conn:
            rows = conn.execute(query, query_params).fetchall()
        failures = [self._failure_row_to_dict(row) for row in rows]
        if domain:
            normalized_domain = domain.lower().strip()
            failures = [
                failure
                for failure in failures
                if urllib.parse.urlsplit(failure["url"]).netloc.lower() == normalized_domain
            ][offset : offset + limit]
        return failures

    def retry_crawl_failures(
        self,
        job_id: str,
        *,
        failure_ids: list[str] | None = None,
        urls: list[str] | None = None,
        retry_all: bool = False,
    ) -> list[dict[str, Any]]:
        if not retry_all and not failure_ids and not urls:
            raise ValueError("Select failure_ids, urls, or retry_all=true.")
        now = time.time()
        with self._connect() as conn:
            job = conn.execute("select * from jobs where id = ?", (job_id,)).fetchone()
            if job is None:
                raise KeyError(job_id)
            if str(job["type"]) != "crawl":
                raise ValueError("Only crawl jobs have retryable URL failures.")
            if str(job["status"]) not in {"completed", "failed"}:
                raise ValueError(f"Cannot retry failures while job is {job['status']}.")
            clauses = ["job_id = ?", "status = 'open'", "retryable = 1"]
            params: list[Any] = [job_id]
            if failure_ids:
                placeholders = ", ".join("?" for _ in failure_ids)
                clauses.append(f"id in ({placeholders})")
                params.extend(failure_ids)
            if urls:
                placeholders = ", ".join("?" for _ in urls)
                clauses.append(f"url in ({placeholders})")
                params.extend(urls)
            rows = conn.execute(
                f"select * from crawl_failures where {' and '.join(clauses)} order by failed_at, url",
                tuple(params),
            ).fetchall()
            failures = [self._failure_row_to_dict(row) for row in rows]
            if not failures:
                return []
            documents = self.get_job_documents(job_id)
            visited = sorted({str(document["url"]) for document in documents})
            retry_urls = [failure["url"] for failure in failures]
            request = json.loads(job["request_json"])
            root = normalize_url(str(request["url"]), str(request["url"]))
            request["max_pages"] = max(
                int(request.get("max_pages") or 0),
                len(visited) + len(retry_urls),
            )
            checkpoint = {
                "version": 2,
                "root": root,
                "queue": [
                    {"url": url, "depth": 0, "attempt": 0, "ready_at": 0.0} for url in retry_urls
                ],
                "queued": sorted(set(visited + retry_urls)),
                "visited": visited,
                "discovered": sorted(set(visited + retry_urls)),
                "errors": [],
                "failed_urls": [],
                "terminal_failures": [],
                "retry_attempts": {},
            }
            progress = {
                "visited": len(visited),
                "pending": len(retry_urls),
                "failed": 0,
                "discovered": len(set(visited + retry_urls)),
                "retries": 0,
            }
            conn.execute(
                """
                update jobs
                set status = 'queued', request_json = ?, result_json = null, error = null,
                    progress_json = ?, checkpoint_json = ?, available_at = 0,
                    cancel_requested = 0, updated_at = ?
                where id = ?
                """,
                (json.dumps(request), json.dumps(progress), json.dumps(checkpoint), now, job_id),
            )
            conn.executemany(
                """
                update crawl_failures
                set status = 'retried', retried_at = ?, updated_at = ?
                where id = ?
                """,
                ((now, now, failure["id"]) for failure in failures),
            )
            return failures

    @staticmethod
    def _failure_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "job_id": str(row["job_id"]),
            "url": str(row["url"]),
            "attempts": int(row["attempts"]),
            "error_type": str(row["error_type"]),
            "message": str(row["message"]),
            "retryable": bool(row["retryable"]),
            "status": str(row["status"]),
            "failed_at": float(row["failed_at"]),
            "retried_at": float(row["retried_at"]) if row["retried_at"] else None,
            "resolved_at": float(row["resolved_at"]) if row["resolved_at"] else None,
            "created_at": float(row["created_at"]),
            "updated_at": float(row["updated_at"]),
        }

    def get_job(
        self,
        job_id: str,
        *,
        document_offset: int = 0,
        document_limit: int | None = None,
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("select * from jobs where id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        result = json.loads(row["result_json"]) if row["result_json"] else None
        if result is not None:
            embedded_documents = list(result.get("documents") or [])
            total = self.job_document_count(job_id)
            if total:
                documents = self.get_job_documents(
                    job_id,
                    offset=document_offset,
                    limit=document_limit,
                )
            else:
                total = len(embedded_documents)
                end = None if document_limit is None else document_offset + document_limit
                documents = embedded_documents[document_offset:end]
            result["documents"] = documents
            result.setdefault("metadata", {})["document_count"] = total
            result["pagination"] = {
                "offset": document_offset,
                "limit": document_limit,
                "returned": len(documents),
                "total": total,
                "has_more": document_offset + len(documents) < total,
            }
        return {
            "id": row["id"],
            "type": row["type"],
            "status": row["status"],
            "request": json.loads(row["request_json"]),
            "result": result,
            "error": row["error"],
            "progress": json.loads(row["progress_json"]) if row["progress_json"] else {},
            "available_at": float(row["available_at"]),
            "cancel_requested": bool(row["cancel_requested"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def record_usage(self, api_key: str | None, endpoint: str, units: int = 1) -> None:
        with self._connect() as conn:
            conn.execute(
                "insert into usage_events values (?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, api_key, endpoint, units, time.time()),
            )

    def usage_count(self, api_key: str | None = None) -> int:
        with self._connect() as conn:
            if api_key:
                row = conn.execute(
                    "select coalesce(sum(units), 0) as total from usage_events where api_key = ?",
                    (api_key,),
                ).fetchone()
            else:
                row = conn.execute(
                    "select coalesce(sum(units), 0) as total from usage_events"
                ).fetchone()
        return int(row["total"])

    def get_cache(self, cache_key: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "select response_json from scrape_cache where cache_key = ? and expires_at > ?",
                (cache_key, time.time()),
            ).fetchone()
        return json.loads(row["response_json"]) if row else None

    def set_cache(
        self, cache_key: str, url: str, response: dict[str, Any], ttl_seconds: int
    ) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "replace into scrape_cache values (?, ?, ?, ?, ?)",
                (cache_key, url, json.dumps(response), now, now + ttl_seconds),
            )

    def cleanup_cache(self) -> int:
        with self._connect() as conn:
            cursor = conn.execute("delete from scrape_cache where expires_at <= ?", (time.time(),))
            deleted = int(cursor.rowcount or 0)
        self.checkpoint_wal()
        return deleted

    def checkpoint_wal(self, mode: str = "passive") -> None:
        if mode not in {"passive", "full", "restart", "truncate"}:
            raise ValueError(f"Unsupported WAL checkpoint mode: {mode}")
        with self._connect() as conn:
            conn.execute(f"pragma wal_checkpoint({mode})")

    def cache_count(self) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "select count(*) as total from scrape_cache where expires_at > ?", (time.time(),)
            ).fetchone()
        return int(row["total"])

    def clear_cache(self, domain: str | None = None, url: str | None = None) -> int:
        with self._connect() as conn:
            if url:
                cursor = conn.execute("delete from scrape_cache where url = ?", (url,))
                return int(cursor.rowcount or 0)
            if not domain:
                cursor = conn.execute("delete from scrape_cache")
                return int(cursor.rowcount or 0)
            normalized_domain = _cache_domain(domain)
            rows = conn.execute("select cache_key, url from scrape_cache").fetchall()
            keys = [
                str(row["cache_key"])
                for row in rows
                if _cache_domain(str(row["url"])) == normalized_domain
            ]
            conn.executemany(
                "delete from scrape_cache where cache_key = ?", ((key,) for key in keys)
            )
            return len(keys)

    def cache_by_domain(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._connect() as conn:
            rows = conn.execute(
                "select url from scrape_cache where expires_at > ?",
                (time.time(),),
            ).fetchall()
        for row in rows:
            parsed = urllib.parse.urlsplit(str(row["url"]))
            domain = parsed.netloc.lower() or "local"
            counts[domain] = counts.get(domain, 0) + 1
        return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))

    def job_counts(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "select status, count(*) as total from jobs group by status"
            ).fetchall()
        return {str(row["status"]): int(row["total"]) for row in rows}

    def crawl_queue_metrics(self) -> dict[str, int]:
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                """
                select
                    sum(case when status = 'queued' and available_at <= ? then 1 else 0 end) as ready,
                    sum(case when status = 'queued' and available_at > ? then 1 else 0 end) as delayed,
                    sum(case when status = 'running' then 1 else 0 end) as running,
                    sum(case when status = 'cancelling' then 1 else 0 end) as cancelling
                from jobs
                where type = 'crawl'
                """,
                (now, now),
            ).fetchone()
        return {
            "ready": int(row["ready"] or 0),
            "delayed": int(row["delayed"] or 0),
            "running": int(row["running"] or 0),
            "cancelling": int(row["cancelling"] or 0),
        }

    def crawl_failure_metrics(self) -> dict[str, Any]:
        with self._connect() as conn:
            status_rows = conn.execute(
                "select status, count(*) as total from crawl_failures group by status"
            ).fetchall()
            type_rows = conn.execute(
                """
                select error_type, count(*) as total
                from crawl_failures
                where status = 'open'
                group by error_type
                """
            ).fetchall()
            retryable_open = conn.execute(
                """
                select count(*) as total from crawl_failures
                where status = 'open' and retryable = 1
                """
            ).fetchone()
        by_status = {str(row["status"]): int(row["total"]) for row in status_rows}
        return {
            "by_status": by_status,
            "open_retryable": int(retryable_open["total"] or 0),
            "open_by_error_type": {str(row["error_type"]): int(row["total"]) for row in type_rows},
        }

    def usage_by_endpoint(self) -> dict[str, int]:
        with self._connect() as conn:
            rows = conn.execute(
                "select endpoint, coalesce(sum(units), 0) as total from usage_events group by endpoint"
            ).fetchall()
        return {str(row["endpoint"]): int(row["total"]) for row in rows}
