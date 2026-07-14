"""Runtime prompt compiler for the LINE translation bot.

The historical system prompt intentionally keeps every production failure rule as
an auditable knowledge base. Sending the whole knowledge base for every short
message is slow and can dilute instruction priority, so this module compiles it
into a stable principle layer plus only the terminology and failure rules that
are relevant to the current source text.

The full prompt remains the source of truth and is used as a fail-safe fallback.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import List, Sequence, Tuple

PROMPT_OPTIMIZER_VERSION = "2026-07-14.3-auto-tone-signal"

_TAG_RE_TEMPLATE = r"<{tag}>(.*?)</{tag}>"
_HAN_RE = re.compile(r"[\u3400-\u9fff]+")
_LATIN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ0-9._/+:%×x-]*")
_EQUIPMENT_RE = re.compile(r"(?<![A-Za-z0-9])(?:I\d{1,2}|E\d{1,2}|BF\d+|AP|PM\d+|UT|K\d+)(?![A-Za-z0-9])", re.I)
_MEASUREMENT_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:mm|cm|m|kg|g|t|噸|公斤|支|把|台|件|批|捆|°C|℃|%)", re.I)

_COMMON_ID_VOCAB_TOKENS = {
    "mesin", "material", "barang", "data", "proses", "produksi", "kerja",
    "produk", "batang", "stasiun", "station", "operator", "kualitas",
    "nomor", "bagian", "ukuran", "berat", "hasil", "masuk", "keluar",
    "sudah", "tidak", "untuk", "dengan", "yang", "dan", "atau",
}


@dataclass(frozen=True)
class PromptCompileStats:
    original_chars: int
    compiled_chars: int
    vocab_items: int
    context_rules: int
    historical_rules: int
    fallback_used: bool = False

    @property
    def saved_ratio(self) -> float:
        if self.original_chars <= 0:
            return 0.0
        return max(0.0, 1.0 - self.compiled_chars / self.original_chars)


# These are concise abstractions of recurring real-world failures.  The full
# historical details/examples stay in app.py; only matching principles are sent.
# Each row: (id, source directions, trigger regex, compact rule).
_HISTORICAL_RULES: Sequence[Tuple[str, Tuple[str, ...], str, str]] = (
    (
        "release-vs-put",
        ("zh>id",),
        r"放行|放了|已放|放完|幫放|先放|誰放|放在|放到|放地|放料|退庫|過帳",
        "Resolve 放 by syntax: ERP/work-order flow=release data; QC放行=QC release without adding data; location/object placement=taruh/menaruh/meletakkan; 放料=feed material. Never default all 放 to physical placement.",
    ),
    (
        "qing-polysemy",
        ("zh>id",),
        r"請客|請的|公司請|總部請|台北.*請|請幫|請確認|請注意|請拿|請問|申請|請假",
        "Resolve 請 from context: food/drink/welfare sponsorship means traktir/ditraktir/dibayarin, never diminta; requests/admin actions use tolong/mohon/izin as appropriate.",
    ),
    (
        "maaf-apology",
        ("id>zh",),
        r"\b(?:maaf|maafkan|mafkan|maf\s+kan|mafin|mohon\s+maaf|minta\s+maaf|mhn\s+maaf)\b",
        "Every maaf spelling family is an apology (抱歉/對不起), never a request phrase such as 麻煩你了. Preserve sincerity and any promise not to repeat the mistake.",
    ),
    (
        "bare-quantity",
        ("zh>id",),
        r"(?:^|[，。！？\s])\d+\s*(?:台|把|支|個|件)(?:$|[，。！？\s])|[一二兩三四五六七八九十]+(?:台|把|支|個|件)",
        "For a bare number+classifier reply with the noun omitted, stay generic and do not invent the noun: 台/個→buah, 把→bundel, 支→batang, 件→potong. Never use unit unless the source literally says 單位.",
    ),
    (
        "negative-polarity",
        ("zh>id", "id>zh"),
        r"不擋|不得|不能|不可|禁止|不要|未|無法配合|\b(?:tidak|jangan|dilarang|tidak\s+boleh|boleh)\b",
        "Preserve polarity exactly. 不擋 means not blocked/allowed, not prohibited. 無法配合規定 in discipline context means unwilling/noncompliant, not physical inability. Never weaken a prohibition into advice.",
    ),
    (
        "passive-voice",
        ("zh>id",),
        r"被列管|被開立|被罰|被\s*bypass|將被|遭|受到",
        "Preserve Chinese passive roles: the source subject receives the action. Do not reverse actor and object; keep who supervises, fines, bypasses or controls whom.",
    ),
    (
        "factory-material",
        ("zh>id",),
        r"料|來料|棒材|盤元|母材|線材|吊料|上料|下料|入料|出料|送料|卡料|斷料|混料",
        "In this factory, 料 means production material/steel, never feed. Material handling verbs must preserve movement, direction and production state; 吊去 means lift/move away, never steal.",
    ),
    (
        "factory-process",
        ("zh>id",),
        r"研磨|研磨棒|無心研磨|調機|削皮|冷抽|退火|酸洗|矯直|倒角|拋光|噴漆|洗料",
        "Use established shop-floor process terminology. 研磨棒 in production reporting is grinding rod; 調機 is machine setup/adjustment; process names must not be reinterpreted as ordinary household actions.",
    ),
    (
        "factory-reporting",
        ("zh>id",),
        r"工單|工單資訊|短尺|來料尺寸|表面品質|重量確認|ERP|站別|站號|爐號|料號|過帳|退庫",
        "Treat ERP/work-order terms as factory data operations. 工單=work order; 爐號=heat number; preserve station/equipment/work-order IDs exactly and do not translate them as material IDs or ordinary nouns.",
    ),
    (
        "equipment-code",
        ("zh>id", "id>zh"),
        r"(?<![A-Za-z0-9])(?:I\d{1,2}|E\d{1,2}|BF\d+|AP|PM\d+|UT|K\d+)(?![A-Za-z0-9])",
        "Equipment/station codes are immutable. Infer location versus action from syntax; add a natural machine/station label only when it improves clarity, never alter the code.",
    ),
    (
        "direction-time",
        ("id>zh", "zh>id"),
        r"\b(?:sebelum|sesudah|depan|belakang|ujung|bagian)\b|加工前|處理前|前端|後端|尾端|夾頭端|自由端",
        "Keep time and physical direction separate. For example, sebelum diproses + belakang means 加工前，後端…, never the fused and misleading 加工前後端.",
    ),
    (
        "actor-severity",
        ("zh>id",),
        r"高層|施壓|敷衍|福利|警告|處分|記過|重大|職災|停工|列管",
        "Preserve actor, severity and consequence without exaggeration. 高層施壓 should be natural workplace oversight language; 敷衍 is not lying unless the source alleges deception; 福利 is collective welfare unless specific allowances/facilities are named.",
    ),
    (
        "quality-defect",
        ("zh>id", "id>zh"),
        r"異常|缺陷|刮傷|不良|NG|品質|品保|QC|cacat|tidak\s+sesuai|masalah",
        "Use concrete quality language and preserve whether the message reports a defect, asks for confirmation, authorizes release, or orders a stop. Do not turn a report into a command or vice versa.",
    ),
    (
        "taiwan-colloquial",
        ("zh>id",),
        r"乾|幹|靠|傻眼|扯|誇張|笑死|氣死|累死|母湯|感溫|蛤|啦|喔|咧|要不要|需不需要|搞什麼|搞定",
        "Translate Taiwanese colloquial intent, not characters. Preserve complaint, suggestion, disbelief, urgency and friendliness; rhetorical 要不要/需不需要 often suggests an action rather than asking neutrally.",
    ),
    (
        "indonesian-slang",
        ("id>zh",),
        r"\b(?:gak|udah|udh|gimana|bgt|org|yg|tdk|dg|krn|blm|hrs|bs|gw|lu|dong|nih|sih|lho)\b",
        "Normalize Indonesian chat abbreviations and slang before translating, while preserving tone and not making the Chinese unnecessarily formal.",
    ),
    (
        "equipment-material-damage",
        ("id>zh",),
        r"\b(?:rusak|tidak\s+berfungsi|tidak\s+bisa\s+dipakai)\b|pelindung|penutup|panel|sensor|tombol|pintu|interlock|permukaan|batang|material",
        "For Indonesian rusak, distinguish function from surface condition: equipment/safety devices that cannot function use 損壞 or 故障; processed material/product surface defects use 損傷. Never use 損傷 for a broken guard, cover, panel, sensor, button, door or interlock.",
    ),
    (
        "station-location-vs-action",
        ("zh>id",),
        r"(?:削皮|矯直|拋光|倒角|切斷|酸洗|退火|噴砂|研磨|口付|壓光|解捲|冷抽|秤重)(?:那邊|站|機|區|優先|趕快|放行|過帳|退庫)|送去|進(?:削皮|矯直|拋光|倒角|切斷|酸洗|退火|研磨)",
        "Factory process names can denote a station location or an action. Syntax such as X那邊/X站/X機, 送去X, 進X, or X優先放行/過帳/退庫 means the station/location; an explicit object + 要做X/在做X normally means the process action.",
    ),
    (
        "taiwan-traditional-chinese",
        ("id>zh",),
        r".",
        "Write natural Traditional Chinese used in Taiwan. Avoid Simplified Chinese and Mainland-specific wording. Keep Indonesian worker tone and urgency without making the Chinese unnecessarily formal.",
    ),
    (
        "factory-place-safety",
        ("zh>id",),
        r"鹽水廠|台中廠|冷精棒冷抽課|職安署|重大職災|罰單|警告單|列管|帽扣|違規作業|違規操作|巡視設備|記過|抓到",
        "Use the plant-approved place, department, safety and disciplinary terminology. Preserve the exact severity and whether a consequence is supervision, a warning, a violation record or a production stop; do not turn workplace enforcement into police/arrest language.",
    ),
    (
        "format-structure",
        ("*",),
        r"\n|✅|❌|⚠|📢|[•▪▫]|\d+[.)、]",
        "Mirror line breaks, list order, markers and section structure. Do not merge, reorder, add headings or invent emoji. Preserve cause→condition→action chains.",
    ),
    (
        "numbers-units",
        ("*",),
        r"\d|mm|cm|kg|°C|℃|%|噸|公斤|毫米|公尺",
        "Preserve every number, decimal, unit, range, sign and comparison operator exactly. Never convert or round unless the source explicitly requests it.",
    ),
)


def _tag(text: str, name: str) -> str:
    match = re.search(_TAG_RE_TEMPLATE.format(tag=re.escape(name)), text or "", re.S | re.I)
    return match.group(1).strip() if match else ""


def _prefix_before_role(text: str) -> str:
    pos = (text or "").find("<role>")
    return (text[:pos].strip() if pos > 0 else "")


def _direction(src: str, tgt: str) -> str:
    return f"{(src or '').lower()}>{(tgt or '').lower()}"


def _tokenize_for_overlap(text: str) -> set[str]:
    tokens: set[str] = set()
    for h in _HAN_RE.findall(text or ""):
        # Whole Chinese runs plus 2-4 character windows catch glossary terms.
        if len(h) >= 2:
            tokens.add(h.casefold())
            for size in (2, 3, 4):
                if len(h) >= size:
                    tokens.update(h[i:i + size].casefold() for i in range(len(h) - size + 1))
        else:
            tokens.add(h)
    for word in _LATIN_RE.findall(text or ""):
        if len(word) >= 2:
            tokens.add(word.casefold())
    return tokens


def _split_context_rules(section: str) -> List[str]:
    if not section:
        return []
    body = re.sub(r"^\s*10\.\s*CRITICAL CONTEXT RULES:\s*", "", section, flags=re.I)
    starts = list(re.finditer(r"(?<![A-Za-z])([a-z])\)\s+", body))
    if not starts:
        return [body.strip()]
    chunks: List[str] = []
    for idx, start in enumerate(starts):
        end = starts[idx + 1].start() if idx + 1 < len(starts) else len(body)
        chunk = body[start.start():end].strip()
        if chunk:
            chunks.append(chunk)
    return chunks


def _context_score(chunk: str, source_tokens: set[str], source_text: str) -> int:
    chunk_tokens = _tokenize_for_overlap(chunk)
    overlap = source_tokens & chunk_tokens
    score = sum(min(8, len(token)) for token in overlap)
    # Exact equipment/measurement references deserve stronger relevance.
    for code in _EQUIPMENT_RE.findall(source_text or ""):
        if code.casefold() in chunk.casefold():
            score += 12
    if _MEASUREMENT_RE.search(source_text or "") and any(x in chunk for x in ("量詞", "LENGTH", "mm", "米", "台", "把", "支")):
        score += 8
    return score


def _select_context_rules(section: str, source_text: str, limit: int = 3) -> List[str]:
    tokens = _tokenize_for_overlap(source_text)
    ranked = []
    for chunk in _split_context_rules(section):
        score = _context_score(chunk, tokens, source_text)
        if score > 0:
            ranked.append((score, len(chunk), chunk))
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return [row[2] for row in ranked[:limit]]


def _split_vocab_entries(section: str) -> List[str]:
    if not section:
        return []
    body = re.sub(r"^\s*9\.\s*FACTORY VOCABULARY:\s*", "", section, flags=re.I)
    # Vocabulary rows are comma-separated. Headings are retained with the next
    # matching entry only when useful, so sending a whole category is avoided.
    parts = [p.strip() for p in re.split(r",\s*(?=[^,]{1,100}=)", body) if p.strip()]
    return parts


def _normalise_latin_phrase(value: str) -> str:
    value = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ0-9]+", " ", value or " ")
    return re.sub(r"\s+", " ", value).strip().casefold()


def _entry_score(entry: str, source_text: str, direction: str) -> int:
    """Return a specificity-weighted relevance score for one glossary row.

    Indonesian glossary values contain many generic words such as ``mesin`` and
    ``material``. Matching those alone injected dozens of unrelated rows. Exact
    multi-word phrases and uncommon complete words now win; generic tokens alone
    are ignored.
    """
    if "=" not in entry:
        return 0
    left, right = entry.split("=", 1)
    if direction.startswith("zh>"):
        left_terms = [x.strip(" 【】[]()（）:：/\n") for x in re.split(r"[/、|]", left)]
        matches = [term for term in left_terms if term and term in (source_text or "")]
        return max((100 + len(term) * 10 for term in matches), default=0)

    if not direction.startswith("id>"):
        return 0
    source_norm = _normalise_latin_phrase(source_text)
    if not source_norm:
        return 0
    best = 0
    # Slash-separated alternatives are terminology candidates. Parenthetical
    # explanations are useful only when their complete phrase appears.
    for raw in re.split(r"[/;|]", right):
        phrase = _normalise_latin_phrase(raw.split("(", 1)[0])
        if not phrase:
            continue
        words = phrase.split()
        if len(words) >= 2 and re.search(r"(?<![a-z0-9])" + re.escape(phrase) + r"(?![a-z0-9])", source_norm):
            best = max(best, 120 + len(phrase))
        for word in words:
            if len(word) < 4 or word in _COMMON_ID_VOCAB_TOKENS:
                continue
            if re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", source_norm):
                best = max(best, 20 + len(word))
    return best


def _select_vocab(section: str, source_text: str, direction: str, limit: int = 8) -> List[str]:
    ranked: List[Tuple[int, int, str]] = []
    seen = set()
    for entry in _split_vocab_entries(section):
        score = _entry_score(entry, source_text, direction)
        if score <= 0:
            continue
        clean = re.sub(r"\s+", " ", entry).strip()
        key = clean.casefold()
        if clean and key not in seen:
            ranked.append((score, len(clean), clean))
            seen.add(key)
    ranked.sort(key=lambda row: (-row[0], row[1]))
    return [row[2] for row in ranked[: max(1, limit)]]


def _matching_historical_rules(source_text: str, direction: str, limit: int = 4) -> List[str]:
    out: List[str] = []
    for rule_id, directions, pattern, instruction in _HISTORICAL_RULES:
        if "*" not in directions and direction not in directions:
            continue
        if re.search(pattern, source_text or "", re.I):
            out.append(f"[{rule_id}] {instruction}")
        if len(out) >= limit:
            break
    return out


def _variant_instruction(variant: str, tgt_lang: str) -> str:
    variant = (variant or "default").lower()
    target = tgt_lang or "the target language"
    if variant == "natural":
        return f"Use natural native workplace phrasing in {target}; preserve all facts, force and structure, but avoid translationese."
    if variant == "literal":
        return f"Use a close, transparent translation in {target}; retain source sentence order and explicit wording while remaining grammatical."
    if variant == "formal":
        return f"Use formal, professional announcement wording in {target}; do not add a title or stronger authority than the source."
    if variant == "backcheck":
        return "Translate conservatively for verification: prioritize semantic reversibility and explicit actor/action/negation over stylistic polish."
    return "Follow the configured tone while preserving the source's level of formality and urgency."


def _direction_principles(src: str, tgt: str) -> str:
    src_l = (src or "").lower()
    tgt_l = (tgt or "").lower()
    rules: List[str] = []
    if tgt_l.startswith("id"):
        rules.append(
            "Use plain, immediately understandable Indonesian factory language: standard spelling, short sentences and direct actor-action-object order. Match source formality; use casual slang only when the source is casual. Use kita for shared workplace impact and kalian only for a direct instruction to workers."
        )
        rules.append(
            "Do not literalize Taiwanese workplace concepts: leadership pressure, collective welfare, perfunctory reporting, factory material handling and ERP operations must be rendered by their operational meaning and original severity, without adding accusations or facts."
        )
    if tgt_l.startswith("zh"):
        rules.append(
            "Write natural Traditional Chinese used in Taiwan, never Simplified Chinese or Mainland-specific phrasing. Normalize Indonesian chat abbreviations internally while preserving the worker's tone."
        )
        rules.append(
            "For rusak and similar defect wording, distinguish function from surface condition: broken/nonfunctional equipment or safety devices use 損壞/故障; processed material or product surface defects use 損傷."
        )
    if src_l.startswith("zh"):
        rules.append(
            "Resolve omitted Chinese subjects only from available factory/chat context. Taiwanese rhetorical questions may suggest an action rather than request neutral information; preserve that pragmatic force."
        )
    return "\n".join(rules)


def _core_principles(src: str, tgt: str, tone_instruction: str, variant: str) -> str:
    tone = (tone_instruction or "Match the source tone.").strip()
    directional = _direction_principles(src, tgt)
    return (
        "<translation_principles>\n"
        f"Direction: {src}->{tgt}. Output only one final translation in {tgt}.\n"
        "Priority: immutable placeholders/names/codes/data > runtime semantic contract > hard glossary > complete source meaning > natural target wording.\n"
        "Translate the intended workplace meaning, not isolated dictionary words. Preserve actor, action, object, time, condition, negation, severity, cause and consequence; never add facts.\n"
        "Preserve @mentions exactly. Preserve Chinese person names, customer names, immutable placeholders, equipment/work-order/lot codes, numbers, decimals, units, ranges and symbols exactly.\n"
        "Preserve emoji, line breaks, blank lines, paragraph order and lists. Do not merge paragraphs or add headings, markdown, explanations, alternatives or commentary. "
        "Use the supplied automatic tone analysis to choose wording, but do not invent additional emoji; the server handles any final emoji decoration after validation.\n"
        "Do not leak source-language ordinary words; translate them into the target language.\n"
        + (directional + "\n" if directional else "")
        + f"Tone: {tone}\n"
        + f"Variant: {_variant_instruction(variant, tgt)}\n"
        + "</translation_principles>"
    )


def compile_translation_prompt(
    full_prompt: str,
    source_text: str,
    src_lang: str,
    tgt_lang: str,
    *,
    tone_instruction: str = "",
    variant: str = "default",
    max_chars: int | None = None,
) -> Tuple[str, PromptCompileStats]:
    """Compile the large historical prompt for one translation request.

    The stable principle block is always first, improving cacheability. Dynamic
    terminology and incident rules follow it. Optional blocks are dropped when
    they exceed the request budget; the historical all-rules prompt is not restored.
    """
    original = full_prompt or ""
    if not original.strip():
        stats = PromptCompileStats(0, 0, 0, 0, 0, True)
        return original, stats
    enabled = os.environ.get("PROMPT_OPTIMIZER_ENABLED", "1").strip().lower() not in {"0", "false", "off", "no"}
    if not enabled:
        stats = PromptCompileStats(len(original), len(original), 0, 0, 0, True)
        return original, stats

    try:
        direction = _direction(src_lang, tgt_lang)
        # The old prefix duplicated target-language/output rules and added over
        # one thousand characters to every request.  The compact principle block
        # below is the single source of truth for runtime delivery.
        semantic_contract = _tag(original, "semantic_contract")
        vocab = _select_vocab(_tag(original, "factory_vocabulary"), source_text, direction)
        context = _select_context_rules(_tag(original, "context_disambiguation"), source_text)
        historical = _matching_historical_rules(source_text, direction)

        role_block = (
            "<role>You are a professional translator for a Taiwan stainless-steel factory LINE work chat. "
            "Produce operationally clear, culturally natural translations for Taiwanese and migrant workers.</role>"
        )
        core_block = _core_principles(src_lang, tgt_lang, tone_instruction, variant)
        semantic_block = ("<semantic_contract>" + semantic_contract + "</semantic_contract>") if semantic_contract else ""
        output_block = (
            "<output_format>Output only the translation. Preserve original paragraph and line breaks. "
            "No prefix, explanation, markdown or added content.</output_format>"
        )
        optional_blocks: List[str] = []
        if vocab:
            optional_blocks.append("<relevant_factory_terms>\n" + "\n".join(f"- {item}" for item in vocab) + "\n</relevant_factory_terms>")
        if historical:
            optional_blocks.append("<relevant_failure_rules>\n" + "\n".join(f"- {item}" for item in historical) + "\n</relevant_failure_rules>")
        if context:
            optional_blocks.append("<relevant_context_rules>\n" + "\n".join(f"- {item}" for item in context) + "\n</relevant_context_rules>")

        cap = max_chars or int(os.environ.get("PROMPT_MAX_CHARS", "6000"))
        # Long/OCR documents may need more relevant terms but should still be far
        # below the original 30k+ prompt.
        if len(source_text or "") >= 800:
            cap = max(cap, 9000)

        # Never fall back to the historical all-rules prompt merely because the
        # compact prompt exceeds the budget.  That old behavior caused the exact
        # cost/quality regression this compiler is meant to prevent.  Keep the
        # invariant layer and request-specific semantic contract, then append only
        # optional blocks that fit.
        sections: List[str] = [role_block, core_block]
        if semantic_block:
            sections.append(semantic_block)
        for block in optional_blocks:
            candidate = "\n".join(sections + [block, output_block]).strip()
            if len(candidate) <= cap:
                sections.append(block)
        sections.append(output_block)
        compiled = "\n".join(section for section in sections if section).strip()
        if not compiled:
            stats = PromptCompileStats(len(original), len(original), 0, 0, 0, True)
            return original, stats
        stats = PromptCompileStats(len(original), len(compiled), len(vocab), len(context), len(historical), False)
        return compiled, stats
    except Exception:
        stats = PromptCompileStats(len(original), len(original), 0, 0, 0, True)
        return original, stats


def prompt_contains_required_invariants(prompt: str) -> bool:
    """Cheap startup/test invariant for the compiled prompt."""
    required = (
        "Output only one final translation",
        "immutable placeholders",
        "runtime semantic contract",
        "Preserve @mentions",
        "Do not leak source-language ordinary words",
    )
    return all(item in (prompt or "") for item in required)
