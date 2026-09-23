"""Source-grounded semantics for broadcast notices and short shop-floor replies.

LINE's @All is an addressee, not a participant in the next clause.  A factory
notice can be syntactically terse, and treating this mention as a subject makes
the model invent a conjunction and extra people.  Keep the human's text and a
photographed work order as separate sources of truth: a translation of a chat
question must reproduce the question, even when the speaker misread the form.
"""

from __future__ import annotations

import re
from typing import Any


_LEADING_BROADCAST = re.compile(r"^\s*@all(?=$|[\s，,。:：!！?？\u3400-\u9fff])", re.I)
_AUDIT_SOURCE = re.compile(r"外(?:部)?稽(?:核)?(?:人員|人员)?|外部(?:稽核|稽查|審核|审核)(?:人員|人员)?")
_AUDIT_ID = re.compile(r"\b(?:auditor|pemeriksa|inspektur)\s+(?:eksternal|dari\s+luar)\b", re.I)
_OTHER_SOURCE = re.compile(r"其他|其它|另外|其餘|其余|額外|额外")
_OTHER_ID = re.compile(r"(?P<subject>\b(?:auditor|pemeriksa|inspektur)\s+(?:eksternal|dari\s+luar))\s+(?:lainnya|yang\s+lain)\b", re.I)
_PEELING_REPORT = re.compile(r"(?:已|已经|已經|已經有|已向|已經向).{0,4}反[應映].{0,3}削皮(?:股)?(?:主管|股長|股长)")
_URGENT_NOTE = re.compile(r"特殊備註|特殊备注")
_URGENT_ORDER = re.compile(r"急[單单]|(?:緊急|紧急)(?:的)?(?:工[單单]|訂單|订单)")
_RING_QUESTION = re.compile(r"(?:為什麼|为什么|怎麼|怎么).{0,24}(?:看成|看作|誤認|误认|讀成|读成).{0,10}(?:不需|不用|不要|不必).{0,3}套[環环]")
_DATE = re.compile(r"(?<!\d)\d{1,2}\s*[/／.-]\s*\d{1,2}(?!\d)")
_REPORT_TODAY = re.compile(r"報表.{0,5}(?:補到|补到|填到|寫到|写到)(?:今(?:天|日))")
_FUTURE_DATE_NEGATION = re.compile(r"(?:不要|不能|不可|別|别|禁止).{0,5}(?:寫|写|填).{0,6}(?:未來|未来)(?:的)?日期")


def source_facts(source: Any, src_lang: str, tgt_lang: str) -> dict[str, bool]:
    """Extract only claims supported by the current Chinese source text."""
    if not (str(src_lang).lower().startswith("zh") and str(tgt_lang).lower().startswith("id")):
        return {}
    text = str(source or "")
    broadcast = bool(_LEADING_BROADCAST.search(text))
    audit = bool(_AUDIT_SOURCE.search(text))
    return {
        "broadcast": broadcast,
        "external_audit": audit,
        "no_other_auditors": broadcast and audit and not _OTHER_SOURCE.search(text),
        "peeling_supervisor_report": bool(_PEELING_REPORT.search(text)),
        "urgent_special_note": bool(_URGENT_NOTE.search(text) and _URGENT_ORDER.search(text)),
        "urgent_note_ring_question": bool(_URGENT_NOTE.search(text) and _URGENT_ORDER.search(text)
                                          and _RING_QUESTION.search(text)),
        "urgent_note_date": bool(_URGENT_NOTE.search(text) and _URGENT_ORDER.search(text)
                                 and _DATE.search(text)),
        "report_today": bool(_REPORT_TODAY.search(text)),
        "prohibit_future_date": bool(_FUTURE_DATE_NEGATION.search(text)),
    }


def build_prompt(source: Any, src_lang: str, tgt_lang: str) -> str:
    facts = source_facts(source, src_lang, tgt_lang)
    lines = []
    if facts.get("broadcast"):
        lines.append("@All is a LINE broadcast addressee. Copy it literally, then start the message; "
                     "do not make @All a participant coordinated with the next noun by dan/and.")
    if facts.get("external_audit"):
        lines.append("外稽人員 means external auditors/personnel; preserve the future site inspection. "
                     "Do not add lainnya/other inspectors without an explicit source word for others.")
    if facts.get("peeling_supervisor_report"):
        lines.append("已反應／已反映削皮主管 reports that the information was already conveyed "
                     "to the supervisor of Bagian Peeling; 削皮 names the factory unit, not an action on skin.")
    if facts.get("urgent_special_note"):
        lines.append("Keep the source's claim about a special note and an urgent work order; "
                     "if a date is written, copy the date. A nearby photo is a different source and must not "
                     "silently change this chat speaker's words.")
    if facts.get("urgent_note_ring_question"):
        lines.append("The no-protective-ring statement is what the speaker says someone read, inside a WHY question. "
                     "Translate as a questioned interpretation, never assert it as the actual instruction. "
                     "套環 means Cincin Pelindung.")
    if facts.get("report_today") or facts.get("prohibit_future_date"):
        lines.append("報表補到今天 asks to bring the report up to today's date; "
                     "不要寫未來日期 forbids dates after today. Preserve both conditions independently.")
    return "<factory_chat_notice>\n" + "\n".join(lines) + "\n</factory_chat_notice>" if lines else ""


def canonicalize(source: Any, candidate: Any, src_lang: str, tgt_lang: str) -> str:
    """Repair only a source-unsupported coordination of @All with auditors.

    No new sentence, fact, negation, role, or instruction is generated here.
    The generic broadcast addressee is protected separately by immutable-span
    handling; this narrowly removes an invented connector/extra-people suffix.
    """
    result = str(candidate or "")
    facts = source_facts(source, src_lang, tgt_lang)
    if not (facts.get("broadcast") and facts.get("external_audit")):
        return result
    if facts.get("no_other_auditors"):
        result = _OTHER_ID.sub(lambda m: m.group("subject"), result)
    # Only the message-initial mention's immediate auditors are affected. This
    # will not touch a real coordination later in the text or '@All, dan mohon'.
    result = re.sub(
        r"(?P<mention>@all)(?P<sep>\s*(?:[,，:]\s*)?)dan\s+(?=(?:auditor|pemeriksa|inspektur)\s+(?:eksternal|dari\s+luar)\b)",
        lambda m: m.group("mention") + (m.group("sep") if m.group("sep") else " "),
        result,
        count=1,
        flags=re.I,
    )
    return result


def translation_issues(source: Any, candidate: Any, src_lang: str, tgt_lang: str) -> list[str]:
    """Local diagnostics control learning admission; delivery stays available."""
    facts = source_facts(source, src_lang, tgt_lang)
    text = str(candidate or "")
    issues = []
    if facts.get("external_audit") and not _AUDIT_ID.search(text):
        issues.append("factory_chat_notice:external_auditors_missing")
    if facts.get("no_other_auditors") and _OTHER_ID.search(text):
        issues.append("factory_chat_notice:auditors_unsupported_others")
    if facts.get("broadcast") and facts.get("external_audit") and re.search(
        r"@all\s*(?:[,，:]\s*)?dan\s+(?=(?:auditor|pemeriksa|inspektur)\s+(?:eksternal|dari\s+luar)\b)",
        text, re.I,
    ):
        issues.append("factory_chat_notice:broadcast_treated_as_actor")
    if facts.get("peeling_supervisor_report"):
        if not re.search(r"\b(?:sudah|telah)\b", text, re.I):
            issues.append("factory_chat_notice:report_completion_missing")
        if not re.search(r"\b(?:kepala|atasan|supervisor|pengawas)\b.{0,28}\bpeeling\b", text, re.I):
            issues.append("factory_chat_notice:peeling_supervisor_missing")
    if facts.get("urgent_special_note"):
        if not re.search(r"\b(?:catatan|keterangan)\s+khusus(?:nya)?\b", text, re.I):
            issues.append("factory_chat_notice:special_note_missing")
        if not re.search(r"\b(?:pesanan|order|work\s*order)\b.{0,24}\b(?:mendesak|urgent|urgen)\b|"
                         r"\b(?:mendesak|urgent|urgen)\b.{0,24}\b(?:pesanan|order|work\s*order)\b", text, re.I):
            issues.append("factory_chat_notice:urgent_order_missing")
    if facts.get("urgent_note_date"):
        dates = {re.sub(r"\s+", "", m.group()) for m in _DATE.finditer(str(source))}
        actual = {re.sub(r"\s+", "", m.group()) for m in _DATE.finditer(text)}
        if not dates.intersection(actual):
            issues.append("factory_chat_notice:urgent_order_date_missing")
    if facts.get("urgent_note_ring_question"):
        if not re.search(r"\b(?:mengapa|kenapa|bagaimana\s+bisa)\b", text, re.I):
            issues.append("factory_chat_notice:question_modality_missing")
        if not re.search(r"\b(?:membaca|mengartikan|menafsirkan|memahami|menganggap)(?:nya)?\b", text, re.I):
            issues.append("factory_chat_notice:ring_statement_attribution_missing")
    if facts.get("report_today") and not re.search(r"\b(?:hingga|sampai|termasuk)\s+(?:hari\s+ini|tanggal\s+hari\s+ini)\b", text, re.I):
        issues.append("factory_chat_notice:report_today_cutoff_missing")
    if facts.get("prohibit_future_date") and not re.search(
        r"\b(?:jangan|tidak\s+boleh|dilarang)\b.{0,32}\b(?:tanggal|tarikh)\b.{0,24}\b(?:mendatang|depan|akan\s+datang)\b",
        text, re.I,
    ):
        issues.append("factory_chat_notice:future_date_prohibition_missing")
    return issues
