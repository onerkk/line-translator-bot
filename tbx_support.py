"""
tbx_support.py — TermBase eXchange (TBX) v1.0 (2026-05-20)

TBX 是 ISO 30042 國際標準的術語庫交換格式,業界所有 TMS 平台都支援:
- SDL Trados, memoQ, Wordfast
- Lokalise, Smartcat, Phrase
- TermStar, Across

【為什麼要支援 TBX】
- 工廠 glossary(232 條)可匯出給專業翻譯公司審校
- 可從業界其他工具匯入既有術語庫
- 跨平台、跨工具相容

【格式】
TBX 是 XML-based,結構:
<martif type="TBX-Basic">
  <text>
    <body>
      <termEntry id="t001">
        <descrip type="subjectField">factory</descrip>
        <langSet xml:lang="zh">
          <tig><term>砂輪</term></tig>
        </langSet>
        <langSet xml:lang="id">
          <tig><term>batu gerinda</term></tig>
        </langSet>
      </termEntry>
    </body>
  </text>
</martif>

【參考】
- ISO 30042: https://www.iso.org/standard/62510.html
- TBX 規範: https://www.tbxinfo.net/
- LISA TBX-Basic 規範
"""

import os
import logging
import time
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from typing import Dict, Any, List, Callable, Optional

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# 匯出
# ═══════════════════════════════════════════════════════════════════
def export_glossary_to_tbx(glossary_dict: Dict[str, Any], filepath: str,
                           subject_field: str = "stainless_steel_factory") -> int:
    """把 glossary 匯出為 TBX-Basic 格式
    
    Args:
        glossary_dict: { "中文術語": {"idn": "印尼譯", "note_zh": "...", "note_id": "..."} }
                       或 { "中文術語": "印尼譯" }
        filepath: 輸出檔案路徑
        subject_field: 主題領域標籤
    
    Returns: 匯出的 termEntry 數
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<!DOCTYPE martif SYSTEM "TBXBasiccoreStructV02.dtd">',
        '<martif type="TBX-Basic" xml:lang="en">',
        '  <martifHeader>',
        '    <fileDesc>',
        '      <titleStmt>',
        '        <title>LINE Translation Bot - Factory Glossary</title>',
        '      </titleStmt>',
        '      <sourceDesc>',
        '        <p>Exported from LINE Translation Bot v3.9.38</p>',
        '      </sourceDesc>',
        '    </fileDesc>',
        f'    <encodingDesc><p type="DCSName">TBXBasicXCSV02.xcs</p></encodingDesc>',
        '  </martifHeader>',
        '  <text>',
        '    <body>',
    ]
    
    count = 0
    for i, (zh_term, info) in enumerate(glossary_dict.items(), 1):
        if not zh_term:
            continue
        
        # 支援兩種 glossary 結構
        if isinstance(info, dict):
            idn = info.get("idn", "")
            note_zh = info.get("note_zh", "")
            note_id = info.get("note_id", "")
        else:
            idn = str(info)
            note_zh = note_id = ""
        
        if not idn:
            continue
        
        lines.append(f'      <termEntry id="t{i:04d}">')
        lines.append(f'        <descrip type="subjectField">{escape(subject_field)}</descrip>')
        if note_zh:
            lines.append(f'        <descrip type="definition" xml:lang="zh">{escape(note_zh)}</descrip>')
        if note_id:
            lines.append(f'        <descrip type="definition" xml:lang="id">{escape(note_id)}</descrip>')
        
        # 中文 langSet
        lines.append('        <langSet xml:lang="zh">')
        lines.append('          <tig>')
        lines.append(f'            <term>{escape(zh_term)}</term>')
        lines.append('            <termNote type="termType">fullForm</termNote>')
        lines.append('          </tig>')
        lines.append('        </langSet>')
        
        # 印尼文 langSet
        lines.append('        <langSet xml:lang="id">')
        lines.append('          <tig>')
        lines.append(f'            <term>{escape(idn)}</term>')
        lines.append('            <termNote type="termType">fullForm</termNote>')
        lines.append('          </tig>')
        lines.append('        </langSet>')
        
        lines.append('      </termEntry>')
        count += 1
    
    lines.append('    </body>')
    lines.append('  </text>')
    lines.append('</martif>')
    
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        logger.info("[TBX] exported %d entries to %s", count, filepath)
        return count
    except Exception as e:
        logger.error("[TBX] export failed: %s", e)
        return 0


# ═══════════════════════════════════════════════════════════════════
# 匯入
# ═══════════════════════════════════════════════════════════════════
def import_tbx_to_glossary(filepath: str,
                           merge_callback: Optional[Callable] = None) -> int:
    """從 TBX 檔匯入術語
    
    Args:
        filepath: TBX 檔案路徑
        merge_callback: 可選的 callback(zh_term, idn, note_zh, note_id),
                        若無則 return parsed list
    
    Returns:
        - 若有 merge_callback:成功匯入數量
        - 若無:list[dict] of {"zh": "", "id": "", "note_zh": "", "note_id": ""}
    """
    if not os.path.exists(filepath):
        logger.error("[TBX] file not found: %s", filepath)
        return 0
    
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except Exception as e:
        logger.error("[TBX] parse failed: %s", e)
        return 0
    
    ns_strip = lambda tag: tag.split("}")[-1] if "}" in tag else tag
    
    # 找 body
    body = None
    for elem in root.iter():
        if ns_strip(elem.tag) == "body":
            body = elem
            break
    
    if body is None:
        logger.error("[TBX] no body found")
        return 0
    
    results = []
    count = 0
    
    for term_entry in body:
        if ns_strip(term_entry.tag) != "termEntry":
            continue
        
        entry = {"zh": "", "id": "", "note_zh": "", "note_id": ""}
        
        for child in term_entry:
            tag = ns_strip(child.tag)
            if tag == "descrip":
                d_type = child.get("type", "")
                d_lang = (child.get("{http://www.w3.org/XML/1998/namespace}lang")
                          or child.get("lang") or "")
                if d_type == "definition":
                    if d_lang == "zh":
                        entry["note_zh"] = (child.text or "").strip()
                    elif d_lang == "id":
                        entry["note_id"] = (child.text or "").strip()
            elif tag == "langSet":
                lang = (child.get("{http://www.w3.org/XML/1998/namespace}lang")
                        or child.get("lang") or "")
                # 找 tig → term
                for sub in child:
                    if ns_strip(sub.tag) == "tig":
                        for t in sub:
                            if ns_strip(t.tag) == "term":
                                if lang.startswith("zh"):
                                    entry["zh"] = (t.text or "").strip()
                                elif lang.startswith("id"):
                                    entry["id"] = (t.text or "").strip()
                                break
                        break
        
        if entry["zh"] and entry["id"]:
            if merge_callback:
                try:
                    merge_callback(entry["zh"], entry["id"], entry["note_zh"], entry["note_id"])
                    count += 1
                except Exception as e:
                    logger.warning("[TBX] merge callback failed for %s: %s", entry["zh"], e)
            else:
                results.append(entry)
                count += 1
    
    logger.info("[TBX] parsed %d entries from %s", count, filepath)
    if merge_callback:
        return count
    return results


# ═══════════════════════════════════════════════════════════════════
# 驗證
# ═══════════════════════════════════════════════════════════════════
def validate_tbx(filepath: str) -> Dict[str, Any]:
    """驗證 TBX 檔結構是否正確
    
    Returns: {"valid": bool, "errors": [...], "entry_count": int, "lang_pairs": [...]}
    """
    result = {"valid": False, "errors": [], "entry_count": 0, "lang_pairs": set()}
    if not os.path.exists(filepath):
        result["errors"].append("file not found")
        return result
    
    try:
        tree = ET.parse(filepath)
        root = tree.getroot()
    except Exception as e:
        result["errors"].append(f"XML parse error: {e}")
        return result
    
    ns_strip = lambda tag: tag.split("}")[-1] if "}" in tag else tag
    
    if ns_strip(root.tag) != "martif":
        result["errors"].append(f"root tag is {ns_strip(root.tag)}, expected 'martif'")
    
    body = None
    for elem in root.iter():
        if ns_strip(elem.tag) == "body":
            body = elem
            break
    
    if body is None:
        result["errors"].append("no <body> element found")
        return result
    
    for term_entry in body:
        if ns_strip(term_entry.tag) != "termEntry":
            continue
        result["entry_count"] += 1
        langs_in_entry = []
        for child in term_entry:
            if ns_strip(child.tag) == "langSet":
                lang = (child.get("{http://www.w3.org/XML/1998/namespace}lang")
                        or child.get("lang") or "")
                if lang:
                    langs_in_entry.append(lang)
        if len(langs_in_entry) >= 2:
            result["lang_pairs"].add(tuple(sorted(langs_in_entry[:2])))
    
    result["lang_pairs"] = [list(p) for p in result["lang_pairs"]]
    result["valid"] = len(result["errors"]) == 0 and result["entry_count"] > 0
    return result
