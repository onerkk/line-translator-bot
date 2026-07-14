"""Local expressive-media manifest and deterministic selection.

The selector never performs network or AI calls.  It loads the versioned local
asset manifest, filters by context/safety/style, and uses a short in-process
cooldown so active LINE groups are not flooded with the same card.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

EXPRESSIVE_ASSETS_VERSION = "2026-07-14.3-workplace-context"

_ROOT = Path(__file__).resolve().parent
_DEFAULT_MANIFEST = _ROOT / "static" / "expressive_media" / "manifest.json"


@dataclass(frozen=True)
class ExpressiveAsset:
    id: str
    file: str
    category: str
    intent: tuple[str, ...]
    emotion: tuple[str, ...]
    styles: tuple[str, ...]
    min_intensity: int = 1
    max_intensity: int = 3
    workplace_safe: bool = True
    factory_safe: bool = False
    short_message: bool = True


@dataclass(frozen=True)
class VisualSelection:
    asset: ExpressiveAsset
    presentation: str  # image / card
    title_key: str

    @property
    def relative_url(self) -> str:
        return "/static/expressive_media/" + self.asset.file.lstrip("/")


_MANIFEST_LOCK = threading.Lock()
_MANIFEST_CACHE: tuple[float, tuple[ExpressiveAsset, ...]] | None = None
_RECENT_LOCK = threading.Lock()
_RECENT_BY_CONTEXT: dict[str, list[tuple[float, str]]] = {}

_STYLE_VALUES = {"auto", "cute", "minimal", "formal", "factory", "photo"}

_TITLE_KEYS = {
    "greeting": "greeting",
    "joy": "joy",
    "gratitude": "gratitude",
    "apology": "apology",
    "praise": "praise",
    "encouragement": "encouragement",
    "concern": "concern",
    "question": "question",
    "reminder": "reminder",
    "notice": "notice",
    "warning": "warning",
    "urgent": "urgent",
    "celebration": "celebration",
    "calm": "calm",
    "factory_notice": "factory_notice",
    "safety": "safety",
    "equipment": "equipment",
    "quality": "quality",
    "gathering": "gathering",
    "weather": "weather",
}

_FACTORY_RE = re.compile(
    r"機台|設備|工單|料號|爐號|站別|品質|品保|研磨|冷抽|退火|酸洗|矯直|拋光|倒角|噴砂|噴漆|"
    r"包裝|捆包|棒材|線材|盤元|吊料|上料|下料|停機|停線|安全帽|護具|"
    r"\b(?:mesin|work\s*order|material|station|quality|grinding|painting|annealing|pickling|"
    r"polishing|packing|bundle|shutdown|safety)\b",
    re.I,
)
_NOTICE_RE = re.compile(
    r"公告|通知|提醒|請大家|務必|規定|主管|處長|巡視|人數|聚集|"
    r"會議|班會|股會|班股|交班|集合|上班|下班|加班|班別|早班|夜班|小夜班|會議室|"
    r"@all|＠all|"
    r"\b(?:pengumuman|pemberitahuan|harap|wajib|aturan|atasan|kepala\s+divisi|berkumpul|"
    r"rapat|shift|lembur|ruang\s+rapat|masuk\s+kerja|pulang\s+kerja|kumpul)\b",
    re.I,
)
_WARNING_RE = re.compile(
    r"危險|警告|禁止|不得|不可|緊急|立即|立刻|注意安全|停機|停線|"
    r"\b(?:bahaya|peringatan|dilarang|darurat|segera|wajib|stop)\b",
    re.I,
)
_GATHERING_RE = re.compile(r"聚集|集合|人數|吸菸區|休息區|\b(?:berkumpul|jumlah\s+orang|area\s+merokok)\b", re.I)
_EQUIPMENT_RE = re.compile(r"機台|設備|故障|維修|保養|刀具|砂輪|\b(?:mesin|peralatan|rusak|perbaikan|maintenance)\b", re.I)
_QUALITY_RE = re.compile(r"品質|品保|檢查|不良|異常|尺寸|重量|\b(?:quality|qc|inspeksi|cacat|abnormal|ukuran|berat)\b", re.I)
_WEATHER_RE = re.compile(r"天氣|晴天|下雨|很熱|很冷|\b(?:cuaca|cerah|hujan|panas|dingin)\b", re.I)


def normalise_style(value: str | None) -> str:
    style = str(value or "auto").strip().lower()
    return style if style in _STYLE_VALUES else "auto"


def _as_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item))
    return ()


def load_assets(manifest_path: str | Path | None = None) -> tuple[ExpressiveAsset, ...]:
    """Load and validate the manifest, cached by mtime."""
    global _MANIFEST_CACHE
    path = Path(manifest_path) if manifest_path else _DEFAULT_MANIFEST
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return ()
    with _MANIFEST_LOCK:
        if _MANIFEST_CACHE and _MANIFEST_CACHE[0] == mtime:
            return _MANIFEST_CACHE[1]
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return ()
        assets: list[ExpressiveAsset] = []
        for row in raw.get("assets", []):
            if not isinstance(row, dict):
                continue
            file_name = str(row.get("file", "")).strip().replace("\\", "/")
            asset_id = str(row.get("id", "")).strip()
            category = str(row.get("category", "")).strip()
            if not file_name or not asset_id or not category or ".." in Path(file_name).parts:
                continue
            if not (_DEFAULT_MANIFEST.parent / file_name).is_file():
                continue
            assets.append(ExpressiveAsset(
                id=asset_id,
                file=file_name,
                category=category,
                intent=_as_tuple(row.get("intent")),
                emotion=_as_tuple(row.get("emotion")),
                styles=_as_tuple(row.get("styles")),
                min_intensity=max(1, int(row.get("min_intensity", 1) or 1)),
                max_intensity=min(3, max(1, int(row.get("max_intensity", 3) or 3))),
                workplace_safe=bool(row.get("workplace_safe", True)),
                factory_safe=bool(row.get("factory_safe", False)),
                short_message=bool(row.get("short_message", True)),
            ))
        _MANIFEST_CACHE = (mtime, tuple(assets))
        return _MANIFEST_CACHE[1]


def classify_context(text: str | None) -> str:
    source = str(text or "")
    if _FACTORY_RE.search(source):
        return "factory"
    if _NOTICE_RE.search(source):
        return "workplace"
    return "social"


def infer_category(text: str | None, tone: str, visual_mood: str = "") -> str:
    source = str(text or "")
    if _WARNING_RE.search(source):
        return "urgent" if re.search(r"緊急|立即|立刻|darurat|segera", source, re.I) else "warning"
    if _GATHERING_RE.search(source):
        return "gathering"
    if _EQUIPMENT_RE.search(source):
        return "equipment"
    if _QUALITY_RE.search(source):
        return "quality"
    if _WEATHER_RE.search(source):
        return "weather"
    mapping = {
        "urgent_warning": "warning",
        "announcement": "notice",
        "instruction": "reminder",
        "management_pressure": "factory_notice",
        "crowd_report": "gathering",
        "positive": "joy",
        "frustration": "concern",
        "anger": "warning",
        "request": "reminder",
    }
    candidate = mapping.get(tone, visual_mood or tone)
    return candidate if candidate in _TITLE_KEYS else "calm"


def _auto_style(context: str, category: str, seed: str) -> str:
    if context == "factory":
        return "factory"
    if context == "workplace" or category in {"notice", "reminder", "warning", "urgent"}:
        return "formal"
    options = ("cute", "minimal", "photo")
    digest = hashlib.sha256(seed.encode("utf-8", "ignore")).digest()
    return options[digest[0] % len(options)]


def _intensity_number(value: str | int | None) -> int:
    if isinstance(value, int):
        return min(3, max(1, value))
    token = str(value or "natural").strip().lower()
    if token in {"subtle", "1"}:
        return 1
    if token in {"lively", "3"}:
        return 3
    return 2


def _cleanup_recent(now: float, max_age: float = 600.0) -> None:
    for key in list(_RECENT_BY_CONTEXT):
        rows = [(ts, asset_id) for ts, asset_id in _RECENT_BY_CONTEXT[key] if now - ts <= max_age]
        if rows:
            _RECENT_BY_CONTEXT[key] = rows[-10:]
        else:
            _RECENT_BY_CONTEXT.pop(key, None)


def select_visual(
    *,
    source_text: str | None,
    tone: str,
    visual_mood: str = "",
    preferred_style: str = "auto",
    intensity: str | int = "natural",
    presentation: str = "image",
    context_id: str = "global",
    short_message: bool = True,
    formal_safety: bool = True,
    now: float | None = None,
    group_cooldown_seconds: float = 180.0,
    same_asset_window: int = 10,
) -> VisualSelection | None:
    """Choose one safe local asset or return ``None`` during cooldown."""
    source = str(source_text or "").strip()
    if not source:
        return None
    assets = load_assets()
    if not assets:
        return None
    context = classify_context(source)
    category = infer_category(source, tone, visual_mood)
    style = normalise_style(preferred_style)
    if style == "auto":
        style = _auto_style(context, category, source + context_id)
    level = _intensity_number(intensity)
    candidates = [
        asset for asset in assets
        if asset.category == category
        and level >= asset.min_intensity
        and level <= asset.max_intensity
        and (style in asset.styles)
        and (not short_message or asset.short_message)
        and (context != "workplace" or asset.workplace_safe)
        and (context != "factory" or asset.factory_safe)
    ]
    if not candidates and style != "minimal":
        candidates = [
            asset for asset in assets
            if asset.category == category
            and "minimal" in asset.styles
            and (context != "factory" or asset.factory_safe)
        ]
    if not candidates:
        # Work notices can fall back to a semantically safe generic card.
        fallback = "factory_notice" if context == "factory" else "notice" if context == "workplace" else "calm"
        candidates = [asset for asset in assets if asset.category == fallback and (context != "factory" or asset.factory_safe)]
    if not candidates:
        return None

    current = float(now if now is not None else time.time())
    key = str(context_id or "global")
    with _RECENT_LOCK:
        _cleanup_recent(current)
        recent = _RECENT_BY_CONTEXT.get(key, [])
        if recent and current - recent[-1][0] < group_cooldown_seconds:
            return None
        recent_ids = [asset_id for _, asset_id in recent[-same_asset_window:]]
        unseen = [asset for asset in candidates if asset.id not in recent_ids]
        pool = unseen or candidates
        digest = hashlib.sha256((source + "|" + key + "|" + category + "|" + style).encode("utf-8", "ignore")).digest()
        selected = pool[int.from_bytes(digest[:4], "big") % len(pool)]
        _RECENT_BY_CONTEXT.setdefault(key, []).append((current, selected.id))
    selected_presentation = "card" if presentation == "card" else "image"
    if formal_safety and context in {"factory", "workplace"}:
        selected_presentation = "card"
    return VisualSelection(selected, selected_presentation, _TITLE_KEYS.get(selected.category, "notice"))


def asset_count() -> int:
    return len(load_assets())
