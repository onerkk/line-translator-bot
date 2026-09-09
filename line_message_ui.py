"""Content-first Flex cards; builders are pure and perform no cloud reads."""
import re
from line_command_catalog import txt


def short(value, units):
    raw = str(value or "").encode("utf-16-le")
    return str(value or "") if len(raw) <= units * 2 else raw[:(units - 1) * 2].decode("utf-16-le", errors="ignore") + "…"


def notice_footer(token, buttons):
    primary, secondary = [], []
    for label, action in buttons:
        button = {"type": "button", "height": "sm", "style": "primary" if action == "factory_ack" else "link",
                  "color": "#087F78" if action != "factory_stop" else "#9B4854",
                  "action": {"type": "postback", "label": short(label, 20),
                             "data": "action=" + action + "&token=" + token}}
        (primary if action in {"factory_ack", "factory_help"} else secondary).append(button)
    contents = primary[:]
    if secondary:
        contents.append({"type": "box", "layout": "horizontal", "spacing": "sm", "contents": secondary})
    contents.append(txt("7 天內可回覆；了解不代表作業完成。\nBerlaku 7 hari; paham ≠ pekerjaan selesai.", "xxs", "#657888"))
    return {"type": "box", "layout": "vertical", "spacing": "md", "paddingAll": "16px",
            "backgroundColor": "#F1F7F8", "contents": contents}


def notice_card(token, text, record, footer):
    original = str(record.get("original") or text or "")
    translated = str(record.get("translated") or "")
    source_label = "通知內容 / Isi pesan"
    # Display the entire supported 1500-unit notice, without a one-line clamp.
    body = [txt(source_label, "xs", "#087F78", weight="bold"),
            txt(short(original, 1800), "xl", "#173448", weight="bold", margin="sm")]
    if translated:
        cells = [txt("譯文 / Terjemahan", "xs", "#087F78", weight="bold")]
        # Multi-target translations arrive as [id] ... [vi] ...; keep each label.
        for part in re.split(r"\n(?=\[[a-z]{2}\]\s)", short(translated, 3400)):
            part = re.sub(r"^\[id\]\s*", "🇮🇩 ", part)
            part = re.sub(r"^\[zh\]\s*", "🇹🇼 ", part)
            # A single Flex text node is limited to 2000 characters.
            raw = part.encode("utf-16-le")
            while raw:
                chunk = raw[:3600].decode("utf-16-le", errors="ignore")
                raw = raw[len(chunk.encode("utf-16-le")):]
                cells.append(txt(chunk, "lg", "#173448", margin="sm"))
        body.append({"type": "box", "layout": "vertical", "paddingAll": "14px", "cornerRadius": "12px",
                     "backgroundColor": "#ECF7F4", "margin": "lg", "contents": cells})
        if len(translated.encode("utf-16-le")) > 6800:
            body.append(txt("此處為譯文摘要，完整內容見原通知。\nLihat pesan awal untuk teks lengkap.", "xs", "#657888"))
    # A receipt update keeps feedback below the actual question, never above it.
    if "\n通知 / Pesan #" in text:
        feedback = text.split("\n", 1)[0]
        body.append(txt(feedback, "sm", "#087F78", weight="bold", margin="lg"))
        marker = "\n\n✅"
        if marker in text:
            summary = "✅" + text.split(marker, 1)[1]
            body.append(txt(summary, "xs", "#526577", margin="sm"))
    elif text.startswith(("🛑", "✅")) and text != original:
        body.append(txt(short(text, 600), "sm", "#526577", margin="lg"))
    sender = str(record.get("sender_name") or "")
    header = [txt("作業確認 / KONFIRMASI", "xs", "#90E0D1", weight="bold"),
              txt("#" + token[:6] + ("  ·  " + short(sender, 80) if sender else ""), "xs", "#D3E1E9", margin="xs")]
    return {"type": "bubble", "size": "mega",
            "header": {"type": "box", "layout": "vertical", "paddingAll": "16px", "backgroundColor": "#102F42", "contents": header},
            "body": {"type": "box", "layout": "vertical", "paddingAll": "20px", "backgroundColor": "#FFFFFF", "contents": body},
            "footer": footer}
