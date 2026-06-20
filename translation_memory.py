"""
translation_memory.py — Translation Memory (TM) 模組 v1.0 (2026-05-20)

業界 30 年成熟技術,SQLite + rapidfuzz fuzzy match 實作。

【設計依據】
- TMX 1.4b ISO standard (https://www.gala-global.org/lisa-oscar-standards)
- Lokalise / Smartcat / Translated 等業界平台的 RAG-powered TM 架構
- Intento State of Translation Automation 2025 報告

【3 層 match 策略】
- Tier 1 (Exact hash match)        : score = 100  → bypass LLM,直接返回譯文
- Tier 2 (Fuzzy match >= 95%)      : 95 <= score → bypass LLM,直接返回譯文(可調門檻)
- Tier 3 (Fuzzy inject 70-94%)     : 70 <= score < 95 → 注入 top-K references 到 LLM prompt
- Miss                             : score < 70 → 走全新 LLM 翻譯

【核心 API】
- tm_lookup(src_text, src_lang, tgt_lang, group_id=None) → dict | None
- tm_store(src_text, tgt_text, src_lang, tgt_lang, group_id, model, quality_score)
- tm_inject_prompt(refs) → str (XML block 供注入 LLM)
- tm_stats() → dict (entries, hit_rate, top groups, ...)
- tm_export_tmx(filepath) → int (匯出 TMX 1.4b 標準格式)
- tm_import_tmx(filepath) → int (匯入 TMX)
- tm_delete(entry_id) → bool
- tm_search(keyword, src_lang=None, tgt_lang=None, limit=100) → list

【儲存】
- SQLite,路徑優先級:env TM_DB_PATH > /var/data > /data > /tmp > .
- Schema:tm_entries 表,UNIQUE(src_lang, tgt_lang, src_text_hash, group_id)

【執行緒安全】
- SQLite 連線 per-call (避免 multi-worker 衝突)
- 統計用 RLock
"""

import sqlite3
import os
import time
import hashlib
import threading
import logging
from typing import Optional, List, Tuple, Dict, Any

logger = logging.getLogger(__name__)

try:
    from rapidfuzz import fuzz
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False
    from difflib import SequenceMatcher
    logger.warning("rapidfuzz 未安裝,fallback 用 stdlib difflib(較慢)。請 pip install rapidfuzz")


# ═══════════════════════════════════════════════════════════════════
# 配置常數
# ═══════════════════════════════════════════════════════════════════

# Tier 2:>= 此分數直接 bypass LLM
TM_FUZZY_THRESHOLD_BYPASS = 95

# Tier 3:此分數以下也算 miss,不注入
TM_FUZZY_THRESHOLD_INJECT = 70

# Tier 3 注入時最多取幾條 top
TM_FUZZY_TOPK = 3

# 每次 fuzzy 搜尋最多比較幾條 candidates(rapidfuzz 性能控制)
# 1000 條 candidates 在 rapidfuzz 是 sub-ms,所以調大很 OK
TM_MAX_CANDIDATES = 1000


# ═══════════════════════════════════════════════════════════════════
# 模組級狀態(per-process,跨 worker 共用 SQLite file)
# ═══════════════════════════════════════════════════════════════════

TM_DB_PATH: Optional[str] = None
_lock = threading.RLock()
_init_done = False

# 統計(per-process,重啟歸零;持久化統計從 DB COUNT 取)
_stats = {
    "lookups": 0,
    "exact_hits": 0,
    "fuzzy_bypass": 0,
    "fuzzy_inject": 0,
    "misses": 0,
    "stores": 0,
}


# ═══════════════════════════════════════════════════════════════════
# 初始化
# ═══════════════════════════════════════════════════════════════════

def _resolve_db_path() -> str:
    """同 translation_log 邏輯,Render persistent disk 優先"""
    env = os.environ.get("TM_DB_PATH", "").strip()
    if env:
        return env
    for d in ("/var/data", "/data", "/tmp"):
        if os.path.isdir(d) and os.access(d, os.W_OK):
            return os.path.join(d, "translation_memory.db")
    return "translation_memory.db"


def init():
    """初始化 SQLite schema(idempotent,可重複呼叫)"""
    global TM_DB_PATH, _init_done
    if _init_done:
        return
    TM_DB_PATH = _resolve_db_path()
    try:
        with sqlite3.connect(TM_DB_PATH) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS tm_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    src_lang TEXT NOT NULL,
                    tgt_lang TEXT NOT NULL,
                    src_text TEXT NOT NULL,
                    src_text_hash TEXT NOT NULL,
                    tgt_text TEXT NOT NULL,
                    group_id TEXT DEFAULT '',
                    model TEXT,
                    hit_count INTEGER DEFAULT 0,
                    quality_score REAL,
                    created_at INTEGER NOT NULL,
                    last_used_at INTEGER NOT NULL,
                    UNIQUE(src_lang, tgt_lang, src_text_hash, group_id)
                );
                CREATE INDEX IF NOT EXISTS idx_tm_src_lang ON tm_entries(src_lang, tgt_lang);
                CREATE INDEX IF NOT EXISTS idx_tm_group ON tm_entries(group_id);
                CREATE INDEX IF NOT EXISTS idx_tm_hash ON tm_entries(src_text_hash);
                CREATE INDEX IF NOT EXISTS idx_tm_last_used ON tm_entries(last_used_at DESC);
                CREATE INDEX IF NOT EXISTS idx_tm_hit_count ON tm_entries(hit_count DESC);
            """)
        _init_done = True
        logger.info("[TM] init OK, db=%s, rapidfuzz=%s", TM_DB_PATH, HAS_RAPIDFUZZ)
    except Exception as e:
        logger.error("[TM] init failed: %s", e)


def _hash_text(text: str) -> str:
    """SHA-256 截前 16 位(碰撞機率 1/2^64 → 對 TM 規模足夠)"""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()[:16]


# ═══════════════════════════════════════════════════════════════════
# Fuzzy similarity 計算(rapidfuzz 優先,fallback difflib)
# ═══════════════════════════════════════════════════════════════════

def _similarity(a: str, b: str) -> int:
    """回傳 0-100 整數分數
    
    rapidfuzz.fuzz.token_set_ratio:
    - 對短句容忍 token 順序變化(工廠口語很適合)
    - 對長句也夠準
    - 例:「料卡住了」vs「料卡住」→ 100
         「請幫包這把」vs「幫忙包一下這把」→ ~85
    """
    if not a or not b:
        return 0
    if HAS_RAPIDFUZZ:
        return int(fuzz.token_set_ratio(a, b))
    else:
        # difflib SequenceMatcher 的 ratio() 是 0-1 浮點
        return int(SequenceMatcher(None, a, b).ratio() * 100)


# ═══════════════════════════════════════════════════════════════════
# 核心 API: tm_lookup
# ═══════════════════════════════════════════════════════════════════

def tm_lookup(src_text: str, src_lang: str, tgt_lang: str,
              group_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """查詢 TM,3 層 match 策略
    
    Returns:
        None — miss (score < 70)
        {
            "match_type": "exact" | "fuzzy_bypass" | "fuzzy_inject",
            (v3.22: 排序加入 quality_score — 詞庫種子(100)與 APE 修正版
             優先於低 QE 分的舊譯;群組專屬譯文仍最優先)
            "score": int (0-100),
            "tgt_text": str (僅 exact / fuzzy_bypass,可直接用作翻譯結果),
            "references": [(score, src, tgt), ...] (僅 fuzzy_inject,供 LLM prompt 注入),
            "matched_src": str (僅 fuzzy_bypass,debug 用)
        }
    """
    if not _init_done:
        init()
    if not src_text or not src_text.strip():
        return None
    src_text = src_text.strip()
    src_hash = _hash_text(src_text)
    group_id = group_id or ""
    
    with _lock:
        _stats["lookups"] += 1
    
    try:
        with sqlite3.connect(TM_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            
            # ─── Tier 1: Exact hash match ───
            # 同 group_id 優先(語境一致),然後降到全局
            row = conn.execute("""
                SELECT * FROM tm_entries
                WHERE src_lang=? AND tgt_lang=? AND src_text_hash=?
                ORDER BY (group_id=?) DESC, COALESCE(quality_score,-1) DESC, hit_count DESC, last_used_at DESC
                LIMIT 1
            """, (src_lang, tgt_lang, src_hash, group_id)).fetchone()
            
            if row and row["src_text"].strip() == src_text:
                # 防 hash 碰撞:再比 raw 字串
                with _lock:
                    _stats["exact_hits"] += 1
                conn.execute(
                    "UPDATE tm_entries SET hit_count=hit_count+1, last_used_at=? WHERE id=?",
                    (int(time.time()), row["id"])
                )
                logger.info("[TM] EXACT hit: %s→%s id=%d", src_lang, tgt_lang, row["id"])
                return {
                    "match_type": "exact",
                    "score": 100,
                    "tgt_text": row["tgt_text"],
                }
            
            # ─── Tier 2 + 3: Fuzzy match ───
            # 拉同方向 candidates,group_id 優先
            candidates = conn.execute("""
                SELECT id, src_text, tgt_text, group_id, hit_count
                FROM tm_entries
                WHERE src_lang=? AND tgt_lang=?
                ORDER BY (group_id=?) DESC, COALESCE(quality_score,-1) DESC, hit_count DESC, last_used_at DESC
                LIMIT ?
            """, (src_lang, tgt_lang, group_id, TM_MAX_CANDIDATES)).fetchall()
        
        if not candidates:
            with _lock:
                _stats["misses"] += 1
            return None
        
        # rapidfuzz scoring
        scored = []
        for c in candidates:
            score = _similarity(src_text, c["src_text"])
            scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        
        top_score, top_c = scored[0]
        
        # Tier 2: fuzzy bypass(直接用譯文)
        if top_score >= TM_FUZZY_THRESHOLD_BYPASS:
            with _lock:
                _stats["fuzzy_bypass"] += 1
            try:
                with sqlite3.connect(TM_DB_PATH) as conn:
                    conn.execute(
                        "UPDATE tm_entries SET hit_count=hit_count+1, last_used_at=? WHERE id=?",
                        (int(time.time()), top_c["id"])
                    )
            except Exception:
                pass
            logger.info("[TM] FUZZY_BYPASS hit: %s→%s score=%d", src_lang, tgt_lang, top_score)
            return {
                "match_type": "fuzzy_bypass",
                "score": top_score,
                "tgt_text": top_c["tgt_text"],
                "matched_src": top_c["src_text"],
            }
        
        # Tier 3: fuzzy inject(注入 prompt 當 reference)
        refs = [
            (s, c["src_text"], c["tgt_text"])
            for s, c in scored[:TM_FUZZY_TOPK]
            if s >= TM_FUZZY_THRESHOLD_INJECT
        ]
        if refs:
            with _lock:
                _stats["fuzzy_inject"] += 1
            logger.info("[TM] FUZZY_INJECT hit: %s→%s top_score=%d refs=%d",
                        src_lang, tgt_lang, top_score, len(refs))
            return {
                "match_type": "fuzzy_inject",
                "score": top_score,
                "references": refs,
            }
        
        # 全 miss
        with _lock:
            _stats["misses"] += 1
        return None
    
    except Exception as e:
        logger.error("[TM] lookup failed: %s", e)
        with _lock:
            _stats["misses"] += 1
        return None


# ═══════════════════════════════════════════════════════════════════
# 核心 API: tm_store
# ═══════════════════════════════════════════════════════════════════

def tm_store(src_text: str, tgt_text: str, src_lang: str, tgt_lang: str,
             group_id: Optional[str] = None, model: Optional[str] = None,
             quality_score: Optional[float] = None) -> bool:
    """翻譯成功後 store TM
    
    UPSERT:同 (src_lang, tgt_lang, src_text_hash, group_id) 已存在則更新 tgt_text 並 hit_count+=1
    
    Returns: True 成功, False 失敗(不影響主翻譯流程)
    """
    if not _init_done:
        init()
    if not src_text or not tgt_text:
        return False
    src_text = src_text.strip()
    tgt_text = tgt_text.strip()
    if not src_text or not tgt_text:
        return False
    
    # 不存「以 ⚠️ 開頭的低品質譯文」— 這是 round-trip 反譯失敗的標記
    if tgt_text.startswith("⚠️") or tgt_text.startswith("⚠"):
        logger.info("[TM] skip storing low-confidence translation (⚠️ prefix)")
        return False
    
    src_hash = _hash_text(src_text)
    now = int(time.time())
    group_id = group_id or ""
    
    try:
        with sqlite3.connect(TM_DB_PATH) as conn:
            conn.execute("""
                INSERT INTO tm_entries
                    (src_lang, tgt_lang, src_text, src_text_hash, tgt_text,
                     group_id, model, hit_count, quality_score, created_at, last_used_at)
                VALUES (?,?,?,?,?,?,?,1,?,?,?)
                ON CONFLICT(src_lang, tgt_lang, src_text_hash, group_id) DO UPDATE SET
                    tgt_text=excluded.tgt_text,
                    model=excluded.model,
                    quality_score=excluded.quality_score,
                    last_used_at=excluded.last_used_at,
                    hit_count=hit_count+1
            """, (src_lang, tgt_lang, src_text, src_hash, tgt_text,
                  group_id, model, quality_score, now, now))
        with _lock:
            _stats["stores"] += 1
        return True
    except Exception as e:
        logger.warning("[TM] store failed: %s", e)
        return False


# ═══════════════════════════════════════════════════════════════════
# Prompt 注入工具
# ═══════════════════════════════════════════════════════════════════

def tm_inject_prompt(refs: List[Tuple[int, str, str]]) -> str:
    """把 fuzzy_inject 的 top-K references 包成 XML block 供 LLM 注入
    
    符合 Anthropic 官方 multishot-prompting 規範(用 <example> XML tag)
    """
    if not refs:
        return ""
    lines = [
        "<translation_memory>",
        "以下是過去翻譯過的類似句子(供 reference,但不必照抄,若新句不同請按新句翻):"
    ]
    for score, src, tgt in refs:
        lines.append(f'  <example similarity="{score}%"><source>{src}</source><translation>{tgt}</translation></example>')
    lines.append("</translation_memory>")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 統計 API
# ═══════════════════════════════════════════════════════════════════

def tm_stats() -> Dict[str, Any]:
    """取得 TM 統計(供後台監控)"""
    if not _init_done:
        init()
    with _lock:
        s = dict(_stats)
    
    # Hit rate 計算
    if s["lookups"] > 0:
        s["hit_rate_bypass"] = round((s["exact_hits"] + s["fuzzy_bypass"]) / s["lookups"], 4)
        s["hit_rate_inject"] = round(s["fuzzy_inject"] / s["lookups"], 4)
        s["hit_rate_total"] = round((s["exact_hits"] + s["fuzzy_bypass"] + s["fuzzy_inject"]) / s["lookups"], 4)
    else:
        s["hit_rate_bypass"] = 0
        s["hit_rate_inject"] = 0
        s["hit_rate_total"] = 0
    
    # DB 內 entry 數 + 統計
    try:
        with sqlite3.connect(TM_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            s["total_entries"] = conn.execute("SELECT COUNT(*) FROM tm_entries").fetchone()[0]
            
            lang_pairs = conn.execute(
                "SELECT src_lang, tgt_lang, COUNT(*) c FROM tm_entries GROUP BY src_lang, tgt_lang ORDER BY c DESC"
            ).fetchall()
            s["lang_pairs"] = [{"src": r["src_lang"], "tgt": r["tgt_lang"], "count": r["c"]} for r in lang_pairs]
            
            top_groups = conn.execute(
                "SELECT group_id, COUNT(*) c FROM tm_entries WHERE group_id != '' GROUP BY group_id ORDER BY c DESC LIMIT 10"
            ).fetchall()
            s["top_groups"] = [{"group_id": r["group_id"], "count": r["c"]} for r in top_groups]
            
            top_reused = conn.execute(
                "SELECT src_text, tgt_text, src_lang, tgt_lang, hit_count FROM tm_entries WHERE hit_count > 1 ORDER BY hit_count DESC LIMIT 10"
            ).fetchall()
            s["top_reused"] = [
                {"src": r["src_text"], "tgt": r["tgt_text"], "src_lang": r["src_lang"],
                 "tgt_lang": r["tgt_lang"], "hits": r["hit_count"]}
                for r in top_reused
            ]
    except Exception as e:
        logger.warning("[TM] stats query failed: %s", e)
    
    s["db_path"] = TM_DB_PATH
    s["rapidfuzz_available"] = HAS_RAPIDFUZZ
    s["thresholds"] = {
        "fuzzy_bypass": TM_FUZZY_THRESHOLD_BYPASS,
        "fuzzy_inject": TM_FUZZY_THRESHOLD_INJECT,
        "fuzzy_topk": TM_FUZZY_TOPK,
        "max_candidates": TM_MAX_CANDIDATES,
    }
    return s


# ═══════════════════════════════════════════════════════════════════
# TMX 1.4b 標準匯出/匯入
# ═══════════════════════════════════════════════════════════════════

def tm_export_tmx(filepath: str) -> int:
    """匯出為 TMX 1.4b 標準格式(ISO standard,跨平台相容)
    
    可被 Lokalise / Smartcat / SDL Trados / memoQ 等所有業界 TMS 工具讀取
    """
    if not _init_done:
        init()
    from xml.sax.saxutils import escape
    
    count = 0
    out_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE tmx SYSTEM "tmx14.dtd">',
        '<tmx version="1.4">',
        '  <header creationtool="LINETranslateBot" creationtoolversion="v3.9.37" '
        'segtype="sentence" o-tmf="sqlite" adminlang="en" srclang="*all*" datatype="plaintext"/>',
        '  <body>',
    ]
    try:
        with sqlite3.connect(TM_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM tm_entries ORDER BY created_at").fetchall()
            for r in rows:
                created = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(r["created_at"]))
                lastused = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(r["last_used_at"]))
                out_lines.append(
                    f'    <tu creationdate="{created}" changedate="{lastused}" '
                    f'usagecount="{r["hit_count"]}">'
                )
                if r["group_id"]:
                    out_lines.append(f'      <prop type="x-group-id">{escape(r["group_id"])}</prop>')
                if r["model"]:
                    out_lines.append(f'      <prop type="x-model">{escape(r["model"])}</prop>')
                out_lines.append(
                    f'      <tuv xml:lang="{escape(r["src_lang"])}">'
                    f'<seg>{escape(r["src_text"])}</seg></tuv>'
                )
                out_lines.append(
                    f'      <tuv xml:lang="{escape(r["tgt_lang"])}">'
                    f'<seg>{escape(r["tgt_text"])}</seg></tuv>'
                )
                out_lines.append('    </tu>')
                count += 1
        out_lines.append('  </body>')
        out_lines.append('</tmx>')
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(out_lines))
        logger.info("[TM] exported %d entries to TMX: %s", count, filepath)
        return count
    except Exception as e:
        logger.error("[TM] export TMX failed: %s", e)
        return 0


def tm_import_tmx(filepath: str) -> int:
    """從 TMX 標準格式匯入"""
    if not _init_done:
        init()
    import xml.etree.ElementTree as ET
    
    count = 0
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
        ns_strip = lambda tag: tag.split("}")[-1] if "}" in tag else tag
        
        body = None
        for child in root:
            if ns_strip(child.tag) == "body":
                body = child
                break
        if body is None:
            return 0
        
        for tu in body:
            if ns_strip(tu.tag) != "tu":
                continue
            tuvs = [c for c in tu if ns_strip(c.tag) == "tuv"]
            if len(tuvs) < 2:
                continue
            
            def _get_lang(tuv):
                for attr_key in tuv.attrib:
                    if attr_key.endswith("}lang") or attr_key == "lang":
                        return tuv.attrib[attr_key]
                return "?"
            
            def _get_seg(tuv):
                for c in tuv:
                    if ns_strip(c.tag) == "seg":
                        return c.text or ""
                return ""
            
            src_lang = _get_lang(tuvs[0])
            tgt_lang = _get_lang(tuvs[1])
            src_seg = _get_seg(tuvs[0])
            tgt_seg = _get_seg(tuvs[1])
            
            if not src_seg or not tgt_seg:
                continue
            
            group_id = ""
            for prop in tu:
                if ns_strip(prop.tag) == "prop" and prop.get("type") == "x-group-id":
                    group_id = prop.text or ""
                    break
            
            if tm_store(src_seg, tgt_seg, src_lang, tgt_lang, group_id=group_id, model="tmx_imported"):
                count += 1
        
        logger.info("[TM] imported %d entries from TMX: %s", count, filepath)
        return count
    except Exception as e:
        logger.error("[TM] import TMX failed: %s", e)
        return count


# ═══════════════════════════════════════════════════════════════════
# 管理 API:刪除 / 搜尋 / 清空
# ═══════════════════════════════════════════════════════════════════

def tm_delete(entry_id: int) -> bool:
    """刪除指定 TM entry"""
    if not _init_done:
        init()
    try:
        with sqlite3.connect(TM_DB_PATH) as conn:
            cur = conn.execute("DELETE FROM tm_entries WHERE id=?", (entry_id,))
            return cur.rowcount > 0
    except Exception as e:
        logger.error("[TM] delete failed: %s", e)
        return False


def tm_delete_by_model(model: str, src_lang: Optional[str] = None,
                       tgt_lang: Optional[str] = None,
                       group_id: Optional[str] = None) -> int:
    """Delete derived TM rows by provenance, optionally limited by direction.

    Glossary-seeded rows are generated assets, not user history.  They must be
    rebuilt from the current glossary so removed or newly-ambiguous reverse rows
    cannot survive indefinitely as exact/fuzzy bypasses.
    """
    if not _init_done:
        init()
    if not model:
        return 0
    where = ["model=?"]
    params: List[Any] = [model]
    if src_lang:
        where.append("src_lang=?")
        params.append(src_lang)
    if tgt_lang:
        where.append("tgt_lang=?")
        params.append(tgt_lang)
    if group_id is not None:
        where.append("group_id=?")
        params.append(group_id)
    try:
        with sqlite3.connect(TM_DB_PATH) as conn:
            cur = conn.execute(
                "DELETE FROM tm_entries WHERE " + " AND ".join(where),
                params,
            )
            count = cur.rowcount
        logger.info("[TM] deleted %d derived rows model=%s direction=%s→%s",
                    count, model, src_lang or "*", tgt_lang or "*")
        return count
    except Exception as e:
        logger.error("[TM] delete_by_model failed: %s", e)
        return 0


def tm_delete_target_texts(target_texts: List[str], src_lang: Optional[str] = None,
                           tgt_lang: Optional[str] = None) -> int:
    """Delete rows whose target exactly matches known-invalid derived labels."""
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
        with sqlite3.connect(TM_DB_PATH) as conn:
            cur = conn.execute("DELETE FROM tm_entries WHERE " + " AND ".join(where), params)
            count = cur.rowcount
        logger.info("[TM] deleted %d rows with invalid derived targets", count)
        return count
    except Exception as e:
        logger.error("[TM] delete_target_texts failed: %s", e)
        return 0


def tm_search(keyword: str = "", src_lang: Optional[str] = None,
              tgt_lang: Optional[str] = None, group_id: Optional[str] = None,
              limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
    """搜尋 TM entries(供後台管理頁用)"""
    if not _init_done:
        init()
    where = []
    params = []
    if keyword:
        where.append("(src_text LIKE ? OR tgt_text LIKE ?)")
        params.extend([f"%{keyword}%", f"%{keyword}%"])
    if src_lang:
        where.append("src_lang=?")
        params.append(src_lang)
    if tgt_lang:
        where.append("tgt_lang=?")
        params.append(tgt_lang)
    if group_id is not None:
        where.append("group_id=?")
        params.append(group_id)
    
    sql = "SELECT * FROM tm_entries"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY last_used_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    try:
        with sqlite3.connect(TM_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("[TM] search failed: %s", e)
        return []


def tm_clear(group_id: Optional[str] = None, src_lang: Optional[str] = None,
             tgt_lang: Optional[str] = None) -> int:
    """清空 TM(可指定 group / lang pair),回傳刪除筆數
    
    危險操作,後台需 confirm
    """
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
    
    sql = "DELETE FROM tm_entries"
    if where:
        sql += " WHERE " + " AND ".join(where)
    
    try:
        with sqlite3.connect(TM_DB_PATH) as conn:
            cur = conn.execute(sql, params)
            n = cur.rowcount
        logger.warning("[TM] cleared %d entries (where=%s)", n, where)
        return n
    except Exception as e:
        logger.error("[TM] clear failed: %s", e)
        return 0


# ═══════════════════════════════════════════════════════════════════
# Phase J: Concordance Search(CAT tool 業界標準)
# ═══════════════════════════════════════════════════════════════════
def tm_concordance(phrase: str, src_lang: Optional[str] = None,
                   tgt_lang: Optional[str] = None,
                   side: str = "both",
                   limit: int = 30) -> List[Dict[str, Any]]:
    """詞組層級 concordance 搜尋 — CAT tool 標準功能
    
    跟 tm_search 不同:concordance 抽取每筆譯文中的 phrase 上下文,
    讓使用者快速看到「這個術語/詞組通常怎麼翻」。
    
    Args:
        phrase: 要搜尋的詞組(可以是中文或印尼文)
        src_lang/tgt_lang: 限制語言對(None = 不限)
        side: "source" 只搜原文 | "target" 只搜譯文 | "both" 兩邊都搜
        limit: 最多回傳幾筆
    
    Returns: list of {
        "src_text": str, "tgt_text": str, "src_lang": str, "tgt_lang": str,
        "src_context": str (高亮 phrase 的上下文片段),
        "tgt_context": str,
        "matched_side": "source" | "target" | "both",
        "hit_count": int (TM entry 命中次數)
    }
    """
    if not _init_done:
        init()
    if not phrase or not phrase.strip():
        return []
    phrase = phrase.strip()
    
    where = []
    params = []
    
    if side == "source":
        where.append("src_text LIKE ?")
        params.append(f"%{phrase}%")
    elif side == "target":
        where.append("tgt_text LIKE ?")
        params.append(f"%{phrase}%")
    else:  # both
        where.append("(src_text LIKE ? OR tgt_text LIKE ?)")
        params.extend([f"%{phrase}%", f"%{phrase}%"])
    
    if src_lang:
        where.append("src_lang=?")
        params.append(src_lang)
    if tgt_lang:
        where.append("tgt_lang=?")
        params.append(tgt_lang)
    
    sql = ("SELECT src_text, tgt_text, src_lang, tgt_lang, hit_count FROM tm_entries WHERE "
           + " AND ".join(where) + " ORDER BY hit_count DESC, last_used_at DESC LIMIT ?")
    params.append(limit)
    
    results = []
    try:
        with sqlite3.connect(TM_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
            for r in rows:
                src_match = phrase in r["src_text"]
                tgt_match = phrase in r["tgt_text"]
                matched_side = "both" if (src_match and tgt_match) else ("source" if src_match else "target")
                results.append({
                    "src_text": r["src_text"],
                    "tgt_text": r["tgt_text"],
                    "src_lang": r["src_lang"],
                    "tgt_lang": r["tgt_lang"],
                    "src_context": _highlight_context(r["src_text"], phrase, window=20) if src_match else "",
                    "tgt_context": _highlight_context(r["tgt_text"], phrase, window=20) if tgt_match else "",
                    "matched_side": matched_side,
                    "hit_count": r["hit_count"],
                })
        return results
    except Exception as e:
        logger.error("[TM] concordance failed: %s", e)
        return []


def _highlight_context(text: str, phrase: str, window: int = 20) -> str:
    """抽 phrase 前後 window 字當 context,phrase 包 【】 標記"""
    idx = text.find(phrase)
    if idx < 0:
        return text[:80]
    start = max(0, idx - window)
    end = min(len(text), idx + len(phrase) + window)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return prefix + text[start:idx] + "【" + phrase + "】" + text[idx + len(phrase):end] + suffix


# ═══════════════════════════════════════════════════════════════════
# 配置調整 API(後台可調 threshold)
# ═══════════════════════════════════════════════════════════════════

def tm_set_thresholds(fuzzy_bypass: Optional[int] = None,
                      fuzzy_inject: Optional[int] = None,
                      fuzzy_topk: Optional[int] = None) -> Dict[str, int]:
    """調整 fuzzy match 門檻(後台可調,持久化)
    
    Returns: 當前 threshold 配置
    """
    global TM_FUZZY_THRESHOLD_BYPASS, TM_FUZZY_THRESHOLD_INJECT, TM_FUZZY_TOPK
    if fuzzy_bypass is not None:
        TM_FUZZY_THRESHOLD_BYPASS = max(70, min(100, int(fuzzy_bypass)))
    if fuzzy_inject is not None:
        TM_FUZZY_THRESHOLD_INJECT = max(30, min(95, int(fuzzy_inject)))
    if fuzzy_topk is not None:
        TM_FUZZY_TOPK = max(1, min(10, int(fuzzy_topk)))
    cfg = {
        "fuzzy_bypass": TM_FUZZY_THRESHOLD_BYPASS,
        "fuzzy_inject": TM_FUZZY_THRESHOLD_INJECT,
        "fuzzy_topk": TM_FUZZY_TOPK,
    }
    try:
        import phase_config_store as _pcs
        _pcs.save_config("tm", cfg)
    except Exception as _e:
        logger.warning("[TM] save persisted config failed: %s", _e)
    return cfg


# ═══════════════════════════════════════════════════════════════════
# 模組載入時自動 init + 載入持久化 threshold
# ═══════════════════════════════════════════════════════════════════
init()
try:
    import phase_config_store as _pcs
    _saved = _pcs.load_config("tm")
    if _saved:
        TM_FUZZY_THRESHOLD_BYPASS = _saved.get("fuzzy_bypass", TM_FUZZY_THRESHOLD_BYPASS)
        TM_FUZZY_THRESHOLD_INJECT = _saved.get("fuzzy_inject", TM_FUZZY_THRESHOLD_INJECT)
        TM_FUZZY_TOPK = _saved.get("fuzzy_topk", TM_FUZZY_TOPK)
        logger.info("[TM] loaded persisted thresholds: %s", _saved)
except Exception as _e:
    logger.warning("[TM] load persisted thresholds failed: %s", _e)
