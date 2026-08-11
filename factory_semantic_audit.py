"""Source-grounded semantic audit for high-risk factory translations.

The ordinary glossary/keyword validators can confirm that words are present, but
cannot confirm that the words express the same operational claim.  This module
builds a compact semantic frame from the Chinese source, supplies that frame to
an independent reviewer, and validates the Indonesian result deterministically.

It intentionally does not contain exact source-sentence replacements.  Rules are
claim-level (time, actor/machine, material, priority, prohibition, movement and
throughput) so paraphrases receive the same protection while unrelated notices
do not inherit a stored target sentence.
"""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

FACTORY_SEMANTIC_AUDIT_API_VERSION = 1
FACTORY_SEMANTIC_AUDIT_BUILD_ID = "2026-08-11.2-source-first-handover-translation"

_MACHINE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,4}\s*-?\s*\d{1,4})(?![A-Za-z0-9])")
_EXPLICIT_CRANE_ZH = ("天車", "吊車", "起重機", "行車", "crane", "derek")
_EXPLICIT_SPEED_ZH = ("轉速", "速度", "低速", "高速", "rpm", "r.p.m")
_FACTORY_CUES = (
    "料", "棒材", "圓棒", "機台", "設備", "生產", "加工", "到料", "進料", "出料",
    "拋光", "研磨", "清洗", "削皮", "包裝", "工單", "tag", "重量", "異常", "客訴",
    "懲處", "班", "品保", "qc", "吊", "跑", "產能", "手套", "防護具", "領取", "领取",
)


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    text = text.replace("\u3000", " ")
    return re.sub(r"\s+", " ", text).strip()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", _norm(value))


def _contains_any(text: str, terms: Iterable[str]) -> bool:
    return any(_norm(term) in text for term in terms if str(term or "").strip())


def _search_any(text: str, patterns: Iterable[str]) -> bool:
    return any(re.search(str(pattern), text, flags=re.I | re.S) for pattern in patterns)


def _parse_small_count(raw: str) -> int | None:
    token = _compact(raw)
    if token.isdigit():
        value = int(token)
        return value if 0 <= value <= 999 else None
    digits = {
        "零": 0, "〇": 0,
        "一": 1, "壹": 1,
        "二": 2, "兩": 2, "两": 2, "貳": 2, "贰": 2,
        "三": 3, "參": 3, "叁": 3,
        "四": 4, "肆": 4,
        "五": 5, "伍": 5,
        "六": 6, "陸": 6, "陆": 6,
        "七": 7, "柒": 7,
        "八": 8, "捌": 8,
        "九": 9, "玖": 9,
    }
    if token in digits:
        return digits[token]
    if "十" in token or "拾" in token:
        left, right = re.split(r"[十拾]", token, maxsplit=1)
        tens = 1 if not left else digits.get(left)
        ones = 0 if not right else digits.get(right)
        if tens is not None and ones is not None:
            return tens * 10 + ones
    return None


def _parse_quantity_value(raw: str) -> float | int | None:
    """Parse a compact Arabic/Chinese quantity, including a trailing half.

    The parser is intentionally limited to shop-floor allocation quantities.  It
    does not guess unit conversions; it only resolves forms such as 2, 兩, 1.5,
    半, 一包半 and 兩袋半 into numeric values for source/target consistency checks.
    """
    token = _compact(raw)
    if not token:
        return None
    if token in ("半",):
        return 0.5
    if re.fullmatch(r"\d+(?:[.,]\d+)?", token):
        value = float(token.replace(",", "."))
        return int(value) if value.is_integer() else value
    parsed = _parse_small_count(token)
    return parsed


def _parse_package_quantity_phrase(raw: str) -> float | int | None:
    token = _compact(raw)
    if not token:
        return None
    token = re.sub(r"(?:包|袋)$", "", token)
    if token == "半":
        return 0.5
    if token.endswith(("包半", "袋半")):
        base = token[:-2]
        base_value = _parse_quantity_value(base) if base else 1
        return (base_value + 0.5) if base_value is not None else None
    # Callers may pass the complete phrase before stripping the unit.
    match = re.fullmatch(r"(?P<base>.+?)(?:包|袋)半", _compact(raw))
    if match:
        base_value = _parse_quantity_value(match.group("base"))
        return (base_value + 0.5) if base_value is not None else None
    return _parse_quantity_value(token)


_PACKAGE_NUMBER_TOKEN = r"(?:\d+(?:[.,]\d+)?|[零〇一二兩两三四五六七八九十壹貳贰參叁肆伍陸陆柒捌玖拾]{1,3})"
_PACKAGE_QUANTITY_PHRASE = rf"(?:半(?:包|袋)|{_PACKAGE_NUMBER_TOKEN}(?:包|袋)(?:半)?)"
_PAIR_NUMBER_TOKEN = _PACKAGE_NUMBER_TOKEN

_REPORT_DEADLINE_PATTERNS = (
    re.compile(
        rf"(?P<value>{_PACKAGE_NUMBER_TOKEN}|半)(?:個|个)?"
        r"(?P<unit>小時|小时|鐘頭|钟头|分鐘|分钟)(?:以)?內",
        re.I,
    ),
    re.compile(
        rf"(?:最晚|不得超過|不得超过|不超過|不超过|至多)"
        rf"(?P<value>{_PACKAGE_NUMBER_TOKEN}|半)(?:個|个)?"
        r"(?P<unit>小時|小时|鐘頭|钟头|分鐘|分钟)",
        re.I,
    ),
)


def _extract_package_quantity(raw: str) -> float | int | None:
    return _parse_package_quantity_phrase(raw)


def _extract_pair_count(raw: str) -> int | None:
    value = _parse_quantity_value(raw)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _extract_report_deadline(compact: str) -> Tuple[float | int | None, str, str]:
    """Extract a source-stated reporting deadline without guessing its unit."""
    for pattern in _REPORT_DEADLINE_PATTERNS:
        match = pattern.search(compact or "")
        if not match:
            continue
        raw_value = match.group("value")
        value = 0.5 if raw_value == "半" else _parse_quantity_value(raw_value)
        raw_unit = match.group("unit")
        unit = "hour" if raw_unit in ("小時", "小时", "鐘頭", "钟头") else "minute"
        return value, unit, match.group(0)
    return None, "", ""


def _extract_count(compact: str, concept_pattern: str) -> int | None:
    count_token = r"(?:\d{1,3}|[零〇一二兩两三四五六七八九十壹貳贰參叁肆伍陸陆柒捌玖拾]{1,3})"
    count_chars = "零〇一二兩两三四五六七八九十壹貳贰參叁肆伍陸陆柒捌玖拾0-9"
    # Do not cross another count token. This binds the nearest quantity to the
    # concept instead of stealing a number from a preceding defect in the same
    # sentence (for example: 一件重量異常，兩件 TAG 貼錯).
    before = re.search(
        rf"(?P<count>{count_token})(?:件|個|个|支|把)?"
        rf"(?:(?![{count_chars}]).){{0,12}}(?:" + concept_pattern + r")",
        compact,
        flags=re.I | re.S,
    )
    if before:
        return _parse_small_count(before.group("count"))
    after = re.search(
        r"(?:" + concept_pattern + r")"
        rf"(?:(?![{count_chars}]).){{0,8}}"
        rf"(?P<count>{count_token})(?:件|個|个|支|把)?",
        compact,
        flags=re.I | re.S,
    )
    return _parse_small_count(after.group("count")) if after else None


def _machine_ids(source: str) -> List[str]:
    values: List[str] = []
    for match in _MACHINE_RE.findall(source or ""):
        canonical = re.sub(r"\s+", "", match).upper()
        if canonical not in values:
            values.append(canonical)
    return values


def build_source_frame(source: str, src_lang: str, tgt_lang: str) -> Dict[str, Any]:
    """Build a deterministic claim frame for Chinese -> Indonesian factory text."""
    src = str(source or "")
    compact = _compact(src)
    frame: Dict[str, Any] = {
        "active": False,
        "src_lang": str(src_lang or "").lower(),
        "tgt_lang": str(tgt_lang or "").lower(),
        "claims": [],
        "flags": {},
        "machine_ids": _machine_ids(src),
        "counts": {},
        "units": {},
        "quantity_tokens": {},
        "mentions": re.findall(
            r"(?:@[^\s,，。!?！？:：;；]{1,48}|__MENTION_\d+__)",
            src,
            flags=re.I,
        ),
        "ambiguities": [],
        "prohibited_inferences": [],
        "risk_score": 0,
    }
    if frame["src_lang"] != "zh" or frame["tgt_lang"] != "id" or not src.strip():
        return frame
    package_quantity_cue = bool(re.search(_PACKAGE_QUANTITY_PHRASE, compact))
    if not any(cue in compact for cue in _FACTORY_CUES) and not package_quantity_cue:
        return frame

    flags = frame["flags"]
    flags["deadline_month_end"] = _contains_any(compact, ("月底前", "本月底前", "月末前"))
    flags["current_month_scope"] = _contains_any(compact, ("本月份", "本月", "這個月", "这个月"))
    flags["large_size"] = _contains_any(compact, ("大尺寸", "大規格", "大规格", "大徑", "大径"))
    flags["small_size"] = _contains_any(compact, ("小尺寸", "小規格", "小规格", "小徑", "小径"))
    flags["bar_material"] = _contains_any(compact, ("棒材", "圓棒", "圆棒", "棒料"))
    flags["polishing"] = _contains_any(compact, ("拋光", "抛光", "polishing"))
    flags["arrival"] = _contains_any(compact, ("到料", "到貨", "到货", "來料", "来料", "進料", "进料"))
    # Arrival-profile modifiers must be attached to an arrival context. Generic
    # "很多" in a defect notice means many cases, not bulk material arrival.
    flags["bulk"] = (
        _contains_any(compact, ("大量", "大批", "批量"))
        or (flags["arrival"] and _contains_any(compact, ("很多", "不少")))
    )
    flags["concentrated"] = _contains_any(compact, ("集中", "密集", "同時", "同时", "一起到"))
    # "先做" is only a production-priority cue when it is not the ordinary
    # sequence marker in phrases such as "先做清楚記號".
    flags["priority"] = bool(
        _contains_any(compact, ("優先", "优先", "先跑", "先從", "先从", "先行生產", "先行生产"))
        or re.search(r"先做(?!清楚|明確|明确|標記|标记|記號|记号)", compact)
    )
    flags["production"] = _contains_any(compact, ("生產", "生产", "加工", "跑料", "跑"))
    flags["prohibition"] = _contains_any(compact, ("不可以", "不可", "不得", "禁止", "不能", "不要"))
    flags["hoist_or_load"] = _contains_any(compact, ("吊", "上料", "上機", "上机", "裝料", "装料"))
    flags["slow_run"] = _contains_any(compact, ("慢慢跑", "慢跑", "跑很慢", "慢慢生產", "慢慢生产"))
    flags["explicit_crane"] = _contains_any(compact, _EXPLICIT_CRANE_ZH)
    flags["explicit_speed"] = _contains_any(compact, _EXPLICIT_SPEED_ZH)

    # Shift-handover reporting notices combine a time context, universal issue
    # scope, escalation recipient, deadline and a purpose clause.  Treating
    # these as independent keywords allowed fluent output to change
    # 「比較好做異常反應」 into an unsupported promise of faster handling.
    flags["shift_handover"] = _contains_any(compact, (
        "交接班", "交班", "換班", "换班", "班次交接", "輪班交接", "轮班交接",
    ))
    flags["production_problem"] = _search_any(compact, (
        r"(?:生產|生产|製程|制程|作業|作业).{0,5}(?:問題|问题|異常|异常)",
        r"(?:問題|问题|異常|异常).{0,5}(?:生產|生产|製程|制程|作業|作业)",
    ))
    flags["any_production_problem"] = bool(
        flags["production_problem"]
        and _search_any(compact, (
            r"(?:任何|任一|所有|全部|不論任何|不论任何).{0,8}(?:生產|生产|製程|制程|作業|作业).{0,5}(?:問題|问题|異常|异常)",
            r"(?:生產|生产|製程|制程|作業|作业).{0,5}(?:任何|任一|所有|全部).{0,5}(?:問題|问题|異常|异常)",
        ))
    )
    flags["report_to_shift_leader"] = _search_any(compact, (
        r"(?:反應|反应|反映|回報|回报|報告|报告|通報|通报|告知).{0,8}(?:給|给|向)?(?:當班|当班|值班)?班長",
        r"(?:給|给|向)(?:當班|当班|值班)?班長.{0,5}(?:反應|反应|反映|回報|回报|報告|报告|通報|通报|告知)",
    ))
    report_deadline_value, report_deadline_unit, report_deadline_token = _extract_report_deadline(compact)
    flags["report_deadline"] = bool(
        flags["report_to_shift_leader"] and report_deadline_value is not None and report_deadline_unit
    )
    if flags["report_deadline"]:
        frame["counts"]["report_deadline"] = report_deadline_value
        frame["units"]["report_deadline"] = report_deadline_unit
        frame["quantity_tokens"]["report_deadline"] = report_deadline_token
    flags["abnormal_followup_purpose"] = _search_any(compact, (
        r"(?:班長).{0,15}(?:比較好|更好|較好|方便|容易).{0,15}(?:異常|异常).{0,10}(?:反應|反应|處理|处理|應變|应变|追蹤|追踪|跟進|跟进)",
        r"(?:比較好|更好|較好|方便|容易).{0,10}(?:讓|让)?(?:班長).{0,15}(?:異常|异常).{0,10}(?:反應|反应|處理|处理|應變|应变|追蹤|追踪|跟進|跟进)",
        r"(?:班長).{0,15}(?:異常|异常).{0,10}(?:反應|反应|處理|处理|應變|应变|追蹤|追踪|跟進|跟进).{0,10}(?:比較好|更好|較好|方便|容易)",
    ))
    flags["explicit_followup_speed"] = _search_any(compact, (
        r"(?:班長|異常|异常).{0,20}(?:更快|比較快|较快|快速|迅速|盡快|尽快|儘快|立即|即時|即时|馬上|马上)",
        r"(?:更快|比較快|较快|快速|迅速|盡快|尽快|儘快|立即|即時|即时|馬上|马上).{0,20}(?:班長|異常|异常)",
    ))

    # Compositional operational claims. These are phrase-level plant meanings,
    # not complete-sentence corrections. Each flag can combine with the others
    # in unseen wording, and the target is validated by claim relations rather
    # than by matching one stored translation.
    flags["no_more_search"] = _search_any(compact, (
        r"(?:不用|不必|免|別|别|不要)(?:再)?找(?:了)?",
    ))
    flags["peeling_location"] = _search_any(compact, (
        r"(?:在|位於|位于|放在|擺在|摆在)(?:削皮|peeling)(?:站|區|区|工位|那邊|那边)?",
        r"(?:削皮|peeling)(?:站|區|区|工位|那邊|那边)(?:有|那裡|那里)?",
    ))
    flags["tag_front_rear_error"] = bool(
        "tag" in compact
        and _search_any(compact, (
            r"(?:前後|前后).{0,8}(?:貼錯|贴错|貼反|贴反|對調|对调|顛倒|颠倒)",
            r"(?:貼錯|贴错|貼反|贴反|對調|对调|顛倒|颠倒).{0,8}(?:前後|前后)",
        ))
    )
    flags["pending_system_record"] = _search_any(compact, (
        r"(?:帳|账)(?:務|务)?(?:資料|资料)?(?:還|还|尚)?(?:沒|没|未)(?:來|来|到|進|进)",
        r"(?:系統|系统)?(?:帳務|账务|資料|资料).{0,6}(?:還|还|尚)?(?:沒|没|未)(?:進|进|到|來|来)",
        r"(?:還|还|尚)?(?:沒|没|未)(?:進|进)(?:系統|系统)",
    ))
    flags["clear_marking"] = _search_any(compact, (
        r"(?:標記|标记|做記號|做记号|註記|注记).{0,4}(?:清楚|明確|明确)",
        r"(?:清楚|明確|明确).{0,4}(?:標記|标记|做記號|做记号|註記|注记)",
        r"做(?:清楚|明確|明确)(?:的)?(?:記號|记号|標記|标记)",
    ))
    flags["bundle_by_bundle"] = _search_any(compact, (
        r"(?:一把一把|逐把|每把(?:分開|分开)?|一捆一捆|逐捆)",
    ))
    flags["self_uncertainty"] = _search_any(compact, (
        r"(?:懷疑|怀疑|不(?:太)?放心|不太相信)(?:我)?自己",
        r"(?:不(?:太)?放心|不確定|不确定).{0,6}(?:貼|贴)(?:tag)?(?:結果|结果)?",
    ))
    flags["double_check"] = _search_any(compact, (
        r"(?:多|再)(?:看|查|確認|确认|檢查|检查|複核|复核)一次",
        r"(?:再一次)(?:確認|确认|檢查|检查|複核|复核)",
    ))
    # PPE/package allocation semantics.  包/袋 is a physical package
    # classifier (bungkus), while 把/捆 is a rod-material bundle (bundel).  The
    # relationship between a package and pairs must also survive: 又/加 means an
    # additional quantity; 有/含 means package contents.
    flags["gloves"] = _contains_any(compact, ("手套", "工作手套", "防護手套", "防护手套"))
    flags["before_offwork"] = _contains_any(compact, ("下班前", "收工前", "離開前", "离开前"))
    flags["pickup_action"] = bool(
        flags["gloves"] and _search_any(compact, (
            r"(?:記得|记得|要|請|请|別忘|别忘)?(?:領取|领取|領|领|拿|取)(?:一下)?(?:手套|工作手套|防護手套|防护手套)",
            r"(?:手套|工作手套|防護手套|防护手套).{0,4}(?:記得|记得|要|請|请)?(?:領取|领取|領|领|拿|取)",
        ))
    )
    per_person_match = re.search(
        rf"(?:一人|每人|每個人|每个人)(?:可|要|領|领|拿|取|分|發|发|給|给|各)?"
        rf"(?:手套|工作手套|防護手套|防护手套)?(?P<package>{_PACKAGE_QUANTITY_PHRASE})",
        compact,
        flags=re.I,
    )
    flags["per_person_package"] = bool(per_person_match)
    if per_person_match:
        frame["counts"]["package_allocation"] = _extract_package_quantity(per_person_match.group("package"))
        frame["quantity_tokens"]["package_allocation"] = per_person_match.group("package")

    additive_match = re.search(
        rf"(?P<package>{_PACKAGE_QUANTITY_PHRASE}).{{0,5}}?"
        rf"(?:又|另(?:外)?(?:再)?加|再加|加上|加)"
        rf"(?P<pairs>{_PAIR_NUMBER_TOKEN})(?:雙|双)",
        compact,
        flags=re.I,
    )
    additive_pair_only = re.search(
        rf"(?:又|另(?:外)?(?:再)?加|再加|加上|加)(?P<pairs>{_PAIR_NUMBER_TOKEN})(?:雙|双)",
        compact,
        flags=re.I,
    )
    containment_match = re.search(
        rf"(?P<package>{_PACKAGE_QUANTITY_PHRASE}).{{0,5}}?"
        rf"(?:裡面|里面|內|内)?(?:有|含|內含|内含|裝有|装有|裝|装)"
        rf"(?P<pairs>{_PAIR_NUMBER_TOKEN})(?:雙|双)",
        compact,
        flags=re.I,
    )
    containment_pair_only = re.search(
        rf"(?:裡面|里面|包內|包内|袋內|袋内|每包|每袋).{{0,3}}?"
        rf"(?:有|含|內含|内含|裝有|装有|裝|装)?(?P<pairs>{_PAIR_NUMBER_TOKEN})(?:雙|双)",
        compact,
        flags=re.I,
    )
    if not containment_match:
        containment_match = re.search(
            rf"(?:每包|每袋|一包|一袋).{{0,2}}?(?P<pairs>{_PAIR_NUMBER_TOKEN})(?:雙|双)"
            rf"(?!.*(?:又|另(?:外)?(?:再)?加|再加|加上))",
            compact,
            flags=re.I,
        )

    package_phrase = None
    pair_token = None
    if additive_match:
        package_phrase = additive_match.group("package")
        pair_token = additive_match.group("pairs")
    elif per_person_match and additive_pair_only:
        package_phrase = per_person_match.group("package")
        pair_token = additive_pair_only.group("pairs")
    flags["package_plus_pairs"] = bool(package_phrase and pair_token)

    contains_package_phrase = None
    contains_pair_token = None
    if not flags["package_plus_pairs"]:
        if containment_match:
            contains_package_phrase = containment_match.groupdict().get("package")
            contains_pair_token = containment_match.group("pairs")
        elif per_person_match and containment_pair_only:
            contains_package_phrase = per_person_match.group("package")
            contains_pair_token = containment_pair_only.group("pairs")
    flags["package_contains_pairs"] = bool(contains_package_phrase and contains_pair_token)

    if flags["package_plus_pairs"]:
        frame["counts"].setdefault("package_allocation", _extract_package_quantity(package_phrase))
        frame["quantity_tokens"].setdefault("package_allocation", package_phrase)
        frame["counts"]["pair_allocation"] = _extract_pair_count(pair_token)
        frame["quantity_tokens"]["pair_allocation"] = pair_token
    elif flags["package_contains_pairs"]:
        frame["counts"].setdefault("package_allocation", _extract_package_quantity(contains_package_phrase))
        frame["quantity_tokens"].setdefault("package_allocation", contains_package_phrase)
        frame["counts"]["pair_allocation"] = _extract_pair_count(contains_pair_token)
        frame["quantity_tokens"]["pair_allocation"] = contains_pair_token

    correction_match = re.search(
        rf"(?:不是|並非|并非|不對|不对)(?P<from>{_PACKAGE_QUANTITY_PHRASE})"
        rf"(?:[,，。;；:：]?)(?:而是|是|應是|应是|應該是|应该是)"
        rf"(?P<to>{_PACKAGE_QUANTITY_PHRASE})",
        compact,
        flags=re.I,
    )
    flags["package_quantity_correction"] = bool(correction_match)
    if correction_match:
        frame["counts"]["package_correction_from"] = _extract_package_quantity(correction_match.group("from"))
        frame["counts"]["package_correction_to"] = _extract_package_quantity(correction_match.group("to"))
        frame["quantity_tokens"]["package_correction_from"] = correction_match.group("from")
        frame["quantity_tokens"]["package_correction_to"] = correction_match.group("to")
    flags["physical_package_unit"] = bool(
        flags["per_person_package"]
        or flags["package_plus_pairs"]
        or flags["package_contains_pairs"]
        or flags["package_quantity_correction"]
    )
    flags["material_bundle_unit"] = bool(
        flags.get("bundle_by_bundle")
        or re.search(rf"{_PACKAGE_NUMBER_TOKEN}(?:把|捆)", compact, flags=re.I)
    )

    flags["abnormal_weight"] = _contains_any(compact, (
        "重量異常", "重量异常", "重量不正常", "重量有問題", "重量有问题",
    ))
    flags["wrong_tag_attachment"] = bool(
        "tag" in compact and _contains_any(compact, (
            "tag貼錯", "tag贴错", "tag貼反", "tag贴反", "tag錯貼", "tag错贴",
        ))
    )
    flags["own_shift"] = _contains_any(compact, ("我們班", "我们班", "本班", "自己班"))
    flags["recent_many_cases"] = _search_any(compact, (
        r"(?:這陣子|这阵子|最近|近期|近來|近来).{0,12}(?:太多|很多|不少)(?:件|次|案例|異常|异常)?",
        r"(?:太多|很多|不少)(?:件|次|案例|異常|异常).{0,12}(?:這陣子|这阵子|最近|近期|近來|近来)",
    ))
    flags["customer_complaint"] = _contains_any(compact, (
        "客訴", "客诉", "客戶抱怨", "客户抱怨", "客戶投訴", "客户投诉",
    ))
    flags["cannot_help"] = _contains_any(compact, (
        "幫不上忙", "帮不上忙", "沒辦法幫", "没办法帮", "不能幫忙", "不能帮忙",
        "無法幫忙", "无法帮忙",
    ))
    flags["sanction"] = _contains_any(compact, (
        "懲處", "惩处", "處分", "处分", "被罰", "被罚", "受罰", "受罚",
    ))
    flags["severe_consequence"] = _contains_any(compact, (
        "會很傷", "会很伤", "很嚴重", "很严重", "後果很重", "后果很重",
        "影響很大", "影响很大", "代價很大", "代价很大",
    ))
    frame["counts"]["abnormal_weight"] = (
        _extract_count(compact, r"重量(?:異常|异常|不正常|有問題|有问题)")
        if flags["abnormal_weight"] else None
    )
    frame["counts"]["wrong_tag_attachment"] = (
        _extract_count(compact, r"tag.{0,4}(?:貼錯|贴错|貼反|贴反|錯貼|错贴)")
        if flags["wrong_tag_attachment"] else None
    )

    def add(claim_id: str, evidence: str, meaning: str, target_hint: str) -> None:
        frame["claims"].append({
            "claim_id": claim_id,
            "source_evidence": evidence,
            "meaning_zh": meaning,
            "required_target_meaning_id": target_hint,
        })

    if flags["deadline_month_end"]:
        add("deadline_month_end", "月底前/月末前", "到料或作業時點在月底以前", "sebelum akhir bulan")
    if flags["arrival"]:
        detail = "材料將到廠／到貨"
        hint = "material akan tiba / masuk"
        if flags["bulk"] and flags["concentrated"]:
            detail = "材料會在相近時間集中且大量到貨"
            hint = "akan tiba dalam jumlah besar dalam waktu yang berdekatan"
        elif flags["bulk"]:
            detail = "材料會大量到貨"
            hint = "akan tiba dalam jumlah besar"
        elif flags["concentrated"]:
            detail = "材料會集中到貨"
            hint = "akan tiba dalam waktu yang berdekatan / secara terkonsentrasi"
        add("material_arrival", "到料/到貨/進料", detail, hint)
    if flags["large_size"] and flags["bar_material"]:
        add("large_bar_material", "大尺寸棒材", "大尺寸的棒材，不是抽象的『大型生產』", "material batang berukuran besar")
    elif flags["large_size"]:
        add("large_size_material", "大尺寸", "大尺寸材料／大規格品", "material berukuran besar")
    if flags["polishing"]:
        add("polishing_process", "拋光機/拋光", "供拋光設備或拋光製程使用", "untuk mesin/proses polishing")
    if frame["machine_ids"]:
        add("machine_assignment", ", ".join(frame["machine_ids"]), "指定這些機台執行要求，代碼必須原樣保留", ", ".join(frame["machine_ids"]))
    if flags["priority"] and flags["production"]:
        meaning = "指定機台要優先安排生產"
        hint = "harus memprioritaskan produksi"
        if flags["current_month_scope"] and flags["large_size"]:
            meaning = "指定機台要先做屬於本月份的大尺寸材料／訂單"
            hint = "harus terlebih dahulu memprioritaskan produksi material berukuran besar untuk bulan ini"
        add("production_priority", "優先生產/先做/先跑", meaning, hint)
    if flags["prohibition"] and flags["small_size"]:
        meaning = "禁止改做小尺寸材料"
        hint = "tidak boleh / jangan ... material berukuran kecil"
        if flags["hoist_or_load"] and flags["slow_run"]:
            meaning = "禁止把小尺寸材料吊／送上機後，以低產出方式慢慢占用機台；不是叫人把吊掛動作做慢"
            hint = "tidak boleh memasukkan/mengangkat material kecil ke mesin lalu menjalankan produksinya secara lambat"
        add("prohibited_small_size_schedule", "不可以吊小尺寸慢慢跑", meaning, hint)

    if flags["shift_handover"]:
        add(
            "shift_handover_context",
            "交接班／交班／換班",
            "問題是在班次交接期間被發現",
            "saat pergantian/serah terima shift",
        )
    if flags["any_production_problem"]:
        add(
            "any_production_problem_scope",
            "任何生產問題",
            "回報要求涵蓋所有生產問題，不可縮成某一種問題",
            "masalah produksi apa pun / setiap masalah produksi",
        )
    if flags["report_to_shift_leader"]:
        add(
            "report_to_shift_leader",
            "反應／回報給班長",
            "向班長回報問題；反應在此不是化學或生理反應",
            "laporkan/melaporkan kepada kepala regu",
        )
        frame["ambiguities"].append({
            "source_term": "反應給班長",
            "resolved_meaning_zh": "向班長回報或通報生產問題",
            "rejected_interpretations": ["翻成 bereaksi", "只說一般回應而沒有回報對象"],
        })
    if flags["report_deadline"]:
        deadline_value = frame["counts"].get("report_deadline")
        deadline_unit = frame["units"].get("report_deadline")
        target_unit = "jam" if deadline_unit == "hour" else "menit"
        add(
            "reporting_deadline",
            report_deadline_token,
            f"必須在來源所定的 {deadline_value} {deadline_unit} 內向班長回報",
            f"dalam waktu {deadline_value} {target_unit} / paling lambat {deadline_value} {target_unit}",
        )
    if flags["abnormal_followup_purpose"]:
        add(
            "abnormal_followup_purpose",
            "班長比較好做異常反應",
            "及時回報是為了讓班長更妥善或更容易進行異常後續；未表示處理速度會更快",
            "agar kepala regu dapat menindaklanjuti kondisi abnormal dengan lebih baik / lebih mudah",
        )
        frame["ambiguities"].append({
            "source_term": "比較好做異常反應",
            "resolved_meaning_zh": "讓班長更妥善或更容易做異常後續處理",
            "rejected_interpretations": ["自行改成班長可以更快處理", "把異常反應直譯成反射或化學反應"],
        })
        if not flags["explicit_followup_speed"]:
            frame["prohibited_inferences"].append(
                "Do not change better/easier abnormal follow-up into faster follow-up; the source does not state increased handling speed."
            )

    if flags["no_more_search"] and flags["peeling_location"]:
        add("stop_searching", "不用／不必再找", "停止尋找目前在找的材料或物件", "tidak usah/perlu dicari lagi")
        add("peeling_station_location", "在削皮／削皮區", "該材料目前位於削皮站或削皮區，不是正在執行削皮動作", "barangnya ada/berada di stasiun atau bagian peeling")
        frame["ambiguities"].append({
            "source_term": "在削皮",
            "resolved_meaning_zh": "在『不用找了』的回覆中表示物件位於削皮站／區域",
            "rejected_interpretations": ["翻成正在被削皮", "翻成對方正在削皮", "加入疑問或不確定語氣"],
        })
    if flags["tag_front_rear_error"]:
        add("front_rear_tag_swap", "TAG 前後貼錯／貼反", "TAG 的前端與後端貼反或對調", "TAG bagian depan dan belakang tertukar / salah ditempel")
    if flags["pending_system_record"]:
        add("pending_system_record", "帳沒來／系統資料尚未進入", "該材料的系統或帳務資料尚未到位，不是一般備註", "data sistem belum masuk / belum tersedia")
        frame["ambiguities"].append({
            "source_term": "帳沒來",
            "resolved_meaning_zh": "工廠作業資料／帳務資料尚未進入系統或尚未到位",
            "rejected_interpretations": ["一般筆記、備註或 catatan 尚未存在"],
        })
    if flags["clear_marking"]:
        add("clear_marking", "標記清楚", "對資料尚未到位的材料做清楚標記", "ditandai / diberi tanda dengan jelas")
    if flags["bundle_by_bundle"]:
        add("bundle_by_bundle", "一把一把／逐把", "TAG 必須逐把／逐捆處理，避免混貼", "satu bundel demi satu bundel / setiap bundel satu per satu")
    if flags["self_uncertainty"] or flags["double_check"]:
        add("self_result_double_check", "懷疑自己／多看一次", "不立即相信自己剛完成的貼標結果，因此再複核一次", "tidak langsung yakin dengan hasil sendiri lalu cek sekali lagi")
    if flags["before_offwork"] and flags["pickup_action"]:
        add("pickup_before_offwork", "下班前領取手套", "離開工作場所前記得領取手套", "sebelum pulang kerja, ingat ambil sarung tangan")
    elif flags["pickup_action"]:
        add("pickup_gloves", "領取手套", "提醒人員領取工作手套", "ingat/tolong ambil sarung tangan")
    if flags["per_person_package"]:
        package_count = frame["counts"].get("package_allocation")
        add(
            "per_person_package_allocation",
            "一人／每人 + 包或袋",
            f"每個人分配 {package_count if package_count is not None else '來源所述數量'} 包；包是實體包裝單位，不是棒材捆",
            "setiap orang ... bungkus; never bundel",
        )
    if flags["package_plus_pairs"]:
        pair_count = frame["counts"].get("pair_allocation")
        add(
            "additional_pairs_allocation",
            "一包又／另加若干雙",
            f"在每人包裝數之外，另外增加 {pair_count if pair_count is not None else '來源所述數量'} 雙；不是包內含量",
            "bungkus ditambah ... pasang; not merely dan and not berisi",
        )
    if flags["package_contains_pairs"]:
        pair_count = frame["counts"].get("pair_allocation")
        add(
            "package_contents_pairs",
            "一包有／含若干雙",
            f"每包內含 {pair_count if pair_count is not None else '來源所述數量'} 雙",
            "bungkus berisi ... pasang",
        )
    if flags["package_quantity_correction"]:
        from_count = frame["counts"].get("package_correction_from")
        to_count = frame["counts"].get("package_correction_to")
        add(
            "package_quantity_correction",
            "不是 X 包，是 Y 包（含半包）",
            f"否定 {from_count} 包並更正為 {to_count} 包；包是 bungkus，不是 bundel",
            "bukan ... bungkus, melainkan/tetapi ... bungkus",
        )
    if flags["physical_package_unit"]:
        frame["ambiguities"].append({
            "source_term": "數字+包／袋",
            "resolved_meaning_zh": "實體包裝數量，印尼文量詞用 bungkus；半包用 setengah bungkus",
            "rejected_interpretations": ["翻成棒材捆 bundel", "把一包半拆成一包再加半捆", "省略半數量"],
        })

    if flags["abnormal_weight"]:
        count = frame["counts"].get("abnormal_weight")
        count_text = str(count) if count is not None else "來源所述數量"
        add("abnormal_weight_case", "重量異常", f"有 {count_text} 件重量異常", "barang dengan berat tidak normal; preserve count")
    if flags["wrong_tag_attachment"]:
        count = frame["counts"].get("wrong_tag_attachment")
        count_text = str(count) if count is not None else "來源所述數量"
        add("wrong_tag_case", "TAG 貼錯", f"有 {count_text} 件 TAG 貼錯", "barang yang TAG-nya salah ditempel; preserve count")
    if flags["own_shift"]:
        add("own_shift_accountability", "都是我們班／本班", "上述異常全部歸屬本班，不可省略責任歸屬", "semuanya berasal dari shift/regu kita")
    if flags["recent_many_cases"]:
        add("recent_repeated_cases", "這陣子太多件／近期很多", "近期同類異常案件過多", "akhir-akhir ini kasus seperti ini terlalu banyak")
    if flags["customer_complaint"]:
        add("customer_complaint_risk", "一旦客訴", "若形成客戶申訴或抱怨", "jika sampai ada keluhan/komplain pelanggan")
    if flags["cannot_help"]:
        add("speaker_cannot_help", "我也幫不上忙", "發生客訴後，說話者也無法協助處理或保護", "saya juga tidak bisa/dapat membantu")
    if flags["sanction"]:
        add("sanction_risk", "被懲處／處分", "可能受到公司懲處或處分", "kena/dikenai/mendapat sanksi")
    if flags["severe_consequence"]:
        add("severe_consequence", "會很傷／後果很重", "受到懲處的後果或影響會很嚴重", "akibat/dampaknya akan sangat berat")

    if flags["hoist_or_load"] and not flags["explicit_crane"]:
        frame["ambiguities"].append({
            "source_term": "吊",
            "resolved_meaning_zh": "工廠語境中的吊料／移料／上機動作；原文未指明設備種類",
            "rejected_interpretations": ["自行補成天車、吊車、crane 或 overhead crane"],
        })
        frame["prohibited_inferences"].append("Do not add crane/derek/overhead-crane equipment unless the source explicitly names it.")
    if flags["slow_run"] and not flags["explicit_speed"]:
        frame["ambiguities"].append({
            "source_term": "慢慢跑/慢跑",
            "resolved_meaning_zh": "生產排程／產出效率偏慢，不等於明確的低轉速操作指令",
            "rejected_interpretations": ["自行補成 RPM、低轉速、降低機台速度"],
        })
        frame["prohibited_inferences"].append("Do not invent RPM, low rotational speed, or a machine-speed setting.")

    risk_weights = {
        "deadline_month_end": 1,
        "arrival": 1,
        "bulk": 1,
        "concentrated": 1,
        "large_size": 1,
        "small_size": 1,
        "priority": 2,
        "prohibition": 2,
        "hoist_or_load": 1,
        "slow_run": 2,
        "no_more_search": 2,
        "peeling_location": 2,
        "tag_front_rear_error": 3,
        "pending_system_record": 3,
        "clear_marking": 1,
        "bundle_by_bundle": 2,
        "self_uncertainty": 1,
        "double_check": 1,
        "abnormal_weight": 2,
        "wrong_tag_attachment": 2,
        "own_shift": 2,
        "recent_many_cases": 2,
        "customer_complaint": 2,
        "cannot_help": 2,
        "sanction": 2,
        "severe_consequence": 2,
        "gloves": 1,
        "before_offwork": 1,
        "pickup_action": 1,
        "per_person_package": 2,
        "package_plus_pairs": 3,
        "package_contains_pairs": 3,
        "package_quantity_correction": 4,
        "physical_package_unit": 2,
        "shift_handover": 1,
        "any_production_problem": 2,
        "report_to_shift_leader": 2,
        "report_deadline": 3,
        "abnormal_followup_purpose": 3,
    }
    frame["risk_score"] = sum(weight for key, weight in risk_weights.items() if flags.get(key))
    decisive = (
        flags.get("tag_front_rear_error")
        or flags.get("pending_system_record")
        or flags.get("abnormal_weight")
        or flags.get("wrong_tag_attachment")
        or flags.get("package_quantity_correction")
        or flags.get("package_plus_pairs")
        or flags.get("package_contains_pairs")
        or (
            flags.get("shift_handover")
            and flags.get("any_production_problem")
            and flags.get("report_to_shift_leader")
            and flags.get("report_deadline")
            and flags.get("abnormal_followup_purpose")
        )
        or (flags.get("no_more_search") and flags.get("peeling_location"))
    )
    frame["active"] = bool(frame["claims"] and (frame["risk_score"] >= 3 or decisive))
    return frame


def should_force_review(frame: Mapping[str, Any]) -> bool:
    if not frame or not frame.get("active"):
        return False
    flags = frame.get("flags") or {}
    return bool(
        int(frame.get("risk_score", 0) or 0) >= 5
        or flags.get("prohibition")
        or (flags.get("priority") and len(frame.get("claims") or ()) >= 3)
        or frame.get("ambiguities")
    )


def build_prompt(frame: Mapping[str, Any]) -> str:
    if not frame or not frame.get("active"):
        return ""
    lines = ["<source_semantic_frame>"]
    lines.append("This frame was derived from the current Chinese source. Preserve every claim relation, not merely the keywords.")
    for claim in frame.get("claims", []) or []:
        lines.append(
            "Claim {claim_id}: source={source_evidence}; meaning={meaning_zh}; target concept={required_target_meaning_id}.".format(**claim)
        )
    for ambiguity in frame.get("ambiguities", []) or []:
        lines.append(
            "Ambiguity {source_term}: resolve as {resolved_meaning_zh}; reject {rejected}.".format(
                source_term=ambiguity.get("source_term", ""),
                resolved_meaning_zh=ambiguity.get("resolved_meaning_zh", ""),
                rejected="; ".join(ambiguity.get("rejected_interpretations", []) or []),
            )
        )
    for item in frame.get("prohibited_inferences", []) or []:
        lines.append("Prohibited inference: " + str(item))
    lines.append("Silently back-translate the final Indonesian and confirm that time, material scope, quantities, classifiers, arithmetic/content relations, machine assignment, priority and prohibition are unchanged.")
    lines.append("</source_semantic_frame>")
    return "\n".join(lines)


def _has_any_target(low: str, terms: Sequence[str]) -> bool:
    return any(_norm(term) in low for term in terms)


def _words_between_match(low: str, left: str, right: str, max_chars: int = 90) -> bool:
    return bool(re.search(left + r".{0," + str(max_chars) + r"}" + right, low, flags=re.I | re.S))


def _same_clause_has(low: str, term_groups: Sequence[Sequence[str]]) -> bool:
    """Return True when one target clause contains at least one term per group.

    Keyword presence alone is not enough for operational notices: a model can
    scatter all required words across unrelated sentences and still change the
    source relation.  This helper makes the decisive action/object/negation
    checks clause-local without requiring one rigid Indonesian wording.
    """
    clauses = [part.strip() for part in re.split(r"[.!?;\n]+", low) if part.strip()]
    for clause in clauses:
        if all(_has_any_target(clause, tuple(group)) for group in term_groups):
            return True
    return False


def _indonesian_number_words(value: int) -> str:
    units = (
        "nol", "satu", "dua", "tiga", "empat", "lima",
        "enam", "tujuh", "delapan", "sembilan",
    )
    if 0 <= value <= 9:
        return units[value]
    if value == 10:
        return "sepuluh"
    if value == 11:
        return "sebelas"
    if 12 <= value <= 19:
        return units[value - 10] + " belas"
    if 20 <= value <= 99:
        tens, ones = divmod(value, 10)
        return units[tens] + " puluh" + ((" " + units[ones]) if ones else "")
    if value == 100:
        return "seratus"
    return ""


def _count_terms(value: int | None) -> Tuple[str, ...]:
    if value is None:
        return ()
    values = [str(value)]
    word = _indonesian_number_words(int(value))
    if word:
        values.append(word)
    return tuple(values)


def _same_clause_has_counted_concept(
    low: str,
    count: int | None,
    concept_groups: Sequence[Sequence[str]],
) -> bool:
    groups: List[Sequence[str]] = list(concept_groups)
    count_group = _count_terms(count)
    if count_group:
        groups.insert(0, count_group)
    return _same_clause_has(low, groups)


def _indonesian_quantity_terms(value: float | int | None) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        return _count_terms(value)
    whole = int(value)
    fraction = round(float(value) - whole, 6)
    values: List[str] = [str(value), str(value).replace(".", ",")]
    if fraction == 0.5:
        if whole == 0:
            values.append("setengah")
        else:
            whole_word = _indonesian_number_words(whole)
            if whole_word:
                values.append(whole_word + " setengah")
    return tuple(dict.fromkeys(v for v in values if v))


def _quantity_unit_in_clause(clause: str, value: float | int | None, units: Sequence[str]) -> bool:
    quantities = _indonesian_quantity_terms(value)
    if not quantities:
        return any(_norm(unit) in clause for unit in units)
    for quantity in quantities:
        for unit in units:
            pattern = r"(?<![a-z0-9])" + re.escape(_norm(quantity)) + r"\s+" + re.escape(_norm(unit)) + r"(?![a-z])"
            if re.search(pattern, clause, flags=re.I):
                return True
    return False


def _quantity_unit_spans(
    clause: str, value: float | int | None, units: Sequence[str]
) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    quantities = _indonesian_quantity_terms(value)
    if not quantities:
        return spans
    for quantity in quantities:
        for unit in units:
            pattern = r"(?<![a-z0-9])" + re.escape(_norm(quantity)) + r"\s+" + re.escape(_norm(unit)) + r"(?![a-z])"
            spans.extend((match.start(), match.end()) for match in re.finditer(pattern, clause, flags=re.I))
    return sorted(set(spans))


def _same_clause_has_quantity_relation(
    low: str,
    left_value: float | int | None,
    left_units: Sequence[str],
    relation_terms: Sequence[str],
    right_value: float | int | None,
    right_units: Sequence[str],
) -> bool:
    clauses = [part.strip() for part in re.split(r"[.!?;\n]+", low) if part.strip()]
    for clause in clauses:
        left_spans = _quantity_unit_spans(clause, left_value, left_units)
        right_spans = _quantity_unit_spans(clause, right_value, right_units)
        relation_spans: List[Tuple[int, int]] = []
        for term in relation_terms:
            relation_spans.extend(
                (match.start(), match.end())
                for match in re.finditer(re.escape(_norm(term)), clause, flags=re.I)
            )
        if any(
            left_end <= relation_start and relation_end <= right_start
            for _left_start, left_end in left_spans
            for relation_start, relation_end in relation_spans
            for right_start, _right_end in right_spans
        ):
            return True
    return False


def _format_indonesian_quantity(value: float | int | None, raw_token: str = "") -> str:
    if value is None:
        return ""
    token = _compact(raw_token)
    prefer_digits = bool(re.search(r"\d", token))
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, int):
        if prefer_digits:
            return str(value)
        return _indonesian_number_words(value) or str(value)
    whole = int(value)
    fraction = round(float(value) - whole, 6)
    if fraction == 0.5:
        if prefer_digits and whole == 0:
            return "0,5"
        if whole == 0:
            return "setengah"
        whole_text = str(whole) if prefer_digits else (_indonesian_number_words(whole) or str(whole))
        return whole_text + " setengah"
    return str(value).replace(".", ",")


def _package_rebuild(frame: Mapping[str, Any]) -> str:
    flags = frame.get("flags") or {}
    counts = frame.get("counts") or {}
    raw = frame.get("quantity_tokens") or {}
    mentions = [str(x).strip() for x in frame.get("mentions", []) or [] if str(x).strip()]
    mention_prefix = ((" ".join(mentions)) + " ") if mentions else ""

    if flags.get("package_quantity_correction"):
        before = _format_indonesian_quantity(
            counts.get("package_correction_from"), raw.get("package_correction_from", "")
        )
        after = _format_indonesian_quantity(
            counts.get("package_correction_to"), raw.get("package_correction_to", "")
        )
        if before and after:
            return f"{mention_prefix}bukan {before} bungkus, melainkan {after} bungkus."

    if not (flags.get("per_person_package") and flags.get("gloves")):
        return ""
    package_qty = _format_indonesian_quantity(
        counts.get("package_allocation"), raw.get("package_allocation", "")
    )
    if not package_qty:
        return ""
    sentences: List[str] = []
    if flags.get("before_offwork") and flags.get("pickup_action"):
        sentences.append("Sebelum pulang kerja, ingat ambil sarung tangan.")
    elif flags.get("pickup_action"):
        sentences.append("Ingat ambil sarung tangan.")

    pair_qty = _format_indonesian_quantity(
        counts.get("pair_allocation"), raw.get("pair_allocation", "")
    )
    if flags.get("package_plus_pairs") and pair_qty:
        sentences.append(f"Setiap orang mendapat {package_qty} bungkus ditambah {pair_qty} pasang.")
    elif flags.get("package_contains_pairs") and pair_qty:
        sentences.append(f"Setiap orang mendapat {package_qty} bungkus yang berisi {pair_qty} pasang.")
    else:
        sentences.append(f"Setiap orang mendapat {package_qty} bungkus.")
    return mention_prefix + " ".join(sentences)


def _complete_polishing_priority_frame(frame: Mapping[str, Any]) -> bool:
    flags = frame.get("flags") or {}
    required = (
        "arrival", "large_size", "bar_material", "polishing", "priority",
        "production", "prohibition", "small_size", "hoist_or_load", "slow_run",
    )
    return bool(
        frame.get("active")
        and frame.get("machine_ids")
        and all(flags.get(key) for key in required)
    )


def _complete_shift_handover_reporting_frame(frame: Mapping[str, Any]) -> bool:
    flags = frame.get("flags") or {}
    return bool(
        frame.get("active")
        and all(flags.get(key) for key in (
            "shift_handover",
            "any_production_problem",
            "report_to_shift_leader",
            "report_deadline",
            "abnormal_followup_purpose",
        ))
        and frame.get("counts", {}).get("report_deadline") is not None
        and frame.get("units", {}).get("report_deadline") in ("hour", "minute")
    )


def _shift_handover_reporting_rebuild(frame: Mapping[str, Any]) -> str:
    if not _complete_shift_handover_reporting_frame(frame):
        return ""
    counts = frame.get("counts") or {}
    units = frame.get("units") or {}
    raw = frame.get("quantity_tokens") or {}
    quantity = _format_indonesian_quantity(
        counts.get("report_deadline"), raw.get("report_deadline", "")
    )
    target_unit = "jam" if units.get("report_deadline") == "hour" else "menit"
    if not quantity:
        return ""
    mentions = [str(x).strip() for x in frame.get("mentions", []) or [] if str(x).strip()]
    prefix = ((" ".join(mentions)) + " ") if mentions else ""
    return (
        prefix
        + "Jika menemukan masalah produksi apa pun saat pergantian shift, masalah tersebut harus "
        + f"dilaporkan kepada kepala regu dalam waktu {quantity} {target_unit} agar kepala regu "
        + "dapat menindaklanjuti kondisi abnormal dengan lebih baik."
    )


def deterministic_rebuild(frame: Mapping[str, Any]) -> str:
    """Build a safe Indonesian fallback from source-proven semantic slots.

    Rebuilds are compositional and restricted to frames whose decisive slots are
    complete.  No exact source sentence is stored.  Supported frames currently
    include shift-handover reporting, PPE/package allocation,
    package-quantity correction, and the complete polishing/large-bar scheduling
    instruction.
    """
    handover_rebuilt = _shift_handover_reporting_rebuild(frame)
    if handover_rebuilt:
        ok, _issues = validate_translation(frame, handover_rebuilt)
        if ok:
            return handover_rebuilt
    package_rebuilt = _package_rebuild(frame)
    if package_rebuilt:
        ok, _issues = validate_translation(frame, package_rebuilt)
        if ok:
            return package_rebuilt
    if not _complete_polishing_priority_frame(frame):
        return ""
    flags = frame.get("flags") or {}
    machine_ids = [str(x).strip() for x in frame.get("machine_ids", []) or [] if str(x).strip()]
    if not machine_ids:
        return ""
    machines = machine_ids[0] if len(machine_ids) == 1 else ", ".join(machine_ids[:-1]) + " dan " + machine_ids[-1]

    arrival = "akan tiba"
    if flags.get("bulk"):
        arrival += " dalam jumlah besar"
    if flags.get("concentrated"):
        arrival += " dalam waktu yang berdekatan"
    first = "Batang berukuran besar untuk mesin polishing " + arrival + "."
    if flags.get("deadline_month_end"):
        first = "Sebelum akhir bulan, " + first[0].lower() + first[1:]

    priority_object = "produksi batang berukuran besar"
    if flags.get("current_month_scope"):
        priority_object += " yang dijadwalkan untuk bulan ini"
    second = f"{machines} harus mendahulukan {priority_object}."

    if flags.get("explicit_crane"):
        load_action = "mengangkat batang berukuran kecil dengan crane dan memasukkannya ke mesin"
    else:
        load_action = "mengangkat dan memasukkan batang berukuran kecil ke mesin"
    if flags.get("explicit_speed"):
        slow_action = "menjalankan mesin pada kecepatan rendah"
    else:
        slow_action = "menjalankan produksinya secara lambat"
    third = f"Jangan {load_action} lalu {slow_action}."
    rebuilt = " ".join((first, second, third))
    ok, _issues = validate_translation(frame, rebuilt)
    return rebuilt if ok else ""


def validate_translation(frame: Mapping[str, Any], translation: str) -> Tuple[bool, List[str]]:
    """Check whether the target preserves the source-derived operational frame."""
    if not frame or not frame.get("active"):
        return True, []
    low = _norm(translation)
    flags = frame.get("flags") or {}
    issues: List[str] = []
    if not low:
        return False, ["factory_semantic_audit:empty_translation"]

    if flags.get("shift_handover") and not _has_any_target(low, (
        "saat pergantian shift",
        "ketika pergantian shift",
        "saat serah terima shift",
        "ketika serah terima shift",
        "pada saat pergantian shift",
    )):
        issues.append("factory_semantic_audit:shift_handover_context_missing")

    if flags.get("any_production_problem") and not _has_any_target(low, (
        "masalah produksi apa pun",
        "setiap masalah produksi",
        "semua masalah produksi",
        "segala masalah produksi",
    )):
        issues.append("factory_semantic_audit:any_production_problem_scope_missing")

    if flags.get("report_to_shift_leader"):
        report_relation_ok = _same_clause_has(low, (
            ("laporkan", "melaporkan", "dilaporkan", "beri tahu", "memberi tahu", "disampaikan"),
            ("kepala regu", "ketua shift"),
        ))
        if not report_relation_ok:
            issues.append("factory_semantic_audit:report_to_shift_leader_relation_missing")
        if re.search(r"\bbereaksi\b", low, flags=re.I):
            issues.append("factory_semantic_audit:report_mistranslated_as_bereaksi")

    if flags.get("report_deadline"):
        deadline_value = (frame.get("counts") or {}).get("report_deadline")
        deadline_unit = (frame.get("units") or {}).get("report_deadline")
        target_units = ("jam",) if deadline_unit == "hour" else ("menit",)
        deadline_relation_ok = False
        for clause in [part.strip() for part in re.split(r"[.!?;\n]+", low) if part.strip()]:
            if not _has_any_target(clause, (
                "laporkan", "melaporkan", "dilaporkan", "beri tahu", "memberi tahu", "disampaikan",
            )):
                continue
            if not _has_any_target(clause, ("kepala regu", "ketua shift")):
                continue
            if not _quantity_unit_in_clause(clause, deadline_value, target_units):
                continue
            if not _has_any_target(clause, (
                "dalam waktu", "paling lambat", "maksimal", "tidak lebih dari", "selambat-lambatnya",
            )):
                continue
            deadline_relation_ok = True
            break
        if not deadline_relation_ok:
            issues.append("factory_semantic_audit:reporting_deadline_relation_missing")

    if flags.get("abnormal_followup_purpose"):
        followup_ok = _same_clause_has(low, (
            ("kepala regu", "ketua shift"),
            (
                "menindaklanjuti", "ditindaklanjuti", "tindak lanjut", "menangani", "ditangani",
                "penanganan", "merespons", "direspons", "memberikan respons",
            ),
            (
                "kondisi abnormal", "kondisi tidak normal", "kondisi yang tidak normal",
                "keadaan abnormal", "keadaan tidak normal", "ketidaknormalan", "masalah abnormal",
            ),
            ("dengan lebih baik", "secara lebih baik", "lebih mudah", "lebih efektif"),
        ))
        if not followup_ok:
            issues.append("factory_semantic_audit:abnormal_followup_purpose_missing")
        if not flags.get("explicit_followup_speed"):
            unsupported_speed = _same_clause_has(low, (
                ("kepala regu", "ketua shift"),
                (
                    "menindaklanjuti", "ditindaklanjuti", "tindak lanjut", "menangani", "ditangani",
                    "penanganan", "merespons", "direspons",
                ),
                ("lebih cepat", "lebih segera", "secepatnya", "dengan cepat", "segera"),
            ))
            if unsupported_speed:
                issues.append("factory_semantic_audit:unsupported_followup_speed_inference")

    if flags.get("no_more_search") and flags.get("peeling_location"):
        no_search_ok = bool(re.search(
            r"(?:tidak|tak)\s+(?:usah|perlu)\s+(?:di)?cari(?:kan)?\s+lagi|"
            r"(?:jangan)\s+(?:di)?cari\s+lagi",
            low,
            flags=re.I,
        ))
        location_ok = _same_clause_has(low, (
            ("ada", "berada", "terletak"),
            ("stasiun peeling", "bagian peeling", "area peeling", "tempat peeling"),
        ))
        if not no_search_ok:
            issues.append("factory_semantic_audit:missing_stop_searching")
        if not location_ok:
            issues.append("factory_semantic_audit:missing_peeling_location_relation")
        if re.search(r"\bsedang\s+(?:di)?(?:kupas|mengupas)\b", low, flags=re.I):
            issues.append("factory_semantic_audit:peeling_location_mistranslated_as_action")

    if flags.get("tag_front_rear_error") and not _same_clause_has(low, (
        ("tag",),
        ("depan",),
        ("belakang",),
        ("tertukar", "terbalik", "salah ditempel", "salah tempel"),
    )):
        issues.append("factory_semantic_audit:front_rear_tag_relation_missing")

    if flags.get("pending_system_record"):
        pending_ok = (
            _same_clause_has(low, (
                ("data", "datanya", "informasi"),
                ("belum masuk", "belum tersedia", "belum tercatat di sistem", "belum ada di sistem"),
            ))
            or _has_any_target(low, ("data sistem belum masuk", "data di sistem belum masuk"))
        )
        if not pending_ok:
            issues.append("factory_semantic_audit:pending_system_record_missing")
        if re.search(r"\b(?:belum\s+ada|tidak\s+ada)\s+catatan\b", low, flags=re.I):
            issues.append("factory_semantic_audit:pending_record_mistranslated_as_note")

    if flags.get("clear_marking") and not _same_clause_has(low, (
        ("ditandai", "beri tanda", "diberi tanda", "buat tanda", "penandaan"),
        ("jelas", "dengan jelas"),
    )):
        issues.append("factory_semantic_audit:clear_marking_missing")

    if flags.get("bundle_by_bundle") and not _has_any_target(low, (
        "satu bundel demi satu bundel",
        "setiap bundel satu per satu",
        "bundel satu per satu",
        "satu ikat demi satu ikat",
        "setiap ikat satu per satu",
    )):
        issues.append("factory_semantic_audit:bundle_by_bundle_missing")

    if flags.get("self_uncertainty") or flags.get("double_check"):
        if re.search(r"\b(?:mengecek|memeriksa)\s+diri\s+sendiri\b", low, flags=re.I):
            issues.append("factory_semantic_audit:self_check_mistranslated_as_checking_person")
        double_check_ok = (
            _same_clause_has(low, (
                ("cek", "mengecek", "periksa", "memeriksa", "pastikan", "memastikan"),
                ("sekali lagi", "lagi", "kembali"),
            ))
            or _same_clause_has(low, (
                ("tidak langsung yakin", "tidak begitu yakin", "kurang yakin"),
                ("hasil", "tempelan", "tag"),
            ))
        )
        if not double_check_ok:
            issues.append("factory_semantic_audit:self_result_double_check_missing")

    counts = frame.get("counts") or {}
    if flags.get("physical_package_unit"):
        if not flags.get("material_bundle_unit") and re.search(r"\bbundel\b", low, flags=re.I):
            issues.append("factory_semantic_audit:physical_package_mistranslated_as_bundle")
        if not _has_any_target(low, ("bungkus",)):
            issues.append("factory_semantic_audit:physical_package_unit_missing")

    if flags.get("pickup_action"):
        if not _has_any_target(low, ("sarung tangan",)):
            issues.append("factory_semantic_audit:gloves_item_missing")
        if not _has_any_target(low, ("ambil", "mengambil", "diambil")):
            issues.append("factory_semantic_audit:gloves_pickup_action_missing")
    if flags.get("before_offwork") and flags.get("pickup_action") and not _has_any_target(low, (
        "sebelum pulang kerja", "sebelum selesai kerja", "sebelum meninggalkan tempat kerja",
    )):
        issues.append("factory_semantic_audit:before_offwork_deadline_missing")

    if flags.get("per_person_package"):
        per_person_terms = (
            "setiap orang", "masing-masing orang", "satu orang satu", "jatah tiap orang", "jatah setiap orang",
        )
        allocation_ok = any(
            _has_any_target(clause, per_person_terms)
            and _quantity_unit_in_clause(clause, counts.get("package_allocation"), ("bungkus",))
            for clause in [part.strip() for part in re.split(r"[.!?;\n]+", low) if part.strip()]
        )
        if not allocation_ok:
            issues.append("factory_semantic_audit:per_person_package_allocation_missing")

    if flags.get("package_plus_pairs"):
        additive_ok = _same_clause_has_quantity_relation(
            low,
            counts.get("package_allocation"), ("bungkus",),
            ("ditambah", "tambah", "tambahan", "plus"),
            counts.get("pair_allocation"), ("pasang",),
        )
        if not additive_ok:
            issues.append("factory_semantic_audit:package_plus_pairs_relation_missing")
        if _same_clause_has(low, (("bungkus",), ("berisi", "isinya", "memuat"))):
            issues.append("factory_semantic_audit:additional_pairs_mistranslated_as_contents")

    if flags.get("package_contains_pairs"):
        contents_ok = _same_clause_has_quantity_relation(
            low,
            counts.get("package_allocation"), ("bungkus",),
            ("berisi", "isinya", "memuat", "isi"),
            counts.get("pair_allocation"), ("pasang",),
        )
        if not contents_ok:
            issues.append("factory_semantic_audit:package_contents_pairs_relation_missing")

    if flags.get("package_quantity_correction"):
        clauses = [part.strip() for part in re.split(r"[.!?;\n]+", low) if part.strip()]
        correction_ok = False
        for clause in clauses:
            from_spans = _quantity_unit_spans(clause, counts.get("package_correction_from"), ("bungkus",))
            to_spans = _quantity_unit_spans(clause, counts.get("package_correction_to"), ("bungkus",))
            negative_spans = [
                (m.start(), m.end()) for m in re.finditer(r"\b(?:bukan|tidak)\b", clause, flags=re.I)
            ]
            contrast_spans: List[Tuple[int, int]] = []
            for term in ("melainkan", "tetapi", "namun", "seharusnya", "yang benar"):
                contrast_spans.extend(
                    (m.start(), m.end()) for m in re.finditer(re.escape(term), clause, flags=re.I)
                )
            if any(
                neg_end <= from_start and from_end <= contrast_start and contrast_end <= to_start
                for _neg_start, neg_end in negative_spans
                for from_start, from_end in from_spans
                for contrast_start, contrast_end in contrast_spans
                for to_start, _to_end in to_spans
            ):
                correction_ok = True
                break
        if not correction_ok:
            issues.append("factory_semantic_audit:package_quantity_correction_relation_missing")

    if flags.get("abnormal_weight") and not _same_clause_has_counted_concept(
        low,
        counts.get("abnormal_weight"),
        (
            ("barang", "produk", "material", "batang"),
            ("berat tidak normal", "berat abnormal", "beratnya tidak normal", "berat bermasalah"),
        ),
    ):
        issues.append("factory_semantic_audit:abnormal_weight_count_relation_missing")

    if flags.get("wrong_tag_attachment") and not _same_clause_has_counted_concept(
        low,
        counts.get("wrong_tag_attachment"),
        (
            ("barang", "produk", "material", "batang"),
            ("tag",),
            ("salah ditempel", "salah tempel", "tertukar", "terbalik"),
        ),
    ):
        issues.append("factory_semantic_audit:wrong_tag_count_relation_missing")

    if flags.get("own_shift") and not _same_clause_has(low, (
        ("shift kita", "shift kami", "regu kita", "regu kami"),
        ("dari", "berasal", "semuanya", "seluruhnya"),
    )):
        issues.append("factory_semantic_audit:own_shift_accountability_missing")

    if flags.get("recent_many_cases") and not _same_clause_has(low, (
        ("akhir-akhir ini", "belakangan ini", "akhir ini", "baru-baru ini", "dalam beberapa waktu terakhir"),
        ("terlalu banyak", "banyak sekali", "sudah banyak"),
    )):
        issues.append("factory_semantic_audit:recent_repeated_cases_missing")

    if flags.get("customer_complaint") and not _has_any_target(low, (
        "keluhan pelanggan", "komplain pelanggan", "pengaduan pelanggan",
    )):
        issues.append("factory_semantic_audit:customer_complaint_missing")
    if flags.get("cannot_help") and not _has_any_target(low, (
        "saya juga tidak bisa membantu", "saya juga tidak dapat membantu",
        "saya tidak bisa membantu", "saya tidak dapat membantu",
    )):
        issues.append("factory_semantic_audit:speaker_cannot_help_missing")
    if flags.get("customer_complaint") and flags.get("cannot_help"):
        complaint_to_help = bool(re.search(
            r"(?:keluhan|komplain|pengaduan)\s+pelanggan.{0,180}"
            r"saya(?:\s+juga)?\s+tidak\s+(?:bisa|dapat)\s+membantu",
            low,
            flags=re.I | re.S,
        ))
        if not complaint_to_help:
            issues.append("factory_semantic_audit:complaint_cannot_help_relation_missing")

    if flags.get("sanction") and not _has_any_target(low, (
        "kena sanksi", "dikenai sanksi", "mendapat sanksi", "terkena sanksi",
        "mendapat hukuman", "dikenai hukuman",
    )):
        issues.append("factory_semantic_audit:sanction_missing")
    if flags.get("severe_consequence") and not _has_any_target(low, (
        "akibatnya akan sangat berat", "dampaknya akan sangat berat",
        "akibatnya sangat berat", "dampaknya sangat berat",
        "akan sangat merugikan", "konsekuensinya sangat berat",
    )):
        issues.append("factory_semantic_audit:severe_consequence_missing")
    if flags.get("sanction") and flags.get("severe_consequence") and not _same_clause_has(low, (
        ("sanksi", "hukuman"),
        ("sangat berat", "sangat merugikan"),
    )):
        issues.append("factory_semantic_audit:sanction_severity_relation_missing")

    for machine_id in frame.get("machine_ids", []) or []:
        if _norm(machine_id) not in low:
            issues.append("factory_semantic_audit:missing_machine_id:" + str(machine_id))

    if flags.get("deadline_month_end") and not _has_any_target(low, (
        "sebelum akhir bulan", "sebelum bulan ini berakhir", "sebelum penghujung bulan", "hingga sebelum akhir bulan",
    )):
        issues.append("factory_semantic_audit:missing_month_end_deadline")

    if flags.get("bar_material") and not _has_any_target(low, ("material batang", "batang", "bahan batang")):
        issues.append("factory_semantic_audit:missing_bar_material")
    if flags.get("large_size") and not _has_any_target(low, (
        "berukuran besar", "ukuran besar", "berdimensi besar", "diameter besar",
    )):
        issues.append("factory_semantic_audit:missing_large_size")
    if flags.get("small_size") and not _has_any_target(low, (
        "berukuran kecil", "ukuran kecil", "berdimensi kecil", "diameter kecil",
    )):
        issues.append("factory_semantic_audit:missing_small_size")
    if flags.get("polishing") and not _has_any_target(low, (
        "mesin polishing", "proses polishing", "mesin pemoles", "proses pemolesan", "pemolesan",
    )):
        issues.append("factory_semantic_audit:missing_polishing_context")
    if flags.get("arrival") and not _has_any_target(low, ("akan tiba", "akan datang", "akan masuk", "kedatangan", "diterima")):
        issues.append("factory_semantic_audit:missing_material_arrival")
    if flags.get("bulk") and not _has_any_target(low, (
        "dalam jumlah besar", "dalam jumlah banyak", "volume besar", "secara besar-besaran",
    )):
        issues.append("factory_semantic_audit:missing_bulk_arrival")
    if flags.get("concentrated") and not _has_any_target(low, (
        "dalam waktu yang berdekatan", "secara bersamaan", "secara terkonsentrasi", "dalam periode yang sama", "serentak",
    )):
        issues.append("factory_semantic_audit:missing_concentrated_arrival")

    arrival_terms = ("akan tiba", "akan datang", "akan masuk", "kedatangan", "diterima")
    if flags.get("arrival") and flags.get("bulk") and not _same_clause_has(low, (
        arrival_terms,
        ("dalam jumlah besar", "dalam jumlah banyak", "volume besar", "secara besar-besaran"),
    )):
        issues.append("factory_semantic_audit:bulk_not_attached_to_arrival")
    if flags.get("arrival") and flags.get("concentrated") and not _same_clause_has(low, (
        arrival_terms,
        ("dalam waktu yang berdekatan", "secara bersamaan", "secara terkonsentrasi", "dalam periode yang sama", "serentak"),
    )):
        issues.append("factory_semantic_audit:concentration_not_attached_to_arrival")

    if flags.get("priority") and not _has_any_target(low, (
        "memprioritaskan", "diprioritaskan", "prioritas", "didahulukan", "mendahulukan", "terlebih dahulu",
    )):
        issues.append("factory_semantic_audit:missing_priority")
    if flags.get("production") and not _has_any_target(low, (
        "produksi", "memproduksi", "dikerjakan", "diproses", "menjalankan",
    )):
        issues.append("factory_semantic_audit:missing_production_action")
    if flags.get("prohibition") and not _has_any_target(low, ("tidak boleh", "jangan", "dilarang", "tidak diperbolehkan")):
        issues.append("factory_semantic_audit:missing_prohibition")
    if flags.get("hoist_or_load") and not _has_any_target(low, (
        "mengangkat", "memasukkan", "memuat", "menaikkan", "menempatkan",
    )):
        issues.append("factory_semantic_audit:missing_hoist_or_load_action")
    if flags.get("slow_run") and not _has_any_target(low, ("secara lambat", "secara perlahan", "berjalan lambat", "produksi lambat")):
        issues.append("factory_semantic_audit:missing_slow_production_concept")

    if frame.get("machine_ids") and flags.get("priority"):
        priority_terms = ("memprioritaskan", "diprioritaskan", "prioritas", "didahulukan", "mendahulukan", "terlebih dahulu")
        if not any(_same_clause_has(low, ((str(machine_id),), priority_terms)) for machine_id in frame.get("machine_ids", []) or []):
            issues.append("factory_semantic_audit:machine_priority_relation_missing")

    if flags.get("prohibition") and flags.get("small_size") and flags.get("hoist_or_load") and flags.get("slow_run"):
        if not _same_clause_has(low, (
            ("tidak boleh", "jangan", "dilarang", "tidak diperbolehkan"),
            ("berukuran kecil", "ukuran kecil", "berdimensi kecil", "diameter kecil"),
            ("mengangkat", "memasukkan", "memuat", "menaikkan", "menempatkan"),
            ("secara lambat", "secara perlahan", "berjalan lambat", "produksi lambat"),
        )):
            issues.append("factory_semantic_audit:prohibited_small_size_slow_run_relation_missing")

    # The current-month phrase must scope a material/order/product, not an
    # abstract phrase such as "produksi ukuran besar dari bulan ini".
    if flags.get("current_month_scope") and flags.get("large_size"):
        scope_patterns = (
            r"(?:material|bahan|batang|pesanan|order|produk).{0,70}(?:berukuran besar|ukuran besar|berdimensi besar|diameter besar).{0,70}bulan ini",
            r"bulan ini.{0,70}(?:material|bahan|batang|pesanan|order|produk).{0,70}(?:berukuran besar|ukuran besar|berdimensi besar|diameter besar)",
            r"(?:material|bahan|batang|pesanan|order|produk).{0,70}bulan ini.{0,70}(?:berukuran besar|ukuran besar|berdimensi besar|diameter besar)",
        )
        if not any(re.search(pattern, low, flags=re.I | re.S) for pattern in scope_patterns):
            issues.append("factory_semantic_audit:current_month_large_material_scope_ambiguous")
        if re.search(r"produksi\s+(?:ber)?ukuran\s+besar\s+dari\s+bulan\s+ini", low, flags=re.I):
            issues.append("factory_semantic_audit:known_bad_current_month_scope")

    if not flags.get("explicit_crane") and re.search(r"\b(?:crane|overhead\s+crane|derek)\b", low, flags=re.I):
        issues.append("factory_semantic_audit:unsupported_crane_inference")
    if not flags.get("explicit_speed") and re.search(
        r"\b(?:rpm|putaran\s+rendah|kecepatan\s+(?:mesin\s+)?rendah|menurunkan\s+kecepatan)\b",
        low, flags=re.I,
    ):
        issues.append("factory_semantic_audit:unsupported_machine_speed_inference")

    # When source says "吊...慢慢跑", the adverb must describe production/run,
    # not the lifting motion itself.
    if flags.get("hoist_or_load") and flags.get("slow_run"):
        if re.search(r"(?:mengangkat|memuat|menaikkan).{0,30}(?:secara perlahan|secara lambat)(?!.*(?:produksi|proses|mesin|menjalankan))", low, re.I | re.S):
            issues.append("factory_semantic_audit:slowly_lifting_wrong_attachment")

    return not issues, list(dict.fromkeys(issues))


def translate_source_directly(source: str, src_lang: str, tgt_lang: str) -> str:
    """Translate a complete handover-reporting source before any provider call.

    This is a source-to-target translation route, not a candidate-rejection
    path. It renders only when the source supplies every decisive slot:
    handover context, universal production-problem scope, reporting recipient,
    deadline and abnormal-follow-up purpose. Partial or unrelated text remains
    on the normal translation pipeline.
    """
    frame = build_source_frame(source, src_lang, tgt_lang)
    if not _complete_shift_handover_reporting_frame(frame):
        return ""
    translated = _shift_handover_reporting_rebuild(frame)
    if not translated:
        return ""
    ok, _issues = validate_translation(frame, translated)
    return translated if ok else ""


def structured_review_schema() -> Dict[str, Any]:
    """Strict provider-neutral JSON schema for source-grounded adjudication."""
    claim_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "claim_id": {"type": "string"},
            "source_span": {"type": "string"},
            "meaning_zh": {"type": "string"},
            "required_target_meaning_id": {"type": "string"},
        },
        "required": ["claim_id", "source_span", "meaning_zh", "required_target_meaning_id"],
    }
    ambiguity_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "source_term": {"type": "string"},
            "resolved_meaning_zh": {"type": "string"},
            "rejected_interpretations": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["source_term", "resolved_meaning_zh", "rejected_interpretations"],
    }
    coverage_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "claim_id": {"type": "string"},
            "preserved": {"type": "boolean"},
            "target_evidence": {"type": "string"},
        },
        "required": ["claim_id", "preserved", "target_evidence"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "source_claims": {"type": "array", "items": claim_item},
            "ambiguity_resolutions": {"type": "array", "items": ambiguity_item},
            "corrected_translation": {"type": "string"},
            "claim_coverage": {"type": "array", "items": coverage_item},
            "unsupported_additions": {"type": "array", "items": {"type": "string"}},
            "verdict": {"type": "string", "enum": ["pass", "corrected", "needs_human_review"]},
        },
        "required": [
            "source_claims", "ambiguity_resolutions", "corrected_translation",
            "claim_coverage", "unsupported_additions", "verdict",
        ],
    }


def build_structured_review_messages(
    source: str,
    candidate: str,
    src_lang: str,
    tgt_lang: str,
    issues: Sequence[str],
    glossary_pairs: Sequence[Tuple[str, str]],
    review_context: str,
    frame: Mapping[str, Any],
) -> List[Dict[str, str]]:
    terminology = "\n".join(f"- {s} => {t}" for s, t in glossary_pairs[:80]) or "(none)"
    issue_text = "\n".join(f"- {item}" for item in issues[:40]) or "- independent source audit"
    frame_text = build_prompt(frame)
    system = (
        "You are the final source-grounded adjudicator for Traditional Chinese to Indonesian factory translations. "
        "Do not polish the candidate blindly. First reconstruct every operational claim from the Chinese source, "
        "including actor/machine, action, object/material, time, scope, priority, negation/prohibition, sequence, "
        "movement and production-throughput meaning. Then produce one natural Indonesian translation. "
        "Never invent equipment, causes, quantities, crane type, RPM or speed settings that the source does not state. "
        "Treat the source as authoritative; retrieved examples are contrastive evidence only. Return the required JSON object only."
    )
    user = (
        f"SOURCE LANGUAGE: {src_lang}\nTARGET LANGUAGE: {tgt_lang}\n\n"
        f"SOURCE:\n{source}\n\nCURRENT CANDIDATE:\n{candidate}\n\n"
        f"LOCAL ISSUES/WARNINGS:\n{issue_text}\n\n"
        f"LOCKED TERMINOLOGY:\n{terminology}\n\n"
        f"DETERMINISTIC SOURCE FRAME:\n{frame_text or '(none)'}\n\n"
        f"RETRIEVED VERIFIED CONTEXT:\n{str(review_context or '')[:24000] or '(none)'}\n\n"
        "For each source claim, record evidence and whether the corrected translation preserves it. "
        "List every target-side addition unsupported by the source. If any material ambiguity cannot be resolved, "
        "set verdict to needs_human_review; otherwise return pass or corrected."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_structured_payload(raw: str) -> Dict[str, Any]:
    text = str(raw or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.I | re.S)
    if fenced:
        text = fenced.group(1).strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("structured review payload must be an object")
    return value


def validate_structured_payload(payload: Mapping[str, Any], frame: Mapping[str, Any]) -> Tuple[bool, List[str]]:
    issues: List[str] = []
    verdict = str(payload.get("verdict") or "")
    if verdict not in ("pass", "corrected"):
        issues.append("factory_semantic_audit:review_needs_human")
    translation = str(payload.get("corrected_translation") or "").strip()
    if not translation:
        issues.append("factory_semantic_audit:review_empty_translation")
    unsupported = [str(x).strip() for x in (payload.get("unsupported_additions") or []) if str(x).strip()]
    if unsupported:
        issues.append("factory_semantic_audit:review_unsupported_additions:" + " | ".join(unsupported[:4]))
    coverage = payload.get("claim_coverage") or []
    expected_ids = {str(c.get("claim_id")) for c in frame.get("claims", []) or [] if c.get("claim_id")}
    source_claims = payload.get("source_claims") or []
    declared_ids = {
        str(item.get("claim_id")) for item in source_claims
        if isinstance(item, Mapping) and str(item.get("claim_id") or "").strip()
    }
    for claim_id in sorted(expected_ids - declared_ids):
        issues.append("factory_semantic_audit:review_missing_source_claim:" + claim_id)
    for claim_id in sorted(declared_ids - expected_ids):
        issues.append("factory_semantic_audit:review_unknown_source_claim:" + claim_id)
    preserved_ids = {
        str(item.get("claim_id")) for item in coverage
        if isinstance(item, Mapping) and bool(item.get("preserved"))
    }
    for claim_id in sorted(expected_ids - preserved_ids):
        issues.append("factory_semantic_audit:review_uncovered_claim:" + claim_id)
    for item in coverage:
        if isinstance(item, Mapping) and not bool(item.get("preserved")):
            issues.append("factory_semantic_audit:review_claim_not_preserved:" + str(item.get("claim_id") or "unknown"))
        if isinstance(item, Mapping) and bool(item.get("preserved")):
            evidence = _norm(item.get("target_evidence"))
            if not evidence or evidence not in _norm(translation):
                issues.append("factory_semantic_audit:review_target_evidence_not_found:" + str(item.get("claim_id") or "unknown"))

    expected_ambiguities = {
        _norm(item.get("source_term")) for item in frame.get("ambiguities", []) or []
        if isinstance(item, Mapping) and _norm(item.get("source_term"))
    }
    resolved_ambiguities = {
        _norm(item.get("source_term")) for item in payload.get("ambiguity_resolutions", []) or []
        if isinstance(item, Mapping) and _norm(item.get("source_term"))
    }
    for term in sorted(expected_ambiguities - resolved_ambiguities):
        issues.append("factory_semantic_audit:review_missing_ambiguity_resolution:" + term)
    local_ok, local_issues = validate_translation(frame, translation)
    if not local_ok:
        issues.extend(local_issues)
    return not issues, list(dict.fromkeys(issues))
