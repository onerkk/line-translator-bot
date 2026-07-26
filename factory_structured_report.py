"""Deterministic translation for source-verifiable factory measurement reports.

This module handles compact Indonesian quality/measurement messages whose
meaning is fully represented by a machine identifier, named measurement
positions and explicit pass/fail status fields.  Such messages do not need a
second generative-model call: every output field can be reconstructed directly
from the current source text, which removes provider/reviewer availability as a
single point of failure while remaining fail-closed for unrecognised content.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

FACTORY_STRUCTURED_REPORT_API_VERSION = 1
FACTORY_STRUCTURED_REPORT_BUILD_ID = "2026-07-26.1-source-verifiable-measurement-report"

_SPACE_RE = re.compile(r"[ \t]+")
_LINE_SPLIT_RE = re.compile(r"\r?\n+")
_NUMBER_VALUE_RE = re.compile(
    r"^[=:：\-–—]?\s*(?P<value>[+-]?\d+(?:[.,]\d+)?)"
    r"(?P<unit>\s*(?:mm|cm|m|μm|um|kg|g|%|°c|℃))?\s*[.!。]?$",
    re.IGNORECASE,
)
_MACHINE_ONLY_RE = re.compile(
    r"^(?:mesin\s+)?(?P<code>[A-Za-z]{1,8}\d{1,8}(?:[-./_][A-Za-z0-9]+)*)\s*[.:：-]?$",
    re.IGNORECASE,
)

# The aliases are field-level semantics rather than stored sentence matches.
# New spelling variants can be added without changing the rendering logic.
_POSITION_FIELDS: Sequence[Tuple[str, str, Sequence[str]]] = (
    ("front", "前端", ("depan", "bagian depan", "ujung depan", "front")),
    ("middle", "中間", ("tengah", "bagian tengah", "middle")),
    ("rear", "後端", ("belakang", "bagian belakang", "ujung belakang", "rear", "back")),
)
_QUALITY_FIELDS: Sequence[Tuple[str, str, Sequence[str]]] = (
    ("roundness", "圓度", ("kebulatan", "roundness")),
    ("straightness", "直線度", ("kelurusan", "straightness")),
    ("diameter", "直徑", ("diameter",)),
)

_STATUS_OK = {
    "ok", "oke", "baik", "normal", "lulus", "pass", "passed", "sesuai",
}
_STATUS_NG = {
    "ng", "not ok", "tidak ok", "tidak oke", "tidak baik", "abnormal",
    "gagal", "fail", "failed", "tidak sesuai",
}


def _norm(value: str) -> str:
    text = str(value or "").strip().casefold()
    text = text.replace("_", " ")
    text = re.sub(r"[：:]", " ", text)
    return _SPACE_RE.sub(" ", text).strip(" .;,-–—")


def _canonical_unit(raw: str) -> str:
    unit = _SPACE_RE.sub("", str(raw or ""))
    if unit.casefold() == "°c":
        return "°C"
    if unit.casefold() == "um":
        return "μm"
    return unit


def _field_alias_index() -> Dict[str, Tuple[str, str, str]]:
    result: Dict[str, Tuple[str, str, str]] = {}
    for key, zh, aliases in _POSITION_FIELDS:
        for alias in aliases:
            result[_norm(alias)] = ("position", key, zh)
    for key, zh, aliases in _QUALITY_FIELDS:
        for alias in aliases:
            result[_norm(alias)] = ("quality", key, zh)
    return result


_FIELD_ALIASES = _field_alias_index()
_FIELD_PREFIXES = tuple(sorted(_FIELD_ALIASES, key=len, reverse=True))


@dataclass(frozen=True)
class StructuredMeasurementReport:
    machine_code: str
    rows: Tuple[Tuple[str, str, str, str], ...]
    # row tuple: (kind, canonical_key, zh_label, rendered_value)


def _parse_field_line(line: str) -> Optional[Tuple[str, str, str, str]]:
    normalized_line = _SPACE_RE.sub(" ", str(line or "").strip())
    low = normalized_line.casefold()
    for alias in _FIELD_PREFIXES:
        # Require a real field boundary so "depanan" cannot match "depan".
        match = re.match(
            r"^" + re.escape(alias) + r"(?=$|\s|[:：=\-–—])",
            _norm(low),
        )
        if not match:
            continue

        # Consume the alias against the original line with tolerant whitespace.
        alias_pattern = r"\s+".join(re.escape(part) for part in alias.split())
        original_match = re.match(
            r"^\s*" + alias_pattern + r"(?=$|\s|[:：=\-–—])",
            normalized_line,
            flags=re.IGNORECASE,
        )
        if not original_match:
            continue
        remainder = normalized_line[original_match.end():].strip()
        kind, key, zh_label = _FIELD_ALIASES[alias]

        number_match = _NUMBER_VALUE_RE.fullmatch(remainder)
        if number_match:
            value = number_match.group("value")
            unit = _canonical_unit(number_match.group("unit") or "")
            return kind, key, zh_label, value + unit

        status = _norm(remainder)
        if status in _STATUS_OK:
            return kind, key, zh_label, "正常"
        if status in _STATUS_NG:
            return kind, key, zh_label, "異常"
        return None
    return None


def parse_id_zh_measurement_report(
    text: str,
    *,
    normalize_equipment_codes: Optional[Callable[[str], Tuple[str, list]]] = None,
) -> Optional[StructuredMeasurementReport]:
    """Parse a complete Indonesian structured measurement report.

    Fail-closed rules:
    - every non-empty line must be a machine header or a known field;
    - at most one header is allowed;
    - duplicate fields are rejected;
    - at least two positional measurements plus one quality/status field are
      required, preventing an ordinary prose sentence from being misclassified.
    """
    if not text or not isinstance(text, str):
        return None
    source = text
    if normalize_equipment_codes is not None:
        try:
            source, _ = normalize_equipment_codes(source)
        except Exception:
            return None

    lines = [line.strip() for line in _LINE_SPLIT_RE.split(source) if line.strip()]
    if len(lines) < 4 or len(lines) > 16:
        return None

    machine_code = ""
    rows: List[Tuple[str, str, str, str]] = []
    seen_fields = set()

    for line in lines:
        machine_match = _MACHINE_ONLY_RE.fullmatch(line)
        if machine_match:
            code = machine_match.group("code").upper()
            if machine_code and machine_code != code:
                return None
            machine_code = code
            continue

        parsed = _parse_field_line(line)
        if not parsed:
            return None
        _kind, key, _zh_label, _value = parsed
        if key in seen_fields:
            return None
        seen_fields.add(key)
        rows.append(parsed)

    position_count = sum(1 for kind, *_rest in rows if kind == "position")
    quality_count = sum(1 for kind, *_rest in rows if kind == "quality")
    if position_count < 2 or quality_count < 1:
        return None
    return StructuredMeasurementReport(machine_code=machine_code, rows=tuple(rows))


def render_id_zh_measurement_report(report: StructuredMeasurementReport) -> str:
    lines: List[str] = []
    if report.machine_code:
        lines.append(report.machine_code)
    lines.extend(f"{zh_label}：{value}" for _kind, _key, zh_label, value in report.rows)
    return "\n".join(lines)


def translate_id_zh_measurement_report(
    text: str,
    *,
    normalize_equipment_codes: Optional[Callable[[str], Tuple[str, list]]] = None,
) -> Optional[str]:
    report = parse_id_zh_measurement_report(
        text,
        normalize_equipment_codes=normalize_equipment_codes,
    )
    return render_id_zh_measurement_report(report) if report else None
