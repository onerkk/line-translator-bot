"""Central terminology policy for factory Chinese↔Indonesian translation.

The glossary is a mixed knowledge base: some rows are canonical terms that may
be enforced verbatim, while other rows are explanations, alternatives or
context notes.  Treating every ``idn`` value as an exact target phrase causes
translationese and can leak definitions into the final sentence.  This module
is the single source of truth for deciding how each row may be used.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, Iterator, Mapping, Tuple

POLICY_VERSION = 1

# Corrections are glossary data migrations, not sentence-specific replacements.
# They repair corrupted canonical entries wherever the glossary is consumed:
# prompt grounding, TM seeding, forward enforcement and reverse lookup.
_CORE_MIGRATIONS: Dict[str, Dict[str, Any]] = {
    "標籤": {
        "canonical_idn": "label produk",
        "translation_mode": "hard",
        "reverse_safe": True,
        "forbidden_idn": ["Faktur Pemesanan", "Surat pesanan pelanggan"],
    },
    "研磨機": {
        "canonical_idn": "mesin grinding",
        "translation_mode": "hard",
        "reverse_safe": True,
        "forbidden_idn": ["Mesin Penghalus cetakkan", "Mesin Penghalus cetakan"],
    },
    "工單訂單資訊「長度 MIN」": {
        "canonical_idn": "Panjang MIN",
        "translation_mode": "hard",
        "reverse_safe": True,
    },
    "工單訂單資訊「長度 MAX」": {
        "canonical_idn": "Panjang MAX",
        "translation_mode": "hard",
        "reverse_safe": True,
    },
    "工單製程紀錄「長度」": {
        "canonical_idn": "Panjang",
        "translation_mode": "hard",
        "reverse_safe": True,
    },
    "工單": {
        "canonical_idn": "work order",
        "translation_mode": "hard",
        "reverse_safe": True,
        "forbidden_idn": ["Tempat Buku bahan", "Buku bahan", "lembar kerja", "order kerja"],
    },
    "膠膜": {
        "canonical_idn": "plastik pembungkus",
        "translation_mode": "hard",
        "reverse_safe": True,
        "forbidden_idn": ["Sejenis Bubble Wrap", "bubble wrap sejenis"],
    },
    "噴漆": {
        "canonical_idn": "pengecatan semprot",
        # Verbs must be allowed to inflect naturally (disemprot cat / melakukan
        # pengecatan semprot), so this is context guidance rather than a literal
        # substring requirement.
        "translation_mode": "soft",
        "reverse_safe": False,
        "forbidden_idn": ["spray cat", "di-spray cat", "tidak spray cat"],
    },
    "短尺維護": {
        "canonical_idn": "penanganan material pendek",
        "translation_mode": "soft",
        "reverse_safe": False,
        "forbidden_idn": ["perawatan ukuran pendek"],
    },
    "來料尺寸": {
        "canonical_idn": "ukuran material masuk",
        "translation_mode": "hard",
        "reverse_safe": True,
    },
    "表面品質": {
        "canonical_idn": "kualitas permukaan",
        "translation_mode": "hard",
        "reverse_safe": True,
    },
    "重量確認": {
        "canonical_idn": "konfirmasi berat",
        "translation_mode": "hard",
        "reverse_safe": True,
    },
    "木箱": {
        "canonical_idn": "peti kayu",
        "translation_mode": "hard",
        "reverse_safe": True,
        "forbidden_idn": ["kotak kayu"],
    },
    "裝箱": {
        "canonical_idn": "memasukkan material ke dalam peti kayu",
        "translation_mode": "soft",
        "reverse_safe": False,
        "forbidden_idn": ["masukkan barang ke dalam kotak kayu", "memasukkan barang ke dalam kotak kayu"],
    },
    "支援裝箱": {
        "canonical_idn": "membantu proses pengemasan ke dalam peti kayu",
        "translation_mode": "soft",
        "reverse_safe": False,
        "forbidden_idn": ["bantu proses memasukkan barang ke dalam kotak kayu"],
    },
    "木箱包": {
        "canonical_idn": "pengemasan dengan peti kayu",
        "translation_mode": "soft",
        "reverse_safe": False,
        "forbidden_idn": ["packing kotak kayu"],
    },
    "抓帳": {
        "canonical_idn": "tutup buku",
        "translation_mode": "hard",
        "reverse_safe": False,
        "forbidden_idn": ["cek data", "periksa data", "rekap data"],
    },
    "會計結帳": {
        "canonical_idn": "tutup buku",
        "translation_mode": "hard",
        "reverse_safe": False,
        "forbidden_idn": ["cek data", "periksa data", "rekap data"],
    },
    "陸續到料": {
        "canonical_idn": "material akan tiba secara bertahap",
        "translation_mode": "soft",
        "reverse_safe": False,
    },
    "到料": {
        "canonical_idn": "material tiba",
        "translation_mode": "soft",
        "reverse_safe": False,
    },
    "優先安排包裝": {
        "canonical_idn": "memprioritaskan pengaturan proses pengemasan",
        "translation_mode": "soft",
        "reverse_safe": False,
    },
    "電子系統": {
        "canonical_idn": "sistem elektronik",
        "translation_mode": "hard",
        "reverse_safe": True,
    },
    "自然拉動": {
        "canonical_idn": "tarikan alami/pasif",
        "translation_mode": "soft",
        "reverse_safe": False,
        "forbidden_idn": ["ditarik secara manual", "sistem tarik manual", "pengoperasian manual"],
    },
}

_DESCRIPTION_MARKERS = (
    " yang ", " untuk ", " digunakan ", " dipakai ", " di pakai ",
    " agar ", " supaya ", " adalah ", " merupakan ", " sejenis ",
    " yaitu ", " artinya ", " maksudnya ", " tempat ",
)

def _as_mapping(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {"idn": str(value or "")}


def _clean_target(value: Any) -> str:
    text = str(value or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def canonical_target(value: Any) -> str:
    """Return the target term intended for translation output."""
    row = _as_mapping(value)
    return _clean_target(row.get("canonical_idn") or row.get("idn"))


def _looks_like_description(target: str) -> bool:
    text = _clean_target(target)
    if not text:
        return True
    low = f" {text.casefold()} "
    if any(marker in low for marker in _DESCRIPTION_MARKERS):
        return True
    if len(text) > 48 or len(text.split()) > 5:
        return True
    if re.search(r"[.!?。！？;；:]", text):
        return True
    # Slash-separated alternatives are useful reference data but cannot be
    # enforced as one literal output phrase.
    if "/" in text or "｜" in text:
        return True
    if text.startswith(("(", "（")) or text.endswith((")", "）")):
        return True
    return False


def translation_mode(value: Any) -> str:
    """Return ``hard``, ``soft`` or ``disabled`` for a glossary row.

    Explicit metadata is authoritative.  Legacy rows are classified
    conservatively: only compact canonical-looking terms become hard.
    """
    row = _as_mapping(value)
    explicit = str(row.get("translation_mode") or row.get("mode") or "").strip().lower()
    if explicit in {"hard", "soft", "disabled"}:
        return explicit
    target = canonical_target(row)
    if not target:
        return "disabled"
    return "soft" if _looks_like_description(target) else "hard"


def is_hard(value: Any) -> bool:
    return translation_mode(value) == "hard" and bool(canonical_target(value))


def is_soft(value: Any) -> bool:
    return translation_mode(value) == "soft" and bool(canonical_target(value))


def normalize_entry(source_term: str, value: Any) -> Dict[str, Any]:
    row = copy.deepcopy(_as_mapping(value))
    migration = _CORE_MIGRATIONS.get(str(source_term))
    if migration:
        row.update(copy.deepcopy(migration))
        # Keep the legacy field synchronized because older admin/UI code reads
        # ``idn`` directly.
        row["idn"] = migration["canonical_idn"]
        row["policy_migrated"] = True
    else:
        target = canonical_target(row)
        if target:
            row["canonical_idn"] = target
        row["translation_mode"] = translation_mode(row)
    return row


def normalize_glossary(glossary: Mapping[str, Any] | None) -> Dict[str, Dict[str, Any]]:
    return {
        str(term): normalize_entry(str(term), value)
        for term, value in (glossary or {}).items()
        if str(term).strip()
    }


def iter_matches(
    source_text: str,
    glossary: Mapping[str, Any] | None,
    *,
    limit: int = 50,
) -> Iterator[Tuple[str, Dict[str, Any]]]:
    text = source_text or ""
    count = 0
    for term in sorted((glossary or {}).keys(), key=lambda x: len(str(x)), reverse=True):
        term_s = str(term)
        if term_s and term_s in text:
            yield term_s, normalize_entry(term_s, (glossary or {})[term])
            count += 1
            if count >= limit:
                return


def hard_pairs(
    source_text: str,
    glossary: Mapping[str, Any] | None,
    *,
    limit: int = 50,
) -> list[Tuple[str, str]]:
    pairs: list[Tuple[str, str]] = []
    for term, row in iter_matches(source_text, glossary, limit=limit):
        if is_hard(row):
            target = canonical_target(row)
            if target:
                pairs.append((term, target))
    return pairs


def soft_hints(
    source_text: str,
    glossary: Mapping[str, Any] | None,
    *,
    limit: int = 30,
) -> list[Tuple[str, str, str]]:
    hints: list[Tuple[str, str, str]] = []
    for term, row in iter_matches(source_text, glossary, limit=limit):
        if not is_soft(row):
            continue
        target = canonical_target(row)
        note = _clean_target(row.get("note_id") or row.get("note_zh"))
        if target:
            hints.append((term, target, note))
    return hints


def forbidden_phrases(value: Any) -> Tuple[str, ...]:
    row = _as_mapping(value)
    raw = row.get("forbidden_idn") or ()
    if isinstance(raw, str):
        raw = [raw]
    return tuple(_clean_target(x) for x in raw if _clean_target(x))



def deprecated_indonesian_phrases() -> Tuple[str, ...]:
    """Phrases removed by glossary migrations and never valid as final output."""
    out: list[str] = []
    seen = set()
    for migration in _CORE_MIGRATIONS.values():
        raw = migration.get("forbidden_idn") or ()
        for phrase in raw:
            cleaned = _clean_target(phrase)
            key = cleaned.casefold()
            if cleaned and key not in seen:
                seen.add(key)
                out.append(cleaned)
    return tuple(out)

def glossary_forbidden_phrases(glossary: Mapping[str, Any] | None) -> Tuple[str, ...]:
    out: list[str] = []
    seen = set()
    for term, value in (glossary or {}).items():
        row = normalize_entry(str(term), value)
        for phrase in forbidden_phrases(row):
            key = phrase.casefold()
            if key not in seen:
                seen.add(key)
                out.append(phrase)
    return tuple(out)
