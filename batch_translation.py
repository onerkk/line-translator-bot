"""
batch_translation.py — Batch Translation API integration v1.0 (2026-05-20)

業界主流批次翻譯架構,適合非即時場景:
- 歷史訊息批次回填 TM
- 大量文件翻譯
- 後台批量重翻(改 glossary 後)

【官方文件】
- Anthropic Message Batches API:
  https://docs.anthropic.com/en/docs/build-with-claude/batch-processing
  價格 50% off,24h 內處理(通常 1h 內),100,000 messages per batch
- OpenAI Batch API:
  https://platform.openai.com/docs/guides/batch
  價格 50% off,24h 內處理,50,000 requests per batch

【雙系統相容】
- 跟隨 active_provider 自動選用對應 batch API
- API response format 統一

【不用於即時翻譯】
- LINE 訊息進來必須即時回應,不能等 batch
- batch 只用於管理任務(後台「重翻所有低分翻譯」、「歷史訊息匯入」等)

【設計】
- submit_batch():建立 batch job,return job_id
- check_batch_status(job_id):查狀態
- retrieve_batch_results(job_id):取結果
- list_batches():列出所有 jobs(供後台監控)
"""

import os
import json
import time
import logging
import tempfile
import threading
import uuid
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════════════════
BATCH_ENABLED = True

BATCH_MODEL_BY_PROVIDER = {
    "openai": "gpt-5.4-mini",                    # 批次翻譯品質/成本平衡
    "anthropic": "claude-haiku-4-5-20251001",
}

# Restore backend configuration from the unified cloud phase store.
try:
    import phase_config_store as _pcs
    _saved_batch = _pcs.load_config("batch")
    if isinstance(_saved_batch, dict):
        if "enabled" in _saved_batch:
            BATCH_ENABLED = bool(_saved_batch["enabled"])
        _models = _saved_batch.get("model_by_provider")
        if isinstance(_models, dict):
            for _provider in ("openai", "anthropic"):
                if _models.get(_provider):
                    BATCH_MODEL_BY_PROVIDER[_provider] = str(_models[_provider])
except Exception as _e:
    logger.warning("[Batch] load persisted config failed: %s", _e)

# In-memory job registry(重啟會清掉,重要 job 要持久化到 DB)
# 結構:{job_id: {"provider":..., "batch_id":..., "submitted_at":..., "status":...}}
_jobs: Dict[str, Dict[str, Any]] = {}
_lock = threading.RLock()

_stats = {
    "submitted": 0,
    "completed": 0,
    "failed": 0,
    "total_requests_batched": 0,
}


def _resolve_batch_model() -> str:
    try:
        import ai_provider
        provider = ai_provider.get_active_provider()
        return BATCH_MODEL_BY_PROVIDER.get(provider, BATCH_MODEL_BY_PROVIDER["openai"])
    except Exception:
        return BATCH_MODEL_BY_PROVIDER["openai"]


def _resolve_provider() -> str:
    try:
        import ai_provider
        return ai_provider.get_active_provider()
    except Exception:
        return "openai"


# ═══════════════════════════════════════════════════════════════════
# Anthropic Message Batches API
# ═══════════════════════════════════════════════════════════════════
def _submit_anthropic_batch(translation_tasks: List[Dict[str, str]],
                           model: str) -> Optional[str]:
    """提交 Anthropic batch
    
    Args:
        translation_tasks: [{"id": "task_1", "src_text":..., "src_lang":..., "tgt_lang":...}, ...]
    
    Returns: batch_id or None
    """
    try:
        from anthropic import Anthropic
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.warning("[Batch] no ANTHROPIC_API_KEY")
            return None
        client = Anthropic(api_key=api_key)
        
        requests = []
        for task in translation_tasks:
            requests.append({
                "custom_id": task["id"],
                "params": {
                    "model": model,
                    "max_tokens": 1024,
                    "messages": [{
                        "role": "user",
                        "content": f"Translate from {task['src_lang']} to {task['tgt_lang']}: {task['src_text']}"
                    }],
                }
            })
        
        batch = client.messages.batches.create(requests=requests)
        logger.info("[Batch] Anthropic batch submitted: %s, requests=%d",
                    batch.id, len(requests))
        return batch.id
    except Exception as e:
        logger.error("[Batch] Anthropic submit failed: %s", e)
        return None


def _check_anthropic_batch(batch_id: str) -> Optional[Dict[str, Any]]:
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        batch = client.messages.batches.retrieve(batch_id)
        return {
            "processing_status": batch.processing_status,  # "in_progress" | "ended" | "canceling" | "canceled"
            "request_counts": {
                "processing": batch.request_counts.processing,
                "succeeded": batch.request_counts.succeeded,
                "errored": batch.request_counts.errored,
                "canceled": batch.request_counts.canceled,
                "expired": batch.request_counts.expired,
            },
            "ended_at": str(batch.ended_at) if batch.ended_at else None,
            "results_url": batch.results_url if hasattr(batch, "results_url") else None,
        }
    except Exception as e:
        logger.error("[Batch] Anthropic check failed: %s", e)
        return None


def _retrieve_anthropic_results(batch_id: str) -> List[Dict[str, Any]]:
    try:
        from anthropic import Anthropic
        client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
        results = []
        for entry in client.messages.batches.results(batch_id):
            entry_dict = {"custom_id": entry.custom_id}
            if entry.result.type == "succeeded":
                msg = entry.result.message
                text = ""
                if msg.content:
                    for block in msg.content:
                        if hasattr(block, "text"):
                            text += block.text
                entry_dict["status"] = "succeeded"
                entry_dict["text"] = text.strip()
            elif entry.result.type == "errored":
                entry_dict["status"] = "errored"
                entry_dict["error"] = str(entry.result.error)
            else:
                entry_dict["status"] = entry.result.type
            results.append(entry_dict)
        return results
    except Exception as e:
        logger.error("[Batch] Anthropic retrieve failed: %s", e)
        return []


# ═══════════════════════════════════════════════════════════════════
# OpenAI Batch API
# ═══════════════════════════════════════════════════════════════════
def _submit_openai_batch(translation_tasks: List[Dict[str, str]],
                        model: str) -> Optional[str]:
    """OpenAI batch 流程:upload JSONL → create batch → return batch.id"""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""), timeout=60.0)
        
        # 寫 JSONL 暫存檔
        lines = []
        for task in translation_tasks:
            lines.append(json.dumps({
                "custom_id": task["id"],
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": model,
                    "messages": [{
                        "role": "user",
                        "content": f"Translate from {task['src_lang']} to {task['tgt_lang']}: {task['src_text']}"
                    }],
                    "max_tokens": 1024,
                }
            }, ensure_ascii=False))
        
        # Upload
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False, encoding="utf-8") as tf:
            tf.write("\n".join(lines))
            tmp_path = tf.name
        try:
            with open(tmp_path, "rb") as f:
                file_obj = client.files.create(file=f, purpose="batch")
            batch = client.batches.create(
                input_file_id=file_obj.id,
                endpoint="/v1/chat/completions",
                completion_window="24h",
            )
            logger.info("[Batch] OpenAI batch submitted: %s, requests=%d",
                        batch.id, len(lines))
            return batch.id
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    except Exception as e:
        logger.error("[Batch] OpenAI submit failed: %s", e)
        return None


def _check_openai_batch(batch_id: str) -> Optional[Dict[str, Any]]:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        batch = client.batches.retrieve(batch_id)
        return {
            "processing_status": batch.status,
            "request_counts": {
                "total": batch.request_counts.total,
                "completed": batch.request_counts.completed,
                "failed": batch.request_counts.failed,
            },
            "ended_at": str(batch.completed_at) if batch.completed_at else None,
            "output_file_id": batch.output_file_id,
            "error_file_id": batch.error_file_id,
        }
    except Exception as e:
        logger.error("[Batch] OpenAI check failed: %s", e)
        return None


def _retrieve_openai_results(batch_id: str) -> List[Dict[str, Any]]:
    try:
        from openai import OpenAI
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
        batch = client.batches.retrieve(batch_id)
        if not batch.output_file_id:
            return []
        content = client.files.content(batch.output_file_id).read()
        if isinstance(content, bytes):
            content = content.decode("utf-8")
        results = []
        for line in content.strip().split("\n"):
            if not line:
                continue
            obj = json.loads(line)
            custom_id = obj.get("custom_id", "")
            response = obj.get("response", {})
            body = response.get("body", {})
            choices = body.get("choices", [])
            text = ""
            if choices:
                text = choices[0].get("message", {}).get("content", "")
            results.append({
                "custom_id": custom_id,
                "status": "succeeded" if text else "errored",
                "text": text.strip() if text else "",
                "error": obj.get("error"),
            })
        return results
    except Exception as e:
        logger.error("[Batch] OpenAI retrieve failed: %s", e)
        return []


# ═══════════════════════════════════════════════════════════════════
# 統一介面
# ═══════════════════════════════════════════════════════════════════
def submit_batch(translation_tasks: List[Dict[str, str]]) -> Optional[str]:
    """提交批次翻譯,雙系統自動路由
    
    Args:
        translation_tasks: [{"id": str, "src_text": str, "src_lang": str, "tgt_lang": str}, ...]
    
    Returns: job_id (內部用) 或 None
    """
    if not BATCH_ENABLED or not translation_tasks:
        return None
    
    provider = _resolve_provider()
    model = _resolve_batch_model()
    
    if provider == "anthropic":
        batch_id = _submit_anthropic_batch(translation_tasks, model)
    else:
        batch_id = _submit_openai_batch(translation_tasks, model)
    
    if not batch_id:
        return None
    
    job_id = f"job_{uuid.uuid4().hex[:12]}"
    with _lock:
        _jobs[job_id] = {
            "provider": provider,
            "model": model,
            "batch_id": batch_id,
            "submitted_at": int(time.time()),
            "status": "submitted",
            "task_count": len(translation_tasks),
        }
        _stats["submitted"] += 1
        _stats["total_requests_batched"] += len(translation_tasks)
    
    return job_id


def check_batch_status(job_id: str) -> Optional[Dict[str, Any]]:
    """查 batch job 狀態"""
    with _lock:
        job = _jobs.get(job_id)
    if not job:
        return None
    
    if job["provider"] == "anthropic":
        status = _check_anthropic_batch(job["batch_id"])
    else:
        status = _check_openai_batch(job["batch_id"])
    
    if status:
        with _lock:
            _jobs[job_id]["last_check"] = int(time.time())
            _jobs[job_id]["status"] = status.get("processing_status", "unknown")
        return {**job, **status}
    return job


def retrieve_batch_results(job_id: str) -> List[Dict[str, Any]]:
    """取 batch 結果"""
    with _lock:
        job = _jobs.get(job_id)
    if not job:
        return []
    
    if job["provider"] == "anthropic":
        results = _retrieve_anthropic_results(job["batch_id"])
    else:
        results = _retrieve_openai_results(job["batch_id"])
    
    if results:
        with _lock:
            success = sum(1 for r in results if r.get("status") == "succeeded")
            _jobs[job_id]["completed_count"] = success
            _stats["completed"] += success
    
    return results


def list_batches(limit: int = 50) -> List[Dict[str, Any]]:
    """列出所有 job(供後台監控)"""
    with _lock:
        jobs = sorted(_jobs.items(),
                      key=lambda kv: kv[1].get("submitted_at", 0),
                      reverse=True)[:limit]
    return [{"job_id": jid, **info} for jid, info in jobs]


def batch_stats() -> Dict[str, Any]:
    with _lock:
        s = dict(_stats)
        s["jobs_in_registry"] = len(_jobs)
    s["enabled"] = BATCH_ENABLED
    s["active_provider"] = _resolve_provider()
    s["model_current"] = _resolve_batch_model()
    s["model_by_provider"] = dict(BATCH_MODEL_BY_PROVIDER)
    s["note"] = "Batch API 不用於即時翻譯,僅供後台批量任務。Anthropic + OpenAI 都是 50% off。"
    return s


def batch_set_config(enabled: Optional[bool] = None,
                     openai_model: Optional[str] = None,
                     anthropic_model: Optional[str] = None) -> Dict[str, Any]:
    global BATCH_ENABLED
    if enabled is not None:
        BATCH_ENABLED = bool(enabled)
    if openai_model:
        try:
            import ai_provider as _aip
            BATCH_MODEL_BY_PROVIDER["openai"] = _aip.normalize_translation_model(
                openai_model, _aip.DEFAULT_OPENAI_MODEL)
        except Exception:
            BATCH_MODEL_BY_PROVIDER["openai"] = "gpt-5.4-mini"
    if anthropic_model:
        BATCH_MODEL_BY_PROVIDER["anthropic"] = str(anthropic_model)
    cfg = {
        "enabled": BATCH_ENABLED,
        "model_by_provider": dict(BATCH_MODEL_BY_PROVIDER),
        "model_current": _resolve_batch_model(),
    }
    try:
        import phase_config_store as _pcs
        _pcs.save_config("batch", {
            "enabled": BATCH_ENABLED,
            "model_by_provider": dict(BATCH_MODEL_BY_PROVIDER),
        })
    except Exception as _e:
        logger.warning("[Batch] save persisted config failed: %s", _e)
    return cfg
