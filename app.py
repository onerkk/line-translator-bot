import os
import re
import json
import urllib.request
import urllib.parse
import logging
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError
from openai import OpenAI

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LINE_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")

configuration = Configuration(access_token=LINE_TOKEN)
handler = WebhookHandler(LINE_SECRET)
oai = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None

group_settings = {}

LANG_FLAGS = {
    "zh": "\U0001f1f9\U0001f1fc",
    "id": "\U0001f1ee\U0001f1e9",
    "en": "\U0001f1ec\U0001f1e7",
    "vi": "\U0001f1fb\U0001f1f3",
    "th": "\U0001f1f9\U0001f1ed",
    "ja": "\U0001f1ef\U0001f1f5",
    "ko": "\U0001f1f0\U0001f1f7",
    "ms": "\U0001f1f2\U0001f1fe",
    "tl": "\U0001f1f5\U0001f1ed",
}

LANG_NAMES = {
    "zh": "Traditional Chinese",
    "id": "Indonesian",
    "en": "English",
    "vi": "Vietnamese",
    "th": "Thai",
    "ja": "Japanese",
    "ko": "Korean",
    "ms": "Malay",
    "tl": "Filipino/Tagalog",
}


def extract_mentions(text):
    # Only match @followed by latin letters/numbers/spaces, stop at Chinese or newline
    mentions = re.findall(r'@[a-zA-Z0-9][a-zA-Z0-9 ]*', text)
    # Trim trailing spaces
    mentions = [m.rstrip() for m in mentions]
    return mentions


def protect_mentions(text):
    mentions = extract_mentions(text)
    protected = text
    placeholders = {}
    for i, m in enumerate(mentions):
        ph = "MHOLD" + str(i) + "ER"
        placeholders[ph] = m
        protected = protected.replace(m, ph, 1)
    return protected, placeholders


def restore_mentions(text, placeholders):
    restored = text
    for ph, original in placeholders.items():
        restored = restored.replace(ph, original)
    return restored


def strip_mentions_for_detect(text):
    # Remove @mentions (latin only) for language detection
    clean = re.sub(r'@[a-zA-Z0-9][a-zA-Z0-9 ]*', ' ', text)
    return clean


def has_chinese(text):
    return len(re.findall(r'[\u4e00-\u9fff]', text)) >= 2


def has_japanese(text):
    hira = len(re.findall(r'[\u3040-\u309f]', text))
    kata = len(re.findall(r'[\u30a0-\u30ff]', text))
    return (hira + kata) >= 2


def has_korean(text):
    return len(re.findall(r'[\uac00-\ud7af]', text)) >= 2


def has_thai(text):
    return len(re.findall(r'[\u0e00-\u0e7f]', text)) >= 2


def has_vietnamese(text):
    vi_special = re.findall(r'[\u01a0\u01a1\u01af\u01b0\u0110\u0111]', text)
    vi_chars = re.findall(r'[\u00e0-\u00ff\u1ea0-\u1ef9]', text.lower())
    vi_marks = re.findall(r'[\u0300-\u036f]', text)
    words = text.lower().split()
    vi_markers = set([
        'cua', 'nhung', 'trong', 'duoc', 'khong', 'nhu', 'mot',
        'toi', 'ban', 'anh', 'chi', 'em', 'ong', 'ba',
        'la', 'va', 'cac', 'cho', 'voi', 'tai', 'nay', 'khi',
        'con', 'roi', 'lam', 'biet', 'muon', 'den', 'di',
        'xin', 'cam', 'chao', 'dep', 'ngon', 'tot', 'xau',
    ])
    marker_count = sum(1 for w in words if w in vi_markers)
    if len(vi_special) >= 1:
        return True
    if len(vi_chars) >= 3 and marker_count >= 1:
        return True
    if len(vi_marks) >= 2 and marker_count >= 1:
        return True
    return False


def has_indonesian(text):
    if has_chinese(text) or has_thai(text) or has_korean(text) or has_japanese(text):
        return False
    words = re.findall(r'[a-zA-Z]+', text.lower())
    if len(words) < 2:
        return False
    id_words = set([
        'yang', 'dan', 'ini', 'itu', 'ada', 'untuk', 'dengan', 'dari',
        'tidak', 'akan', 'sudah', 'bisa', 'juga', 'saya', 'kami', 'kita',
        'mereka', 'dia', 'apa', 'bagaimana', 'kenapa', 'kapan', 'dimana',
        'siapa', 'belum', 'sedang', 'harus', 'boleh', 'mau', 'ingin',
        'bukan', 'jangan', 'tolong', 'terima', 'kasih', 'selamat',
        'pagi', 'siang', 'sore', 'malam', 'baik', 'bagus', 'benar',
        'salah', 'besar', 'kecil', 'makan', 'minum', 'tidur', 'kerja',
        'pulang', 'pergi', 'rumah', 'kantor', 'uang', 'harga', 'berapa',
        'banyak', 'sedikit', 'semua', 'karena', 'tetapi', 'tapi', 'atau',
        'jika', 'kalau', 'sampai', 'masih', 'lagi', 'saja', 'dulu',
        'nanti', 'sekarang', 'hari', 'minggu', 'bulan', 'tahun',
        'gak', 'nggak', 'udah', 'gimana', 'dong', 'sih', 'nih',
        'kok', 'yuk', 'ayo', 'banget', 'orang', 'baru', 'lembur',
        'cuti', 'gaji', 'minta', 'ambil', 'kirim', 'tunggu', 'cepat',
        'lambat', 'susah', 'gampang', 'senang', 'sedih', 'marah',
        'takut', 'capek', 'lapar', 'haus', 'sakit', 'sehat',
        'di', 'ke', 'jam', 'hari', 'bisa', 'pergi', 'ruang',
        'baca', 'soal', 'ujian', 'terakhir', 'kamu',
    ])
    count = sum(1 for w in words if w in id_words)
    if count >= 2:
        return True
    if len(words) >= 3 and count >= 1 and count / len(words) > 0.2:
        return True
    return False


def has_english(text):
    if has_chinese(text) or has_thai(text) or has_korean(text) or has_japanese(text):
        return False
    if has_vietnamese(text) or has_indonesian(text):
        return False
    words = re.findall(r'[a-zA-Z]+', text.lower())
    if len(words) < 3:
        return False
    en_words = set([
        'the', 'is', 'are', 'was', 'were', 'have', 'has', 'had',
        'will', 'would', 'could', 'should', 'can', 'may', 'might',
        'this', 'that', 'these', 'those', 'what', 'which', 'who',
        'where', 'when', 'how', 'why', 'not', 'but', 'and', 'or',
        'for', 'with', 'from', 'about', 'into', 'your', 'you',
        'we', 'they', 'she', 'him', 'her', 'its', 'our', 'their',
        'just', 'also', 'very', 'much', 'more', 'most', 'some',
        'any', 'all', 'each', 'every', 'been', 'being', 'does',
        'did', 'doing', 'going', 'want', 'need', 'know', 'think',
        'come', 'make', 'like', 'time', 'good', 'new', 'first',
        'please', 'thank', 'thanks', 'sorry', 'hello', 'okay',
        'yes', 'yeah', 'already', 'still', 'here', 'there',
    ])
    count = sum(1 for w in words if w in en_words)
    if count >= 2:
        return True
    if len(words) > 0 and count / len(words) > 0.25:
        return True
    return False


def detect_language(text):
    # Remove @mentions before detecting language
    clean = strip_mentions_for_detect(text).strip()
    if not clean or len(clean) < 2:
        return None
    # Check for mixed: if has both Chinese and Indonesian words, decide by ratio
    zh_count = len(re.findall(r'[\u4e00-\u9fff]', clean))
    latin_words = re.findall(r'[a-zA-Z]+', clean.lower())
    # If mostly Chinese characters with some latin names, treat as Chinese
    if zh_count >= 2 and len(latin_words) <= 2:
        return "zh"
    if has_japanese(clean):
        return "ja"
    if has_korean(clean):
        return "ko"
    if has_thai(clean):
        return "th"
    # If has Chinese and also has Indonesian/other latin, check which is dominant
    if zh_count >= 2:
        # Has Chinese characters - check if the latin part is just names or actual language
        id_words = set([
            'yang', 'dan', 'ini', 'itu', 'ada', 'untuk', 'dengan', 'dari',
            'tidak', 'akan', 'sudah', 'bisa', 'juga', 'saya', 'kami', 'kita',
            'belum', 'harus', 'boleh', 'mau', 'karena', 'tapi', 'atau',
            'kalau', 'masih', 'lagi', 'nanti', 'sekarang',
            'gak', 'nggak', 'udah', 'gimana', 'dong', 'sih',
            'di', 'ke', 'jam', 'hari', 'bisa', 'pergi', 'ruang',
            'baca', 'soal', 'ujian', 'terakhir', 'kamu',
            'makan', 'minum', 'kerja', 'pulang', 'rumah',
        ])
        id_count = sum(1 for w in latin_words if w in id_words)
        if id_count >= 3:
            return "id"
        return "zh"
    if has_vietnamese(clean):
        return "vi"
    if has_indonesian(clean):
        return "id"
    if has_english(clean):
        return "en"
    return None


def translate_openai(text, src, tgt):
    if not oai:
        return None
    try:
        src_name = LANG_NAMES.get(src, src)
        tgt_name = LANG_NAMES.get(tgt, tgt)
        protected, placeholders = protect_mentions(text)
        msg = "Translate from " + src_name + " to " + tgt_name + ": " + protected
        sys_prompt = (
            "You are a professional translator for a factory work group chat. "
            "This is a group with Taiwanese managers and Indonesian workers. "
            "CRITICAL RULES: "
            "1. NEVER translate person names. Keep all names exactly as they are. "
            "For example: Chinese nicknames for people must stay unchanged. If you see characters that are clearly a person's name or nickname, do NOT translate them literally. "
            "2. Any text like MHOLD0ER, MHOLD1ER etc are placeholders - keep them exactly as is. "
            "3. Translate naturally like real people talk at work. Use casual daily language. "
            "4. Indonesian slang: gak=tidak, udah=sudah, gimana=bagaimana, bgt=banget, org=orang, yg=yang, tdk=tidak, dg=dengan, krn=karena, blm=belum, hrs=harus, bs=bisa, lg=lagi, gw=saya, lu=kamu. "
            "5. Chinese slang and abbreviations too. "
            "6. Target Traditional Chinese = Taiwan style, not mainland. "
            "7. Target Indonesian = simple clear daily language for factory workers. "
            "8. Context: factory work - shifts, overtime, orders, tasks, meals, breaks, meetings, exams. "
            "Only output the translation. No quotes, no explanation, no prefix."
        )
        r = oai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": msg}
            ],
            temperature=0.3,
            max_tokens=2000,
        )
        result = r.choices[0].message.content.strip()
        result = restore_mentions(result, placeholders)
        return result
    except Exception as e:
        logger.error("OpenAI error: %s", e)
        return None


def translate_google(text, src, tgt):
    try:
        protected, placeholders = protect_mentions(text)
        lang_map = {
            "zh": "zh-TW", "id": "id", "en": "en",
            "vi": "vi", "th": "th", "ja": "ja",
            "ko": "ko", "ms": "ms", "tl": "tl",
        }
        sl = lang_map.get(src, src)
        tl = lang_map.get(tgt, tgt)
        q = urllib.parse.quote(protected)
        url = "https://translate.googleapis.com/translate_a/single?client=gtx&sl=" + sl + "&tl=" + tl + "&dt=t&q=" + q
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            parts = []
            for item in data[0]:
                if item[0]:
                    parts.append(item[0])
            result = "".join(parts)
            result = restore_mentions(result, placeholders)
            return result
    except Exception as e:
        logger.error("Google translate error: %s", e)
        return None


def translate(text, src, tgt):
    result = translate_openai(text, src, tgt)
    if result:
        return result
    return translate_google(text, src, tgt)


def make_notice(content):
    id_text = translate(content, "zh", "id")
    if not id_text:
        id_text = "(translation failed)"
    lines = []
    lines.append("\U0001f4e2 \u516c\u544a / Pengumuman")
    lines.append("====================")
    lines.append("\U0001f1f9\U0001f1fc " + content)
    lines.append("\U0001f1ee\U0001f1e9 " + id_text)
    lines.append("====================")
    return "\n".join(lines)


def make_notice_from_id(content):
    zh_text = translate(content, "id", "zh")
    if not zh_text:
        zh_text = "(translation failed)"
    lines = []
    lines.append("\U0001f4e2 \u516c\u544a / Pengumuman")
    lines.append("====================")
    lines.append("\U0001f1f9\U0001f1fc " + zh_text)
    lines.append("\U0001f1ee\U0001f1e9 " + content)
    lines.append("====================")
    return "\n".join(lines)


def get_help_text():
    lines = []
    lines.append("\U0001f310 \u7ffb\u8b6f\u6a5f\u5668\u4eba / Bot Penerjemah")
    lines.append("====================")
    lines.append("/on  - \u958b\u555f\u7ffb\u8b6f / Aktifkan")
    lines.append("/off - \u95dc\u9589\u7ffb\u8b6f / Nonaktifkan")
    lines.append("/status - \u67e5\u770b\u72c0\u614b / Cek status")
    lines.append("/notice \u5167\u5bb9 - \u96d9\u8a9e\u516c\u544a / Pengumuman")
    lines.append("/help - \u8aaa\u660e / Bantuan")
    lines.append("====================")
    lines.append("\u652f\u63f4\u8a9e\u8a00 / Bahasa:")
    lines.append("\U0001f1f9\U0001f1fc \u4e2d\u6587 / Chinese")
    lines.append("\U0001f1ee\U0001f1e9 \u5370\u5c3c\u6587 / Indonesia")
    lines.append("\U0001f1ec\U0001f1e7 \u82f1\u6587 / English")
    lines.append("\U0001f1fb\U0001f1f3 \u8d8a\u5357\u6587 / Vietnam")
    lines.append("\U0001f1f9\U0001f1ed \u6cf0\u6587 / Thai")
    lines.append("\U0001f1ef\U0001f1f5 \u65e5\u6587 / Jepang")
    lines.append("\U0001f1f0\U0001f1f7 \u97d3\u6587 / Korea")
    lines.append("\U0001f1f2\U0001f1fe \u99ac\u4f86\u6587 / Melayu")
    lines.append("\U0001f1f5\U0001f1ed \u83f2\u5f8b\u8cd3\u6587 / Filipina")
    lines.append("====================")
    lines.append("\u81ea\u52d5\u5075\u6e2c\uff0c\u81ea\u52d5\u7ffb\u8b6f\uff01")
    lines.append("Deteksi otomatis & terjemahkan!")
    lines.append("\u4e2d\u6587 \u2192 \u5370\u5c3c\u6587")
    lines.append("\u5176\u4ed6\u8a9e\u8a00 \u2192 \u4e2d\u6587")
    return "\n".join(lines)


def handle_command(text, group_id):
    cmd = text.strip().lower()
    if cmd == "/help":
        return get_help_text()
    elif cmd == "/on":
        group_settings[group_id] = True
        return "\u2705 \u7ffb\u8b6f\u5df2\u958b\u555f / Penerjemah aktif"
    elif cmd == "/off":
        group_settings[group_id] = False
        return "\u274c \u7ffb\u8b6f\u5df2\u95dc\u9589 / Penerjemah nonaktif"
    elif cmd == "/status":
        is_on = group_settings.get(group_id, True)
        if is_on:
            return "\u2705 \u7ffb\u8b6f\u72c0\u614b\uff1a\u958b\u555f\u4e2d / Status: Aktif"
        else:
            return "\u274c \u7ffb\u8b6f\u72c0\u614b\uff1a\u5df2\u95dc\u9589 / Status: Nonaktif"
    elif text.strip().startswith("/notice ") or text.strip().startswith("/notice\u3000"):
        content = text.strip()[8:].strip()
        if not content:
            return "\u26a0\ufe0f \u8acb\u8f38\u5165\u516c\u544a\u5167\u5bb9\n\u4f8b\u5982 / Contoh: /notice \u660e\u5929\u653e\u5047\u4e00\u5929"
        if has_chinese(content):
            return make_notice(content)
        elif has_indonesian(content):
            return make_notice_from_id(content)
        else:
            return make_notice(content)
    return None


@app.route("/callback", methods=["POST"])
def callback():
    sig = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, sig)
    except InvalidSignatureError:
        abort(400)
    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    text = event.message.text.strip()
    if len(text) < 2:
        return

    source = event.source
    group_id = getattr(source, 'group_id', None) or getattr(source, 'room_id', None) or getattr(source, 'user_id', None)

    if text.startswith("/"):
        cmd_result = handle_command(text, group_id)
        if cmd_result:
            with ApiClient(configuration) as api_client:
                api = MessagingApi(api_client)
                api.reply_message(ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=cmd_result)]
                ))
        return

    is_on = group_settings.get(group_id, True)
    if not is_on:
        return

    if text.startswith("!"):
        return

    lang = detect_language(text)
    if lang is None:
        return

    reply = None
    if lang == "zh":
        result = translate(text, "zh", "id")
        if result:
            reply = LANG_FLAGS.get("id", "") + " " + result
    else:
        result = translate(text, lang, "zh")
        if result:
            reply = LANG_FLAGS.get("zh", "") + " " + result

    if reply is None:
        return

    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        api.reply_message(ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text=reply)]
        ))


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
