"""Compositional Indonesian -> Chinese shop-floor measurement semantics.

This module handles terse factory shorthand such as a known equipment code plus
measurement/state words.  It intentionally does not own the equipment-code list;
the caller passes canonical codes extracted from the plant's existing station
asset so aliases stay single-sourced.

The parser is fail-closed.  It only produces a deterministic Chinese sentence
when the whole source can be explained by supported shorthand slots.  Longer or
ambiguous messages still go through the normal LLM pipeline, but the semantic
frame and validator prevent the common literal "micro/small machine" reading.
"""

from __future__ import annotations

import re
from typing import Iterable, Mapping, Sequence

FACTORY_MEASUREMENT_SEMANTICS_API_VERSION = 1
FACTORY_MEASUREMENT_SEMANTICS_BUILD_ID = "2026-08-08.3-id-zh-work-order-material-dimension"

# Strong measurement cues.  ``mikro`` is plant shorthand for a dimensional
# micrometer/measurement reading in this message shape; it is not an adjective
# meaning a miniature machine.
_MEASUREMENT_CUES = {
    "mikro": "micrometer",
    "mikrometer": "micrometer",
    "micrometer": "micrometer",
    "pengukuran": "measurement",
    "hasil ukur": "measurement",
    "hasil pengukuran": "measurement",
}

# Explicit dimensional nouns are useful as supporting evidence, but unlike the
# strong cues above they do not by themselves make ``mesin kecil`` a measurement
# message.
_DIMENSION_CUES = {
    "ukuran": "dimension",
    "diameter": "diameter",
    "toleransi": "tolerance",
}

_STATE_CUES = {
    "kecil": "undersize",
    "kekecilan": "undersize",
    "terlalu kecil": "undersize",
    "besar": "oversize",
    "kebesaran": "oversize",
    "terlalu besar": "oversize",
    "masuk": "in_tolerance",
}

_STATE_ZH = {
    "undersize": "尺寸偏小",
    "oversize": "尺寸偏大",
    "in_tolerance": "尺寸在公差內",
}

# These are only accepted as structural filler in a complete terse shorthand.
# Keeping this list small is deliberate: unknown content must fall back to the
# general translator rather than be silently dropped.
_FILLER_WORDS = {
    "mesin",
    "mesinnya",
    "untuk",
    "di",
    "nya",
    "hasil",
    "ukur",
}

_BAD_MACHINE_SIZE_ZH = (
    "微型機台",
    "微型小機台",
    "微小機台",
    "小型機台",
    "小機台",
    "迷你機台",
    "微型設備",
    "微小設備",
    "小型設備",
    "小設備",
    "迷你設備",
    "微型機器",
    "微小機器",
    "小型機器",
    "小機器",
    "迷你機器",
)

_BAD_MACHINE_SCALE_PATTERNS = (
    re.compile(r"(?:機台|設備|機器)(?:本身)?(?:很|太|較|比較|偏|過於)小(?:型)?"),
    re.compile(r"(?:機台|設備|機器)(?:本身)?(?:很|太|較|比較|偏|過於)大(?:型)?"),
)


def _normalize_id(text: str) -> str:
    value = (text or "").casefold()
    value = value.replace("–", "-").replace("—", "-").replace("−", "-")
    value = re.sub(r"[。．.!！?？,，:：;；()（）\[\]{}]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _has_phrase(text: str, phrase: str) -> bool:
    return bool(
        re.search(
            r"(?<![a-z])" + re.escape(phrase.casefold()) + r"(?![a-z])",
            text,
            flags=re.IGNORECASE,
        )
    )


def _first_phrase(text: str, mapping: Mapping[str, str]):
    for phrase in sorted(mapping, key=len, reverse=True):
        if _has_phrase(text, phrase):
            return phrase, mapping[phrase]
    return None, None


def _remove_phrase(text: str, phrase: str) -> str:
    return re.sub(
        r"(?<![a-z])" + re.escape(phrase.casefold()) + r"(?![a-z])",
        " ",
        text,
        flags=re.IGNORECASE,
    )


def _normalize_codes(equipment_codes: Iterable[str] | None) -> list[str]:
    out = []
    for code in equipment_codes or ():
        code = str(code or "").strip()
        if code and code not in out:
            out.append(code)
    return out


def build_frame(
    text: str,
    *,
    equipment_codes: Sequence[str] | None = None,
    work_order_context: bool = False,
) -> dict:
    """Build an ID->ZH measurement semantic frame from compositional slots.

    ``active`` requires a size/tolerance state plus a measurement cue.  ``mikro``
    is a strong measurement cue.  A bare ``mesin kecil`` therefore stays
    inactive and remains available to the ordinary translator.
    """
    normalized = _normalize_id(text)
    codes = _normalize_codes(equipment_codes)
    if not normalized:
        return {"active": False, "complete": False, "equipment_codes": codes}

    strong_phrase, measurement_kind = _first_phrase(normalized, _MEASUREMENT_CUES)
    dimension_phrase, dimension_kind = _first_phrase(normalized, _DIMENSION_CUES)

    # Resolve the dimensional state compositionally and detect contradictory
    # shorthand instead of silently taking whichever keyword happens to sort
    # first.  ``terlalu kecil`` + ``kecil`` is one state; ``kecil`` + ``besar``
    # is an ambiguity and must never be deterministically collapsed.
    present_state_phrases = [
        (phrase, state_name)
        for phrase, state_name in _STATE_CUES.items()
        if _has_phrase(normalized, phrase)
    ]
    state_names = list(dict.fromkeys(state_name for _phrase, state_name in present_state_phrases))
    state_ambiguous = len(state_names) > 1
    state = state_names[0] if len(state_names) == 1 else None
    state_phrase = None
    if state is not None:
        matching = [phrase for phrase, state_name in present_state_phrases if state_name == state]
        state_phrase = max(matching, key=len) if matching else None
    elif present_state_phrases:
        state_phrase = max((phrase for phrase, _state_name in present_state_phrases), key=len)

    # ``mikro`` by itself is lexically ambiguous in ordinary Indonesian.  The
    # plant-specific measurement reading is activated only when it is anchored
    # to a canonical equipment code from the existing STATION_CODES asset.
    # Explicit measurement words such as ``mikrometer`` / ``hasil ukur`` remain
    # usable without a code.  This generalizes across every known equipment code
    # without globally redefining the common word ``mikro``.
    if state in {"undersize", "oversize"} or state_ambiguous:
        if strong_phrase == "mikro":
            measurement_evidence = bool(codes)
        else:
            measurement_evidence = bool(strong_phrase)
    else:
        measurement_evidence = bool(strong_phrase or dimension_phrase == "toleransi")
    active = bool(state_phrase and measurement_evidence)
    if not active:
        return {
            "active": False,
            "complete": False,
            "equipment_codes": codes,
            "normalized": normalized,
        }

    remainder = normalized
    # Remove caller-supplied canonical codes without assuming any plant code.
    for code in codes:
        remainder = re.sub(
            r"(?<![a-z0-9])" + re.escape(code.casefold()) + r"(?![a-z0-9])",
            " ",
            remainder,
            flags=re.IGNORECASE,
        )
    for phrase in sorted(
        set(_MEASUREMENT_CUES) | set(_DIMENSION_CUES) | set(_STATE_CUES),
        key=len,
        reverse=True,
    ):
        remainder = _remove_phrase(remainder, phrase)

    tokens = [tok for tok in re.split(r"\s+", remainder.strip()) if tok]
    substantive_tokens = [tok for tok in tokens if tok not in _FILLER_WORDS]

    # Deterministic rendering is deliberately narrower than frame activation.
    # A known equipment code plus one unambiguous state and only supported
    # shorthand slots is sufficient.
    complete = bool(codes and state is not None and not state_ambiguous and not substantive_tokens)
    effective_work_order_context = bool(work_order_context and codes)

    # When a confirmed recent work-order photo is available, the terse factory
    # shorthand is deictic: the equipment code identifies the machine currently
    # processing that photographed order, while the dimensional state belongs to
    # the order's incoming material.  Keep these semantic roles explicit so all
    # providers and deterministic renderers share the same interpretation.
    work_order_relation = "current_production" if effective_work_order_context else ""
    measurement_object = "incoming_material_dimension" if effective_work_order_context else "material_dimension"

    return {
        "active": True,
        "complete": complete,
        "normalized": normalized,
        "equipment_codes": codes,
        "measurement_cue": strong_phrase or dimension_phrase or "",
        "measurement_kind": measurement_kind or dimension_kind or "measurement",
        "dimension_cue": dimension_phrase or "",
        "state_cue": state_phrase or "",
        "state": state,
        "state_ambiguous": bool(state_ambiguous),
        "state_zh": (_STATE_ZH.get(state) if state else ""),
        "work_order_context": effective_work_order_context,
        "work_order_relation": work_order_relation,
        "measurement_object": measurement_object,
        "unparsed_tokens": substantive_tokens,
    }


def deterministic_translation(frame: Mapping) -> str | None:
    """Render a complete terse frame without an LLM; otherwise return ``None``."""
    if not frame or not frame.get("active") or not frame.get("complete"):
        return None
    codes = [str(c) for c in frame.get("equipment_codes") or () if str(c).strip()]
    state_zh = str(frame.get("state_zh") or "").strip()
    if not codes or not state_zh:
        return None
    machine = "、".join(codes)
    if frame.get("work_order_context"):
        # The photographed work order is the omitted object.  The machine code
        # tells which equipment is producing it; the size judgement belongs to
        # the incoming material, never to the machine body itself.
        material_state = {
            "尺寸偏小": "來料尺寸偏小",
            "尺寸偏大": "來料尺寸偏大",
            "尺寸在公差內": "來料尺寸在公差內",
        }.get(state_zh, "來料" + state_zh)
        return f"{machine} 現在生產的這個訂單，{material_state}"
    # Even without a photographed order, the dimensional state belongs to the
    # material being processed at that machine.  Never attach the size to the
    # machine body itself.
    return f"{machine} 生產中的材料{state_zh}"


def build_prompt(frame: Mapping) -> str:
    """Return a compact provider instruction for an active frame."""
    if not frame or not frame.get("active"):
        return ""
    codes = "、".join(str(c) for c in frame.get("equipment_codes") or ()) or "（未指定）"
    state_zh = frame.get("state_zh") or "尺寸量測狀態"
    work_order = "是" if frame.get("work_order_context") else "否"
    return (
        "<id_zh_measurement_shorthand>"
        f"設備代碼={codes}; 量測判定={state_zh}; 最近工單照片上下文={work_order}。"
        "此類現場短句的 mikro/mikrometer 是分厘卡或尺寸量測語意；"
        "與 kecil/kekecilan 搭配表示尺寸偏小，與 besar/kebesaran 搭配表示尺寸偏大，"
        "masuk 表示尺寸在公差內。"
        "mesin + 已知設備代碼是在指定機台，不可把 mikro/kecil 合併成『微型/小型/迷你機台』。"
        "語義角色必須分開：設備代碼是生產設備；尺寸判定的主體是材料，不是設備本體。"
        "若最近工單照片上下文=是，這是承接剛才照片的省略句：設備代碼表示該機台現在正在生產/加工照片中的這個訂單，"
        "mikro + 尺寸狀態表示這個訂單的來料尺寸狀態。應理解成『I5 現在生產的這個訂單，來料尺寸偏小/偏大/在公差內』這類關係；"
        "禁止翻成『I5 這台設備的工單尺寸偏小』、禁止說設備本身尺寸偏小，也不要把量測動作錯掛在設備本體。"
        "若最近工單照片上下文=否，不可自行補出工單；但尺寸主體仍是該設備生產/加工中的材料，不能翻成設備本體量測尺寸偏小/偏大。"
        "</id_zh_measurement_shorthand>"
    )


def validate_translation(frame: Mapping, translation: str) -> tuple[bool, list[str]]:
    """Validate the semantic obligations of an active measurement frame."""
    if not frame or not frame.get("active"):
        return True, []
    target = (translation or "").strip()
    if not target:
        return False, ["measurement_translation_empty"]

    issues: list[str] = []
    for bad in _BAD_MACHINE_SIZE_ZH:
        if bad in target:
            issues.append("measurement_literal_machine_size:" + bad)
            break
    for pattern in _BAD_MACHINE_SCALE_PATTERNS:
        match = pattern.search(target)
        if match:
            issues.append("measurement_literal_machine_scale:" + match.group(0))
            break

    # A standalone 「微型」 is also suspicious when the source uses the strong
    # ``mikro`` cue; this catches variants such as 「I5 微型的小機台」.
    if frame.get("measurement_cue") in {"mikro", "mikrometer", "micrometer"}:
        if "微型" in target or "迷你" in target:
            issues.append("measurement_mikro_misread_as_machine_scale")

    for code in frame.get("equipment_codes") or ():
        if str(code) not in target:
            issues.append("measurement_equipment_code_missing:" + str(code))

    state = frame.get("state")
    if state == "undersize" and not any(x in target for x in ("尺寸偏小", "量測偏小", "尺寸過小", "低於下限")):
        issues.append("measurement_undersize_missing")
    elif state == "oversize" and not any(x in target for x in ("尺寸偏大", "量測偏大", "尺寸過大", "超出上限")):
        issues.append("measurement_oversize_missing")
    elif state == "in_tolerance" and not any(x in target for x in ("公差內", "進公差", "尺寸合格", "量測合格")):
        issues.append("measurement_in_tolerance_missing")

    if frame.get("complete"):
        if not any(x in target for x in ("材料尺寸", "料尺寸", "來料尺寸", "棒材尺寸", "產品尺寸")):
            issues.append("measurement_material_object_missing")
        if re.search(r"(?:這台)?設備(?:本身)?(?:量測|測量|測得|量到).{0,6}尺寸偏[大小]", target):
            issues.append("measurement_state_attached_to_machine_measurement")

    if frame.get("work_order_context") and frame.get("complete"):
        if not any(x in target for x in ("工單", "訂單")):
            issues.append("measurement_work_order_context_missing")
        if "來料尺寸" not in target:
            issues.append("measurement_incoming_material_object_missing")
        if not any(x in target for x in ("生產", "加工", "處理")):
            issues.append("measurement_equipment_order_relation_missing")
        # Explicitly reject the old wrong attachment where the order is merely
        # possessed by the machine and the dimensional object is left implicit.
        if re.search(r"(?:這台)?設備(?:的)?(?:這張|這個)?(?:工單|訂單).{0,8}尺寸偏[大小]", target):
            issues.append("measurement_size_attached_to_equipment_order")

    return not issues, issues


def health() -> dict:
    undersize = build_frame("Mesin I5 mikro kecil", equipment_codes=["I5"])
    order_undersize = build_frame(
        "Mesin I5 mikro kecil", equipment_codes=["I5"], work_order_context=True
    )
    oversize = build_frame("Mesin BF3 mikro besar", equipment_codes=["BF3"])
    conflict = build_frame("Mesin I15 mikro kecil besar", equipment_codes=["I15"])
    bare_small = build_frame("mesin kecil", equipment_codes=[])
    generic_micro = build_frame("produk mikro kecil", equipment_codes=[])

    checks = [
        undersize.get("active") is True,
        undersize.get("complete") is True,
        deterministic_translation(undersize) == "I5 生產中的材料尺寸偏小",
        deterministic_translation(order_undersize) == "I5 現在生產的這個訂單，來料尺寸偏小",
        deterministic_translation(oversize) == "BF3 生產中的材料尺寸偏大",
        conflict.get("active") is True and conflict.get("complete") is False,
        deterministic_translation(conflict) is None,
        bare_small.get("active") is False,
        generic_micro.get("active") is False,
        validate_translation(undersize, "I5 微型小機台")[0] is False,
        validate_translation(undersize, "I5 這台設備很小")[0] is False,
        validate_translation(undersize, "I5 這台設備量測尺寸偏小")[0] is False,
        validate_translation(undersize, "I5 生產中的材料尺寸偏小")[0] is True,
        validate_translation(order_undersize, "I5 這台設備量測尺寸偏小")[0] is False,
        validate_translation(order_undersize, "I5 這台設備的這張工單，尺寸偏小")[0] is False,
        validate_translation(order_undersize, "I5 現在生產的這個訂單，來料尺寸偏小")[0] is True,
    ]
    return {
        "api_version": FACTORY_MEASUREMENT_SEMANTICS_API_VERSION,
        "build_id": FACTORY_MEASUREMENT_SEMANTICS_BUILD_ID,
        "self_test": {"ok": all(checks), "checks": len(checks)},
    }
