"""
tm_maintenance.py — Translation Memory Maintenance v1.0 (2026-05-20)

業界資料治理標準(ISO 17100 翻譯資料管理):
- 去重(Deduplication)
- Pruning(刪舊+少用 entry)
- Quality decay(老 entry 品質分隨時間降低,讓新 LLM 翻譯有機會覆蓋)
- 統計報告

【為什麼需要】
- TM 跑 6 個月後可能累積 10,000+ entries,其中很多 stale/duplicate
- LLM 翻譯品質持續提升,1 年前的 TM 可能不如現在 LLM 翻
- 但 human_corrected 條目永遠保留(ground truth)

【操作】
- deduplicate():同 src+tgt 多版本 → 留最高 hit_count + 最新版
- prune_unused(days, min_hits):刪 N 天沒用且 hit < min_hits 的條目
- decay_quality(days, factor):老 entry quality_score 衰減
- maintenance_report():統計重複/老舊/低用條目

【安全】
- 全部操作預設 dry_run=True(只報告不刪)
- human_corrected (model='human_corrected') 永遠保留
"""

import sqlite3
import time
import logging
import threading
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_stats = {
    "dedup_runs": 0,
    "dedup_removed": 0,
    "prune_runs": 0,
    "prune_removed": 0,
    "decay_runs": 0,
    "decay_affected": 0,
}


def _get_tm_db_path() -> Optional[str]:
    """取 TM DB 路徑(透過 translation_memory module)"""
    try:
        import translation_memory
        return translation_memory.TM_DB_PATH
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════════════
# 去重
# ═══════════════════════════════════════════════════════════════════
def deduplicate(dry_run: bool = True,
                preserve_human_corrected: bool = True) -> Dict[str, Any]:
    """去重:同 (src_lang, tgt_lang, src_text_hash) 多條 → 留最高 hit_count + 最新版
    
    Args:
        dry_run: True 只報告,不刪除
        preserve_human_corrected: True 永遠保留 model='human_corrected' 的條目
    
    Returns: {"found_dupes": N, "removed": N, "dry_run": bool, "details": [...]}
    """
    db_path = _get_tm_db_path()
    if not db_path:
        return {"ok": False, "error": "TM DB 未初始化"}
    
    result = {"found_dupes": 0, "removed": 0, "dry_run": dry_run, "details": []}
    
    try:
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            # 找出有重複的 (src_lang, tgt_lang, src_text_hash) 組合
            # NOTE: TM 已有 UNIQUE(src_lang, tgt_lang, src_text_hash, group_id) constraint
            # 所以真重複是「同 src 不同 group」— 這通常是有意的(語境隔離)
            # 但我們可以找:同 src 不同 tgt(LLM 翻不同版本)
            dupes = conn.execute("""
                SELECT src_lang, tgt_lang, src_text_hash, COUNT(*) c, COUNT(DISTINCT tgt_text) tgt_variants
                FROM tm_entries
                GROUP BY src_lang, tgt_lang, src_text_hash
                HAVING tgt_variants > 1
                ORDER BY c DESC
                LIMIT 100
            """).fetchall()
            
            for d in dupes:
                # 取此組所有 entries
                rows = conn.execute("""
                    SELECT id, src_text, tgt_text, group_id, hit_count, last_used_at, model, quality_score
                    FROM tm_entries
                    WHERE src_lang=? AND tgt_lang=? AND src_text_hash=?
                    ORDER BY 
                        CASE WHEN model='human_corrected' THEN 0 ELSE 1 END,
                        hit_count DESC, last_used_at DESC
                """, (d["src_lang"], d["tgt_lang"], d["src_text_hash"])).fetchall()
                
                if len(rows) <= 1:
                    continue
                
                # 第一筆是「優先保留」— human_corrected > 高 hit_count > 最新
                # 其餘可刪
                keeper = rows[0]
                losers = rows[1:]
                
                # 但若 keeper 不是 human_corrected,而 losers 中有 human_corrected,優先保留它
                # (上面 ORDER BY 已處理,這裡 double check)
                
                result["found_dupes"] += len(losers)
                result["details"].append({
                    "src_text": keeper["src_text"][:80],
                    "kept_tgt": keeper["tgt_text"][:80],
                    "kept_id": keeper["id"],
                    "loser_count": len(losers),
                    "loser_tgts": [l["tgt_text"][:50] for l in losers[:3]],
                })
                
                if not dry_run:
                    for loser in losers:
                        if preserve_human_corrected and loser["model"] == "human_corrected":
                            continue
                        conn.execute("DELETE FROM tm_entries WHERE id=?", (loser["id"],))
                        result["removed"] += 1
        
        with _lock:
            _stats["dedup_runs"] += 1
            _stats["dedup_removed"] += result["removed"]
        
        logger.info("[TM-Maint] dedup: found %d dupes, removed %d (dry_run=%s)",
                    result["found_dupes"], result["removed"], dry_run)
        return result
    except Exception as e:
        logger.error("[TM-Maint] dedup failed: %s", e)
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════
# Pruning
# ═══════════════════════════════════════════════════════════════════
def prune_unused(days_unused: int = 180, min_hits: int = 1,
                 dry_run: bool = True,
                 preserve_human_corrected: bool = True) -> Dict[str, Any]:
    """刪除 N 天沒用且 hit_count <= min_hits 的 entries
    
    Args:
        days_unused: 多少天沒用就算 unused(預設 180 天 = 6 個月)
        min_hits: hit_count <= 此值才會被 prune(預設 1 = 只用過一次)
        dry_run: 只報告不刪
        preserve_human_corrected: 永遠保留人工修正
    """
    db_path = _get_tm_db_path()
    if not db_path:
        return {"ok": False, "error": "TM DB 未初始化"}
    
    cutoff = int(time.time()) - days_unused * 86400
    result = {"found": 0, "removed": 0, "dry_run": dry_run, "cutoff_timestamp": cutoff}
    
    try:
        with sqlite3.connect(db_path) as conn:
            where = "last_used_at < ? AND hit_count <= ?"
            params = [cutoff, min_hits]
            if preserve_human_corrected:
                where += " AND (model IS NULL OR model != 'human_corrected')"
            
            count_row = conn.execute(f"SELECT COUNT(*) FROM tm_entries WHERE {where}",
                                      params).fetchone()
            result["found"] = count_row[0]
            
            if not dry_run and result["found"] > 0:
                cur = conn.execute(f"DELETE FROM tm_entries WHERE {where}", params)
                result["removed"] = cur.rowcount
        
        with _lock:
            _stats["prune_runs"] += 1
            _stats["prune_removed"] += result["removed"]
        
        logger.info("[TM-Maint] prune: found %d (>%dd unused, hits<=%d), removed %d (dry_run=%s)",
                    result["found"], days_unused, min_hits, result["removed"], dry_run)
        return result
    except Exception as e:
        logger.error("[TM-Maint] prune failed: %s", e)
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════
# Quality Decay
# ═══════════════════════════════════════════════════════════════════
def decay_quality(days_old: int = 365, factor: float = 0.9,
                  dry_run: bool = True) -> Dict[str, Any]:
    """老 entry 的 quality_score 衰減
    
    用途:LLM 持續進步,老的 LLM 翻譯應該讓出位置給新翻譯
    
    Args:
        days_old: created_at 早於 N 天的才衰減
        factor: quality_score *= factor(0-1)
        dry_run: 只報告不執行
    """
    db_path = _get_tm_db_path()
    if not db_path:
        return {"ok": False, "error": "TM DB 未初始化"}
    
    cutoff = int(time.time()) - days_old * 86400
    factor = max(0.0, min(1.0, factor))
    result = {"affected": 0, "dry_run": dry_run, "factor": factor}
    
    try:
        with sqlite3.connect(db_path) as conn:
            # human_corrected 永遠不衰減
            count_row = conn.execute("""
                SELECT COUNT(*) FROM tm_entries
                WHERE created_at < ?
                  AND quality_score IS NOT NULL
                  AND (model IS NULL OR model != 'human_corrected')
            """, (cutoff,)).fetchone()
            result["affected"] = count_row[0]
            
            if not dry_run and result["affected"] > 0:
                conn.execute("""
                    UPDATE tm_entries
                    SET quality_score = quality_score * ?
                    WHERE created_at < ?
                      AND quality_score IS NOT NULL
                      AND (model IS NULL OR model != 'human_corrected')
                """, (factor, cutoff))
        
        with _lock:
            _stats["decay_runs"] += 1
            _stats["decay_affected"] += result["affected"]
        
        logger.info("[TM-Maint] decay: %d entries affected (>%dd old, factor=%.2f, dry_run=%s)",
                    result["affected"], days_old, factor, dry_run)
        return result
    except Exception as e:
        logger.error("[TM-Maint] decay failed: %s", e)
        return {"ok": False, "error": str(e)}


# ═══════════════════════════════════════════════════════════════════
# 報告
# ═══════════════════════════════════════════════════════════════════
def maintenance_report() -> Dict[str, Any]:
    """全面報告:dupes / stale / low-quality / total"""
    db_path = _get_tm_db_path()
    if not db_path:
        return {"ok": False, "error": "TM DB 未初始化"}
    
    now = int(time.time())
    report = {}
    try:
        with sqlite3.connect(db_path) as conn:
            report["total_entries"] = conn.execute("SELECT COUNT(*) FROM tm_entries").fetchone()[0]
            
            report["human_corrected"] = conn.execute(
                "SELECT COUNT(*) FROM tm_entries WHERE model='human_corrected'"
            ).fetchone()[0]
            
            # 重複組數
            dupe_row = conn.execute("""
                SELECT COUNT(*) FROM (
                    SELECT 1 FROM tm_entries
                    GROUP BY src_lang, tgt_lang, src_text_hash
                    HAVING COUNT(DISTINCT tgt_text) > 1
                )
            """).fetchone()
            report["duplicate_groups"] = dupe_row[0]
            
            # Stale(180 天沒用)
            cutoff_180d = now - 180 * 86400
            report["stale_180d"] = conn.execute(
                "SELECT COUNT(*) FROM tm_entries WHERE last_used_at < ?", (cutoff_180d,)
            ).fetchone()[0]
            
            # 365 天前建立(可衰減)
            cutoff_365d = now - 365 * 86400
            report["older_than_365d"] = conn.execute(
                "SELECT COUNT(*) FROM tm_entries WHERE created_at < ?", (cutoff_365d,)
            ).fetchone()[0]
            
            # 低使用率(hit_count <= 1)
            report["low_usage"] = conn.execute(
                "SELECT COUNT(*) FROM tm_entries WHERE hit_count <= 1"
            ).fetchone()[0]
            
            # 平均 quality_score
            qs_row = conn.execute(
                "SELECT AVG(quality_score) FROM tm_entries WHERE quality_score IS NOT NULL"
            ).fetchone()
            report["avg_quality_score"] = round(qs_row[0], 2) if qs_row[0] else None
        
        with _lock:
            report["maintenance_stats"] = dict(_stats)
        return report
    except Exception as e:
        logger.error("[TM-Maint] report failed: %s", e)
        return {"ok": False, "error": str(e)}
