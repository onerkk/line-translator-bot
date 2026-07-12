"""Additional LINE translation workflows.

This module intentionally contains pure, independently testable helpers for:
- personal language preferences;
- shift-handover summarisation prompts;
- original-image + translated-text comparison images;
- the LIFF turn-by-turn voice interpreter page.

Network/API calls and LINE delivery remain in ``app.py`` so these helpers can be
validated without credentials.
"""

from __future__ import annotations

import html
import io
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


SUPPORTED_PERSONAL_LANGS = ("zh", "id", "vi", "th", "tl", "en", "ja", "ko", "hi")

LANGUAGE_LABELS = {
    "auto": "自動偵測",
    "zh": "繁體中文",
    "id": "印尼文",
    "vi": "越南文",
    "th": "泰文",
    "tl": "菲律賓文",
    "en": "英文",
    "ja": "日文",
    "ko": "韓文",
    "hi": "印地文",
}

LANGUAGE_ALIASES = {
    "中文": "zh",
    "繁中": "zh",
    "繁體中文": "zh",
    "chinese": "zh",
    "zh-tw": "zh",
    "印尼": "id",
    "印尼文": "id",
    "印尼語": "id",
    "indonesian": "id",
    "bahasa": "id",
    "越南": "vi",
    "越南文": "vi",
    "越南語": "vi",
    "vietnamese": "vi",
    "泰文": "th",
    "泰語": "th",
    "thai": "th",
    "菲律賓文": "tl",
    "菲律賓語": "tl",
    "他加祿": "tl",
    "tagalog": "tl",
    "filipino": "tl",
    "英文": "en",
    "英語": "en",
    "english": "en",
    "日文": "ja",
    "日語": "ja",
    "japanese": "ja",
    "韓文": "ko",
    "韓語": "ko",
    "korean": "ko",
    "印地文": "hi",
    "印地語": "hi",
    "hindi": "hi",
}


def normalize_personal_language(value: str | None) -> str | None:
    """Normalise a language code/name accepted by ``/mylang``."""
    raw = (value or "").strip().lower()
    if not raw:
        return None
    raw = raw.replace("_", "-")
    if raw in ("zh-tw", "zh-hant", "zh-hant-tw"):
        return "zh"
    if raw in SUPPORTED_PERSONAL_LANGS:
        return raw
    return LANGUAGE_ALIASES.get(raw)


@dataclass(frozen=True)
class HandoverEntry:
    timestamp: float
    sender: str
    source_language: str
    source_text: str
    translations: Mapping[str, str]


def compact_handover_entries(
    entries: Iterable[Mapping[str, Any] | HandoverEntry],
    *,
    max_entries: int = 80,
    max_chars: int = 12_000,
) -> list[dict[str, Any]]:
    """Keep the newest useful messages while preserving codes/numbers verbatim."""
    normalised: list[dict[str, Any]] = []
    for item in entries:
        if isinstance(item, HandoverEntry):
            row = {
                "timestamp": item.timestamp,
                "sender": item.sender,
                "source_language": item.source_language,
                "source_text": item.source_text,
                "translations": dict(item.translations),
            }
        elif isinstance(item, Mapping):
            row = {
                "timestamp": float(item.get("timestamp", item.get("ts", 0)) or 0),
                "sender": str(item.get("sender", "") or ""),
                "source_language": str(item.get("source_language", item.get("lang", "")) or ""),
                "source_text": str(item.get("source_text", item.get("text", "")) or "").strip(),
                "translations": dict(item.get("translations", {}) or {}),
            }
        else:
            continue
        if not row["source_text"]:
            continue
        normalised.append(row)

    normalised.sort(key=lambda row: row["timestamp"])
    normalised = normalised[-max(1, max_entries):]

    kept: list[dict[str, Any]] = []
    used = 0
    for row in reversed(normalised):
        packed = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
        if kept and used + len(packed) > max_chars:
            break
        kept.append(row)
        used += len(packed)
    kept.reverse()
    return kept


def build_handover_messages(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    """Build a concise bilingual shift-handover request for one LLM call."""
    payload = json.dumps(list(entries), ensure_ascii=False, separators=(",", ":"))
    system = (
        "You prepare a bilingual shift handover for a stainless-steel factory. "
        "Use only facts present in the supplied messages. Never invent status, causes, owners, deadlines, "
        "or completion. Preserve every equipment code, material/order code, number, unit, time, negation, "
        "and safety restriction exactly. Merge duplicates but keep unresolved contradictions explicit. "
        "Return strict JSON only with keys zh and id. Each value must be a compact plain-text handover with "
        "these headings when applicable: 設備/Peralatan, 品質/Kualitas, 未完成/Belum selesai, "
        "下一班注意/Perhatian shift berikutnya, 工安/Keselamatan. Omit empty sections. "
        "The Indonesian must convey the same facts and strength as the Chinese."
    )
    user = (
        "Summarise the following chronological group messages. Messages may already contain translations; "
        "treat the source_text as primary and translations only as interpretation aids.\n"
        f"<messages>{payload}</messages>"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_handover_response(raw: str | None) -> dict[str, str] | None:
    """Parse strict or fenced JSON and reject incomplete bilingual results."""
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    zh = str(data.get("zh", "") or "").strip()
    id_text = str(data.get("id", "") or "").strip()
    if not zh or not id_text:
        return None
    return {"zh": zh, "id": id_text}


def _font_candidates(bold: bool = False) -> tuple[str, ...]:
    names = (
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold
        else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    return tuple(path for path in names if os.path.exists(path))


def _load_font(size: int, *, bold: bool = False):
    from PIL import ImageFont

    for path in _font_candidates(bold=bold):
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap_pixel(draw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in (text or "").splitlines() or [""]:
        if not paragraph:
            lines.append("")
            continue
        current = ""
        for ch in paragraph:
            trial = current + ch
            try:
                width = draw.textlength(trial, font=font)
            except Exception:
                width = len(trial) * 16
            if current and width > max_width:
                lines.append(current)
                current = ch
            else:
                current = trial
        lines.append(current)
    return lines


def render_translation_comparison_image(
    image_bytes: bytes,
    source_text: str,
    translated_text: str,
    *,
    source_label: str = "原文",
    target_label: str = "譯文",
    max_image_width: int = 1100,
    jpeg_quality: int = 88,
) -> bytes:
    """Render an original-image + readable translation panel as a JPEG.

    A comparison panel is deliberately used instead of guessing OCR coordinates.
    It preserves the original pixels and makes every translated line auditable.
    """
    if not image_bytes:
        raise ValueError("image_bytes is empty")
    if not translated_text or not translated_text.strip():
        raise ValueError("translated_text is empty")

    from PIL import Image, ImageDraw, ImageOps

    with Image.open(io.BytesIO(image_bytes)) as opened:
        original = ImageOps.exif_transpose(opened).convert("RGB")
    if original.width > max_image_width:
        ratio = max_image_width / float(original.width)
        original = original.resize((max_image_width, max(1, int(original.height * ratio))))

    panel_width = max(420, min(900, original.width))
    margin = 30
    body_font_size = max(22, min(38, panel_width // 24))
    title_font = _load_font(body_font_size + 6, bold=True)
    body_font = _load_font(body_font_size)
    small_font = _load_font(max(16, body_font_size - 6))

    probe = Image.new("RGB", (panel_width, 100), "white")
    probe_draw = ImageDraw.Draw(probe)
    source_lines = _wrap_pixel(probe_draw, source_text.strip(), small_font, panel_width - margin * 2)
    target_lines = _wrap_pixel(probe_draw, translated_text.strip(), body_font, panel_width - margin * 2)
    line_h = body_font_size + 12
    small_h = max(22, body_font_size)
    panel_height = (
        margin + (body_font_size + 12) + 12
        + max(1, len(target_lines)) * line_h
        + 26 + (body_font_size + 6) + 10
        + max(1, len(source_lines)) * small_h
        + margin
    )
    panel_height = max(panel_height, original.height)

    canvas = Image.new("RGB", (original.width + panel_width, panel_height), (245, 247, 250))
    y_image = max(0, (panel_height - original.height) // 2)
    canvas.paste(original, (0, y_image))
    draw = ImageDraw.Draw(canvas)
    x0 = original.width
    draw.rectangle((x0, 0, x0 + panel_width, panel_height), fill=(248, 250, 252))
    draw.rectangle((x0, 0, x0 + 8, panel_height), fill=(42, 157, 143))

    x = x0 + margin
    y = margin
    draw.text((x, y), target_label, font=title_font, fill=(20, 35, 50))
    y += body_font_size + 22
    for line in target_lines:
        draw.text((x, y), line or " ", font=body_font, fill=(10, 20, 30))
        y += line_h

    y += 14
    draw.line((x, y, x0 + panel_width - margin, y), fill=(205, 212, 220), width=2)
    y += 18
    draw.text((x, y), source_label, font=title_font, fill=(80, 90, 105))
    y += body_font_size + 16
    for line in source_lines:
        draw.text((x, y), line or " ", font=small_font, fill=(90, 100, 115))
        y += small_h

    out = io.BytesIO()
    canvas.save(out, format="JPEG", quality=max(70, min(95, jpeg_quality)), optimize=True)
    return out.getvalue()


def build_interpreter_html(*, liff_id: str = "") -> str:
    """Return a mobile-first, turn-by-turn voice interpreter UI."""
    safe_liff = html.escape(liff_id or "", quote=True)
    options = "".join(
        f'<option value="{code}">{html.escape(label)}</option>'
        for code, label in LANGUAGE_LABELS.items()
        if code != "auto"
    )
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<meta name="liff-id" content="{safe_liff}">
<title>即時雙向口譯</title>
<style>
:root{{--bg:#0d1321;--card:#172038;--text:#f7fafc;--muted:#9ca9bd;--accent:#18a999;--danger:#ef5b5b}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,"Noto Sans TC",sans-serif}}
main{{max-width:720px;margin:auto;padding:18px}}h1{{font-size:22px;margin:4px 0 6px}}.sub{{color:var(--muted);font-size:13px;margin-bottom:16px}}
.card{{background:var(--card);border-radius:16px;padding:16px;margin-bottom:14px;box-shadow:0 10px 25px #0004}}
.row{{display:grid;grid-template-columns:1fr 54px 1fr;gap:8px;align-items:center}}select,button{{font:inherit;border:0;border-radius:12px}}
select{{width:100%;padding:12px;background:#25304c;color:var(--text)}}button{{cursor:pointer}}#swap{{height:44px;background:#25304c;color:white}}
#record{{width:150px;height:150px;border-radius:50%;display:block;margin:22px auto;background:var(--accent);color:white;font-size:20px;font-weight:700;box-shadow:0 0 0 12px #18a99922}}
#record.recording{{background:var(--danger);box-shadow:0 0 0 12px #ef5b5b22}}#status{{text-align:center;color:var(--muted);min-height:24px}}
.label{{font-size:12px;color:var(--muted);margin-bottom:7px}}.text{{white-space:pre-wrap;line-height:1.6;min-height:46px}}
.actions{{display:flex;gap:8px;margin-top:10px}}.actions button{{padding:10px 14px;background:#25304c;color:white;flex:1}}
small{{color:var(--muted)}}
</style>
<script src="https://static.line-scdn.net/liff/edge/2/sdk.js"></script>
</head>
<body><main>
<h1>🎙️ 即時雙向口譯</h1><div class="sub">按一下開始說話，再按一下停止。系統會辨識、翻譯並播放譯文。</div>
<div class="card"><div class="row"><select id="src">{options}</select><button id="swap">⇄</button><select id="tgt">{options}</select></div>
<button id="record">開始說話</button><div id="status">準備完成</div></div>
<div class="card"><div class="label">辨識原文</div><div class="text" id="transcript">—</div></div>
<div class="card"><div class="label">翻譯結果</div><div class="text" id="translation">—</div><div class="actions"><button id="play">🔊 播放譯文</button><button id="reverse">↩ 交換再說</button></div></div>
<small>此模式採短回合錄音，避免長時間開啟麥克風；設備代號、數字與單位會交由相同翻譯品質管線處理。</small>
<audio id="audio" preload="none"></audio>
</main>
<script>
const NONCE=new URLSearchParams(location.search).get('nonce')||'';
const src=document.getElementById('src'),tgt=document.getElementById('tgt');src.value='zh';tgt.value='id';
let recorder=null,chunks=[],audioUrl='';
async function initLiff(){{try{{const id=document.querySelector('meta[name="liff-id"]').content;if(id&&window.liff)await liff.init({{liffId:id}})}}catch(e){{}}}}
function swap(){{const a=src.value;src.value=tgt.value;tgt.value=a}}document.getElementById('swap').onclick=swap;document.getElementById('reverse').onclick=swap;
document.getElementById('play').onclick=()=>{{if(audioUrl){{const a=document.getElementById('audio');a.src=audioUrl;a.play()}}}};
async function start(){{
 const stream=await navigator.mediaDevices.getUserMedia({{audio:true}});chunks=[];
 const preferred=['audio/webm;codecs=opus','audio/mp4','audio/webm'].find(x=>MediaRecorder.isTypeSupported(x));
 recorder=new MediaRecorder(stream,preferred?{{mimeType:preferred}}:undefined);recorder.ondataavailable=e=>{{if(e.data.size)chunks.push(e.data)}};
 recorder.onstop=async()=>{{stream.getTracks().forEach(t=>t.stop());await send(new Blob(chunks,{{type:recorder.mimeType||'audio/webm'}}))}};
 recorder.start();document.getElementById('record').classList.add('recording');document.getElementById('record').textContent='停止並翻譯';document.getElementById('status').textContent='正在聆聽…';
}}
async function stop(){{if(recorder&&recorder.state==='recording')recorder.stop();document.getElementById('record').classList.remove('recording');document.getElementById('record').textContent='開始說話';document.getElementById('status').textContent='辨識與翻譯中…'}}
document.getElementById('record').onclick=async()=>{{try{{if(recorder&&recorder.state==='recording')await stop();else await start()}}catch(e){{document.getElementById('status').textContent='無法使用麥克風：'+e.message}}}};
async function send(blob){{
 const fd=new FormData();fd.append('audio',blob,'speech.webm');fd.append('nonce',NONCE);fd.append('source',src.value);fd.append('target',tgt.value);fd.append('speak','1');
 try{{const r=await fetch('/api/interpreter/translate',{{method:'POST',body:fd}});const d=await r.json();if(!r.ok||!d.ok)throw new Error(d.error||r.status);
 document.getElementById('transcript').textContent=d.transcript||'—';document.getElementById('translation').textContent=d.translation||'—';audioUrl=d.audio_url||'';
 document.getElementById('status').textContent='完成';if(audioUrl){{const a=document.getElementById('audio');a.src=audioUrl;await a.play().catch(()=>{{}})}}
 }}catch(e){{document.getElementById('status').textContent='失敗：'+e.message}}
}}
initLiff();
</script></body></html>"""
