"""Bidirectional source-relation semantics for factory chat translation.

Glossary enforcement can prove that isolated words and numbers are present, but
it cannot prove that the target keeps their source roles.  This module extracts
small, compositional source frames for relations that are especially dangerous
on the shop floor: equipment-to-reading comparisons, reporting with a leader's
ID, and movement-to-a-location followed by inspection.

The rules are not sentence replacements.  Values, units, aspect, destination,
objects and mentions are read from the current source.  A direct translation is
produced only when every meaningful source token belongs to a supported slot;
otherwise the same frame becomes a provider prompt and a deterministic
completeness check for the ordinary translation path.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable, Mapping


FACTORY_MESSAGE_SEMANTICS_API_VERSION = 1
FACTORY_MESSAGE_SEMANTICS_BUILD_ID = "2026-08-13.1-contextual-shift-paint-claims"

_NUMBER = r"\d+(?:[.,]\d+)?"
_MENTION_RE = re.compile(
    r"(?:@[^\s,，。!?！？:：;；]{1,48}|__MENTION_\d+__)", re.I
)

_MONITOR_ID = (
    "layar monitor",
    "monitor display",
    "display monitor",
    "monitor",
)
_HOIST_SCALE_ID = (
    "timbangan gantung elektronik",
    "timbangan elektronik pada crane",
    "timbangan elektronik crane",
    "timbangan katrol",
    "timbangan gantung",
    "timbangan crane",
    "timbangan derek",
    "timbangan tian che",
    "timbangan hoist",
)
_LEADER_ID = (
    "ketu kelas",       # common missing-a typo in shop-floor chat
    "ketua kelas",      # factory-context misuse for the shift leader
    "ketua shift",
    "ketua regu",
    "kepala shift",
    "kepala regu",
)
_REPORT_ID = (
    "lapor",
    "laporan",
    "melapor",
    "melaporkan",
    "laporkan",
)

# Shop-floor chat often spells ``shift`` phonetically as sip/sif/shif.  ``sip``
# is also an ordinary acknowledgement (OK), so it must never be normalized as a
# shift by itself.  A following shift period plus a real clause is the required
# disambiguating evidence.  This keeps "Sip, terima kasih" conversational while
# making "sip pagi tidak ..." a morning-shift production claim.
_SHIFT_ALIAS_ID_RE = re.compile(
    r"(?<![a-z])(?P<shift>shift|shif|sif|sip)\s+"
    r"(?P<period>pagi|siang|sore|malam)(?![a-z])",
    re.I,
)
_SHIFT_CLAUSE_EVIDENCE_ID_RE = re.compile(
    r"(?<![a-z])(?:tidak|tida|tdk|tdak|gak|ga|nggak|ngga|belum|sudah|akan|"
    r"masih|jangan|mesin|material|barang|produksi|operator|cat|pengecatan|"
    r"rusak|selesai|masuk|keluar|kerja|bekerja|melakukan|memberi|mengasih|"
    r"mengecat|menyemprot)(?![a-z])",
    re.I,
)
_SHIFT_PERIOD_ZH = {
    "pagi": "早班",
    "siang": "中班",
    "sore": "小夜班",
    "malam": "夜班",
}
_NEGATIVE_ID_RE = re.compile(
    r"(?<![a-z])(?P<negative>tidak|tida|tdk|tdak|gak|ga|nggak|ngga|belum)(?![a-z])",
    re.I,
)
_PAINT_APPLICATION_ID_RE = re.compile(
    r"(?<![a-z])(?:"
    r"(?:mengasih|memberi|memberikan|kasih)\s+(?:warna\s+cat|cat\s+warna)"
    r"|(?:melakukan\s+)?pengecatan(?:\s+semprot)?"
    r"|(?:melakukan\s+)?penyemprotan\s+cat"
    r"|mengecat(?:\s+dengan\s+semprotan)?"
    r"|menyemprot\s+cat"
    r"|semprot\s+cat"
    r")(?![a-z])",
    re.I,
)
_NEGATED_PAINT_ZH_RE = re.compile(
    r"(?:沒有|没有|沒(?:有|做)?|没(?:有|做)?|未做|尚未|還沒|还没|未執行|未执行|未進行|未进行)"
    r".{0,10}(?:噴漆|喷漆|塗裝|涂装)"
    r"|(?:噴漆|喷漆|塗裝|涂装)(?:作業|作业)?.{0,10}"
    r"(?:沒有做|没有做|沒做|没做|未做|尚未執行|尚未执行|未執行|未执行|尚未完成)",
    re.I,
)

_ZH_MOTION = (
    "過去", "过去", "去那邊", "去那边", "到那邊", "到那边",
    "去那裡", "去那里", "到那裡", "到那里", "去現場", "去现场",
    "到現場", "到现场",
)
_ZH_INSPECTION = (
    "了解看看", "瞭解看看", "了解一下", "瞭解一下", "確認看看", "确认看看",
    "確認一下", "确认一下", "檢查看看", "检查看看", "檢查一下", "检查一下",
    "查看一下", "看看", "看一下", "查看", "檢查", "检查", "確認", "确认",
    "了解", "瞭解",
)


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("–", "-").replace("—", "-").replace("−", "-")
    # Keep decimal commas/points between digits; remove the same glyphs when
    # they are sentence punctuation.  Indonesian operators commonly write
    # measurements such as ``995,5 kg``.
    text = re.sub(r"(?<!\d)[。．.,，]|[。．.,，](?!\d)", " ", text)
    text = re.sub(r"[!！?？:：;；()（）\[\]{}]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_indonesian_factory_colloquialisms(source: Any) -> tuple[str, int]:
    """Normalize context-dependent shop-floor Indonesian without an API call.

    The important distinction is structural, not lexical: ``sip`` means OK in
    ordinary chat, but ``sip/sif/shif + a shift period + a predicate`` denotes a
    work shift.  Paint-application slang is canonicalized only when it belongs
    to that shift claim, so unrelated messages about choosing or supplying a
    paint colour are not rewritten as production work.
    """
    value = str(source or "")
    if not value:
        return value, 0
    replacements = 0

    def _normalize_shift(match: re.Match[str]) -> str:
        nonlocal replacements
        raw_shift = match.group("shift")
        period = match.group("period")
        # Do not reinterpret a bare acknowledgement such as ``Sip pagi!``.  A
        # predicate/negation in the same clause is mandatory evidence.
        tail = value[match.end():]
        same_clause = re.split(r"[\n.!！?？;；]", tail, maxsplit=1)[0][:160]
        if raw_shift.casefold() != "shift" and not _SHIFT_CLAUSE_EVIDENCE_ID_RE.search(same_clause):
            return match.group(0)
        canonical = f"shift {period.casefold()}"
        if match.group(0).casefold() != canonical:
            replacements += 1
        return canonical

    value = _SHIFT_ALIAS_ID_RE.sub(_normalize_shift, value)
    value, typo_count = re.subn(
        r"(?<![a-z])tida(?![a-z])", "tidak", value, flags=re.I
    )
    replacements += typo_count

    shift_match = _SHIFT_ALIAS_ID_RE.search(value)
    negative_match = _NEGATIVE_ID_RE.search(value)
    paint_match = _PAINT_APPLICATION_ID_RE.search(value)
    if (
        shift_match
        and negative_match
        and paint_match
        and shift_match.end() <= negative_match.start() <= paint_match.start()
        and paint_match.start() - shift_match.end() <= 160
    ):
        canonical_paint = "melakukan pengecatan semprot"
        if paint_match.group(0).casefold() != canonical_paint:
            value = (
                value[:paint_match.start()]
                + canonical_paint
                + value[paint_match.end():]
            )
            replacements += 1
    return value, replacements


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _norm(value))


def _lang_family(value: Any) -> str:
    """Collapse locale/provider aliases without weakening direction checks."""
    lang = _norm(value).replace("_", "-")
    if lang in {"zh", "zh-tw", "zh-cn", "zh-hant", "zh-hans", "chinese"}:
        return "zh"
    if lang in {"id", "id-id", "ind", "indonesian", "bahasa indonesia"}:
        return "id"
    return lang.split("-", 1)[0]


def _has_phrase(text: str, phrases: Iterable[str]) -> bool:
    return any(
        re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", text, re.I)
        for phrase in phrases
    )


def _first_phrase(text: str, phrases: Iterable[str]) -> str:
    for phrase in sorted(set(phrases), key=len, reverse=True):
        if re.search(r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])", text, re.I):
            return phrase
    return ""


def _extract_weight_after(text: str, phrases: Iterable[str]) -> str:
    phrase_pattern = "|".join(re.escape(item) for item in sorted(set(phrases), key=len, reverse=True))
    # Prefer the explicit ``995 kg di <device>`` attachment.  This must run
    # before the post-device form: once chat punctuation is normalized away,
    # the following device's reading can otherwise look adjacent to the first
    # device (``995 kg di monitor, 989 kg di timbangan``).
    match = re.search(
        rf"(?P<value>{_NUMBER})\s*(?:kg|kilogram)\s*"
        rf"(?:pada|di)\s*(?:{phrase_pattern})\b",
        text,
        flags=re.I,
    )
    if match:
        return match.group("value")
    match = re.search(
        rf"(?:di\s+)?(?:{phrase_pattern})\s*"
        rf"(?:menunjukkan|menampilkan|tertera|tertulis|adalah|sebesar|=)?\s*"
        rf"(?P<value>{_NUMBER})\s*(?:kg|kilogram)\b",
        text,
        flags=re.I,
    )
    return match.group("value") if match else ""


def _extract_difference(text: str) -> str:
    patterns = (
        rf"(?:selisih|beda|berbeda)(?:nya|\s+sebesar)?\s*(?P<value>{_NUMBER})\s*(?:kg|kilogram)\b",
        rf"(?P<value>{_NUMBER})\s*(?:kg|kilogram)\s+(?:selisih|beda)\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I)
        if match:
            return match.group("value")
    return ""


def _strip_id_supported_tokens(
    source: str, supported_numbers: Iterable[str] = ()
) -> str:
    """Return Indonesian content not represented by the deterministic frame."""
    value = _norm(_MENTION_RE.sub("", str(source or "")))
    phrases = (
        set(_MONITOR_ID)
        | set(_HOIST_SCALE_ID)
        | set(_LEADER_ID)
        | set(_REPORT_ID)
        | {
            "menunjukkan", "menampilkan", "tertera", "tertulis", "adalah",
            "sebesar", "selisihnya", "selisih", "berbeda", "bedanya", "beda",
            "dibandingkan", "dibanding", "antara", "sedangkan", "dengan", "dan",
            "menggunakan", "gunakan", "memakai", "pakai", "sudah", "telah",
            "beratnya", "berat", "nilainya", "nilai", "hasil", "ada",
            "kilogram", "kg", "saya", "aku", "pada", "dari", "di", "id", "nya",
        }
    )
    for phrase in sorted(phrases, key=len, reverse=True):
        value = re.sub(
            r"(?<![a-z])" + re.escape(phrase) + r"(?![a-z])",
            " ",
            value,
            flags=re.I,
        )
    # Remove only numeric occurrences already assigned to a source slot.  A
    # blanket numeric deletion could hide an unrelated code/value and make the
    # direct route silently drop it.
    for number in supported_numbers:
        number = str(number or "").strip()
        if number:
            value = re.sub(
                r"(?<!\d)" + re.escape(number) + r"(?!\d)",
                " ",
                value,
                count=1,
            )
    value = re.sub(r"[=/+\-]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _claim(frame: dict, claim_id: str, source_evidence: str, meaning: str, target: str) -> None:
    frame["claims"].append({
        "claim_id": claim_id,
        "source_evidence": source_evidence,
        "meaning": meaning,
        "required_target": target,
    })


def _base_frame(source: str, src_lang: str, tgt_lang: str) -> dict:
    return {
        "active": False,
        "complete": False,
        "kind": "",
        "source": str(source or ""),
        "src_lang": _lang_family(src_lang),
        "tgt_lang": _lang_family(tgt_lang),
        "claims": [],
        "slots": {},
        "mentions": _MENTION_RE.findall(str(source or "")),
        "unparsed": "",
    }


def _build_id_zh_frame(source: str, frame: dict) -> dict:
    text = _norm(source)
    shift_match = _SHIFT_ALIAS_ID_RE.search(text)
    shift_period = shift_match.group("period").casefold() if shift_match else ""
    negative_match = _NEGATIVE_ID_RE.search(text)
    paint_match = _PAINT_APPLICATION_ID_RE.search(text)
    shift_paint_claim = bool(
        shift_match
        and negative_match
        and paint_match
        and shift_match.end() <= negative_match.start() <= paint_match.start()
        and paint_match.start() - shift_match.end() <= 160
    )
    if shift_paint_claim:
        frame["kind"] = "id_zh_shift_process_status"
        frame["slots"].update({
            "shift_alias": shift_match.group("shift").casefold(),
            "shift_period": shift_period,
            "shift_target": _SHIFT_PERIOD_ZH[shift_period],
            "negative_term": negative_match.group("negative").casefold(),
            "completion": "not_yet" if negative_match.group("negative").casefold() == "belum" else "not_done",
            "process": "spray_painting",
            "process_source": paint_match.group(0),
        })
        _claim(
            frame,
            "shift_actor",
            shift_match.group(0),
            f"{_SHIFT_PERIOD_ZH[shift_period]}是執行者／責任班別，不是問候語",
            _SHIFT_PERIOD_ZH[shift_period],
        )
        _claim(
            frame,
            "process_negation",
            negative_match.group(0),
            "製程沒有執行；否定不能遺失或翻成缺少供應",
            "沒有",
        )
        _claim(
            frame,
            "spray_painting_process",
            paint_match.group(0),
            "現場噴漆／塗裝作業，不是提供油漆顏色",
            "噴漆",
        )
        supported_spans = sorted(
            (shift_match.span(), negative_match.span(), paint_match.span()),
            reverse=True,
        )
        unparsed = text
        for start, end in supported_spans:
            unparsed = unparsed[:start] + " " + unparsed[end:]
        frame["unparsed"] = re.sub(r"[\s,，。.!！?？:：;；()（）\[\]{}]+", " ", unparsed).strip()
        frame["active"] = True
        frame["complete"] = not frame["unparsed"]
        return frame

    monitor_term = _first_phrase(text, _MONITOR_ID)
    scale_term = _first_phrase(text, _HOIST_SCALE_ID)
    monitor_weight = _extract_weight_after(text, _MONITOR_ID)
    scale_weight = _extract_weight_after(text, _HOIST_SCALE_ID)
    difference = _extract_difference(text)
    report_term = _first_phrase(text, _REPORT_ID)
    leader_term = _first_phrase(text, _LEADER_ID)
    first_person = bool(re.search(r"(?<![a-z])saya(?![a-z])", text, re.I))
    id_relation = bool(
        leader_term
        and re.search(
            r"\bid\b.{0,18}(?:ketu(?:a)?\s+kelas|ketua\s+(?:shift|regu)|kepala\s+shift|kepala\s+regu)"
            r"|(?:ketu(?:a)?\s+kelas|ketua\s+(?:shift|regu)|kepala\s+shift|kepala\s+regu).{0,18}\bid\b",
            text,
            flags=re.I,
        )
    )
    report_completed = bool(
        report_term
        and re.search(r"\b(?:sudah|telah)\b.{0,18}\b(?:lapor|melapor|melaporkan|laporkan)\b", text, re.I)
    )
    weight_context = bool(scale_term and (monitor_term or re.search(r"\b(?:kg|kilogram)\b", text)))

    if not weight_context:
        return frame

    frame["kind"] = "id_zh_weight_display_relation"
    frame["slots"].update({
        "monitor_term": monitor_term,
        "scale_term": scale_term,
        "monitor_weight": monitor_weight,
        "scale_weight": scale_weight,
        "difference": difference,
        "report_term": report_term,
        "first_person": first_person,
        "leader_term": leader_term,
        "leader_id_relation": id_relation,
        "report_completed": report_completed,
    })
    frame["unparsed"] = _strip_id_supported_tokens(
        source, (monitor_weight, scale_weight, difference)
    )

    if monitor_term:
        _claim(frame, "monitor_display", monitor_term, "螢幕顯示的重量", "螢幕顯示")
    if scale_term:
        _claim(
            frame,
            "overhead_crane_scale",
            scale_term,
            "安裝於天車的電子磅秤；不是字面滑輪秤",
            "天車電子磅秤",
        )
    if monitor_weight:
        _claim(frame, "monitor_weight", monitor_weight + " kg", "螢幕重量讀值", monitor_weight + " 公斤")
    if scale_weight:
        _claim(frame, "scale_weight", scale_weight + " kg", "天車電子磅秤讀值", scale_weight + " 公斤")
    if difference:
        _claim(frame, "weight_difference", difference + " kg", "兩個讀值的差值", "相差 " + difference + " 公斤")
    if report_term:
        _claim(frame, "report_action", report_term, "說話者進行回報", "我回報")
    if id_relation:
        _claim(frame, "leader_id", "ID " + leader_term, "使用班長的 ID 回報", "用班長的 ID 回報")

    comparison_complete = bool(
        monitor_term and scale_term and (difference or (monitor_weight and scale_weight))
    )
    report_complete = bool(not report_term or (first_person and (not leader_term or id_relation)))
    frame["active"] = bool(frame["claims"])
    frame["complete"] = bool(
        comparison_complete and report_complete and not frame["unparsed"]
    )
    return frame


def _strip_zh_supported_tokens(source: str) -> str:
    value = str(source or "")
    for token in sorted(
        set(_ZH_MOTION)
        | set(_ZH_INSPECTION)
        | {
            "我", "先", "會", "会", "要", "再", "已", "已經", "已经", "一下", "了", "的", "那邊", "那边",
            "那裡", "那里", "現場", "现场", "情況", "情况", "狀況", "状况",
            "機台", "机台", "設備", "设备", "機器", "机器", "材料", "料件", "棒材",
        },
        key=len,
        reverse=True,
    ):
        value = value.replace(token, "")
    value = _MENTION_RE.sub("", value)
    value = re.sub(r"[\s,，。.!！?？:：;；()（）\[\]{}]+", "", value)
    return value


def _build_zh_id_frame(source: str, frame: dict) -> dict:
    compact = _compact(source)
    motion_term = next((term for term in sorted(_ZH_MOTION, key=len, reverse=True) if term in compact), "")
    inspect_term = next((term for term in sorted(_ZH_INSPECTION, key=len, reverse=True) if term in compact), "")
    if not (motion_term and inspect_term):
        return frame

    first_person = "我" in compact and "我們" not in compact and "我们" not in compact
    completed = any(term in compact for term in ("已經過去", "已经过去", "已經到現場", "已经到现场", "已到現場", "已到现场"))
    future = any(term in compact for term in ("會", "会", "要"))
    later = "再" in compact
    explicit_first = "先" in compact
    destination = "location" if any(x in compact for x in ("現場", "现场")) else "there"
    if any(x in compact for x in ("機台", "机台", "設備", "设备", "機器", "机器")):
        obj = "machine"
    elif any(x in compact for x in ("材料", "料件", "棒材")):
        obj = "material"
    elif any(x in compact for x in ("情況", "情况", "狀況", "状况")):
        obj = "situation"
    else:
        obj = "implicit_situation"
    unparsed = _strip_zh_supported_tokens(source)

    frame["kind"] = "zh_id_motion_inspection_relation"
    frame["slots"].update({
        "first_person": first_person,
        "motion_term": motion_term,
        "inspection_term": inspect_term,
        "destination": destination,
        "object": obj,
        "completed": completed,
        "future": future,
        "later": later,
        "explicit_first": explicit_first,
        "first_or_soft": bool(explicit_first or "看看" in inspect_term or "一下" in inspect_term),
    })
    frame["unparsed"] = unparsed
    if first_person:
        _claim(frame, "first_person_actor", "我", "說話者本人執行動作", "Saya")
    _claim(frame, "movement_to_location", motion_term, "先移動到那裡／現場", "ke sana / ke lokasi")
    _claim(frame, "inspection_purpose", inspect_term, "到達後查看或確認", "untuk mengecek / memeriksa")
    if obj == "machine":
        _claim(frame, "inspection_object", "機台／設備", "檢查機台狀況", "kondisi mesin")
    elif obj == "material":
        _claim(frame, "inspection_object", "材料", "檢查材料狀況", "kondisi material")
    else:
        _claim(frame, "inspection_object", "情況／省略的現場情況", "查看現場情況", "situasinya")

    frame["active"] = True
    frame["complete"] = bool(first_person and not unparsed)
    return frame


def build_frame(source: str, src_lang: str, tgt_lang: str) -> dict:
    """Extract source-side semantic relations for either supported direction."""
    frame = _base_frame(source, src_lang, tgt_lang)
    if not str(source or "").strip():
        return frame
    if frame["src_lang"] == "id" and frame["tgt_lang"] == "zh":
        return _build_id_zh_frame(source, frame)
    if frame["src_lang"] == "zh" and frame["tgt_lang"] == "id":
        return _build_zh_id_frame(source, frame)
    return frame


def _with_mentions(frame: Mapping, text: str) -> str:
    mentions = [str(item).strip() for item in frame.get("mentions") or () if str(item).strip()]
    return ((" ".join(mentions) + " ") if mentions else "") + text


def deterministic_translation(frame: Mapping) -> str:
    """Render a complete source frame directly; return an empty string otherwise."""
    if not frame or not frame.get("active") or not frame.get("complete"):
        return ""
    slots = frame.get("slots") or {}
    if frame.get("kind") == "id_zh_shift_process_status":
        shift = str(slots.get("shift_target") or "")
        if not shift or slots.get("process") != "spray_painting":
            return ""
        status = "還沒有噴漆" if slots.get("completion") == "not_yet" else "沒有噴漆"
        return _with_mentions(frame, f"{shift}{status}")

    if frame.get("kind") == "id_zh_weight_display_relation":
        parts: list[str] = []
        monitor_weight = str(slots.get("monitor_weight") or "")
        scale_weight = str(slots.get("scale_weight") or "")
        difference = str(slots.get("difference") or "")
        if monitor_weight and scale_weight:
            parts.append(
                f"螢幕顯示 {monitor_weight} 公斤，而天車電子磅秤顯示 {scale_weight} 公斤。"
            )
            if difference:
                parts.append(f"兩者相差 {difference} 公斤。")
        elif difference:
            parts.append(f"螢幕顯示的重量與天車電子磅秤相差 {difference} 公斤。")
        else:
            return ""
        if slots.get("report_term"):
            aspect = "已" if slots.get("report_completed") else ""
            if slots.get("leader_id_relation"):
                parts.append(f"我{aspect}用班長的 ID 回報。")
            else:
                parts.append(f"我{aspect}回報。")
        return _with_mentions(frame, "".join(parts))

    if frame.get("kind") == "zh_id_motion_inspection_relation":
        destination = "ke lokasi" if slots.get("destination") == "location" else "ke sana"
        obj = slots.get("object")
        action = {
            "machine": "memeriksa kondisi mesin",
            "material": "memeriksa kondisi material",
            "situation": "mengecek situasinya",
            "implicit_situation": "mengecek situasinya",
        }.get(obj, "mengecek situasinya")
        if slots.get("completed"):
            text = f"Saya sudah pergi {destination} untuk {action}."
        elif slots.get("later"):
            first = " terlebih dahulu" if slots.get("explicit_first") else ""
            text = f"Nanti saya akan pergi {destination}{first} untuk {action}."
        elif slots.get("future"):
            first = " terlebih dahulu" if slots.get("first_or_soft") else ""
            text = f"Saya akan pergi {destination}{first} untuk {action}."
        else:
            first = " dulu" if slots.get("first_or_soft") else ""
            text = f"Saya {destination}{first} untuk {action}."
        return _with_mentions(frame, text)
    return ""


def _has_any(text: str, terms: Iterable[str]) -> bool:
    low = _norm(text)
    return any(_norm(term) in low for term in terms)


def _number_unit_present_zh(text: str, value: str) -> bool:
    if not value:
        return True
    return bool(re.search(re.escape(value) + r"\s*(?:公斤|kg)", text, flags=re.I))


def validate_translation(frame: Mapping, translation: str) -> tuple[bool, list[str]]:
    """Validate source roles and relations, not merely isolated keywords."""
    if not frame or not frame.get("active"):
        return True, []
    target = str(translation or "").strip()
    if not target:
        return False, ["factory_message_semantics:empty_translation"]
    slots = frame.get("slots") or {}
    issues: list[str] = []

    if frame.get("kind") == "id_zh_shift_process_status":
        shift_target = str(slots.get("shift_target") or "")
        if shift_target and shift_target not in target:
            issues.append("factory_message_semantics:shift_actor_missing")
        if any(term in target for term in ("早上好", "早安", "上午好")):
            issues.append("factory_message_semantics:shift_mistranslated_as_greeting")
        if not any(term in target for term in ("噴漆", "塗裝", "喷漆", "涂装")):
            issues.append("factory_message_semantics:spray_painting_process_missing")
        if not _NEGATED_PAINT_ZH_RE.search(target):
            issues.append("factory_message_semantics:process_negation_missing")
        if any(term in target for term in (
            "提供油漆", "提供漆", "供應油漆", "供应油漆", "油漆顏色", "油漆颜色",
        )):
            issues.append("factory_message_semantics:paint_action_mistranslated_as_supply")

    elif frame.get("kind") == "id_zh_weight_display_relation":
        if slots.get("monitor_term") and not (
            "螢幕" in target and any(term in target for term in ("顯示", "讀值", "數值"))
        ):
            issues.append("factory_message_semantics:monitor_display_relation_missing")
        if slots.get("scale_term") and "天車電子磅秤" not in target:
            issues.append("factory_message_semantics:overhead_crane_scale_term_missing")
        if any(term in target for term in ("滑輪秤", "滑車秤", "捲揚秤")):
            issues.append("factory_message_semantics:literal_pulley_scale_forbidden")
        if not _number_unit_present_zh(target, str(slots.get("monitor_weight") or "")):
            issues.append("factory_message_semantics:monitor_weight_missing")
        if not _number_unit_present_zh(target, str(slots.get("scale_weight") or "")):
            issues.append("factory_message_semantics:scale_weight_missing")
        difference = str(slots.get("difference") or "")
        if difference and not (
            _number_unit_present_zh(target, difference)
            and any(term in target for term in ("相差", "差距", "差了", "差異"))
        ):
            issues.append("factory_message_semantics:weight_difference_relation_missing")
        if slots.get("monitor_weight") and slots.get("scale_weight"):
            monitor = re.escape(str(slots.get("monitor_weight")))
            scale = re.escape(str(slots.get("scale_weight")))
            relation_ok = bool(
                re.search(rf"螢幕.{{0,35}}{monitor}.{{0,80}}天車電子磅秤.{{0,35}}{scale}", target, re.S)
                or re.search(rf"天車電子磅秤.{{0,35}}{scale}.{{0,80}}螢幕.{{0,35}}{monitor}", target, re.S)
            )
            if not relation_ok:
                issues.append("factory_message_semantics:weight_readings_attached_to_wrong_devices")
        if slots.get("report_term"):
            if not any(term in target for term in ("回報", "報告", "通報")):
                issues.append("factory_message_semantics:report_action_missing")
            if slots.get("first_person") and "我" not in target:
                issues.append("factory_message_semantics:first_person_reporter_missing")
        if slots.get("leader_id_relation"):
            if "班長" not in target or not re.search(r"(?<![A-Za-z])ID(?![A-Za-z])", target, re.I):
                issues.append("factory_message_semantics:leader_id_relation_missing")
        if re.search(r"\b(?:ketu(?:a)?\s+kelas|ketua\s+(?:shift|regu)|kepala\s+(?:shift|regu))\b", target, re.I):
            issues.append("factory_message_semantics:untranslated_leader_role")

    elif frame.get("kind") == "zh_id_motion_inspection_relation":
        low = _norm(target)
        if slots.get("first_person") and not _has_phrase(low, ("saya", "aku")):
            issues.append("factory_message_semantics:first_person_actor_missing")
        if slots.get("completed") and not _has_phrase(low, ("sudah", "telah")):
            issues.append("factory_message_semantics:completed_aspect_missing")
        if slots.get("future") and not _has_phrase(low, ("akan",)):
            issues.append("factory_message_semantics:future_modality_missing")
        if slots.get("later") and not _has_phrase(low, ("nanti", "kemudian")):
            issues.append("factory_message_semantics:later_timing_missing")
        movement_ok = _has_phrase(low, (
            "ke sana", "pergi ke sana", "menuju ke sana", "ke lokasi",
            "pergi ke lokasi", "menuju lokasi", "ke lapangan", "pergi ke lapangan",
        ))
        if not movement_ok:
            issues.append("factory_message_semantics:movement_to_location_missing")
        inspection_ok = _has_phrase(low, (
            "mengecek", "memeriksa", "meninjau", "melihat", "mencari tahu",
        ))
        if not inspection_ok:
            issues.append("factory_message_semantics:inspection_action_missing")
        obj = slots.get("object")
        if obj == "machine" and not _has_phrase(low, ("mesin", "peralatan")):
            issues.append("factory_message_semantics:machine_object_missing")
        elif obj == "material" and not _has_phrase(low, ("material", "bahan")):
            issues.append("factory_message_semantics:material_object_missing")
        elif obj in ("situation", "implicit_situation") and not _has_phrase(
            low, ("situasi", "situasinya", "kondisi", "kondisinya", "keadaan")
        ):
            issues.append("factory_message_semantics:situation_object_missing")

    return not issues, list(dict.fromkeys(issues))


def translate_source_directly(source: str, src_lang: str, tgt_lang: str) -> str:
    """Translate a complete relation frame before TM, NMT or an LLM call."""
    frame = build_frame(source, src_lang, tgt_lang)
    translated = deterministic_translation(frame)
    if not translated:
        return ""
    ok, _issues = validate_translation(frame, translated)
    return translated if ok else ""


def build_prompt(frame: Mapping) -> str:
    if not frame or not frame.get("active"):
        return ""
    lines = ["<factory_message_source_relations>"]
    lines.append(
        "Translate from the source claims below. Preserve actor, action, movement, destination, "
        "equipment, reading-to-device attachment, comparison/difference, unit, reporting recipient "
        "and ID ownership as linked relations; keyword presence alone is insufficient."
    )
    for claim in frame.get("claims") or ():
        lines.append(
            "Claim {claim_id}: source={source_evidence}; meaning={meaning}; target={required_target}.".format(
                **claim
            )
        )
    if frame.get("kind") == "id_zh_weight_display_relation":
        lines.append(
            "In this factory context timbangan katrol/gantung/crane is the overhead-crane electronic "
            "scale: translate it as 天車電子磅秤, never 滑輪秤. Ketu/ketua kelas beside report+ID "
            "means the shift leader: 班長; do not leave Indonesian role words in Chinese."
        )
    elif frame.get("kind") == "id_zh_shift_process_status":
        lines.append(
            "In this shop-floor clause, sip/sif/shif before pagi/siang/sore/malam is a phonetic "
            "spelling of shift, not the acknowledgement sip and not a greeting. Pagi is therefore "
            "早班, not 早上好. Mengasih/memberi warna cat under a negated shift status describes "
            "performing spray-painting work; translate the linked claim as 班別沒有噴漆, never as "
            "not supplying/providing a paint colour."
        )
    elif frame.get("kind") == "zh_id_motion_inspection_relation":
        lines.append(
            "Chinese 過去/到現場 is an explicit movement to another location. Indonesian must contain "
            "ke sana/ke lokasi (or an equivalent movement phrase) as well as the inspection purpose; "
            "Saya lihat situasinya alone is incomplete."
        )
    lines.append("</factory_message_source_relations>")
    return " ".join(lines)


def health() -> dict:
    shift_paint = build_frame(
        "Sip pagi tida mengasih warna cat", "id", "zh"
    )
    discrepancy = build_frame(
        "Kg di layar monitor dengan di timbangan katrol selisih 6 kg. "
        "Saya laporan dengan id Ketu kelas",
        "id", "zh",
    )
    readings = build_frame(
        "Di layar monitor 995 kg sedangkan di timbangan gantung 989 kg",
        "id", "zh",
    )
    movement = build_frame("我過去了了解看看", "zh", "id")
    reversed_readings = (
        "995 kg di layar monitor, 989 kg di timbangan gantung elektronik"
    )
    current_values = (
        "Monitor menunjukkan 1000 kg, sedangkan timbangan gantung elektronik "
        "994 kg. Saya sudah lapor pakai ID ketua regu."
    )
    controls = (
        build_frame("Sip, terima kasih.", "id", "zh"),
        build_frame("Selamat pagi, Pak.", "id", "zh"),
        build_frame("Tolong memberi warna cat biru.", "id", "zh"),
        build_frame("Saya ketua kelas di sekolah.", "id", "zh"),
        build_frame("Katrol rusak.", "id", "zh"),
        build_frame("我先看看情況。", "zh", "id"),
        build_frame("我過去拿工具。", "zh", "id"),
    )
    checks = [
        shift_paint.get("active") is True and shift_paint.get("complete") is True,
        translate_source_directly(shift_paint["source"], "id", "zh")
        == "早班沒有噴漆",
        normalize_indonesian_factory_colloquialisms(shift_paint["source"])[0]
        == "shift pagi tidak melakukan pengecatan semprot",
        discrepancy.get("active") is True and discrepancy.get("complete") is True,
        translate_source_directly(
            discrepancy["source"], "id", "zh"
        ) == "螢幕顯示的重量與天車電子磅秤相差 6 公斤。我用班長的 ID 回報。",
        readings.get("active") is True and readings.get("complete") is True,
        translate_source_directly(
            readings["source"], "id", "zh"
        ) == "螢幕顯示 995 公斤，而天車電子磅秤顯示 989 公斤。",
        movement.get("active") is True and movement.get("complete") is True,
        translate_source_directly("我過去了了解看看", "zh", "id")
        == "Saya ke sana dulu untuk mengecek situasinya.",
        translate_source_directly(reversed_readings, "id-ID", "zh-TW")
        == "螢幕顯示 995 公斤，而天車電子磅秤顯示 989 公斤。",
        translate_source_directly(current_values, "ind", "zh-Hant")
        == "螢幕顯示 1000 公斤，而天車電子磅秤顯示 994 公斤。我已用班長的 ID 回報。",
        translate_source_directly("我過去了了解看看", "zh-TW", "id-ID")
        == "Saya ke sana dulu untuk mengecek situasinya.",
        translate_source_directly(
            discrepancy["source"] + " Besok mesin dihentikan.", "id", "zh"
        ) == "",
        translate_source_directly(readings["source"] + " 77", "id", "zh") == "",
        translate_source_directly(
            "Di layar monitor 995,5 kg sedangkan di timbangan katrol 989,25 kg",
            "id", "zh",
        ) == "螢幕顯示 995,5 公斤，而天車電子磅秤顯示 989,25 公斤。",
        translate_source_directly("我會過去了解看看", "zh", "id")
        == "Saya akan pergi ke sana terlebih dahulu untuk mengecek situasinya.",
        validate_translation(
            build_frame("我會過去了解看看", "zh", "id"),
            "Saya pergi ke sana untuk mengecek situasinya.",
        )[0] is False,
        validate_translation(
            discrepancy,
            "螢幕上的公斤數與滑輪秤相差 6 kg。我已用 Ketu kelas 的 ID 回報。",
        )[0] is False,
        validate_translation(movement, "Saya lihat dulu situasinya.")[0] is False,
        all(not frame.get("active") for frame in controls),
    ]
    return {
        "api_version": FACTORY_MESSAGE_SEMANTICS_API_VERSION,
        "build_id": FACTORY_MESSAGE_SEMANTICS_BUILD_ID,
        "self_test": {"ok": all(checks), "checks": len(checks)},
    }
