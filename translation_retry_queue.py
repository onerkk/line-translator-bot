"""Durable, lease-based work queue for translation delivery.

The queue is the availability boundary for text and media translation jobs:

* jobs survive process restarts;
* multiple Gunicorn workers cannot process the same job concurrently;
* leases expire automatically after a worker crash;
* retries have no terminal exhausted state;
* extracted source and prepared deliveries survive transport failures.

The public API keeps the v1 helpers used by older application code while adding
``claim_due_jobs`` for safe multi-process workers and ``job_kind`` for media
reprocessing.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
import os
import sqlite3
import threading
import time
import uuid
from threading import Thread as _LeaseThread
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOCK = threading.RLock()
_SCHEMA_VERSION = 2


def _default_db_path() -> str:
    configured = str(os.environ.get("TRANSLATION_RETRY_DB_PATH", "") or "").strip()
    if configured:
        return configured
    for directory in ("/var/data", "/data", "/tmp"):
        try:
            path = Path(directory)
            if path.is_dir() and os.access(str(path), os.W_OK):
                return str(path / "translation_retry_queue.db")
        except Exception:
            continue
    return str(Path(__file__).with_name("translation_retry_queue.db"))


DB_PATH = _default_db_path()


class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def _connect() -> sqlite3.Connection:
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None, factory=_ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {str(row[1]) for row in conn.execute("PRAGMA table_info(translation_retry_jobs)")}


def initialize() -> None:
    """Create or migrate the queue schema without dropping pending v1 jobs."""
    with _LOCK, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS translation_retry_jobs (
                    job_key TEXT PRIMARY KEY,
                    job_kind TEXT NOT NULL DEFAULT 'text',
                    payload_json TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_error TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    lease_owner TEXT NOT NULL DEFAULT '',
                    lease_until REAL NOT NULL DEFAULT 0,
                    schema_version INTEGER NOT NULL DEFAULT 2
                )
                """
            )
            cols = _columns(conn)
            migrations = {
                "job_kind": "ALTER TABLE translation_retry_jobs ADD COLUMN job_kind TEXT NOT NULL DEFAULT 'text'",
                "lease_owner": "ALTER TABLE translation_retry_jobs ADD COLUMN lease_owner TEXT NOT NULL DEFAULT ''",
                "lease_until": "ALTER TABLE translation_retry_jobs ADD COLUMN lease_until REAL NOT NULL DEFAULT 0",
            }
            for name, sql in migrations.items():
                if name not in cols:
                    conn.execute(sql)
            conn.execute(
                "UPDATE translation_retry_jobs SET status='pending', lease_owner='', lease_until=0 "
                "WHERE status NOT IN ('pending','leased') OR status IS NULL"
            )
            conn.execute(
                "UPDATE translation_retry_jobs SET schema_version=? WHERE schema_version < ?",
                (_SCHEMA_VERSION, _SCHEMA_VERSION),
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_translation_retry_due "
                "ON translation_retry_jobs(status, next_attempt_at, lease_until)"
            )
            conn.execute("CREATE TABLE IF NOT EXISTS translation_delivery_receipts "
                         "(job_key TEXT PRIMARY KEY, delivered_at REAL NOT NULL)")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def enqueue(
    job_key: str,
    payload: Dict[str, Any],
    *,
    delay_seconds: float = 0.0,
    job_kind: Optional[str] = None,
) -> bool:
    """Insert or refresh one job; return ``True`` only for a new row.

    A repeated webhook never overwrites extracted source, a prepared delivery,
    or completed target languages. Only checkpoint() may update existing work.
    """
    initialize()
    key = str(job_key or "").strip()
    if not key:
        raise ValueError("job_key is required")
    body_payload = payload if isinstance(payload, dict) else {}
    kind = str(job_kind or body_payload.get("job_kind") or "text").strip() or "text"
    now = time.time()
    due = now + max(0.0, float(delay_seconds or 0.0))
    body = json.dumps(body_payload, ensure_ascii=False, separators=(",", ":"))
    with _LOCK, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute("SELECT 1 FROM translation_delivery_receipts WHERE job_key=? AND delivered_at>?",
                            (key, now - 7 * 86400)).fetchone():
                conn.execute("COMMIT")
                return False
            row = conn.execute(
                "SELECT job_key, status, next_attempt_at FROM translation_retry_jobs WHERE job_key=?",
                (key,),
            ).fetchone()
            inserted = row is None
            if inserted:
                conn.execute(
                    """
                    INSERT INTO translation_retry_jobs
                        (job_key, job_kind, payload_json, attempts, next_attempt_at,
                         created_at, updated_at, last_error, status, lease_owner,
                         lease_until, schema_version)
                    VALUES (?, ?, ?, 0, ?, ?, ?, '', 'pending', '', 0, ?)
                    """,
                    (key, kind, body, due, now, now, _SCHEMA_VERSION),
                )
            else:
                status = str(row["status"] or "pending")
                current_due = float(row["next_attempt_at"] or due)
                conn.execute(
                    """
                    UPDATE translation_retry_jobs
                       SET next_attempt_at=?, updated_at=?,
                           status=CASE WHEN status='leased' THEN 'leased' ELSE 'pending' END,
                           schema_version=?
                     WHERE job_key=?
                    """,
                    (min(current_due, due) if status == "pending" else current_due,
                     now, _SCHEMA_VERSION, key),
                )
            conn.execute("COMMIT")
            return inserted
        except Exception:
            conn.execute("ROLLBACK")
            raise


def checkpoint(job_key: str, updates: Dict[str, Any], *, owner: Optional[str] = None) -> bool:
    """Persist completed work without resetting attempts, due time or a lease.

    A retrying worker may write only while it owns the live lease. The immediate
    handler may checkpoint only before any worker has claimed the pending job.
    """
    initialize()
    with _LOCK, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT payload_json,status,lease_owner,lease_until FROM translation_retry_jobs WHERE job_key=?",
            (str(job_key),),
        ).fetchone()
        if row is None:
            return False
        if owner:
            if row["status"] != "leased" or row["lease_owner"] != owner or row["lease_until"] <= time.time():
                return False
        elif row["status"] != "pending":
            return False
        payload = json.loads(row["payload_json"])
        payload.update(updates)
        conn.execute(
            "UPDATE translation_retry_jobs SET payload_json=?,updated_at=? WHERE job_key=?",
            (json.dumps(payload, ensure_ascii=False, separators=(",", ":")), time.time(), str(job_key)),
        )
    return True


def get(job_key: str) -> Optional[Dict[str, Any]]:
    initialize()
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM translation_retry_jobs WHERE job_key=?",
            (str(job_key),),
        ).fetchone()
    return _row_to_job(row) if row else None


def list_pending(*, limit: int = 500) -> List[Dict[str, Any]]:
    """Return all outstanding jobs, including currently leased jobs."""
    initialize()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM translation_retry_jobs
             WHERE status IN ('pending','leased')
             ORDER BY next_attempt_at ASC, created_at ASC
             LIMIT ?
            """,
            (max(1, min(int(limit or 500), 5000)),),
        ).fetchall()
    return [_row_to_job(row) for row in rows]


def due_jobs(*, now: Optional[float] = None, limit: int = 20) -> List[Dict[str, Any]]:
    """Compatibility read-only due query; workers should use ``claim_due_jobs``."""
    initialize()
    ts = time.time() if now is None else float(now)
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM translation_retry_jobs
             WHERE (status='pending' AND next_attempt_at<=?)
                OR (status='leased' AND lease_until<=?)
             ORDER BY next_attempt_at ASC, created_at ASC
             LIMIT ?
            """,
            (ts, ts, max(1, min(int(limit or 20), 200))),
        ).fetchall()
    return [_row_to_job(row) for row in rows]


def claim_due_jobs(
    *,
    owner: Optional[str] = None,
    now: Optional[float] = None,
    limit: int = 20,
    lease_seconds: float = 180.0,
) -> List[Dict[str, Any]]:
    """Atomically lease due jobs to one worker.

    Expired leases are reclaimable.  This is the key difference from the old
    in-memory worker and prevents duplicate provider calls across Gunicorn
    processes while guaranteeing crash recovery.
    """
    initialize()
    ts = time.time() if now is None else float(now)
    worker = str(owner or f"pid-{os.getpid()}-{uuid.uuid4().hex[:12]}")
    lim = max(1, min(int(limit or 20), 200))
    lease_until = ts + max(15.0, float(lease_seconds or 180.0))
    with _LOCK, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            rows = conn.execute(
                """
                SELECT job_key FROM translation_retry_jobs
                 WHERE (status='pending' AND next_attempt_at<=?)
                    OR (status='leased' AND lease_until<=?)
                 ORDER BY next_attempt_at ASC, created_at ASC
                 LIMIT ?
                """,
                (ts, ts, lim),
            ).fetchall()
            keys = [str(row["job_key"]) for row in rows]
            for key in keys:
                conn.execute(
                    """
                    UPDATE translation_retry_jobs
                       SET status='leased', lease_owner=?, lease_until=?, updated_at=?
                     WHERE job_key=?
                       AND ((status='pending' AND next_attempt_at<=?)
                         OR (status='leased' AND lease_until<=?))
                    """,
                    (worker, lease_until, ts, key, ts, ts),
                )
            claimed = []
            if keys:
                placeholders = ",".join("?" for _ in keys)
                claimed = conn.execute(
                    f"SELECT * FROM translation_retry_jobs WHERE lease_owner=? "
                    f"AND status='leased' AND job_key IN ({placeholders}) "
                    "ORDER BY next_attempt_at ASC, created_at ASC",
                    (worker, *keys),
                ).fetchall()
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return [_row_to_job(row) for row in claimed]


def renew_lease(job_key: str, *, owner: str, lease_seconds: float = 180.0) -> bool:
    initialize()
    now = time.time()
    with _LOCK, _connect() as conn:
        cur = conn.execute(
            """
            UPDATE translation_retry_jobs
               SET lease_until=?, updated_at=?
             WHERE job_key=? AND status='leased' AND lease_owner=? AND lease_until>?
            """,
            (now + max(15.0, float(lease_seconds or 180.0)), now, str(job_key), str(owner), now),
        )
    return bool(cur.rowcount)


class LeaseLostError(RuntimeError):
    pass


def claim_job(job_key: str, *, owner: str, lease_seconds: float = 240.0) -> bool:
    """Claim a foreground intent, even before its delayed retry due time."""
    initialize()
    now = time.time()
    with _LOCK, _connect() as conn:
        cur = conn.execute(
            "UPDATE translation_retry_jobs SET status='leased',lease_owner=?,lease_until=?,updated_at=? "
            "WHERE job_key=? AND (status='pending' OR (status='leased' AND lease_until<=?))",
            (str(owner), now + max(15.0, lease_seconds), now, str(job_key), now),
        )
    return bool(cur.rowcount)


@contextmanager
def maintain_lease(job_key: str, *, owner: str, lease_seconds: float = 240.0):
    """Renew only this active job, stopping immediately when its scope exits."""
    stop = threading.Event()
    lost = threading.Event()
    interval = max(1.0, min(30.0, lease_seconds / 3.0))

    def ensure_owned():
        if lost.is_set() or not renew_lease(job_key, owner=owner, lease_seconds=lease_seconds):
            lost.set()
            raise LeaseLostError("translation job lease is no longer owned")

    def heartbeat():
        while not stop.wait(interval):
            try:
                ensure_owned()
            except Exception:
                lost.set()
                return

    ensure_owned()
    worker = _LeaseThread(target=heartbeat, name="translation-job-lease", daemon=True)
    worker.start()
    try:
        yield ensure_owned
    finally:
        stop.set()
        worker.join(timeout=1.0)


def reschedule(job_key: str, *, delay_seconds: float, error: str = "", owner: Optional[str] = None) -> bool:
    """Release a job back to pending and increment attempt count."""
    initialize()
    now = time.time()
    params: list[Any] = [
        now + max(1.0, float(delay_seconds or 1.0)),
        now,
        str(error or "")[:2000],
        str(job_key),
    ]
    owner_clause = " AND status='pending'"
    if owner:
        owner_clause = " AND status='leased' AND lease_owner=? AND lease_until>?"
        params.extend((str(owner), now))
    with _LOCK, _connect() as conn:
        cur = conn.execute(
            """
            UPDATE translation_retry_jobs
               SET attempts=attempts+1, next_attempt_at=?, updated_at=?,
                   last_error=?, status='pending', lease_owner='', lease_until=0
             WHERE job_key=?
            """ + owner_clause,
            tuple(params),
        )
    return bool(cur.rowcount)


def mark_delivered(job_key: str, *, owner: Optional[str] = None) -> bool:
    initialize()
    params: list[Any] = [str(job_key)]
    now = time.time()
    owner_clause = " AND status='pending'"
    if owner:
        owner_clause = " AND status='leased' AND lease_owner=? AND lease_until>?"
        params.extend((str(owner), now))
    with _LOCK, _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.execute(
            "DELETE FROM translation_retry_jobs WHERE job_key=?" + owner_clause,
            tuple(params),
        )
        if cur.rowcount:
            conn.execute("INSERT OR REPLACE INTO translation_delivery_receipts VALUES (?,?)",
                         (str(job_key), now))
            conn.execute("DELETE FROM translation_delivery_receipts WHERE delivered_at<?",
                         (now - 7 * 86400,))
    return bool(cur.rowcount)


def was_delivered(job_key: str) -> bool:
    initialize()
    with _LOCK, _connect() as conn:
        return bool(conn.execute(
            "SELECT 1 FROM translation_delivery_receipts WHERE job_key=? AND delivered_at>?",
            (str(job_key), time.time() - 7 * 86400),
        ).fetchone())


def remove(job_key: str) -> None:
    mark_delivered(job_key)


def pending_count() -> int:
    initialize()
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM translation_retry_jobs "
            "WHERE status IN ('pending','leased')"
        ).fetchone()
    return int(row["n"] if row else 0)


def _row_to_job(row: sqlite3.Row) -> Dict[str, Any]:
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except Exception:
        payload = {}
    keys = set(row.keys())
    return {
        "job_key": str(row["job_key"]),
        "job_kind": str(row["job_kind"] if "job_kind" in keys else "text"),
        "payload": payload if isinstance(payload, dict) else {},
        "attempts": int(row["attempts"] or 0),
        "next_attempt_at": float(row["next_attempt_at"] or 0.0),
        "created_at": float(row["created_at"] or 0.0),
        "updated_at": float(row["updated_at"] or 0.0),
        "last_error": str(row["last_error"] or ""),
        "status": str(row["status"] or "pending"),
        "lease_owner": str(row["lease_owner"] if "lease_owner" in keys else ""),
        "lease_until": float(row["lease_until"] if "lease_until" in keys else 0.0),
    }
