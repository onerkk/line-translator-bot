"""Content-first Flex cards; builders are pure and perform no cloud reads."""
import re
from datetime import datetime, timedelta, timezone
from line_command_catalog import txt


def stopped_message(record, stopped_at):
    """A small, bilingual tracking receipt; no AI, image or profile request."""
    token = short(str(record.get("token") or "")[:6], 6)
    stopped = datetime.fromtimestamp(stopped_at, timezone(timedelta(hours=8)))
    return {
        "type": "flex",
        "altText": "已停止作業追蹤｜Pemantauan dihentikan #" + token,
        "contents": {
            "type": "bubble", "size": "kilo",
            "body": {
                "type": "box", "layout": "vertical", "paddingAll": "18px",
                "backgroundColor": "#102F42", "contents": [
                    txt("作業確認 / KONFIRMASI", "10px", "#DBBE85", weight="bold"),
                    {"type": "box", "layout": "horizontal", "alignItems": "center",
                     "spacing": "md", "margin": "md", "contents": [
                         {"type": "box", "layout": "vertical", "width": "32px", "height": "32px",
                          "cornerRadius": "10px", "backgroundColor": "#294352",
                          "justifyContent": "center", "alignItems": "center", "contents": [
                              {"type": "box", "layout": "vertical", "width": "10px", "height": "10px",
                               "cornerRadius": "2px", "backgroundColor": "#DBBE85", "contents": []}]},
                         {"type": "box", "layout": "vertical", "flex": 1, "contents": [
                             txt("已停止作業追蹤", "md", "#FFFFFF", weight="bold"),
                             txt("Pemantauan dihentikan", "10px", "#C1D2DC", margin="xs")]}]},
                    {"type": "separator", "color": "#355264", "margin": "lg"},
                    txt("本次追蹤提前結束，不再提醒。", "xs", "#F0F4F6", margin="md"),
                    txt("Pemantauan konfirmasi diakhiri lebih awal. Tidak ada pengingat lanjutan.",
                        "11px", "#C1D2DC", margin="sm"),
                    txt("確認紀錄保留 · Catatan tetap tersimpan", "10px", "#DBBE85", margin="md"),
                    txt("#" + token + "  ·  " + stopped.strftime("%m/%d %H:%M") + "  UTC+8",
                        "10px", "#A6BDCA", margin="sm"),
                ],
            },
        },
    }


def completion_message(record, recipient_count, completed_at):
    """A compact, image-free completion receipt; input is a committed snapshot.

    This confirms receipt of a notice, never execution of the actual work.
    All dynamic text is bounded in UTF-16 units before it reaches LINE.
    """
    token = str(record.get("token") or "")
    completed = datetime.fromtimestamp(completed_at, timezone(timedelta(hours=8)))
    original = short(" ".join(str(record.get("original") or "").split()), 96)
    translated = short(" ".join(str(record.get("translated") or "").split()), 150)
    sender = short(record.get("sender_name") or "—", 48)
    count = str(recipient_count)
    summary = [txt("通知摘要 / Ringkasan", "xs", "#087F78", weight="bold")]
    if original:
        summary.append(txt(original, "sm", "#173448", weight="bold", margin="sm"))
    if translated:
        summary.append(txt(translated, "xs", "#526577", margin="sm"))
    summary.append(txt("發起人 / Pengirim · " + sender, "xs", "#657888", margin="md"))
    scope = "依本通知目標名單核對。\nBerdasarkan daftar penerima pemberitahuan ini."
    if record.get("recipient_scope") != "mentioned" and record.get("roster_basis") == "known_chat_members":
        scope = "依本通知已列入的目標名單核對，未涵蓋尚未辨識的群組成員。\nHanya penerima yang tercantum; anggota yang belum dikenali tidak termasuk."
    bubble = {
        "type": "bubble", "size": "mega",
        "header": {"type": "box", "layout": "vertical", "paddingAll": "18px",
                   "backgroundColor": "#102F42", "contents": [
                       txt("作業確認 / KONFIRMASI", "xs", "#90E0D1", weight="bold"),
                       txt("通知 / Pemberitahuan #" + short(token[:6], 6), "xs", "#D3E1E9", margin="xs")]},
        "body": {"type": "box", "layout": "vertical", "paddingAll": "20px", "contents": [
            {"type": "box", "layout": "horizontal", "alignItems": "center", "spacing": "md", "contents": [
                {"type": "box", "layout": "vertical", "width": "40px", "height": "40px",
                 "cornerRadius": "20px", "backgroundColor": "#E3F4EC", "justifyContent": "center",
                 "contents": [txt("✓", "xl", "#087F78", align="center", weight="bold")]},
                {"type": "box", "layout": "vertical", "flex": 1, "contents": [
                    txt("全員確認完畢", "xl", "#173448", weight="bold"),
                    txt("KONFIRMASI LENGKAP", "xxs", "#087F78", weight="bold", margin="xs")]}]},
            txt("目標人員已全員確認完畢。", "sm", "#173448", margin="lg"),
            txt("Seluruh penerima dalam daftar telah mengonfirmasi.", "xs", "#526577", margin="xs"),
            {"type": "box", "layout": "vertical", "paddingAll": "16px", "margin": "lg",
             "cornerRadius": "12px", "backgroundColor": "#ECF7F4", "contents": [
                 {"type": "box", "layout": "horizontal", "alignItems": "center", "contents": [
                     {"type": "box", "layout": "vertical", "flex": 1, "contents": [
                         txt(count + " / " + count, "xxl", "#087F78", weight="bold"),
                         txt("已確認 / Terkonfirmasi", "xxs", "#526577", margin="xs")]},
                     txt("100%", "xl", "#087F78", weight="bold", flex=0)]},
                 {"type": "box", "layout": "vertical", "height": "4px", "margin": "md",
                  "cornerRadius": "2px", "backgroundColor": "#087F78", "contents": []}]},
            {"type": "box", "layout": "vertical", "margin": "lg", "contents": summary},
            {"type": "separator", "margin": "lg", "color": "#E3EBEE"},
            txt("確認完成時間 / Waktu konfirmasi", "xxs", "#657888", margin="md"),
            txt(completed.strftime("%Y/%m/%d  %H:%M") + " · UTC+8", "xs", "#173448", margin="xs"),
        ]},
        "footer": {"type": "box", "layout": "vertical", "paddingAll": "16px",
                   "backgroundColor": "#F1F7F8", "contents": [
                       txt("感謝配合 / Terima kasih atas kerja samanya", "xs", "#087F78", weight="bold"),
                       txt(scope, "xxs", "#657888", margin="sm")]},
        "styles": {"body": {"backgroundColor": "#FFFFFF"}},
    }
    return {"type": "flex", "altText": "✅ 目標人員已全員確認完畢（" + count + "/" + count +
            "）｜Semua penerima telah mengonfirmasi #" + short(token[:6], 6), "contents": bubble}


def short(value, units):
    raw = str(value or "").encode("utf-16-le")
    return str(value or "") if len(raw) <= units * 2 else raw[:(units - 1) * 2].decode("utf-16-le", errors="ignore") + "…"


def notice_footer(token, buttons):
    primary, secondary = [], []
    for label, action in buttons:
        if action == "factory_help":
            continue
        button = {"type": "button", "height": "sm", "style": "primary" if action == "factory_ack" else "link",
                  "color": "#087F78" if action != "factory_stop" else "#9B4854",
                  "action": {"type": "postback", "label": short(label, 20),
                             "data": "action=" + action + "&token=" + token}}
        (primary if action in {"factory_ack", "factory_help"} else secondary).append(button)
    contents = primary[:]
    if secondary:
        contents.append({"type": "box", "layout": "horizontal", "spacing": "sm", "contents": secondary})
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
    if record.get("recipient_scope") == "mentioned":
        count = len(set(record.get("recipient_ids", [])) - {record.get("sender_id")})
        header.append(txt("追蹤：指定 " + str(count) + " 人 / " + str(count) + " anggota terpilih",
                          "xs", "#D3E1E9", margin="xs"))
    elif record.get("recipient_scope") == "all":
        header.append(txt("追蹤：全群 / Seluruh grup", "xs", "#D3E1E9", margin="xs"))
    return {"type": "bubble", "size": "mega",
            "header": {"type": "box", "layout": "vertical", "paddingAll": "16px", "backgroundColor": "#102F42", "contents": header},
            "body": {"type": "box", "layout": "vertical", "paddingAll": "20px", "backgroundColor": "#FFFFFF", "contents": body},
            "footer": footer}
