"""Moderated human-in-the-loop translation corrections.

Corrections are evidence, not automatically ground truth. A worker report is
stored as ``pending`` and cannot affect prompts, lexical TM or vector TM until a
reviewer approves it. Approval is idempotent; rejection/deletion removes only
the exact approved asset when it is still the active human-corrected value.

Existing databases are migrated in place. Rows created by older releases are
marked ``approved`` to preserve their historical behaviour.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional


logger = logging.getLogger(__name__)

ACTIVE_LEARNING_BUILD_ID = "2026-08-18.1-moderated-corrections"
VALID_STATUSES = frozenset({"pending", "approved", "rejected"})

AL_DB_PATH: Optional[str] = None
_init_done = False
_lock = threading.RLock()

_stats = {
    "corrections_submitted": 0,
    "pending_submitted": 0,
    "approved": 0,
    "rejected": 0,
    "duplicates_ignored": 0,
    "tm_updated": 0,
    "vec_tm_updated": 0,
    "errors": 0,
}


def _resolve_db_path() -> str:
    env = os.environ.get("ACTIVE_LEARNING_DB_PATH", "").strip()
    if env:
        return env
    for directory in ("/var/data", "/data", "/tmp"):
        if os.path.isdir(directory) and os.access(directory, os.W_OK):
            return os.path.join(directory, "active_learning.db")
    return "active_learning.db"


def init() -> None:
    """Create or migrate the correction queue without losing old rows."""
    global AL_DB_PATH, _init_done
    if _init_done:
        return
    AL_DB_PATH = _resolve_db_path()
    try:
        with sqlite3.connect(AL_DB_PATH) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS corrections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    src_lang TEXT NOT NULL,
                    tgt_lang TEXT NOT NULL,
                    src_text TEXT NOT NULL,
                    src_text_hash TEXT NOT NULL,
                    original_translation TEXT NOT NULL,
                    corrected_translation TEXT NOT NULL,
                    correction_reason TEXT,
                    corrected_by TEXT,
                    group_id TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    approved_by TEXT,
                    approved_at INTEGER,
                    rejected_reason TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                """
            )
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(corrections)").fetchall()
            }
            # Old rows already influenced translation. Mark them approved so a
            # deployment does not silently erase established terminology.
            migrations = (
                ("status", "ALTER TABLE corrections ADD COLUMN status TEXT NOT NULL DEFAULT 'approved'"),
                ("approved_by", "ALTER TABLE corrections ADD COLUMN approved_by TEXT"),
                ("approved_at", "ALTER TABLE corrections ADD COLUMN approved_at INTEGER"),
                ("rejected_reason", "ALTER TABLE corrections ADD COLUMN rejected_reason TEXT"),
                ("updated_at", "ALTER TABLE corrections ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0"),
            )
            for column, statement in migrations:
                if column not in columns:
                    conn.execute(statement)
            conn.execute(
                "UPDATE corrections SET status='approved' "
                "WHERE status IS NULL OR status NOT IN ('pending','approved','rejected')"
            )
            conn.execute(
                "UPDATE corrections SET updated_at=created_at WHERE updated_at IS NULL OR updated_at=0"
            )
            conn.execute(
                "UPDATE corrections SET approved_at=created_at "
                "WHERE status='approved' AND approved_at IS NULL"
            )
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_al_lang ON corrections(src_lang, tgt_lang);
                CREATE INDEX IF NOT EXISTS idx_al_hash ON corrections(src_text_hash);
                CREATE INDEX IF NOT EXISTS idx_al_created ON corrections(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_al_status ON corrections(status, updated_at DESC);
                """
            )
        _init_done = True
        logger.info("[AL] moderated queue init OK, db=%s", AL_DB_PATH)
    except Exception as exc:
        logger.error("[AL] init failed: %s", exc)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def _row(correction_id: int) -> Optional[Dict[str, Any]]:
    if not _init_done:
        init()
    try:
        with sqlite3.connect(AL_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            found = conn.execute(
                "SELECT * FROM corrections WHERE id=?", (int(correction_id),)
            ).fetchone()
            return dict(found) if found else None
    except Exception as exc:
        logger.error("[AL] row lookup failed: %s", exc)
        return None


def _vector_sync_enabled() -> bool:
    # Exact TM + approved casebook already solve repeated sentences. Vector
    # embeddings cost money and can over-generalise a correction, so opt in.
    return os.environ.get("ACTIVE_LEARNING_VECTOR_SYNC", "0").strip().lower() in {
        "1", "true", "on", "yes",
    }


def _sync_approved_assets(row: Dict[str, Any]) -> Dict[str, Any]:
    tm_updated = False
    vec_updated = False
    vec_skipped = not _vector_sync_enabled()
    try:
        import translation_memory as tm_module

        tm_updated = tm_module.tm_store(
            row["src_text"], row["corrected_translation"],
            row["src_lang"], row["tgt_lang"], row.get("group_id") or "",
            model="human_corrected", quality_score=100.0,
        )
        if tm_updated:
            with _lock:
                _stats["tm_updated"] += 1
    except Exception as exc:
        logger.warning("[AL] tm_store failed: %s", exc)

    if not vec_skipped:
        try:
            import vector_tm as vector_module

            vec_updated = vector_module.vector_store(
                row["src_text"], row["corrected_translation"],
                row["src_lang"], row["tgt_lang"], row.get("group_id") or "",
                model="human_corrected", quality_score=100.0,
            )
            if vec_updated:
                with _lock:
                    _stats["vec_tm_updated"] += 1
        except Exception as exc:
            logger.warning("[AL] vector_store failed: %s", exc)
    return {
        "tm_updated": bool(tm_updated),
        "vec_updated": bool(vec_updated),
        "vec_skipped": bool(vec_skipped),
    }


def _rollback_approved_assets(row: Dict[str, Any]) -> Dict[str, int]:
    removed = {"tm_removed": 0, "vec_removed": 0}
    try:
        import translation_memory as tm_module

        removed["tm_removed"] = int(tm_module.tm_delete_exact(
            row["src_text"], row["src_lang"], row["tgt_lang"],
            row.get("group_id") or "", model="human_corrected",
            target_text=row["corrected_translation"],
        ))
    except Exception as exc:
        logger.warning("[AL] TM rollback failed: %s", exc)
    try:
        import vector_tm as vector_module

        removed["vec_removed"] = int(vector_module.vector_delete_exact(
            row["src_text"], row["src_lang"], row["tgt_lang"],
            row.get("group_id") or "", model="human_corrected",
            target_text=row["corrected_translation"],
        ))
    except Exception as exc:
        logger.warning("[AL] vector rollback failed: %s", exc)
    return removed


def submit_correction(
    src_text: str,
    original_tgt: str,
    corrected_tgt: str,
    src_lang: str,
    tgt_lang: str,
    correction_reason: Optional[str] = None,
    corrected_by: Optional[str] = None,
    group_id: Optional[str] = None,
    *,
    auto_approve: bool = False,
    approved_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Submit a correction; only explicitly approved rows enter learning assets."""
    if not _init_done:
        init()
    source = str(src_text or "").strip()
    corrected = str(corrected_tgt or "").strip()
    original = str(original_tgt or "").strip()
    source_lang = str(src_lang or "").strip().lower()
    target_lang = str(tgt_lang or "").strip().lower()
    scope = str(group_id or "")
    if not source or not corrected or not source_lang or not target_lang:
        return {"ok": False, "error": "原文、修正版與語言不可為空"}

    source_hash = _hash_text(source)
    now = int(time.time())
    status = "approved" if auto_approve else "pending"
    reviewer = str(approved_by or corrected_by or "").strip() if auto_approve else None

    try:
        with sqlite3.connect(AL_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            duplicate = conn.execute(
                """
                SELECT * FROM corrections
                WHERE src_lang=? AND tgt_lang=? AND src_text_hash=? AND src_text=?
                  AND corrected_translation=? AND group_id=?
                  AND status IN ('pending','approved')
                ORDER BY CASE status WHEN 'approved' THEN 0 ELSE 1 END, updated_at DESC
                LIMIT 1
                """,
                (source_lang, target_lang, source_hash, source, corrected, scope),
            ).fetchone()
            if duplicate:
                with _lock:
                    _stats["duplicates_ignored"] += 1
                duplicate_dict = dict(duplicate)
                if auto_approve and duplicate_dict.get("status") == "pending":
                    conn.execute(
                        """
                        UPDATE corrections
                        SET status='approved', approved_by=?, approved_at=?,
                            rejected_reason=NULL, updated_at=?
                        WHERE id=?
                        """,
                        (reviewer, now, now, int(duplicate_dict["id"])),
                    )
                    conn.commit()
                    duplicate_dict.update({
                        "status": "approved", "approved_by": reviewer,
                        "approved_at": now, "updated_at": now,
                    })
                    assets = _sync_approved_assets(duplicate_dict)
                    with _lock:
                        _stats["approved"] += 1
                    return {
                        "ok": True,
                        "duplicate": True,
                        "promoted_from_pending": True,
                        "correction_id": duplicate_dict["id"],
                        "status": "approved",
                        **assets,
                    }
                return {
                    "ok": True,
                    "duplicate": True,
                    "correction_id": duplicate_dict["id"],
                    "status": duplicate_dict["status"],
                    "tm_updated": False,
                    "vec_updated": False,
                    "vec_skipped": True,
                }

            cursor = conn.execute(
                """
                INSERT INTO corrections
                    (src_lang, tgt_lang, src_text, src_text_hash,
                     original_translation, corrected_translation,
                     correction_reason, corrected_by, group_id, status,
                     approved_by, approved_at, rejected_reason, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    source_lang, target_lang, source, source_hash, original, corrected,
                    correction_reason, corrected_by, scope, status,
                    reviewer, now if auto_approve else None, None, now, now,
                ),
            )
            correction_id = int(cursor.lastrowid)
    except Exception as exc:
        logger.error("[AL] submit failed: %s", exc)
        with _lock:
            _stats["errors"] += 1
        return {"ok": False, "error": str(exc)}

    with _lock:
        _stats["corrections_submitted"] += 1
        _stats["approved" if auto_approve else "pending_submitted"] += 1
    asset_result = {
        "tm_updated": False,
        "vec_updated": False,
        "vec_skipped": True,
    }
    if auto_approve:
        asset_result = _sync_approved_assets(_row(correction_id) or {
            "src_text": source, "corrected_translation": corrected,
            "src_lang": source_lang, "tgt_lang": target_lang, "group_id": scope,
        })
    logger.info("[AL] correction submitted id=%d status=%s", correction_id, status)
    return {
        "ok": True,
        "correction_id": correction_id,
        "status": status,
        **asset_result,
    }


def approve_correction(correction_id: int, approved_by: Optional[str] = None) -> Dict[str, Any]:
    if not _init_done:
        init()
    row = _row(correction_id)
    if not row:
        return {"ok": False, "error": "correction_not_found"}
    if row.get("status") == "approved":
        return {
            "ok": True, "correction_id": int(correction_id), "status": "approved",
            "already_reviewed": True, "tm_updated": False,
            "vec_updated": False, "vec_skipped": not _vector_sync_enabled(),
        }
    now = int(time.time())
    try:
        with sqlite3.connect(AL_DB_PATH) as conn:
            conn.execute(
                """
                UPDATE corrections
                SET status='approved', approved_by=?, approved_at=?,
                    rejected_reason=NULL, updated_at=?
                WHERE id=?
                """,
                (str(approved_by or "").strip() or None, now, now, int(correction_id)),
            )
        row.update({"status": "approved", "approved_by": approved_by, "approved_at": now})
        assets = _sync_approved_assets(row)
        with _lock:
            _stats["approved"] += 1
        return {"ok": True, "correction_id": int(correction_id), "status": "approved", **assets}
    except Exception as exc:
        logger.error("[AL] approve failed: %s", exc)
        with _lock:
            _stats["errors"] += 1
        return {"ok": False, "error": str(exc)}


def reject_correction(
    correction_id: int,
    rejected_by: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    if not _init_done:
        init()
    row = _row(correction_id)
    if not row:
        return {"ok": False, "error": "correction_not_found"}
    if row.get("status") == "rejected":
        return {
            "ok": True, "correction_id": int(correction_id), "status": "rejected",
            "already_reviewed": True, "tm_removed": 0, "vec_removed": 0,
        }
    old_status = row.get("status")
    now = int(time.time())
    try:
        with sqlite3.connect(AL_DB_PATH) as conn:
            conn.execute(
                """
                UPDATE corrections
                SET status='rejected', approved_by=?, approved_at=NULL,
                    rejected_reason=?, updated_at=?
                WHERE id=?
                """,
                (
                    str(rejected_by or "").strip() or None,
                    str(reason or "").strip() or None,
                    now,
                    int(correction_id),
                ),
            )
        rollback = _rollback_approved_assets(row) if old_status == "approved" else {
            "tm_removed": 0, "vec_removed": 0,
        }
        with _lock:
            _stats["rejected"] += 1
        return {"ok": True, "correction_id": int(correction_id), "status": "rejected", **rollback}
    except Exception as exc:
        logger.error("[AL] reject failed: %s", exc)
        with _lock:
            _stats["errors"] += 1
        return {"ok": False, "error": str(exc)}


def list_corrections(
    limit: int = 100,
    offset: int = 0,
    src_lang: Optional[str] = None,
    tgt_lang: Optional[str] = None,
    keyword: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not _init_done:
        init()
    where: List[str] = []
    params: List[Any] = []
    if src_lang:
        where.append("src_lang=?")
        params.append(src_lang)
    if tgt_lang:
        where.append("tgt_lang=?")
        params.append(tgt_lang)
    if keyword:
        where.append("(src_text LIKE ? OR corrected_translation LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    if status:
        normalized = str(status).strip().lower()
        if normalized not in VALID_STATUSES:
            return []
        where.append("status=?")
        params.append(normalized)
    sql = "SELECT * FROM corrections"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?"
    params.extend([max(1, int(limit)), max(0, int(offset))])
    try:
        with sqlite3.connect(AL_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except Exception as exc:
        logger.error("[AL] list failed: %s", exc)
        return []


def delete_correction(correction_id: int) -> bool:
    """Delete a queue row and safely remove its exact approved learning asset."""
    if not _init_done:
        init()
    row = _row(correction_id)
    if not row:
        return False
    try:
        with sqlite3.connect(AL_DB_PATH) as conn:
            cursor = conn.execute("DELETE FROM corrections WHERE id=?", (int(correction_id),))
        if cursor.rowcount > 0 and row.get("status") == "approved":
            _rollback_approved_assets(row)
        return cursor.rowcount > 0
    except Exception as exc:
        logger.error("[AL] delete failed: %s", exc)
        return False


def al_stats() -> Dict[str, Any]:
    if not _init_done:
        init()
    with _lock:
        stats = dict(_stats)
    try:
        with sqlite3.connect(AL_DB_PATH) as conn:
            stats["total_corrections"] = conn.execute(
                "SELECT COUNT(*) FROM corrections"
            ).fetchone()[0]
            status_rows = conn.execute(
                "SELECT status, COUNT(*) FROM corrections GROUP BY status"
            ).fetchall()
            stats["by_status"] = {str(row[0]): int(row[1]) for row in status_rows}
            stats["pending_corrections"] = stats["by_status"].get("pending", 0)
            stats["approved_corrections"] = stats["by_status"].get("approved", 0)
            stats["rejected_corrections"] = stats["by_status"].get("rejected", 0)
            top_correctors = conn.execute(
                "SELECT corrected_by, COUNT(*) AS c FROM corrections "
                "WHERE corrected_by IS NOT NULL GROUP BY corrected_by ORDER BY c DESC LIMIT 5"
            ).fetchall()
            stats["top_correctors"] = [
                {"by": row[0], "count": row[1]} for row in top_correctors
            ]
    except Exception as exc:
        logger.warning("[AL] stats failed: %s", exc)
    stats["vector_sync_enabled"] = _vector_sync_enabled()
    stats["build_id"] = ACTIVE_LEARNING_BUILD_ID
    return stats


init()
