"""Safe continuous learning for factory translation.

The loop deliberately separates *evidence* from *truth*:

* worker feedback is queued and cannot affect translation before approval;
* every correction is checked by the same local integrity/semantic boundary;
* approved revisions become exact verified TM plus a provider-free semantic
  casebook, while older revisions remain recoverable;
* validation failures are learned only as review-risk patterns. They can make a
  similar future sentence receive an independent source review, but they can
  never provide a target sentence by themselves;
* all transitions and quality interventions are auditable in SQLite.

This gives useful online learning without unsupervised model training or the
failure mode where a model treats its own earlier output as truth.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import sqlite3
import threading
import time
import unicodedata
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


logger = logging.getLogger(__name__)

ACTIVE_LEARNING_API_VERSION = 2
ACTIVE_LEARNING_BUILD_ID = "2026-08-30.1-safe-continuous-learning"
VALID_STATUSES = frozenset({
    "pending", "approved", "rejected", "superseded", "quarantined",
})
_SUPPORTED_DIRECTIONS = {("zh", "id"), ("id", "zh")}
_CANONICAL_STRIP_RE = re.compile(r"[^0-9a-z\u3400-\u9fff%+./_@#-]+", re.I)
_LATIN_WORD_RE = re.compile(r"[a-z0-9]+(?:[-_/][a-z0-9]+)*", re.I)
_HAN_RE = re.compile(r"[\u3400-\u9fff]")

AL_DB_PATH: Optional[str] = None
_init_done = False
_lock = threading.RLock()

_stats = {
    "corrections_submitted": 0,
    "pending_submitted": 0,
    "approved": 0,
    "rejected": 0,
    "superseded": 0,
    "restored": 0,
    "quarantined": 0,
    "duplicates_ignored": 0,
    "validation_blocked": 0,
    "tm_updated": 0,
    "vec_tm_updated": 0,
    "learning_events": 0,
    "risk_reviews_triggered": 0,
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


def _connect() -> sqlite3.Connection:
    if not AL_DB_PATH:
        raise RuntimeError("active_learning database path is not initialized")
    conn = sqlite3.connect(AL_DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def _lang(value: Any) -> str:
    low = str(value or "").strip().lower().replace("_", "-")
    if low.startswith("zh"):
        return "zh"
    if low.startswith("id"):
        return "id"
    return low.split("-", 1)[0]


def canonical_source_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("\u3000", " ")
    return _CANONICAL_STRIP_RE.sub("", text)


def _hash_text(text: str) -> str:
    return hashlib.sha256(str(text or "").strip().encode("utf-8")).hexdigest()[:16]


def _canonical_hash(text: str) -> str:
    return _hash_text(canonical_source_key(text))


def _json_list(values: Iterable[Any], *, limit: int = 32) -> str:
    cleaned = []
    for value in values or ():
        item = str(value or "").strip()
        if item and item not in cleaned:
            cleaned.append(item[:240])
        if len(cleaned) >= limit:
            break
    return json.dumps(cleaned, ensure_ascii=False, separators=(",", ":"))


def _read_json_list(value: Any) -> List[str]:
    try:
        loaded = json.loads(str(value or "[]"))
        return [str(item) for item in loaded if str(item).strip()] if isinstance(loaded, list) else []
    except Exception:
        return []


def init() -> None:
    """Create/migrate the correction, audit and adaptive-risk stores."""
    global AL_DB_PATH, _init_done
    if _init_done:
        return
    AL_DB_PATH = _resolve_db_path()
    try:
        with _connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS corrections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    src_lang TEXT NOT NULL,
                    tgt_lang TEXT NOT NULL,
                    src_text TEXT NOT NULL,
                    src_text_hash TEXT NOT NULL,
                    canonical_src_key TEXT DEFAULT '',
                    original_translation TEXT NOT NULL,
                    corrected_translation TEXT NOT NULL,
                    correction_reason TEXT,
                    corrected_by TEXT,
                    group_id TEXT DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    revision INTEGER NOT NULL DEFAULT 1,
                    supersedes_id INTEGER,
                    superseded_by INTEGER,
                    validation_state TEXT NOT NULL DEFAULT 'pending',
                    validation_issues TEXT NOT NULL DEFAULT '[]',
                    approved_policy_fingerprint TEXT DEFAULT '',
                    approved_by TEXT,
                    approved_at INTEGER,
                    rejected_reason TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS learning_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    correction_id INTEGER,
                    src_lang TEXT NOT NULL,
                    tgt_lang TEXT NOT NULL,
                    src_text TEXT NOT NULL,
                    src_text_hash TEXT NOT NULL,
                    group_id TEXT DEFAULT '',
                    candidate_text TEXT DEFAULT '',
                    final_text TEXT DEFAULT '',
                    issues_json TEXT NOT NULL DEFAULT '[]',
                    path TEXT DEFAULT '',
                    reviewed INTEGER DEFAULT 0,
                    cacheable INTEGER DEFAULT 0,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS risk_patterns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    src_lang TEXT NOT NULL,
                    tgt_lang TEXT NOT NULL,
                    src_text TEXT NOT NULL,
                    canonical_hash TEXT NOT NULL,
                    group_id TEXT DEFAULT '',
                    occurrence_count INTEGER NOT NULL DEFAULT 1,
                    risk_weight REAL NOT NULL DEFAULT 0,
                    issue_codes TEXT NOT NULL DEFAULT '[]',
                    last_path TEXT DEFAULT '',
                    first_seen INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL,
                    UNIQUE(src_lang, tgt_lang, canonical_hash, group_id)
                );
                """
            )
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(corrections)").fetchall()
            }
            migrations = (
                ("status", "ALTER TABLE corrections ADD COLUMN status TEXT NOT NULL DEFAULT 'approved'"),
                ("approved_by", "ALTER TABLE corrections ADD COLUMN approved_by TEXT"),
                ("approved_at", "ALTER TABLE corrections ADD COLUMN approved_at INTEGER"),
                ("rejected_reason", "ALTER TABLE corrections ADD COLUMN rejected_reason TEXT"),
                ("updated_at", "ALTER TABLE corrections ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0"),
                ("canonical_src_key", "ALTER TABLE corrections ADD COLUMN canonical_src_key TEXT DEFAULT ''"),
                ("revision", "ALTER TABLE corrections ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"),
                ("supersedes_id", "ALTER TABLE corrections ADD COLUMN supersedes_id INTEGER"),
                ("superseded_by", "ALTER TABLE corrections ADD COLUMN superseded_by INTEGER"),
                ("validation_state", "ALTER TABLE corrections ADD COLUMN validation_state TEXT NOT NULL DEFAULT 'legacy'"),
                ("validation_issues", "ALTER TABLE corrections ADD COLUMN validation_issues TEXT NOT NULL DEFAULT '[]'"),
                ("approved_policy_fingerprint", "ALTER TABLE corrections ADD COLUMN approved_policy_fingerprint TEXT DEFAULT ''"),
            )
            for column, statement in migrations:
                if column not in columns:
                    conn.execute(statement)

            conn.execute(
                "UPDATE corrections SET status='approved' "
                "WHERE status IS NULL OR status NOT IN ('pending','approved','rejected','superseded','quarantined')"
            )
            conn.execute(
                "UPDATE corrections SET updated_at=created_at WHERE updated_at IS NULL OR updated_at=0"
            )
            conn.execute(
                "UPDATE corrections SET approved_at=created_at "
                "WHERE status='approved' AND approved_at IS NULL"
            )
            conn.execute(
                "UPDATE corrections SET validation_state='legacy' "
                "WHERE validation_state IS NULL OR validation_state=''"
            )

            # SQLite cannot reproduce the Unicode canonicalizer. Migrate source
            # keys and stable revision numbers in Python without rewriting text.
            rows = conn.execute(
                "SELECT id,src_lang,tgt_lang,src_text,group_id,canonical_src_key,revision "
                "FROM corrections ORDER BY id"
            ).fetchall()
            revisions: Dict[Tuple[str, str, str, str], int] = {}
            for row in rows:
                key_text = canonical_source_key(row["src_text"])
                revision_key = (
                    _lang(row["src_lang"]), _lang(row["tgt_lang"]),
                    key_text, str(row["group_id"] or ""),
                )
                revisions[revision_key] = revisions.get(revision_key, 0) + 1
                revision = int(row["revision"] or 0)
                if not str(row["canonical_src_key"] or "") or revision <= 0:
                    conn.execute(
                        "UPDATE corrections SET canonical_src_key=?,revision=? WHERE id=?",
                        (key_text, revisions[revision_key], int(row["id"])),
                    )

            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_al_lang ON corrections(src_lang,tgt_lang);
                CREATE INDEX IF NOT EXISTS idx_al_hash ON corrections(src_text_hash);
                CREATE INDEX IF NOT EXISTS idx_al_created ON corrections(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_al_status ON corrections(status,updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_al_revision
                    ON corrections(src_lang,tgt_lang,canonical_src_key,group_id,revision DESC);
                CREATE INDEX IF NOT EXISTS idx_learning_events_time
                    ON learning_events(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_learning_events_direction
                    ON learning_events(src_lang,tgt_lang,group_id,created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_risk_patterns_direction
                    ON risk_patterns(src_lang,tgt_lang,group_id,last_seen DESC);
                """
            )
        _init_done = True
        logger.info("[AL] safe continuous-learning init OK, db=%s", AL_DB_PATH)
    except Exception as exc:
        logger.error("[AL] init failed: %s", exc)


def _row(correction_id: int) -> Optional[Dict[str, Any]]:
    if not _init_done:
        init()
    try:
        with _connect() as conn:
            found = conn.execute(
                "SELECT * FROM corrections WHERE id=?", (int(correction_id),)
            ).fetchone()
            return dict(found) if found else None
    except Exception as exc:
        logger.error("[AL] row lookup failed: %s", exc)
        return None


def _validator_fingerprint() -> str:
    payload: Dict[str, Any] = {"active_learning": ACTIVE_LEARNING_BUILD_ID}
    try:
        import translation_quality_gate as quality_module
        payload["quality_gate"] = getattr(quality_module, "QUALITY_GATE_BUILD_ID", "")
    except Exception:
        payload["quality_gate"] = "unavailable"
    try:
        import factory_translation_guard as guard_module
        payload["factory_guard"] = guard_module.asset_fingerprint()
    except Exception:
        payload["factory_guard"] = "unavailable"
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "human-reviewed:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_correction(
    src_text: str,
    corrected_tgt: str,
    src_lang: str,
    tgt_lang: str,
    *,
    original_tgt: str = "",
) -> Dict[str, Any]:
    """Run a correction through the current deterministic acceptance boundary."""
    source = str(src_text or "").strip()
    corrected = str(corrected_tgt or "").strip()
    original = str(original_tgt or "").strip()
    source_lang = _lang(src_lang)
    target_lang = _lang(tgt_lang)
    issues: List[str] = []
    if not source:
        issues.append("correction_source_empty")
    if not corrected:
        issues.append("correction_target_empty")
    if not source_lang or not target_lang:
        issues.append("correction_language_missing")
    if source_lang and source_lang == target_lang:
        issues.append("correction_language_direction_invalid")
    normalized_corrected = unicodedata.normalize("NFKC", corrected).casefold().strip()
    normalized_original = unicodedata.normalize("NFKC", original).casefold().strip()
    if original and normalized_corrected == normalized_original:
        issues.append("correction_does_not_change_translation")
    if source and normalized_corrected == unicodedata.normalize("NFKC", source).casefold().strip():
        issues.append("correction_unchanged_source")

    if not issues and (source_lang, target_lang) in _SUPPORTED_DIRECTIONS:
        try:
            import translation_quality_gate as quality_module
            report = quality_module.validate_translation(
                source,
                corrected,
                source_lang,
                target_lang,
                require_paragraph_fidelity=False,
            )
            if not report.ok:
                issues.extend("quality_gate:" + str(item) for item in report.hard_issues)
        except Exception as exc:
            issues.append("quality_gate_unavailable:" + type(exc).__name__)
        try:
            import factory_translation_guard as guard_module
            report = guard_module.validate_translation(
                source, corrected, source_lang, target_lang
            )
            if not report.ok:
                issues.extend(str(item) for item in report.hard_issues)
        except Exception as exc:
            issues.append("factory_guard_unavailable:" + type(exc).__name__)

    issues = list(dict.fromkeys(str(item) for item in issues if str(item).strip()))
    return {
        "ok": not issues,
        "issues": issues,
        "policy_fingerprint": _validator_fingerprint(),
        "state": "passed" if not issues else "failed",
    }


def _approval_validation(
    row: Dict[str, Any], *, force: bool, override_reason: Optional[str]
) -> Dict[str, Any]:
    report = validate_correction(
        row.get("src_text", ""),
        row.get("corrected_translation", ""),
        row.get("src_lang", ""),
        row.get("tgt_lang", ""),
        original_tgt=row.get("original_translation", ""),
    )
    if report["ok"]:
        return report
    reason = str(override_reason or "").strip()
    if force and len(reason) >= 8:
        report = dict(report)
        report.update({"ok": True, "state": "override", "override_reason": reason})
        return report
    return report


def _vector_sync_enabled() -> bool:
    # Semantic generalisation is always available through translation_casebook
    # and needs no paid embedding. Vector sync is an optional extra accelerator.
    return os.environ.get("ACTIVE_LEARNING_VECTOR_SYNC", "0").strip().lower() in {
        "1", "true", "on", "yes",
    }


def _notify_learning_changed() -> None:
    try:
        import translation_casebook as casebook_module
        casebook_module.invalidate_active_cache()
    except Exception as exc:
        logger.debug("[AL] casebook cache invalidation skipped: %s", exc)


def _sync_approved_assets(row: Dict[str, Any]) -> Dict[str, Any]:
    tm_updated = False
    vec_updated = False
    vec_skipped = not _vector_sync_enabled()
    fingerprint = str(
        row.get("approved_policy_fingerprint") or _validator_fingerprint()
    )
    try:
        import translation_memory as tm_module
        tm_updated = tm_module.tm_store(
            row["src_text"], row["corrected_translation"],
            row["src_lang"], row["tgt_lang"], row.get("group_id") or "",
            model="human_corrected", quality_score=100.0,
            policy_fingerprint=fingerprint, verified=True,
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
                verified=True, allow_bypass=False,
                policy_fingerprint=fingerprint,
            )
            if vec_updated:
                with _lock:
                    _stats["vec_tm_updated"] += 1
        except Exception as exc:
            logger.warning("[AL] vector_store failed: %s", exc)
    return {
        "tm_updated": bool(tm_updated),
        "semantic_casebook_updated": True,
        "vec_updated": bool(vec_updated),
        "vec_skipped": bool(vec_skipped),
        "policy_fingerprint": fingerprint,
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
    force: bool = False,
    validation_override_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Queue feedback; only a validated explicit approval enters learning."""
    if not _init_done:
        init()
    source = str(src_text or "").strip()
    corrected = str(corrected_tgt or "").strip()
    original = str(original_tgt or "").strip()
    source_lang = _lang(src_lang)
    target_lang = _lang(tgt_lang)
    scope = str(group_id or "")
    if not source or not corrected or not source_lang or not target_lang:
        return {"ok": False, "error": "原文、修正版與語言不可為空"}

    validation = validate_correction(
        source, corrected, source_lang, target_lang, original_tgt=original
    )
    source_hash = _hash_text(source)
    canonical_key = canonical_source_key(source)
    now = int(time.time())

    try:
        with _connect() as conn:
            duplicate = conn.execute(
                """
                SELECT * FROM corrections
                WHERE src_lang=? AND tgt_lang=? AND src_text_hash=? AND src_text=?
                  AND corrected_translation=? AND group_id=?
                  AND status IN ('pending','approved')
                ORDER BY CASE status WHEN 'approved' THEN 0 ELSE 1 END,
                         updated_at DESC LIMIT 1
                """,
                (source_lang, target_lang, source_hash, source, corrected, scope),
            ).fetchone()
    except Exception as exc:
        logger.error("[AL] duplicate lookup failed: %s", exc)
        duplicate = None

    if duplicate:
        with _lock:
            _stats["duplicates_ignored"] += 1
        duplicate_dict = dict(duplicate)
        correction_id = int(duplicate_dict["id"])
        if auto_approve and duplicate_dict.get("status") == "pending":
            promoted = approve_correction(
                correction_id,
                approved_by=approved_by or corrected_by,
                force=force,
                validation_override_reason=validation_override_reason,
            )
            promoted.update({
                "duplicate": True,
                "promoted_from_pending": promoted.get("ok", False),
            })
            return promoted
        return {
            "ok": True,
            "duplicate": True,
            "correction_id": correction_id,
            "status": duplicate_dict["status"],
            "revision": int(duplicate_dict.get("revision") or 1),
            "validation_state": duplicate_dict.get("validation_state") or "legacy",
            "validation_issues": _read_json_list(duplicate_dict.get("validation_issues")),
            "tm_updated": False,
            "semantic_casebook_updated": False,
            "vec_updated": False,
            "vec_skipped": not _vector_sync_enabled(),
        }

    try:
        with _connect() as conn:
            latest = conn.execute(
                """
                SELECT id,status,revision FROM corrections
                WHERE src_lang=? AND tgt_lang=? AND canonical_src_key=? AND group_id=?
                ORDER BY revision DESC,id DESC LIMIT 1
                """,
                (source_lang, target_lang, canonical_key, scope),
            ).fetchone()
            revision = int(latest["revision"] or 0) + 1 if latest else 1
            active = conn.execute(
                """
                SELECT id FROM corrections
                WHERE src_lang=? AND tgt_lang=? AND canonical_src_key=? AND group_id=?
                  AND status='approved'
                ORDER BY revision DESC,id DESC LIMIT 1
                """,
                (source_lang, target_lang, canonical_key, scope),
            ).fetchone()
            cursor = conn.execute(
                """
                INSERT INTO corrections
                    (src_lang,tgt_lang,src_text,src_text_hash,canonical_src_key,
                     original_translation,corrected_translation,correction_reason,
                     corrected_by,group_id,status,revision,supersedes_id,
                     superseded_by,validation_state,validation_issues,
                     approved_policy_fingerprint,approved_by,approved_at,
                     rejected_reason,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    source_lang, target_lang, source, source_hash, canonical_key,
                    original, corrected, correction_reason, corrected_by, scope,
                    "pending", revision, int(active["id"]) if active else None,
                    None, validation["state"], _json_list(validation["issues"]),
                    "", None, None, None, now, now,
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
        _stats["pending_submitted"] += 1
    if auto_approve:
        return approve_correction(
            correction_id,
            approved_by=approved_by or corrected_by,
            force=force,
            validation_override_reason=validation_override_reason,
        )
    logger.info("[AL] correction queued id=%d revision=%d", correction_id, revision)
    return {
        "ok": True,
        "correction_id": correction_id,
        "status": "pending",
        "revision": revision,
        "validation_state": validation["state"],
        "validation_issues": validation["issues"],
        "tm_updated": False,
        "semantic_casebook_updated": False,
        "vec_updated": False,
        "vec_skipped": not _vector_sync_enabled(),
    }


def approve_correction(
    correction_id: int,
    approved_by: Optional[str] = None,
    *,
    force: bool = False,
    validation_override_reason: Optional[str] = None,
) -> Dict[str, Any]:
    if not _init_done:
        init()
    row = _row(correction_id)
    if not row:
        return {"ok": False, "error": "correction_not_found"}
    if row.get("status") == "approved":
        return {
            "ok": True, "correction_id": int(correction_id), "status": "approved",
            "revision": int(row.get("revision") or 1), "already_reviewed": True,
            "tm_updated": False, "semantic_casebook_updated": True,
            "vec_updated": False, "vec_skipped": not _vector_sync_enabled(),
        }

    validation = _approval_validation(
        row, force=bool(force), override_reason=validation_override_reason
    )
    now = int(time.time())
    if not validation["ok"]:
        try:
            with _connect() as conn:
                conn.execute(
                    "UPDATE corrections SET validation_state='failed',validation_issues=?,updated_at=? WHERE id=?",
                    (_json_list(validation["issues"]), now, int(correction_id)),
                )
        except Exception:
            pass
        with _lock:
            _stats["validation_blocked"] += 1
        return {
            "ok": False,
            "error": "correction_validation_failed",
            "correction_id": int(correction_id),
            "status": row.get("status") or "pending",
            "validation_state": "failed",
            "validation_issues": validation["issues"],
        }

    previous_rows: List[Dict[str, Any]] = []
    reviewer = str(approved_by or "").strip() or None
    fingerprint = str(validation["policy_fingerprint"])
    try:
        with _connect() as conn:
            previous_rows = [dict(item) for item in conn.execute(
                """
                SELECT * FROM corrections
                WHERE src_lang=? AND tgt_lang=? AND canonical_src_key=? AND group_id=?
                  AND status='approved' AND id<>?
                ORDER BY revision DESC,id DESC
                """,
                (
                    row["src_lang"], row["tgt_lang"], row["canonical_src_key"],
                    row.get("group_id") or "", int(correction_id),
                ),
            ).fetchall()]
            primary_previous = previous_rows[0]["id"] if previous_rows else row.get("supersedes_id")
            for previous in previous_rows:
                conn.execute(
                    "UPDATE corrections SET status='superseded',superseded_by=?,updated_at=? WHERE id=?",
                    (int(correction_id), now, int(previous["id"])),
                )
            issues = list(validation["issues"])
            if validation.get("state") == "override":
                issues.append("override_reason:" + str(validation.get("override_reason") or ""))
            conn.execute(
                """
                UPDATE corrections
                SET status='approved',approved_by=?,approved_at=?,rejected_reason=NULL,
                    supersedes_id=?,superseded_by=NULL,validation_state=?,
                    validation_issues=?,approved_policy_fingerprint=?,updated_at=?
                WHERE id=?
                """,
                (
                    reviewer, now, primary_previous, validation["state"],
                    _json_list(issues), fingerprint, now, int(correction_id),
                ),
            )
    except Exception as exc:
        logger.error("[AL] approve failed: %s", exc)
        with _lock:
            _stats["errors"] += 1
        return {"ok": False, "error": str(exc)}

    for previous in previous_rows:
        _rollback_approved_assets(previous)
    approved_row = _row(correction_id) or dict(row)
    approved_row["approved_policy_fingerprint"] = fingerprint
    assets = _sync_approved_assets(approved_row)
    _notify_learning_changed()
    record_translation_outcome(
        source_text=approved_row["src_text"],
        candidate_text=approved_row.get("original_translation") or "",
        final_text=approved_row["corrected_translation"],
        src_lang=approved_row["src_lang"], tgt_lang=approved_row["tgt_lang"],
        group_id=approved_row.get("group_id") or "",
        issues=validation["issues"], path="human_correction_approved",
        reviewed=True, cacheable=True, correction_id=int(correction_id),
        event_type="correction_approved", update_risk=False,
    )
    with _lock:
        _stats["approved"] += 1
        _stats["superseded"] += len(previous_rows)
    return {
        "ok": True,
        "correction_id": int(correction_id),
        "status": "approved",
        "revision": int(approved_row.get("revision") or 1),
        "validation_state": validation["state"],
        "validation_issues": validation["issues"],
        "superseded_ids": [int(item["id"]) for item in previous_rows],
        **assets,
    }


def _restore_predecessor(row: Dict[str, Any], reviewer: Optional[str]) -> Optional[Dict[str, Any]]:
    predecessor_id = row.get("supersedes_id")
    if not predecessor_id:
        return None
    predecessor = _row(int(predecessor_id))
    if not predecessor or predecessor.get("status") != "superseded":
        return None
    validation = _approval_validation(predecessor, force=False, override_reason=None)
    now = int(time.time())
    if not validation["ok"]:
        try:
            with _connect() as conn:
                conn.execute(
                    """
                    UPDATE corrections SET status='quarantined',validation_state='failed',
                        validation_issues=?,updated_at=? WHERE id=?
                    """,
                    (_json_list(validation["issues"]), now, int(predecessor_id)),
                )
        except Exception:
            pass
        with _lock:
            _stats["quarantined"] += 1
        return None
    try:
        with _connect() as conn:
            active = conn.execute(
                """
                SELECT id FROM corrections
                WHERE src_lang=? AND tgt_lang=? AND canonical_src_key=? AND group_id=?
                  AND status='approved' LIMIT 1
                """,
                (
                    predecessor["src_lang"], predecessor["tgt_lang"],
                    predecessor["canonical_src_key"], predecessor.get("group_id") or "",
                ),
            ).fetchone()
            if active:
                return None
            fingerprint = str(validation["policy_fingerprint"])
            conn.execute(
                """
                UPDATE corrections SET status='approved',superseded_by=NULL,
                    approved_by=?,approved_at=?,validation_state='passed',
                    validation_issues='[]',approved_policy_fingerprint=?,updated_at=?
                WHERE id=?
                """,
                (
                    str(reviewer or "rollback").strip() or "rollback", now,
                    fingerprint, now, int(predecessor_id),
                ),
            )
        restored = _row(int(predecessor_id)) or predecessor
        _sync_approved_assets(restored)
        with _lock:
            _stats["restored"] += 1
        return restored
    except Exception as exc:
        logger.warning("[AL] predecessor restore failed: %s", exc)
        return None


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
    old_status = str(row.get("status") or "pending")
    now = int(time.time())
    try:
        with _connect() as conn:
            conn.execute(
                """
                UPDATE corrections
                SET status='rejected',approved_by=?,approved_at=NULL,
                    rejected_reason=?,superseded_by=NULL,updated_at=?
                WHERE id=?
                """,
                (
                    str(rejected_by or "").strip() or None,
                    str(reason or "").strip() or None,
                    now, int(correction_id),
                ),
            )
        rollback = _rollback_approved_assets(row) if old_status == "approved" else {
            "tm_removed": 0, "vec_removed": 0,
        }
        restored = _restore_predecessor(row, rejected_by) if old_status == "approved" else None
        _notify_learning_changed()
        record_translation_outcome(
            source_text=row["src_text"], candidate_text=row["corrected_translation"],
            final_text=(restored or {}).get("corrected_translation") or "",
            src_lang=row["src_lang"], tgt_lang=row["tgt_lang"],
            group_id=row.get("group_id") or "", issues=[reason] if reason else [],
            path="human_correction_rejected", reviewed=True, cacheable=False,
            correction_id=int(correction_id), event_type="correction_rejected",
            update_risk=False,
        )
        with _lock:
            _stats["rejected"] += 1
        return {
            "ok": True, "correction_id": int(correction_id), "status": "rejected",
            "restored_correction_id": int(restored["id"]) if restored else None,
            **rollback,
        }
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
    group_id: Optional[str] = None,
    *,
    include_global: bool = False,
) -> List[Dict[str, Any]]:
    if not _init_done:
        init()
    where: List[str] = []
    params: List[Any] = []
    if src_lang:
        where.append("src_lang=?")
        params.append(_lang(src_lang))
    if tgt_lang:
        where.append("tgt_lang=?")
        params.append(_lang(tgt_lang))
    if keyword:
        where.append("(src_text LIKE ? OR corrected_translation LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    if status:
        normalized = str(status).strip().lower()
        if normalized not in VALID_STATUSES:
            return []
        where.append("status=?")
        params.append(normalized)
    group_order = ""
    order_params: List[Any] = []
    if group_id is not None:
        scope = str(group_id or "")
        if include_global and scope:
            where.append("group_id IN (?, '')")
            params.append(scope)
            group_order = "CASE WHEN group_id=? THEN 0 ELSE 1 END,"
            order_params.append(scope)
        else:
            where.append("group_id=?")
            params.append(scope)
    sql = "SELECT * FROM corrections"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY " + group_order + " updated_at DESC,id DESC"
    sql += " LIMIT ? OFFSET ?"
    params.extend(order_params)
    params.extend([max(1, int(limit)), max(0, int(offset))])
    try:
        with _connect() as conn:
            return [dict(row) for row in conn.execute(sql, params).fetchall()]
    except Exception as exc:
        logger.error("[AL] list failed: %s", exc)
        return []


def delete_correction(correction_id: int) -> bool:
    """Delete one row, compare-delete its assets, and restore its predecessor."""
    if not _init_done:
        init()
    row = _row(correction_id)
    if not row:
        return False
    old_status = str(row.get("status") or "")
    try:
        with _connect() as conn:
            cursor = conn.execute(
                "DELETE FROM corrections WHERE id=?", (int(correction_id),)
            )
        if cursor.rowcount <= 0:
            return False
        if old_status == "approved":
            _rollback_approved_assets(row)
            _restore_predecessor(row, "delete_rollback")
        _notify_learning_changed()
        return True
    except Exception as exc:
        logger.error("[AL] delete failed: %s", exc)
        return False


def _risk_features(text: Any, lang: Any) -> Set[str]:
    normalized = unicodedata.normalize("NFKC", str(text or "")).casefold()
    normalized = re.sub(r"\d+(?:[.,]\d+)?", " <num> ", normalized)
    features: Set[str] = set()
    if _lang(lang) == "zh":
        chars = "".join(_HAN_RE.findall(normalized))
        for size in (2, 3, 4):
            for index in range(max(0, len(chars) - size + 1)):
                features.add("h:" + chars[index:index + size])
        features.update("w:" + item for item in _LATIN_WORD_RE.findall(normalized))
    else:
        words = [item for item in _LATIN_WORD_RE.findall(normalized) if len(item) >= 2]
        features.update("w:" + item for item in words)
        features.update("b:" + left + " " + right for left, right in zip(words, words[1:]))
    return features


def _risk_similarity(left: str, right: str, lang: str) -> float:
    if canonical_source_key(left) == canonical_source_key(right):
        return 1.0
    a = _risk_features(left, lang)
    b = _risk_features(right, lang)
    if not a or not b:
        return 0.0
    overlap = len(a & b)
    dice = (2.0 * overlap) / (len(a) + len(b))
    containment = overlap / max(1, min(len(a), len(b)))
    score = 0.65 * dice + 0.35 * containment
    if _lang(lang) == "zh":
        left_han = "".join(_HAN_RE.findall(str(left or "")))
        right_han = "".join(_HAN_RE.findall(str(right or "")))
        left_tri = {left_han[i:i + 3] for i in range(max(0, len(left_han) - 2))}
        right_tri = {right_han[i:i + 3] for i in range(max(0, len(right_han) - 2))}
        # Several independent shared 3-character anchors are strong evidence for
        # a Chinese paraphrase even when one clause is rewritten with synonyms.
        score += min(0.25, 0.025 * len(left_tri & right_tri))
    return min(1.0, score)


def _risk_weight(*, issues: Sequence[str], path: str, candidate: str, final: str,
                 reviewed: bool, cacheable: bool) -> float:
    changed = bool(candidate and final and candidate.strip() != final.strip())
    transient_markers = (
        "review_unavailable", "provider_unavailable", "timeout", "rate_limit",
        "connection", "network", "api_unavailable", "temporarily_unavailable",
    )
    content_issues = [
        str(item) for item in issues
        if not any(marker in str(item).casefold() for marker in transient_markers)
    ]
    if changed and content_issues and (
        "review" in path or "rebuild" in path or "repair" in path
    ):
        return 1.0
    if content_issues and not cacheable:
        return 0.9
    if content_issues:
        return 0.75
    if issues:
        return 0.0
    if not cacheable:
        return 0.65
    if reviewed and changed and content_issues:
        return 0.7
    return 0.0


def record_translation_outcome(
    *,
    source_text: str,
    candidate_text: str,
    final_text: str,
    src_lang: str,
    tgt_lang: str,
    group_id: Optional[str] = None,
    issues: Sequence[str] = (),
    path: str = "",
    reviewed: bool = False,
    cacheable: bool = False,
    correction_id: Optional[int] = None,
    event_type: Optional[str] = None,
    update_risk: bool = True,
) -> Dict[str, Any]:
    """Persist meaningful outcomes and aggregate safe future-review risk.

    No generated target is ever promoted to verified memory here. The aggregate
    can only request a future independent review.
    """
    if not _init_done:
        init()
    source = str(source_text or "").strip()
    source_lang = _lang(src_lang)
    target_lang = _lang(tgt_lang)
    if not source or (source_lang, target_lang) not in _SUPPORTED_DIRECTIONS:
        return {"recorded": False, "risk_updated": False}
    cleaned_issues = [str(item) for item in (issues or ()) if str(item).strip()]
    candidate = str(candidate_text or "").strip()
    final = str(final_text or "").strip()
    changed = bool(candidate and final and candidate != final)
    kind = str(event_type or (
        "translation_repaired" if changed else
        "translation_warning" if cleaned_issues or not cacheable else
        "translation_reviewed"
    ))
    meaningful = bool(cleaned_issues or changed or reviewed or not cacheable or event_type)
    if not meaningful:
        return {"recorded": False, "risk_updated": False}
    now = int(time.time())
    weight = _risk_weight(
        issues=cleaned_issues, path=str(path or ""), candidate=candidate,
        final=final, reviewed=bool(reviewed), cacheable=bool(cacheable),
    ) if update_risk else 0.0
    try:
        with _connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO learning_events
                    (event_type,correction_id,src_lang,tgt_lang,src_text,src_text_hash,
                     group_id,candidate_text,final_text,issues_json,path,reviewed,
                     cacheable,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    kind, int(correction_id) if correction_id else None,
                    source_lang, target_lang, source, _canonical_hash(source),
                    str(group_id or ""), candidate[:4000], final[:4000],
                    _json_list(cleaned_issues), str(path or "")[:240],
                    1 if reviewed else 0, 1 if cacheable else 0, now,
                ),
            )
            event_id = int(cursor.lastrowid)
            if weight >= 0.55:
                conn.execute(
                    """
                    INSERT INTO risk_patterns
                        (src_lang,tgt_lang,src_text,canonical_hash,group_id,
                         occurrence_count,risk_weight,issue_codes,last_path,
                         first_seen,last_seen)
                    VALUES (?,?,?,?,?,1,?,?,?,?,?)
                    ON CONFLICT(src_lang,tgt_lang,canonical_hash,group_id) DO UPDATE SET
                        src_text=excluded.src_text,
                        occurrence_count=occurrence_count+1,
                        risk_weight=MIN(10.0,risk_weight+excluded.risk_weight),
                        issue_codes=excluded.issue_codes,
                        last_path=excluded.last_path,
                        last_seen=excluded.last_seen
                    """,
                    (
                        source_lang, target_lang, source, _canonical_hash(source),
                        str(group_id or ""), float(weight),
                        _json_list(cleaned_issues), str(path or "")[:240], now, now,
                    ),
                )
            retention = max(1000, int(os.environ.get("ACTIVE_LEARNING_EVENT_RETENTION", "10000")))
            if event_id % 128 == 0:
                conn.execute(
                    "DELETE FROM learning_events WHERE id NOT IN "
                    "(SELECT id FROM learning_events ORDER BY id DESC LIMIT ?)",
                    (retention,),
                )
        with _lock:
            _stats["learning_events"] += 1
        return {"recorded": True, "event_id": event_id, "risk_updated": weight >= 0.55}
    except Exception as exc:
        logger.warning("[AL] learning outcome persistence failed: %s", exc)
        with _lock:
            _stats["errors"] += 1
        return {"recorded": False, "risk_updated": False, "error": str(exc)}


def assess_review_risk(
    source_text: str,
    src_lang: str,
    tgt_lang: str,
    group_id: Optional[str] = None,
    *,
    limit: int = 300,
) -> Dict[str, Any]:
    """Return whether prior objective failures justify source review now."""
    if not _init_done:
        init()
    source = str(source_text or "").strip()
    source_lang = _lang(src_lang)
    target_lang = _lang(tgt_lang)
    if not source or (source_lang, target_lang) not in _SUPPORTED_DIRECTIONS:
        return {"requires_review": False, "score": 0.0, "matches": [], "reasons": []}
    scope = str(group_id or "")
    try:
        with _connect() as conn:
            if scope:
                rows = conn.execute(
                    """
                    SELECT * FROM risk_patterns
                    WHERE src_lang=? AND tgt_lang=? AND group_id IN (?, '')
                    ORDER BY (group_id=?) DESC,last_seen DESC LIMIT ?
                    """,
                    (source_lang, target_lang, scope, scope, max(1, int(limit))),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM risk_patterns
                    WHERE src_lang=? AND tgt_lang=? AND group_id=''
                    ORDER BY last_seen DESC LIMIT ?
                    """,
                    (source_lang, target_lang, max(1, int(limit))),
                ).fetchall()
    except Exception as exc:
        logger.warning("[AL] risk assessment unavailable: %s", exc)
        return {"requires_review": False, "score": 0.0, "matches": [], "reasons": []}

    now = time.time()
    matches: List[Dict[str, Any]] = []
    for row in rows:
        similarity = _risk_similarity(source, str(row["src_text"] or ""), source_lang)
        if similarity < 0.54:
            continue
        count = max(1, int(row["occurrence_count"] or 1))
        accumulated = max(0.0, float(row["risk_weight"] or 0.0))
        strength = min(1.0, 0.68 + 0.14 * math.log1p(count) + 0.045 * min(4.0, accumulated))
        age_days = max(0.0, (now - float(row["last_seen"] or now)) / 86400.0)
        age_factor = max(0.40, math.pow(0.5, age_days / 120.0))
        score = similarity * strength * age_factor
        exact = canonical_source_key(source) == canonical_source_key(row["src_text"])
        if exact or score >= 0.50:
            matches.append({
                "pattern_id": int(row["id"]),
                "score": round(score, 4),
                "similarity": round(similarity, 4),
                "occurrences": count,
                "last_path": str(row["last_path"] or ""),
                "issues": _read_json_list(row["issue_codes"])[:8],
                "exact": bool(exact),
            })
    matches.sort(key=lambda item: (item["exact"], item["score"], item["occurrences"]), reverse=True)
    top = matches[0] if matches else None
    requires_review = bool(top and (
        top["exact"]
        or top["score"] >= 0.68
        or (top["occurrences"] >= 2 and top["score"] >= 0.60)
    ))
    reasons: List[str] = []
    for match in matches[:3]:
        reasons.extend(match["issues"][:4])
        if match["last_path"]:
            reasons.append("prior_path:" + match["last_path"])
    reasons = list(dict.fromkeys(reasons))[:10]
    if requires_review:
        with _lock:
            _stats["risk_reviews_triggered"] += 1
    return {
        "requires_review": requires_review,
        "score": float(top["score"]) if top else 0.0,
        "matches": matches[:3],
        "reasons": reasons,
        "build_id": ACTIVE_LEARNING_BUILD_ID,
    }


def build_review_context(risk: Dict[str, Any]) -> str:
    if not isinstance(risk, dict) or not risk.get("requires_review"):
        return ""
    reasons = [str(item) for item in risk.get("reasons", []) if str(item).strip()]
    lines = [
        "<continuous_learning_risk>",
        "A structurally similar past message failed deterministic translation checks. Reconstruct this source independently and verify every actor, action, object, role, movement, time, quantity, negation and modality before returning one translation.",
    ]
    if reasons:
        lines.append("Prior issue categories (diagnostic only, not target text): " + "; ".join(reasons[:8]))
    lines.append("Do not copy any previous generated wording; the current source is authoritative.")
    lines.append("</continuous_learning_risk>")
    return "\n".join(lines)


def audit_approved_corrections(*, quarantine: bool = False, limit: int = 2000) -> Dict[str, Any]:
    """Revalidate active corrections after policy upgrades.

    ``quarantine=False`` is read-only. With explicit quarantine, invalid rows are
    removed from learning assets while their audit history remains recoverable.
    """
    rows = list_corrections(limit=max(1, int(limit)), status="approved")
    invalid: List[Dict[str, Any]] = []
    for row in rows:
        report = validate_correction(
            row["src_text"], row["corrected_translation"],
            row["src_lang"], row["tgt_lang"],
            original_tgt=row.get("original_translation") or "",
        )
        if report["ok"]:
            continue
        invalid.append({"id": int(row["id"]), "issues": report["issues"]})
        if quarantine:
            now = int(time.time())
            try:
                with _connect() as conn:
                    conn.execute(
                        """
                        UPDATE corrections SET status='quarantined',validation_state='failed',
                            validation_issues=?,updated_at=? WHERE id=?
                        """,
                        (_json_list(report["issues"]), now, int(row["id"])),
                    )
                _rollback_approved_assets(row)
                _restore_predecessor(row, "policy_audit_rollback")
                with _lock:
                    _stats["quarantined"] += 1
            except Exception as exc:
                logger.warning("[AL] quarantine failed id=%s: %s", row["id"], exc)
    if quarantine and invalid:
        _notify_learning_changed()
    return {
        "ok": True,
        "checked": len(rows),
        "invalid_count": len(invalid),
        "invalid": invalid,
        "quarantined": bool(quarantine),
        "policy_fingerprint": _validator_fingerprint(),
    }


def al_stats() -> Dict[str, Any]:
    if not _init_done:
        init()
    with _lock:
        stats = dict(_stats)
    try:
        with _connect() as conn:
            stats["total_corrections"] = conn.execute(
                "SELECT COUNT(*) FROM corrections"
            ).fetchone()[0]
            status_rows = conn.execute(
                "SELECT status,COUNT(*) FROM corrections GROUP BY status"
            ).fetchall()
            stats["by_status"] = {str(row[0]): int(row[1]) for row in status_rows}
            for name in VALID_STATUSES:
                stats[name + "_corrections"] = stats["by_status"].get(name, 0)
            stats["total_learning_events"] = conn.execute(
                "SELECT COUNT(*) FROM learning_events"
            ).fetchone()[0]
            stats["active_risk_patterns"] = conn.execute(
                "SELECT COUNT(*) FROM risk_patterns"
            ).fetchone()[0]
            top_correctors = conn.execute(
                "SELECT corrected_by,COUNT(*) AS c FROM corrections "
                "WHERE corrected_by IS NOT NULL GROUP BY corrected_by ORDER BY c DESC LIMIT 5"
            ).fetchall()
            stats["top_correctors"] = [
                {"by": row[0], "count": row[1]} for row in top_correctors
            ]
    except Exception as exc:
        logger.warning("[AL] stats failed: %s", exc)
    stats["semantic_casebook_sync_enabled"] = True
    stats["vector_sync_enabled"] = _vector_sync_enabled()
    stats["validator_policy_fingerprint"] = _validator_fingerprint()
    stats["api_version"] = ACTIVE_LEARNING_API_VERSION
    stats["build_id"] = ACTIVE_LEARNING_BUILD_ID
    return stats


init()
