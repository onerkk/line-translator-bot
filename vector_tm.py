"""
vector_tm.py — Vector RAG Translation Memory v1.0 (2026-05-20)

基於 OpenAI text-embedding-3-small 的語義層 TM。
補強 translation_memory.py(lexical fuzzy match)的盲點:
- 同義詞:「料卡住了」vs「機台料夾住」(rapidfuzz 看不見,語義相同)
- 換句話說:「砂輪要換了」vs「請換一下砂輪」
- 工廠口語變體

【架構】
- 翻譯成功 → 同時 lexical store(translation_memory.py)+ vector store(本模組)
- lookup 順序:lexical exact → lexical fuzzy_bypass → vector bypass → vector inject → lexical inject → LLM
- 兩層 TM 互補:lexical 快但表面,vector 慢但語義

【成本】
- text-embedding-3-small: $0.02/M tokens
- 每筆翻譯 ~20 tokens embedding → 每月 10000 筆 ~$0.04
- query embedding 加 in-memory cache,重複 query 不重複呼叫

【儲存】
- SQLite,vector 用 struct float32 packed BLOB (1536 dim × 4 bytes = 6144 bytes/entry)
- 1000 條 entries ~6MB,10000 條 ~60MB

【參考】
- OpenAI Embeddings: https://platform.openai.com/docs/guides/embeddings
- Anthropic RAG 教學: https://docs.anthropic.com/en/docs/build-with-claude/retrieval
- 業界 (Lokalise) Vector RAG architecture
"""

import os
import sqlite3
import time
import hashlib
import logging
import threading
import struct
from typing import Optional, List, Tuple, Dict, Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════
VECTOR_DB_PATH: Optional[str] = None
EMBEDDING_MODEL = "text-embedding-3-small"  # OpenAI(便宜+夠用)
EMBEDDING_DIM = 1536
VECTOR_TOPK = 5
VECTOR_SIMILARITY_BYPASS = 0.95  # >=0.95 cosine sim → bypass LLM
VECTOR_SIMILARITY_INJECT = 0.75  # 0.75-0.94 → inject prompt
VECTOR_MAX_CANDIDATES = 5000  # 一次掃描上限

# Query embedding cache(in-memory,跨翻譯共用)
_QUERY_EMBED_CACHE: Dict[str, List[float]] = {}
_QUERY_EMBED_CACHE_MAX = 500

_init_done = False
_lock = threading.RLock()

_stats = {
    "embeddings_generated": 0,
    "embeddings_cached": 0,
    "lookups": 0,
    "bypass_hits": 0,
    "inject_hits": 0,
    "misses": 0,
    "stores": 0,
    "api_errors": 0,
}


# ═══════════════════════════════════════════════════════════════════
# 初始化
# ═══════════════════════════════════════════════════════════════════
def _resolve_db_path() -> str:
    env = os.environ.get("VECTOR_TM_DB_PATH", "").strip()
    if env:
        return env
    for d in ("/var/data", "/data", "/tmp"):
        if os.path.isdir(d) and os.access(d, os.W_OK):
            return os.path.join(d, "vector_tm.db")
    return "vector_tm.db"


def init():
    global VECTOR_DB_PATH, _init_done
    if _init_done:
        return
    VECTOR_DB_PATH = _resolve_db_path()
    try:
        with sqlite3.connect(VECTOR_DB_PATH) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS vector_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    src_lang TEXT NOT NULL,
                    tgt_lang TEXT NOT NULL,
                    src_text TEXT NOT NULL,
                    src_text_hash TEXT NOT NULL,
                    tgt_text TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    embedding_model TEXT NOT NULL,
                    group_id TEXT DEFAULT '',
                    model TEXT DEFAULT '',
                    hit_count INTEGER DEFAULT 0,
                    quality_score REAL,
                    created_at INTEGER NOT NULL,
                    last_used_at INTEGER NOT NULL,
                    UNIQUE(src_lang, tgt_lang, src_text_hash, group_id)
                );
                CREATE INDEX IF NOT EXISTS idx_vec_lang ON vector_entries(src_lang, tgt_lang);
                CREATE INDEX IF NOT EXISTS idx_vec_group ON vector_entries(group_id);
                CREATE INDEX IF NOT EXISTS idx_vec_hit ON vector_entries(hit_count DESC);
                CREATE INDEX IF NOT EXISTS idx_vec_used ON vector_entries(last_used_at DESC);
            """)
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(vector_entries)").fetchall()
            }
            if "model" not in columns:
                conn.execute(
                    "ALTER TABLE vector_entries ADD COLUMN model TEXT DEFAULT ''"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_vec_model ON vector_entries(model)"
            )
        _init_done = True
        logger.info("[VecTM] init OK, db=%s", VECTOR_DB_PATH)
    except Exception as e:
        logger.error("[VecTM] init failed: %s", e)


# ═══════════════════════════════════════════════════════════════════
# Vector 序列化(struct float32 packed,純 Python 無 numpy 依賴)
# ═══════════════════════════════════════════════════════════════════
def _pack_vector(vec: List[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack_vector(blob: bytes) -> Tuple[float, ...]:
    n = len(blob) // 4
    return struct.unpack(f"{n}f", blob)


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


def _cosine_similarity(a: List[float], b: Tuple[float, ...]) -> float:
    """純 Python 餘弦相似度(無 numpy 依賴)
    
    對 1536 維向量約 0.5ms,5000 條 candidates 約 2.5s — 對 LINE bot 延遲場景太慢
    優化:用 float32 + zip,實測 1536 維約 0.1ms,5000 條 < 500ms。
    更激進可加 numpy,但增加依賴。先用純 Python 看效能。
    """
    if len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / ((norm_a * norm_b) ** 0.5)


# ═══════════════════════════════════════════════════════════════════
# Embedding API(OpenAI text-embedding-3-small)
# ═══════════════════════════════════════════════════════════════════
# v3.13 速度根治:OpenAI client 改模組級單例。
# 原本每次 embedding 都 new 一個 OpenAI(...) → 每句翻譯多付 1~2 次 TLS 握手
# (lookup 一次 + store 一次)。單例重用 HTTP 連線池,timeout 15→8 秒
# (embedding 正常 <1 秒,15 秒等於讓一句卡死整條 pipeline 15 秒)。
_OPENAI_CLIENT = None
_OPENAI_CLIENT_LOCK = threading.Lock()


def _get_openai_client():
    global _OPENAI_CLIENT
    if _OPENAI_CLIENT is not None:
        return _OPENAI_CLIENT
    with _OPENAI_CLIENT_LOCK:
        if _OPENAI_CLIENT is None:
            api_key = os.environ.get("OPENAI_API_KEY", "")
            if not api_key:
                return None
            from openai import OpenAI
            _OPENAI_CLIENT = OpenAI(api_key=api_key, timeout=8.0)
    return _OPENAI_CLIENT


def _generate_embedding(text: str) -> Optional[List[float]]:
    """呼叫 OpenAI embedding API,with in-memory cache
    
    Returns: list[float] 1536 dim,或 None(API 失敗 / no key)
    """
    if not text or not text.strip():
        return None
    text = text.strip()
    cache_key = _hash_text(text)
    
    # Cache check
    if cache_key in _QUERY_EMBED_CACHE:
        with _lock:
            _stats["embeddings_cached"] += 1
        return _QUERY_EMBED_CACHE[cache_key]
    
    try:
        client = _get_openai_client()
        if client is None:
            logger.warning("[VecTM] no OPENAI_API_KEY, embedding unavailable")
            return None
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=text)
        vec = list(resp.data[0].embedding)
        
        # Simple LRU eviction
        if len(_QUERY_EMBED_CACHE) >= _QUERY_EMBED_CACHE_MAX:
            _QUERY_EMBED_CACHE.pop(next(iter(_QUERY_EMBED_CACHE)))
        _QUERY_EMBED_CACHE[cache_key] = vec
        
        with _lock:
            _stats["embeddings_generated"] += 1
        return vec
    except Exception as e:
        logger.warning("[VecTM] embedding API failed: %s", e)
        with _lock:
            _stats["api_errors"] += 1
        return None


# ═══════════════════════════════════════════════════════════════════
# 核心 API: vector_lookup
# ═══════════════════════════════════════════════════════════════════
def vector_lookup(src_text: str, src_lang: str, tgt_lang: str,
                  group_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """語義層 lookup
    
    Returns:
        None — miss
        {
            "match_type": "vector_bypass" | "vector_inject",
            "similarity": float (0-1),
            "tgt_text": str (僅 vector_bypass,可直接用),
            "references": [(sim, src, tgt), ...] (僅 vector_inject,供 prompt 注入),
            "matched_src": str (debug 用,僅 vector_bypass)
        }
    """
    if not _init_done:
        init()
    if not src_text or not src_text.strip():
        return None
    
    with _lock:
        _stats["lookups"] += 1
    
    query_vec = _generate_embedding(src_text)
    if query_vec is None:
        with _lock:
            _stats["misses"] += 1
        return None
    
    try:
        with sqlite3.connect(VECTOR_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            candidates = conn.execute("""
                SELECT id, src_text, tgt_text, embedding, group_id, hit_count
                FROM vector_entries
                WHERE src_lang=? AND tgt_lang=?
                ORDER BY (group_id=?) DESC, hit_count DESC, last_used_at DESC
                LIMIT ?
            """, (src_lang, tgt_lang, group_id or "", VECTOR_MAX_CANDIDATES)).fetchall()
        
        if not candidates:
            with _lock:
                _stats["misses"] += 1
            return None
        
        scored = []
        for c in candidates:
            try:
                vec = _unpack_vector(c["embedding"])
                sim = _cosine_similarity(query_vec, vec)
                scored.append((sim, c))
            except Exception:
                continue
        scored.sort(key=lambda x: x[0], reverse=True)
        
        if not scored:
            with _lock:
                _stats["misses"] += 1
            return None
        
        top_sim, top_c = scored[0]
        
        # Tier 1: vector bypass
        if top_sim >= VECTOR_SIMILARITY_BYPASS:
            with _lock:
                _stats["bypass_hits"] += 1
            try:
                with sqlite3.connect(VECTOR_DB_PATH) as conn:
                    conn.execute(
                        "UPDATE vector_entries SET hit_count=hit_count+1, last_used_at=? WHERE id=?",
                        (int(time.time()), top_c["id"])
                    )
            except Exception:
                pass
            logger.info("[VecTM] BYPASS hit: sim=%.3f", top_sim)
            return {
                "match_type": "vector_bypass",
                "similarity": top_sim,
                "tgt_text": top_c["tgt_text"],
                "matched_src": top_c["src_text"],
            }
        
        # Tier 2: vector inject
        refs = [
            (s, c["src_text"], c["tgt_text"])
            for s, c in scored[:VECTOR_TOPK]
            if s >= VECTOR_SIMILARITY_INJECT
        ]
        if refs:
            with _lock:
                _stats["inject_hits"] += 1
            logger.info("[VecTM] INJECT hit: top_sim=%.3f refs=%d", top_sim, len(refs))
            return {
                "match_type": "vector_inject",
                "similarity": top_sim,
                "references": refs,
            }
        
        with _lock:
            _stats["misses"] += 1
        return None
    except Exception as e:
        logger.error("[VecTM] lookup failed: %s", e)
        with _lock:
            _stats["misses"] += 1
        return None


# ═══════════════════════════════════════════════════════════════════
# 核心 API: vector_store
# ═══════════════════════════════════════════════════════════════════
def vector_store(src_text: str, tgt_text: str, src_lang: str, tgt_lang: str,
                 group_id: Optional[str] = None, model: Optional[str] = None,
                 quality_score: Optional[float] = None) -> bool:
    if not _init_done:
        init()
    if not src_text or not tgt_text:
        return False
    src_text = src_text.strip()
    tgt_text = tgt_text.strip()
    if not src_text or not tgt_text:
        return False
    if tgt_text.startswith("⚠"):
        return False
    
    vec = _generate_embedding(src_text)
    if vec is None:
        return False
    
    blob = _pack_vector(vec)
    src_hash = _hash_text(src_text)
    now = int(time.time())
    group_id = group_id or ""
    
    try:
        with sqlite3.connect(VECTOR_DB_PATH) as conn:
            conn.execute("""
                INSERT INTO vector_entries
                    (src_lang, tgt_lang, src_text, src_text_hash, tgt_text, embedding,
                     embedding_model, group_id, model, hit_count, quality_score, created_at, last_used_at)
                VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?)
                ON CONFLICT(src_lang, tgt_lang, src_text_hash, group_id) DO UPDATE SET
                    tgt_text=excluded.tgt_text,
                    embedding=excluded.embedding,
                    embedding_model=excluded.embedding_model,
                    model=excluded.model,
                    quality_score=excluded.quality_score,
                    last_used_at=excluded.last_used_at,
                    hit_count=hit_count+1
            """, (src_lang, tgt_lang, src_text, src_hash, tgt_text, blob,
                  EMBEDDING_MODEL, group_id, model or "", quality_score, now, now))
        with _lock:
            _stats["stores"] += 1
        return True
    except Exception as e:
        logger.warning("[VecTM] store failed: %s", e)
        return False


# ═══════════════════════════════════════════════════════════════════
# Prompt inject 工具
# ═══════════════════════════════════════════════════════════════════
def vector_inject_prompt(refs: List[Tuple[float, str, str]]) -> str:
    if not refs:
        return ""
    lines = [
        "<semantic_translation_memory>",
        "以下是語義相似的過去翻譯(供 reference,理解語境用):"
    ]
    for sim, src, tgt in refs:
        pct = int(sim * 100)
        lines.append(f'  <example similarity="{pct}%"><source>{src}</source><translation>{tgt}</translation></example>')
    lines.append("</semantic_translation_memory>")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 管理 API
# ═══════════════════════════════════════════════════════════════════
def vector_stats() -> Dict[str, Any]:
    if not _init_done:
        init()
    with _lock:
        s = dict(_stats)
    if s["lookups"] > 0:
        s["bypass_rate"] = round(s["bypass_hits"] / s["lookups"], 4)
        s["inject_rate"] = round(s["inject_hits"] / s["lookups"], 4)
        s["miss_rate"] = round(s["misses"] / s["lookups"], 4)
    else:
        s["bypass_rate"] = s["inject_rate"] = s["miss_rate"] = 0
    try:
        with sqlite3.connect(VECTOR_DB_PATH) as conn:
            s["total_entries"] = conn.execute("SELECT COUNT(*) FROM vector_entries").fetchone()[0]
            s["db_path"] = VECTOR_DB_PATH
            # DB size
            import os as _os
            if VECTOR_DB_PATH and _os.path.exists(VECTOR_DB_PATH):
                s["db_size_mb"] = round(_os.path.getsize(VECTOR_DB_PATH) / 1024 / 1024, 2)
    except Exception:
        pass
    s["embedding_cache_size"] = len(_QUERY_EMBED_CACHE)
    s["embedding_model"] = EMBEDDING_MODEL
    s["embedding_provider"] = "openai"  # 固定用 OpenAI(Anthropic 沒 embedding API)
    s["note"] = "Vector TM 即使 active provider=anthropic,embedding 也走 OpenAI(需 OPENAI_API_KEY)"
    s["openai_key_available"] = bool(os.environ.get("OPENAI_API_KEY", ""))
    s["thresholds"] = {
        "bypass": VECTOR_SIMILARITY_BYPASS,
        "inject": VECTOR_SIMILARITY_INJECT,
        "topk": VECTOR_TOPK,
    }
    return s


def vector_delete_target_texts(target_texts: List[str], src_lang: Optional[str] = None,
                               tgt_lang: Optional[str] = None) -> int:
    """Delete vector-TM rows whose target is a known-invalid derived label."""
    if not _init_done:
        init()
    values = sorted({str(x).strip() for x in (target_texts or []) if str(x).strip()})
    if not values:
        return 0
    placeholders = ",".join("?" for _ in values)
    where = [f"tgt_text IN ({placeholders})"]
    params: List[Any] = list(values)
    if src_lang:
        where.append("src_lang=?")
        params.append(src_lang)
    if tgt_lang:
        where.append("tgt_lang=?")
        params.append(tgt_lang)
    try:
        with sqlite3.connect(VECTOR_DB_PATH) as conn:
            cur = conn.execute("DELETE FROM vector_entries WHERE " + " AND ".join(where), params)
            count = cur.rowcount
        logger.info("[VecTM] deleted %d rows with invalid derived targets", count)
        return count
    except Exception as e:
        logger.error("[VecTM] delete_target_texts failed: %s", e)
        return 0


def vector_delete_exact(src_text: str, src_lang: str, tgt_lang: str,
                        group_id: Optional[str] = None,
                        model: Optional[str] = None,
                        target_text: Optional[str] = None) -> int:
    """Compare-and-delete one exact vector asset for correction rollback."""
    if not _init_done:
        init()
    source = str(src_text or "").strip()
    if not source:
        return 0
    where = [
        "src_lang=?", "tgt_lang=?", "src_text_hash=?", "src_text=?", "group_id=?",
    ]
    params: List[Any] = [
        src_lang, tgt_lang, _hash_text(source), source, group_id or "",
    ]
    if model is not None:
        where.append("COALESCE(model,'')=?")
        params.append(str(model))
    if target_text is not None:
        where.append("tgt_text=?")
        params.append(str(target_text).strip())
    try:
        with sqlite3.connect(VECTOR_DB_PATH) as conn:
            cursor = conn.execute(
                "DELETE FROM vector_entries WHERE " + " AND ".join(where), params
            )
            count = int(cursor.rowcount)
        if count:
            logger.info("[VecTM] exact correction rollback removed=%d", count)
        return count
    except Exception as exc:
        logger.error("[VecTM] exact delete failed: %s", exc)
        return 0


def vector_clear(group_id: Optional[str] = None, src_lang: Optional[str] = None,
                 tgt_lang: Optional[str] = None) -> int:
    if not _init_done:
        init()
    where = []
    params = []
    if group_id is not None:
        where.append("group_id=?")
        params.append(group_id)
    if src_lang:
        where.append("src_lang=?")
        params.append(src_lang)
    if tgt_lang:
        where.append("tgt_lang=?")
        params.append(tgt_lang)
    sql = "DELETE FROM vector_entries"
    if where:
        sql += " WHERE " + " AND ".join(where)
    try:
        with sqlite3.connect(VECTOR_DB_PATH) as conn:
            cur = conn.execute(sql, params)
            return cur.rowcount
    except Exception:
        return 0


def vector_set_thresholds(bypass: Optional[float] = None,
                          inject: Optional[float] = None,
                          topk: Optional[int] = None) -> Dict[str, Any]:
    global VECTOR_SIMILARITY_BYPASS, VECTOR_SIMILARITY_INJECT, VECTOR_TOPK
    if bypass is not None:
        VECTOR_SIMILARITY_BYPASS = max(0.7, min(1.0, float(bypass)))
    if inject is not None:
        VECTOR_SIMILARITY_INJECT = max(0.3, min(0.95, float(inject)))
    if topk is not None:
        VECTOR_TOPK = max(1, min(20, int(topk)))
    cfg = {
        "bypass": VECTOR_SIMILARITY_BYPASS,
        "inject": VECTOR_SIMILARITY_INJECT,
        "topk": VECTOR_TOPK,
    }
    try:
        import phase_config_store as _pcs
        _pcs.save_config("vec_tm", cfg)
    except Exception as _e:
        logger.warning("[VecTM] save persisted config failed: %s", _e)
    return cfg


# ═══════════════════════════════════════════════════════════════════
# 模組載入時自動 init + 載入持久化 threshold
# ═══════════════════════════════════════════════════════════════════
init()
try:
    import phase_config_store as _pcs
    _saved = _pcs.load_config("vec_tm")
    if _saved:
        VECTOR_SIMILARITY_BYPASS = _saved.get("bypass", VECTOR_SIMILARITY_BYPASS)
        VECTOR_SIMILARITY_INJECT = _saved.get("inject", VECTOR_SIMILARITY_INJECT)
        VECTOR_TOPK = _saved.get("topk", VECTOR_TOPK)
        logger.info("[VecTM] loaded persisted thresholds: %s", _saved)
except Exception as _e:
    logger.warning("[VecTM] load persisted thresholds failed: %s", _e)
