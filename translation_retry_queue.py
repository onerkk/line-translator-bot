"""Durable queue for translations that could not be delivered immediately.

The queue intentionally contains only source text and LINE delivery metadata.
Translation output is never stored until it has been successfully pushed.  Jobs
are deduplicated by a stable key and remain pending across process restarts.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

_LOCK = threading.RLock()
_SCHEMA_VERSION = 1


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


def _connect() -> sqlite3.Connection:
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def initialize() -> None:
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS translation_retry_jobs (
                job_key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                last_error TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                schema_version INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_translation_retry_due "
            "ON translation_retry_jobs(status, next_attempt_at)"
        )
        conn.commit()


def enqueue(job_key: str, payload: Dict[str, Any], *, delay_seconds: float = 0.0) -> bool:
    """Insert or refresh one pending job. Returns True when newly inserted."""
    initialize()
    now = time.time()
    due = now + max(0.0, float(delay_seconds or 0.0))
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT job_key, status FROM translation_retry_jobs WHERE job_key = ?",
            (str(job_key),),
        ).fetchone()
        inserted = row is None
        if inserted:
            conn.execute(
                """
                INSERT INTO translation_retry_jobs
                    (job_key, payload_json, attempts, next_attempt_at, created_at,
                     updated_at, last_error, status, schema_version)
                VALUES (?, ?, 0, ?, ?, ?, '', 'pending', ?)
                """,
                (str(job_key), body, due, now, now, _SCHEMA_VERSION),
            )
        else:
            # Preserve attempt history, but revive a stale/non-pending row and
            # refresh payload fields such as a newer quote token.
            conn.execute(
                """
                UPDATE translation_retry_jobs
                   SET payload_json = ?,
                       next_attempt_at = CASE
                           WHEN status = 'pending' THEN MIN(next_attempt_at, ?)
                           ELSE ?
                       END,
                       updated_at = ?,
                       status = 'pending'
                 WHERE job_key = ?
                """,
                (body, due, due, now, str(job_key)),
            )
        conn.commit()
        return inserted


def get(job_key: str) -> Optional[Dict[str, Any]]:
    initialize()
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT * FROM translation_retry_jobs WHERE job_key = ? AND status = 'pending'",
            (str(job_key),),
        ).fetchone()
    return _row_to_job(row) if row else None


def list_pending(*, limit: int = 500) -> List[Dict[str, Any]]:
    initialize()
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM translation_retry_jobs
             WHERE status = 'pending'
             ORDER BY next_attempt_at ASC, created_at ASC
             LIMIT ?
            """,
            (max(1, min(int(limit or 500), 5000)),),
        ).fetchall()
    return [_row_to_job(row) for row in rows]


def due_jobs(*, now: Optional[float] = None, limit: int = 20) -> List[Dict[str, Any]]:
    initialize()
    ts = time.time() if now is None else float(now)
    with _LOCK, _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM translation_retry_jobs
             WHERE status = 'pending' AND next_attempt_at <= ?
             ORDER BY next_attempt_at ASC, created_at ASC
             LIMIT ?
            """,
            (ts, max(1, min(int(limit or 20), 200))),
        ).fetchall()
    return [_row_to_job(row) for row in rows]


def reschedule(job_key: str, *, delay_seconds: float, error: str = "") -> None:
    initialize()
    now = time.time()
    with _LOCK, _connect() as conn:
        conn.execute(
            """
            UPDATE translation_retry_jobs
               SET attempts = attempts + 1,
                   next_attempt_at = ?,
                   updated_at = ?,
                   last_error = ?,
                   status = 'pending'
             WHERE job_key = ?
            """,
            (
                now + max(1.0, float(delay_seconds or 1.0)),
                now,
                str(error or "")[:1000],
                str(job_key),
            ),
        )
        conn.commit()


def mark_delivered(job_key: str) -> None:
    initialize()
    with _LOCK, _connect() as conn:
        conn.execute("DELETE FROM translation_retry_jobs WHERE job_key = ?", (str(job_key),))
        conn.commit()


def remove(job_key: str) -> None:
    mark_delivered(job_key)


def pending_count() -> int:
    initialize()
    with _LOCK, _connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM translation_retry_jobs WHERE status = 'pending'"
        ).fetchone()
    return int(row["n"] if row else 0)


def _row_to_job(row: sqlite3.Row) -> Dict[str, Any]:
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except Exception:
        payload = {}
    return {
        "job_key": str(row["job_key"]),
        "payload": payload if isinstance(payload, dict) else {},
        "attempts": int(row["attempts"] or 0),
        "next_attempt_at": float(row["next_attempt_at"] or 0.0),
        "created_at": float(row["created_at"] or 0.0),
        "updated_at": float(row["updated_at"] or 0.0),
        "last_error": str(row["last_error"] or ""),
        "status": str(row["status"] or "pending"),
    }
