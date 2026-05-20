"""
active_learning.py — Active Learning Feedback Loop v1.0 (2026-05-20)

業界主流:human-in-the-loop translation
- 後台對任一筆翻譯點「修正」→ 輸入正確譯文
- 修正版以 quality_score=100 寫回 TM(優先於 LLM 生成的)
- 同 source 下次 lookup 拿到修正版,bypass LLM

【為什麼重要】
- LLM 翻譯永遠有 5-10% 邊界 case 會錯
- 人工修正資料是「終極事實」(ground truth)
- 累積 6-12 個月後,修正資料能 fine-tune custom model 或當 RAG 高權重

【架構】
- 修正紀錄存獨立 SQLite 表 (corrections.db)
- 同步寫回 translation_memory + vector_tm(品質滿分)
- 不覆蓋 LLM 翻譯歷史(translation_log 保留兩版供對比)

【參考】
- "Human-in-the-loop NMT" Smartcat / Lokalise 業界做法
- ISO 17100 翻譯品質管理 — review/revision step
"""

import os
import sqlite3
import time
import logging
import threading
import hashlib
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════
AL_DB_PATH: Optional[str] = None
_init_done = False
_lock = threading.RLock()

_stats = {
    "corrections_submitted": 0,
    "tm_updated": 0,
    "vec_tm_updated": 0,
    "errors": 0,
}


def _resolve_db_path() -> str:
    env = os.environ.get("ACTIVE_LEARNING_DB_PATH", "").strip()
    if env:
        return env
    for d in ("/var/data", "/data", "/tmp"):
        if os.path.isdir(d) and os.access(d, os.W_OK):
            return os.path.join(d, "active_learning.db")
    return "active_learning.db"


def init():
    global AL_DB_PATH, _init_done
    if _init_done:
        return
    AL_DB_PATH = _resolve_db_path()
    try:
        with sqlite3.connect(AL_DB_PATH) as conn:
            conn.executescript("""
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
                    created_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_al_lang ON corrections(src_lang, tgt_lang);
                CREATE INDEX IF NOT EXISTS idx_al_hash ON corrections(src_text_hash);
                CREATE INDEX IF NOT EXISTS idx_al_created ON corrections(created_at DESC);
            """)
        _init_done = True
        logger.info("[AL] init OK, db=%s", AL_DB_PATH)
    except Exception as e:
        logger.error("[AL] init failed: %s", e)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════
# 核心 API
# ═══════════════════════════════════════════════════════════════════
def submit_correction(src_text: str, original_tgt: str, corrected_tgt: str,
                      src_lang: str, tgt_lang: str,
                      correction_reason: Optional[str] = None,
                      corrected_by: Optional[str] = None,
                      group_id: Optional[str] = None) -> Dict[str, Any]:
    """提交人工修正
    
    流程:
    1. 寫入 corrections.db 永久記錄
    2. 同步 tm_store(quality_score=100,標 model="human_corrected")— 下次 lookup 優先取
    3. 同步 vector_store(若有 OpenAI key)
    
    Returns: {"ok": bool, "correction_id": int, "tm_updated": bool, "vec_updated": bool}
    """
    if not _init_done:
        init()
    if not src_text or not corrected_tgt:
        return {"ok": False, "error": "src_text 或 corrected_tgt 不可為空"}
    
    src_text = src_text.strip()
    corrected_tgt = corrected_tgt.strip()
    original_tgt = (original_tgt or "").strip()
    src_hash = _hash_text(src_text)
    now = int(time.time())
    group_id = group_id or ""
    
    correction_id = None
    try:
        with sqlite3.connect(AL_DB_PATH) as conn:
            cur = conn.execute("""
                INSERT INTO corrections
                    (src_lang, tgt_lang, src_text, src_text_hash,
                     original_translation, corrected_translation,
                     correction_reason, corrected_by, group_id, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (src_lang, tgt_lang, src_text, src_hash,
                  original_tgt, corrected_tgt,
                  correction_reason, corrected_by, group_id, now))
            correction_id = cur.lastrowid
        with _lock:
            _stats["corrections_submitted"] += 1
    except Exception as e:
        logger.error("[AL] submit failed: %s", e)
        with _lock:
            _stats["errors"] += 1
        return {"ok": False, "error": str(e)}
    
    # 同步寫 TM(品質滿分,model="human_corrected")
    tm_updated = False
    try:
        import translation_memory as _tm
        tm_updated = _tm.tm_store(src_text, corrected_tgt, src_lang, tgt_lang,
                                   group_id, model="human_corrected",
                                   quality_score=100.0)
        if tm_updated:
            with _lock:
                _stats["tm_updated"] += 1
    except Exception as e:
        logger.warning("[AL] tm_store failed: %s", e)
    
    # 同步寫 Vector TM(若有 OpenAI key)
    vec_updated = False
    try:
        import vector_tm as _vec
        vec_updated = _vec.vector_store(src_text, corrected_tgt, src_lang, tgt_lang,
                                         group_id, model="human_corrected",
                                         quality_score=100.0)
        if vec_updated:
            with _lock:
                _stats["vec_tm_updated"] += 1
    except Exception as e:
        logger.warning("[AL] vector_store failed: %s", e)
    
    logger.info("[AL] correction submitted id=%d tm=%s vec=%s",
                correction_id, tm_updated, vec_updated)
    return {
        "ok": True,
        "correction_id": correction_id,
        "tm_updated": tm_updated,
        "vec_updated": vec_updated,
    }


def list_corrections(limit: int = 100, offset: int = 0,
                     src_lang: Optional[str] = None,
                     tgt_lang: Optional[str] = None,
                     keyword: Optional[str] = None) -> List[Dict[str, Any]]:
    if not _init_done:
        init()
    where = []
    params = []
    if src_lang:
        where.append("src_lang=?")
        params.append(src_lang)
    if tgt_lang:
        where.append("tgt_lang=?")
        params.append(tgt_lang)
    if keyword:
        where.append("(src_text LIKE ? OR corrected_translation LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    
    sql = "SELECT * FROM corrections"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    try:
        with sqlite3.connect(AL_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("[AL] list failed: %s", e)
        return []


def delete_correction(correction_id: int) -> bool:
    """刪除修正紀錄(注意:TM 內的修正版不會自動回退)"""
    if not _init_done:
        init()
    try:
        with sqlite3.connect(AL_DB_PATH) as conn:
            cur = conn.execute("DELETE FROM corrections WHERE id=?", (correction_id,))
            return cur.rowcount > 0
    except Exception as e:
        logger.error("[AL] delete failed: %s", e)
        return False


def al_stats() -> Dict[str, Any]:
    if not _init_done:
        init()
    with _lock:
        s = dict(_stats)
    try:
        with sqlite3.connect(AL_DB_PATH) as conn:
            s["total_corrections"] = conn.execute("SELECT COUNT(*) FROM corrections").fetchone()[0]
            top_correctors = conn.execute(
                "SELECT corrected_by, COUNT(*) c FROM corrections "
                "WHERE corrected_by IS NOT NULL GROUP BY corrected_by ORDER BY c DESC LIMIT 5"
            ).fetchall()
            s["top_correctors"] = [{"by": r[0], "count": r[1]} for r in top_correctors]
    except Exception:
        pass
    return s


# 模組載入時自動 init
init()
