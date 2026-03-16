import os
import re
import json
import urllib.request
import urllib.parse
import logging
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, MessagingApiBlob, ReplyMessageRequest, TextMessage
from linebot.v3.webhooks import MessageEvent, TextMessageContent, ImageMessageContent, AudioMessageContent
from linebot.v3.exceptions import InvalidSignatureError
from openai import OpenAI
import base64
import tempfile
import time

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
# Target language for Chinese translation per group, default "id"
group_target_lang = {}
# Image translation toggle per group, default True
group_img_settings = {}
# Audio/voice translation toggle per group, default True
group_audio_settings = {}
# Skip list: set of user_ids per group whose messages won't be translated
group_skip_users = {}

# DM (private message) target language per user, default "id"
dm_target_lang = {}

# Translation cache: key = (text, src, tgt), value = (result, timestamp)
translation_cache = {}
CACHE_MAX_SIZE = 500
CACHE_TTL = 3600  # 1 hour

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

LANG_NAMES_ZH = {
    "id": "\u5370\u5c3c\u6587",
    "en": "\u82f1\u6587",
    "vi": "\u8d8a\u5357\u6587",
    "th": "\u6cf0\u6587",
    "ja": "\u65e5\u6587",
    "ko": "\u97d3\u6587",
    "ms": "\u99ac\u4f86\u6587",
    "tl": "\u83f2\u5f8b\u8cd3\u6587",
}

# Valid target languages (excluding zh since zh is source)
VALID_TARGETS = ["id", "en", "vi", "th", "ja", "ko", "ms", "tl"]


def extract_mentions(text):
    # Capture @mentions conservatively while still allowing common LINE names with spaces.
    # Stop before obvious separators so we do not swallow the rest of the sentence.
    # Also stop before a space + Chinese character (common: "@name 暱稱 ...").
    pattern = r'@[A-Za-z0-9][A-Za-z0-9 _.-]*(?=(?:\s{2,}|\s[一-鿿]|[\n,，。!！?？:：;；()（）\[\]{}<>"“”]|$))'
    mentions = re.findall(pattern, text)
    mentions = [m.rstrip() for m in mentions if m and len(m) > 1]
    # Remove duplicates while preserving order
    seen = set()
    result = []
    for m in mentions:
        if m not in seen:
            seen.add(m)
            result.append(m)
    return result


def protect_mentions(text):
    mentions = extract_mentions(text)
    protected = text
    placeholders = {}
    for i, m in enumerate(mentions):
        # Use a stronger placeholder that is less likely to be translated or split.
        ph = f"__MENTION_{i}__"
        # Check if a short Chinese nickname (1-4 chars) follows the @mention.
        # e.g. "@budi santoso 山多" → "山多" is a nickname, protect it too.
        escaped = re.escape(m)
        nick_pattern = escaped + r'(\s+[\u4e00-\u9fff]{1,4})(?=\s|[,，。!！?？:：;；\n]|$)'
        nick_match = re.search(nick_pattern, protected)
        if nick_match:
            full = m + nick_match.group(1)
            placeholders[ph] = full
            protected = protected.replace(full, ph, 1)
        else:
            placeholders[ph] = m
            protected = protected.replace(m, ph, 1)
    return protected, placeholders


def restore_mentions(text, placeholders):
    restored = text or ""
    for ph, original in placeholders.items():
        idx = ph.replace("__MENTION_", "").replace("__", "")
        variants = [
            ph,
            ph.replace("_", " "),
            ph.replace("__", ""),
            f"MENTION_{idx}",
            f"MENTION {idx}",
            f"__MENTION {idx}__",
            f"[[MENTION_{idx}]]",
        ]
        for v in variants:
            restored = restored.replace(v, original)

    # Final safety net: if any original @mention disappeared during translation,
    # prepend it back so the tagged person is not lost.
    missing = [original for original in placeholders.values() if original not in restored]
    if missing:
        prefix = " ".join(missing)
        restored = (prefix + " " + restored).strip()
    return restored


def strip_mentions_for_detect(text):
    # Strip @mentions including optional Chinese nickname (1-4 chars) that follows
    clean = re.sub(r'@[A-Za-z0-9][A-Za-z0-9 _.-]*(?:\s+[\u4e00-\u9fff]{1,4})?(?=(?:\s|[\n,，。!！?？:：;；()（）\[\]{}<>"“”]|$))', ' ', text)
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
        'di', 'ke', 'jam', 'ruang', 'baca', 'soal', 'ujian',
        'terakhir', 'kamu',
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
    clean = strip_mentions_for_detect(text).strip()
    if not clean or len(clean) < 2:
        return None
    zh_count = len(re.findall(r'[\u4e00-\u9fff]', clean))
    latin_words = re.findall(r'[a-zA-Z]+', clean.lower())
    if zh_count >= 2 and len(latin_words) <= 2:
        return "zh"
    if has_japanese(clean):
        return "ja"
    if has_korean(clean):
        return "ko"
    if has_thai(clean):
        return "th"
    if zh_count >= 2:
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
            'di', 'ke', 'jam', 'ruang', 'baca', 'soal', 'ujian',
            'terakhir', 'kamu', 'jadi', 'harap', 'ukur', 'secara',
            'manual', 'rusak', 'saat', 'mohon', 'pakai', 'bisa',
        ])
        id_count = sum(1 for w in latin_words if w in id_words)
        if id_count >= 2:
            return "id"
        return "zh"
    if has_vietnamese(clean):
        return "vi"
    if has_indonesian(clean):
        return "id"
    if has_english(clean):
        return "en"
    return None


def contains_source_script_outside_placeholders(text, src):
    cleaned = re.sub(r'__MENTION_\d+__', ' ', text or '')
    patterns = {
        "zh": r'[\u4e00-\u9fff]',
        "ja": r'[\u3040-\u30ff\u4e00-\u9fff]',
        "ko": r'[\uac00-\ud7af]',
        "th": r'[\u0e00-\u0e7f]',
    }
    pattern = patterns.get(src)
    if not pattern:
        return False
    return len(re.findall(pattern, cleaned)) >= 2


def is_translation_valid(result, src, tgt):
    if not result or not result.strip():
        return False
    if src != tgt and contains_source_script_outside_placeholders(result, src):
        return False
    return True


def translate_openai(text, src, tgt, strict_no_source_script=False, repair_mode=False, bad_result=None):
    if not oai:
        return None
    try:
        src_name = LANG_NAMES.get(src, src)
        tgt_name = LANG_NAMES.get(tgt, tgt)
        protected, placeholders = protect_mentions(text)

        extra_rule = ""
        if strict_no_source_script and src != tgt:
            if src == "zh":
                extra_rule = (
                    " 10. IMPORTANT: Do not leave any Chinese words untranslated unless they are a person's name or __MENTION__ placeholder."
                    " Terms such as 印籍, 印尼籍, 早班, 夜班, 考試, 讀書, 下班後 must be translated into the target language."
                )
            elif src == "ja":
                extra_rule = " 10. IMPORTANT: Do not leave Japanese text untranslated unless it is a person's name or __MENTION__ placeholder."
            elif src == "ko":
                extra_rule = " 10. IMPORTANT: Do not leave Korean text untranslated unless it is a person's name or __MENTION__ placeholder."
            elif src == "th":
                extra_rule = " 10. IMPORTANT: Do not leave Thai text untranslated unless it is a person's name or __MENTION__ placeholder."

        sys_prompt = (
            "You are a professional translator for a stainless steel factory (Walsin Lihwa/華新麗華, Yanshui plant) work group chat. "
            "This factory produces stainless steel bars, wire rods, peeled bars, cold-drawn bars using processes like rolling, annealing, pickling, peeling, cold drawing, and centerless grinding. "
            "This is a group with Taiwanese managers and Indonesian migrant workers operating centerless grinding (無心研磨) equipment. "
            "CRITICAL RULES: "
            "1. NEVER translate @mentions and never translate person names. Keep all names exactly as they are. "
            "Chinese nicknames for people must stay unchanged. Do NOT translate them literally. "
            "2. Any text like __MENTION_0__, __MENTION_1__ etc are placeholders - keep them exactly as is. "
            "3. Translate all other content completely and naturally like real people talk at work. Use casual daily language. "
            "4. Indonesian slang: gak=tidak, udah=sudah, gimana=bagaimana, bgt=banget, org=orang, yg=yang, tdk=tidak, dg=dengan, krn=karena, blm=belum, hrs=harus, bs=bisa, lg=lagi, gw=saya, lu=kamu. "
            "5. TAIWANESE MANDARIN COLLOQUIAL GUIDE (very important for accurate translation): "
            "【感嘆詞/Exclamations - translate as emotion, not literally】"
            "乾/干=aduh/ya ampun/astaga (expressing frustration/surprise), "
            "靠=astaga/waduh (frustration), 幹=sial/buset (strong frustration/anger), "
            "哇靠/挖靠=waduh/gila (shock), 天啊=ya Tuhan/astaga, "
            "媽的/馬的=sialan (cursing, strong), 靠北/靠杯=sialan/buset (strong frustration), "
            "傻眼=gak percaya/kaget (disbelief), 扯=gila/keterlaluan (ridiculous), "
            "誇張=keterlaluan/berlebihan (outrageous), 瞎=gila/asal-asalan (absurd/nonsense), "
            "屌=keren/gila (cool/amazing, slang), 猛=hebat/gila (impressive), "
            "慘=parah/gawat (bad situation), 衰=sial (unlucky), "
            "煩=bete/kesel (annoyed), 懶得=males (can't be bothered), "
            "無言=speechless/gak bisa ngomong, 傻了=kaget banget, "
            "哭=sedih banget (not literally crying), 笑死=ngakak/lucu banget, "
            "氣死=kesel banget, 累死=capek banget, 熱死=panas banget, "
            "冷死=dingin banget, 餓死=lapar banget, 急死=buru-buru banget, "
            "煩死=bete banget, 無聊死=bosan banget, "
            "【語助詞/Sentence-final particles - convey tone, not literal meaning】"
            "啦=lah/dong (casual emphasis), 喔/哦=ya/lho (reminder/acknowledgment), "
            "耶=dong/nih (excitement/emphasis), 嘛=dong/kan (persuasion/obviously), "
            "咧/勒=nih/dong (questioning/emphasis), 齁=nih/ya (slightly reproachful), "
            "蛤/哈=hah?/apa? (surprise/didn't hear), 吼=eh/hei (calling attention), "
            "吧=kan/ya (suggestion/uncertainty), 呢=ya?/gimana? (wondering), "
            "噢=oh (understanding), 欸/誒=eh (getting attention), "
            "啊=ah/ya (various tones), 嗯=iya/hmm, "
            "厚=ya kan (seeking agreement, Southern Taiwan), "
            "齁齁=hmm (disapproval), 唉=aduh/haih (sigh), "
            "【台灣口語表達/Taiwanese casual expressions】"
            "醬/降=begitu/gitu (=這樣, like this), 醬子=begitu/kayak gitu, "
            "捏/ㄋㄟ=ya/nih (=呢), 惹=udah/dong (=了, completed), "
            "der/ㄉ=punya/yang (=的), 偶=saya/aku (=我), "
            "素=iya/emang (=是), 泥/你=kamu, "
            "hen=sangat (=很), 粉=sangat (=很), "
            "歐=oh ya, 吃土=bokek/gak punya uang (broke), "
            "踩雷=kena zonk/sial (bad luck/bad choice), "
            "母湯=jangan/gak boleh (=不要/不行), 可撥/可悲=kasihan, "
            "87=bodoh/goblok (slang for stupid), "
            "hen棒=bagus banget, 超=sangat/banget (=非常), "
            "有夠=banget (=非常, Southern emphasis), "
            "【反問句/語氣判斷 - Rhetorical questions & tone interpretation】"
            "CRITICAL: Taiwanese rhetorical questions often SUGGEST doing something, not asking. "
            "需不需要X=sebaiknya X / perlu X gak nih (suggesting X should be done), "
            "要不要X=gimana kalau X / mau X gak (suggesting to do X), "
            "是不是該X=harusnya X ya / sebaiknya X (suggesting X is needed), "
            "可不可以X=bisa X gak (requesting), "
            "有沒有X=ada X gak (checking), "
            "好不好=gimana?/ok? (seeking agreement), "
            "對不對=iya kan?/bener kan?, "
            "是不是=iya kan?/bener gak?, "
            "還在X=masih X / sedang X (still doing X, often implies criticism: 還在睡=masih tidur aja), "
            "X個屁/X個鬼=X apaan (dismissive, e.g. 做個屁=kerja apaan/gak kerja sama sekali), "
            "X什麼X=kenapa X sih (e.g. 急什麼急=kenapa buru-buru sih), "
            "哪有=mana ada/gak lah (denial), "
            "才沒有=gak ada tuh (emphatic denial), "
            "才怪=bohong/gak mungkin (sarcastic disbelief), "
            "【工廠口語/Factory casual talk】"
            "提報=laporkan/report, 囤料/堆料=numpuk material/material menumpuk, "
            "品保=QC/quality assurance, 還在下班=sudah pulang (already left, implying they're gone), "
            "趕不出來=gak bisa selesai tepat waktu, 做不完=gak bisa selesaikan, "
            "搞什麼=ngapain sih (what are they doing, frustration), "
            "搞定=beres/selesai, 用好=sudah siap/sudah beres, "
            "弄好=sudah selesai, 生到=produksi sampai/bikin sampai, "
            "跑哪去了=pergi kemana (where did they go), "
            "人咧/人呢=orangnya mana (where is the person), "
            "在幹嘛=lagi ngapain (what are they doing), "
            "怎麼搞的=kenapa bisa begini (what happened/how did this happen), "
            "出包=ada masalah/kacau (something went wrong), "
            "放鴿子=gak datang/mangkir (stood someone up/no show), "
            "摸魚=malas-malasan/gak kerja (slacking off), "
            "偷懶=males/malas kerja, 混=males/asal kerja, "
            "盯=awasin/pantau (watch/supervise), 催=kejar/tagih (urge/rush), "
            "叫他=suruh dia, 跟他說=bilang ke dia, "
            "先這樣=sudah dulu ya/segitu dulu (that's it for now), "
            "就這樣=begitu aja/segitu aja (that's all), "
            "沒差=gak masalah/sama aja, 隨便=terserah, "
            "看看再說=lihat dulu nanti, 再說=nanti aja/lihat nanti, "
            "等一下/等下=bentar/tunggu sebentar, 馬上=segera, "
            "趕快=cepat/buruan, 快點=cepatan, "
            "【數量/程度口語】"
            "一堆=banyak banget (a lot), 一大堆=banyak banget, "
            "一點點=sedikit/dikit, 一些些=sedikit, "
            "差不多=kurang lebih/hampir sama, 大概=kira-kira, "
            "很多=banyak, 超多=banyak banget, 有夠多=banyak banget, "
            "沒幾個=cuma sedikit/gak banyak, "
            "【常見句型/Common patterns】"
            "X到不行=X banget/sangat X (e.g. 累到不行=capek banget), "
            "X得要死=X banget (e.g. 忙得要死=sibuk banget), "
            "X到爆=X banget (e.g. 熱到爆=panas banget), "
            "怎麼這麼X=kok X banget (e.g. 怎麼這麼慢=kok lambat banget), "
            "有夠X=X banget (Southern TW, e.g. 有夠熱=panas banget), "
            "X個不停=X terus (e.g. 講個不停=ngomong terus), "
            "越來越X=makin X (e.g. 越來越多=makin banyak), "
            "動不動就X=gampang banget X (easily X), "
            "【簡訊縮寫/Text abbreviations】"
            "ㄏㄏ/哈哈=haha, ㄎㄎ=hehe/keke, QQ=sedih, "
            "3Q=terima kasih (thank you), GG=tamat/selesai (game over), "
            "ㄅ=bukan/jangan (=不), ㄇ=kan?/ya? (=嗎), "
            "ㄉ=punya (=的), ㄌ=udah (=了), "
            "ㄍ=satu (=個), ㄏ=ok/baik (=好), "
            "ㄅㄅ=bye bye, 88=bye bye, "
            "OP=over power/terlalu hebat, "
            "CP值=value for money/worth it, "
            "感溫=terima kasih (台語 slang for 感恩/grateful), "
            "XD=haha/emoji ketawa, @@=bingung/confused, "
            "ID現況=status ID saat ini, "
            "28.2的嗎=yang diameter 28.2 ya? (numbers before 的 usually refer to diameter in mm), "
            "6. Target Traditional Chinese = Taiwan style, not mainland. "
            "7. Target Indonesian = simple clear daily language for factory workers. "
            "8. Context: factory work - shifts, overtime, orders, tasks, meals, breaks, meetings, exams. "
            "9. IMPORTANT factory vocabulary (Chinese → Indonesian). "
            "This is a stainless steel factory (Walsin Lihwa/華新麗華) with centerless grinding (無心研磨) operations. "
            "【無心研磨/Centerless Grinding】"
            "無心研磨=centerless grinding, 研磨=grinding, 研磨機=mesin grinding, "
            "砂輪=batu gerinda/grinding wheel, 調整輪=roda pengatur/regulating wheel, "
            "刀板=work rest blade/pisau penahan, 進刀=feeding/pemotongan, "
            "通過式研磨=through-feed grinding, 停止式研磨=in-feed grinding, "
            "磨削=penggerindaan, 進料=feeding material, 出料=output material, "
            "真圓度=kebulatan/roundness, 直線度=kelurusan/straightness, "
            "表面粗糙度=kekasaran permukaan/surface roughness, "
            "冷卻液=cairan pendingin/coolant, 修整砂輪=dressing grinding wheel, "
            "【不鏽鋼製程】"
            "不鏽鋼=baja tahan karat/stainless steel, 棒鋼=batang baja/steel bar, "
            "盤元=wire rod, 削皮棒=peeled bar/batang kupas, 冷精棒=cold-drawn bar, "
            "鋼胚=billet baja, 小鋼胚=small billet, 扁鋼胚=flat billet, "
            "熱軋=hot rolling, 軋製=rolling/pengerolan, "
            "退火=annealing/pelunakan, 酸洗=pickling/pencucian asam, "
            "削皮=peeling/kupas, 冷抽=cold drawing/penarikan dingin, "
            "鋼種=jenis baja/steel grade, PMI=PMI (uji material), "
            "來料=material masuk/incoming material, 棒材=batang baja, "
            "混料=tercampur material, 料號=nomor material, "
            "【班次/出勤】"
            "點名=ada pengawas yang datang (inspection/supervisor visit, NOT roll call), "
            "主管點名/主管來點名=ada pengawas/atasan yang datang untuk inspeksi, "
            "夜間點名=pengawas datang malam untuk inspeksi, "
            "早班=shift pagi, 夜班=shift malam, 中班=shift siang, "
            "加班=lembur, 排班=jadwal shift, 調班=tukar shift, "
            "上班=masuk kerja, 下班=pulang kerja, 打卡=absen, "
            "遲到=terlambat, 早退=pulang lebih awal, 曠工=bolos, "
            "請假=izin, 病假=izin sakit, 事假=izin pribadi, 特休=cuti tahunan, "
            "補假=cuti pengganti, 休假=libur, 輪休=libur bergilir, "
            "值班=jaga/piket, 交接=serah terima, 代班=gantikan shift, "
            "【產線/工作】"
            "產線=lini produksi, 機台=mesin, 工站=stasiun kerja, "
            "開機=nyalakan mesin, 關機=matikan mesin, 停機=mesin berhenti, "
            "換線=ganti lini, 換模=ganti cetakan, 調機=setting mesin, "
            "上料=isi material, 下料=keluarkan material, 備料=siapkan material, "
            "物料=material/bahan, 原料=bahan baku, 半成品=barang setengah jadi, 成品=barang jadi, "
            "良品=barang bagus, 不良品=barang reject/NG, 報廢=buang/scrap, "
            "產量=jumlah produksi, 目標=target, 達標=capai target, 超產=produksi berlebih/over production, "
            "訂單=order/pesanan, 出貨=kirim barang, 交期=deadline pengiriman, "
            "趕貨=kejar order, 急單=order urgent, 急單備註=catatan order urgent, "
            "交辦事項=hal yang harus dikerjakan/tugas, "
            "下製程=proses selanjutnya/next process, 異常=abnormal/ada masalah, "
            "拋不完=gak bisa selesai polishing, 做不完=gak bisa selesaikan, "
            "維修中=sedang diperbaiki, 生產完=selesai produksi, "
            "噴漆=cat semprot/spray paint, 拆包=bagi packing, "
            "照訂單量拆包=bagi packing sesuai jumlah order, "
            "【製程站點/Process Stations - CRITICAL】"
            "NOTE: Numbers like 400, 401, 420, 470, 490, 801 are STATION NUMBERS in the factory system. "
            "400站=station 400, 490站=station 490 (秤重站/stasiun timbang), 401站=station 401. "
            "420站=station 420, 470站=station 470 (UT station), UT=mesin UT (di station 470), 801站=station 801. "
            "OL=sedang produksi/online (e.g. 在420 OL=di station 420 sedang produksi). "
            "退庫UT/退庫拆包給UT=data dipindahkan dari station 801 ke station 470 (UT). "
            "回400=kembalikan ke station 400, 回490=kembalikan ke station 490. "
            "站=stasiun (when after a number), "
            "無主=tanpa pemilik/unassigned (material not assigned to any order), "
            "入無主=masukkan ke status tanpa pemilik, "
            "掛單=work order, 重掛單=pasang ulang work order, "
            "工單=work order, 無工單資訊=tidak ada info work order, "
            "改制=ubah proses produksi, 改制去化=ubah proses produksi (reroute), "
            "去化=when used alone means ada order baru yang mau terima/消化 (absorb inventory with new order), "
            "有單去化=ada order baru untuk serap material ini, "
            "原單=work order awal, "
            "帳/帳務=data administrasi (ERP system), "
            "帳已回400=data sudah dikembalikan ke station 400, "
            "過帳=input data produksi (jumlah batang & berat) ke sistem, tanpa melepas data ke stasiun berikutnya, "
            "等等會過帳=nanti akan input data ke sistem, "
            "放行=release data ke stasiun berikutnya (setelah QC lulus), "
            "矯直機=mesin straightening, 秤重站=stasiun timbang, "
            "暫存=simpan sementara, 短尺=ukuran pendek/short length, "
            "退庫=kembalikan ke gudang, 退貨=kembalikan barang, "
            "退庫拆包=keluarkan dari gudang dan bongkar packing untuk dibagi ulang, "
            "發料=keluarkan material/issue material, 存檔=simpan data, "
            "溢量=kelebihan produksi melebihi permintaan pelanggan/over quantity, "
            "併包=gabung packing dari lot berbeda dalam order yang sama, "
            "出貨差=kekurangan pengiriman hari ini/masih kurang untuk kirim, "
            "洗料=cuci material (sebelum polishing/grinding), "
            "粗拋=rough polishing, 粗拋完=selesai rough polishing, "
            "【機台名稱/Machine Names】"
            "E7=mesin polishing E7, BF2=mesin polishing BF2, K4=mesin K4, "
            "NOTE: Machine names like E7, BF2, K4, R2.5 should be kept as-is. "
            "【包裝/入庫流程】"
            "套紙管=pasang tabung kertas/paper tube, 已套紙管入庫=sudah pasang tabung kertas dan masuk gudang, "
            "優先包裝入庫=prioritas packing dan masuk gudang, "
            "需求單=formulir permintaan, 已填需求單=sudah isi formulir permintaan, "
            "可以全收=bisa diterima semua, "
            "櫃子=kontainer/container (shipping container), 櫃子在路上=kontainer sedang di jalan, "
            "裝箱=masukkan ke kotak kayu, 沒木箱可裝箱=gak ada kotak kayu untuk packing, "
            "2700大的木箱=kotak kayu ukuran besar 2700mm, "
            "裝櫃=muat ke kontainer, "
            "【客戶名稱/Customer Names - keep as-is】"
            "DACAPO, CASTLE, LOTUS, METALINOX, KANGRUI, SUNGEUN, STEELINC, 田華榕, 佳東, 蘋果, 常州眾山, 大順, 大成, 巨昌, 北澤, 鴻運, 畯圓, 名威, 右勝, 貝克休斯, 皇銘, "
            "台芝, 百堅, 津展, 曜麟, 廉錩, shinko, 盛昌遠, 永吉, wing keung, GLH, 光輝 etc. are customer names - NEVER translate. "
            "NOTE: 光輝 can mean both customer name AND 光輝退火(bright annealing). Context determines which. "
            "NOTE: 蘋果 here is a CUSTOMER NAME, NOT the fruit. 蘋果316LJ鋼種 = customer 蘋果, steel grade 316LJ. "
            "CASTLE已入庫=CASTLE sudah masuk gudang. "
            "【部門/人員】"
            "業務=bagian sales, 生計=生產計畫/production planning, "
            "資訊=IT department, 待資訊處理中=menunggu IT memproses, "
            "品保=QC/quality assurance, 儲運=bagian gudang & logistik, "
            "人事=HRD, 工安=safety officer, "
            "處長=kepala divisi, A夢=nickname (keep as-is), "
            "抓資料=ambil data, 業務要抓資料=sales perlu ambil data, "
            "【訂單管理/Order Management】"
            "允收=jumlah yang boleh diterima pelanggan (customer acceptance qty), "
            "允收0支=pelanggan tidak terima kelebihan (zero tolerance), "
            "不收短尺=pelanggan tidak terima ukuran pendek, "
            "訂尺=panjang sesuai pesanan pelanggan/order length, "
            "符合訂尺=sesuai panjang pesanan, "
            "爐號=heat number/nomor furnace, 不同爐號合併=gabung heat number berbeda, "
            "分捆=pisah bundel, 短尺分捆=pisahkan yang ukuran pendek ke bundel terpisah, "
            "接單=terima order, 投料=masukkan material ke produksi, "
            "遞延單=order yang ditunda dari bulan sebelumnya/delayed order, "
            "標記急單=tandai sebagai order urgent, "
            "本月份單=order bulan ini, 非本月=bukan order bulan ini, "
            "非本月不入庫=order bukan bulan ini jangan masuk gudang (don't input to station 801), "
            "檔非本月=tahan order bukan bulan ini (block non-current-month orders), "
            "入庫目標=target masuk gudang (e.g. 本月入庫目標2950=target bulan ini 2950), "
            "達標=sudah capai target, 未到站=belum sampai di stasiun, "
            "壓日期=ada deadline ketat, 有壓日期的急單=order urgent yang ada deadline, "
            "【入庫管控/Warehouse Control】"
            "異型棒=batang bentuk khusus/special shape bar, "
            "異型棒不擋=batang bentuk khusus tidak dibatasi (no restriction on special bars), "
            "非本月穩穩的包就好=order bukan bulan ini packing pelan-pelan aja, "
            "只入急單=hanya masukkan order urgent, "
            "【標籤/系統】"
            "TAG=label/tag, TAG列印=cetak label, "
            "儲區=area penyimpanan di sistem, 轉檔=konversi data, "
            "TAG列印如果儲區顯示異常通常是轉檔未成功=kalau area penyimpanan di label error biasanya konversi data belum berhasil, "
            "標籤機=mesin label/label printer, 測試標籤機功能=tes fungsi mesin label, "
            "包裝電腦=komputer packing, "
            "【出勤/HR】"
            "忘卡補=lupa bawa kartu ID untuk absen, pakai sistem untuk input waktu masuk/pulang, "
            "加班時數改天用忘卡補=jam lembur diinput lewat sistem lupa kartu di hari lain, "
            "造冊=buat daftar absensi, 造冊點名=buat daftar dan cek kehadiran, "
            "班股=rapat shift (班股會議), 班股跟削皮撞日=rapat shift bentrok jadwal dengan bagian peeling, "
            "堆高機複訓=pelatihan ulang forklift, "
            "紅包=bonus Tahun Baru Imlek/angpao, 想賺紅包可以代班=mau dapat angpao bisa gantikan shift, "
            "年終獎金=bonus akhir tahun, 慰問金=uang santunan, "
            "年假=libur Tahun Baru Imlek, 過年不停機=Imlek tidak berhenti produksi, "
            "【安全/環境 補充】"
            "太空包=karung besar/FIBC/jumbo bag, "
            "噴漆罐=kaleng cat semprot, 噴漆罐一定要打洞才能丟棄在太空包=kaleng spray HARUS dilubangi sebelum buang ke jumbo bag, "
            "包裝材廢棄物以後丟太空包就好了=sampah bahan packing buang ke jumbo bag aja, "
            "查核=audit/inspeksi, 被查核=kena audit, 缺失=temuan/deficiency, "
            "油漬=noda minyak, 被釘=dimarahi atasan/kena tegur, "
            "套量=ambil dan ukur, 紀念衫=baju peringatan/kaos anniversary, "
            "天車=overhead crane, 台車=trolley/kereta dorong, 吊秤=timbangan gantung, "
            "馬蹄環=shackle, 鋼索=sling baja, 吊掛物=beban gantung, 掛鉤=hook, "
            "開天車=operasikan crane, 天車複訓=pelatihan ulang crane, "
            "開天車務必遵守規定目視吊掛物=operasi crane WAJIB lihat beban gantung sesuai aturan, "
            "寸動=inching/gerakan pelan, 捲入=terseret masuk ke mesin, "
            "公安意外=kecelakaan kerja, 工傷=cedera kerja, "
            "KYT=KYT (pelatihan prediksi bahaya), KYT演練=latihan KYT, "
            "防火演練=latihan pemadam kebakaran, "
            "用餐室=ruang makan, 吸菸區=area merokok, "
            "廚餘=sisa makanan, 分類=pemilahan sampah, 垃圾袋=kantong sampah, "
            "制服=seragam, 冬季制服=seragam musim dingin, 棉手套=sarung tangan katun, "
            "工業用水=air industri, 綠卡=kartu hijau (catatan safety), "
            "入儲=masuk penyimpanan, 盤點=stock opname, 初盤=stock opname awal, "
            "【量詞/Counters - CRITICAL for factory context】"
            "把=bundel (bundle, e.g. 2把=2 bundel), 捆=bundel/ikat, 根=batang (piece/rod), "
            "支=batang, 條=batang, 隻=ekor (animals only), 批=lot/batch, "
            "包=pak/bungkus (package), 箱=kotak/kardus, 台=unit (for machines), "
            "NOTE: 三米/六米/X米 in this factory = batang X meter (bar length, e.g. 三米=batang 3 meter, 六米=batang 6 meter). "
            "When talking about 三米上面放六米, it means 'batang 3 meter ditaruh di atas batang 6 meter' (stacking bars by length). "
            "【堆放/倉儲/包裝 Storage & Packaging】"
            "放料=taruh material/letakkan material, 堆放=tumpuk/menumpuk, "
            "堆料=numpuk material, 囤料=numpuk material/stok material, "
            "料架=rak material, 棧板=pallet, 上面放=ditaruh di atas, "
            "疊起來=ditumpuk, 分開放=pisahkan/taruh terpisah, "
            "混放=campur taruh (putting different items together, BAD), "
            "歸位=kembalikan ke tempat, 對齊=sejajarkan/rapikan, "
            "包=packing/kemas (as verb), 包好=sudah di-packing, "
            "包裝=packing/pengemasan, 綁好=sudah diikat, 標籤=label, "
            "貼標=tempel label, 秤重=timbang, 過磅=timbang berat, "
            "短少=kurang (short/missing), 多出=lebih/kelebihan, "
            "入庫=masuk gudang, 出庫=keluar gudang, 倉庫=gudang, "
            "木箱=kotak kayu, 3200=panjang kotak kayu 3200mm, 500=kapasitas 500kg, "
            "NOTE: When 木箱 context, numbers like 3200/2400 refer to box LENGTH in mm, and 500/1000 refer to weight CAPACITY in kg. "
            "【品質/檢查】"
            "品質=kualitas, 品管=QC, 巡查=inspeksi, 檢查=periksa/cek, "
            "抽檢=sampling check, 全檢=periksa semua/inspeksi penuh, "
            "抽查機制=sistem sampling, "
            "合格=lulus/OK, 不合格=tidak lulus/NG, "
            "重工=rework, 返修=perbaiki ulang, "
            "環狀擦傷=goresan melingkar, 刮傷=goresan, 瑕疵=cacat, "
            "客訴=komplain pelanggan, 客訴環狀擦傷=komplain goresan melingkar, "
            "夾帶樣品=sertakan sampel, 中心裂=center crack, "
            "倒角=chamfer, 補倒角=tambah chamfer, "
            "套套環=pasang ring pelindung, C套環=C-ring, "
            "噴漆罐沒搖均勻=kaleng spray belum dikocok rata, "
            "單邊噴漆=spray cat satu sisi, 噴偏大那端=spray di ujung yang lebih besar, "
            "綁鐵=ikat besi, 待綁鐵=tunggu ikat besi, "
            "掛檔=simpan ke arsip, 稽核=audit, 供應商稽核=audit supplier, "
            "【缺陷/異常 Defects & Abnormalities - CRITICAL for quality discussions】"
            "螺紋=ulir/thread mark (defect on surface), 車刀痕=bekas pisau bubut/turning tool mark, "
            "砂光痕=bekas amplas/sanding mark, 殺光痕=bekas grinding mark, "
            "剝片=pengelupasan/flaking, 印痕=bekas cetak/imprint, 軋輥印痕=bekas roll/roll mark, "
            "碰傷=luka benturan, 撞傷=luka tabrakan, 角碰傷=luka benturan di sudut, "
            "黑皮=kulit hitam (unfinished surface), 端部=ujung batang, "
            "偏小=terlalu kecil/under size, 偏大=terlalu besar/over size, "
            "單點偏小=satu titik under size, 整支性偏小=seluruh batang under size, "
            "深度=kedalaman (defect depth), 條=0.01mm (unit, e.g. 5條=0.05mm), "
            "手感=sentuhan tangan/feel by touch, 目視=cek visual/visual inspection, "
            "表粗有過目視沒過=surface roughness lulus tapi visual tidak lulus, "
            "金相=metallography, 搓刀=kikir/file tool, "
            "限度樣本=limit sample/sampel batas, "
            "壓光=press polish/calendering, 壓光沒壓下來=press polish gagal menekan, "
            "六角棒=hex bar/batang segi enam, "
            "色澤不均=warna tidak merata, 膠膜=film pelindung, "
            "布輪=cloth wheel/roda kain, 布輪修整=dressing cloth wheel, "
            "【品保流程/QC Process】"
            "會驗=joint inspection/inspeksi bersama, 班長會驗=kepala shift inspeksi, "
            "方便會驗嗎=bisa inspeksi bersama gak?, "
            "暫留=tahan dulu/hold sementara, HOLD=tahan, "
            "開立重工=buat work order rework, 開立重工酸洗=buat WO rework pickling, "
            "開立重工研磨=buat WO rework grinding, "
            "重工研磨至尺寸下限=rework grinding sampai batas bawah ukuran, "
            "不要低於公差下限=jangan di bawah batas toleransi bawah, "
            "不允收=pelanggan tidak terima/reject, 營業允收=sales bilang terima, "
            "取樣=ambil sampel, 切斷取樣=potong untuk sampel, "
            "風險批=lot berisiko, 盤元涉及軋輥的風險批=lot wire rod yang kena masalah roll, "
            "走ET檢測=jalankan pengujian ET (eddy current test), "
            "卡料=material macet di mesin, 卡料需關閉電源後再取料=material macet HARUS matikan listrik dulu baru ambil, "
            "治具=jig/alat bantu, 穿線=threading/masukkan kawat, "
            "【生產管理/Production Management】"
            "管控=kontrol/kendalikan, 不管控=tidak dikontrol (bebas masuk gudang), "
            "非本月管控不入庫=kontrol: order bukan bulan ini jangan masuk gudang, "
            "管控被檢討=kena tegur karena kontrol kurang ketat, "
            "MES=MES (sistem produksi), 報表=laporan produksi, 備註=catatan, "
            "條碼=barcode, 掃條碼=scan barcode, "
            "HOLD=HOLD/tahan, 維護=perbaiki data di sistem, 短尺未維護=data ukuran pendek belum diperbaiki, "
            "結帳=tutup buku/finalisasi data, 趕結帳=kejar tutup buku, "
            "到站=material sudah sampai di stasiun, 到料=material sudah datang, "
            "追料=kejar material, 幫追料=tolong kejar material, "
            "追帳=kejar data administrasi, 幫追帳=tolong kejar data administrasi, "
            "積料=material menumpuk, 挖料=cari material dari tempat lain, "
            "轉用=dialihkan untuk order lain, 跳無主轉用=pindah ke tanpa pemilik lalu dialihkan, "
            "請轉=tolong alihkan produksi ke, 轉R45=alihkan ke produksi R45, "
            "圓棒=batang bulat/round bar, 交接=serah terima, 交接事項=hal yang harus diserah-terimakan, "
            "排程更新=jadwal produksi diupdate, "
            "稼動率=utilization rate mesin, 作業效率=efisiensi kerja, "
            "提速=naikkan kecepatan, 降速=turunkan kecepatan, "
            "撥料=feed material/umpan material, 自動撥料=auto feeding, 手動撥料=manual feeding, "
            "過機=lewatkan mesin/proses mesin, 線外=offline/di luar lini produksi, "
            "印勞=pekerja Indonesia (singkatan 印尼勞工), "
            "點檢=inspection check/cek rutin, 護罩=pelindung mesin/safety guard, "
            "表粗=kekasaran permukaan/surface roughness, "
            "技服=technical service, 技服已放行=technical service sudah release, "
            "庫存=stok/inventori, 重跑雷射=ulang laser marking, "
            "水平校正=kalibrasi level/leveling, "
            "磨壞=rusak karena grinding, 異常料=material bermasalah, "
            "interlock=pengunci keamanan (jangan ditahan pakai benda), "
            "交接簿/紀錄簿=buku catatan serah terima, 環境交接簿=buku serah terima lingkungan, "
            "色差=perbedaan warna, 自由端=ujung bebas, "
            "拋光輪=roda polishing, 調機=setting mesin, 更換刀板=ganti work rest blade, "
            "可削皮去化=bisa dialihkan ke proses peeling, "
            "U料=material U (material tanpa order), "
            "【冷抽/倒角/設備維修 Cold Drawing & Maintenance】"
            "冷抽=cold drawing/penarikan dingin, 冷精棒=cold-finished bar, 直棒=straight bar, "
            "倒角=chamfer/bevel, 倒角機=mesin chamfer, 倒角急單=order chamfer urgent, "
            "修磨=repair grinding, 盤元修磨=repair grinding wire rod, 線外修磨=offline repair grinding, "
            "盤元=wire rod/coil, 盤元不佳=wire rod kualitas buruk, "
            "壓光=press polish, 壓光機=mesin press polish, "
            "側磨=side grinding (DILARANG/prohibited), 不可側磨=dilarang side grinding, "
            "矯直=straightening, 矯直機=mesin straightening, 壓輪=roda tekan/press wheel, "
            "抽完=selesai drawing, 上線=mulai produksi, 投產=mulai produksi/start production, "
            "回爐=kirim kembali ke furnace, 回爐處理=proses ulang di furnace, "
            "查修=investigasi dan perbaiki, 修護=maintenance/bagian perbaikan, "
            "儀電=instrument & electrical/bagian instrumen listrik, "
            "機修=mechanic repair, 備品=spare part, "
            "跳異常=error/alarm muncul, 復歸=reset, 復歸無效=reset gagal, "
            "跳機=mesin trip/berhenti mendadak, 恢復生產=kembali produksi, "
            "氣壓缸=silinder pneumatik, 計長器=length counter, "
            "安全圍籬=safety fence, 集塵設備=dust collector, "
            "冷水機=chiller, 馬達=motor, 馬達跳脱=motor trip, "
            "斷料=material putus, 卡料=material macet, "
            "擠料=material terjepit/macet keluar (material squeeze out at machine), "
            "重矯=straightening ulang, 定修=jadwal maintenance rutin, 週保=perawatan mingguan, "
            "線速=kecepatan lini/line speed (m/min), 限速=batas kecepatan, "
            "降速=turunkan kecepatan, 降速至=turunkan kecepatan ke, "
            "眼模=die/cetakan drawing, 眼模室=ruang die, 過模=lewat die, "
            "整修眼模=perbaiki die, 更換眼模=ganti die, "
            "引拔座=drawing bench/dudukan tarik, 引拔油=drawing oil/oli tarik, "
            "砂光機=sanding machine, 砂帶=sanding belt, 膠輪=rubber wheel, 培靈=bearing, "
            "皮膜槽=coating tank, 加熱棒=heater rod, 線圈加熱=coil heating, "
            "離心機=centrifuge, 切斷座=cutting station, 切斷機=cutting machine, "
            "夾輪=clamping wheel, 導管=guide tube, 接線=sambung kawat, "
            "MC=MC (material contact/pelindung material), MC壓輪=MC press wheel, "
            "MC套筒=MC sleeve, 料管箱=material guide box, "
            "光輝退火=bright annealing, 光輝退火爐=bright annealing furnace, "
            "爐溫表=termometer furnace, 跳亂碼=tampil error code, "
            "螺紋(冷抽)=thread mark (defect from drawing), "
            "直度不佳=straightness buruk, 入壓光機的標準=standar masuk mesin press polish, "
            "精整=finishing, AP=nama mesin finishing (精整設備), 要回精整=harus kembali ke mesin finishing AP, "
            "叫修=panggil teknisi perbaikan, 已叫修=sudah panggil teknisi, "
            "進廠查修=teknisi masuk pabrik untuk cek dan perbaiki, "
            "電聯儀電=hubungi bagian instrumen listrik via telepon, "
            "on call=on call (teknisi siaga), "
            "異常跳停=error trip/berhenti mendadak karena error, "
            "已修復=sudah diperbaiki, 未修復=belum diperbaiki, "
            "復歸=reset, 復歸無效=reset gagal, 重開=restart, "
            "測試生產=tes produksi, 已正常生產=sudah produksi normal, "
            "主機手=operator utama mesin, 上料人員=petugas pengisian material, "
            "提報懲處=laporkan untuk sanksi, 會被提報=bisa dilaporkan, "
            "送懲處=kirim untuk sanksi, 會嚴罰=akan dihukum berat, "
            "避免被拍照=hindari difoto (oleh auditor), 避免被提報=hindari dilaporkan, "
            "煙蒂=puntung rokok, 檳榔渣=sisa pinang, "
            "上漆/上油漆=cat/mengecat, 除油污泥=bersihkan lumpur oli, "
            "電溝=parit kabel listrik, 動線=jalur kerja/workflow path, "
            "在製品管制表=tabel kontrol barang setengah jadi/WIP control sheet, "
            "混料=material tercampur (SERIOUS issue), 待判=menunggu keputusan, "
            "型架=rak penyangga, L型架=rak L, U型架=rak U, "
            "加績效=tambah penilaian kinerja (reward), 扣罰績效=potong penilaian kinerja (sanksi), "
            "南檢所=South inspection office (kantor inspeksi selatan), "
            "調班單=formulir tukar shift, 簽核=tanda tangan persetujuan, "
            "【環境/紀律處分】"
            "扣績效=potong penilaian kinerja (sanksi), 劣項=poin buruk/pelanggaran, "
            "納入劣項=dicatat sebagai pelanggaran, "
            "三定=3 tetap (tempat tetap, barang tetap, jumlah tetap), "
            "不要物=barang tidak terpakai/harus dibuang, "
            "標示=label/penanda, 無標示=tidak ada label, "
            "漏油=bocor oli, 生鏽=berkarat, 掉漆=cat mengelupas, "
            "積水=genangan air, 粉塵=debu, 清掃=bersihkan, "
            "護蓋=penutup pelindung, 內務櫃=loker pribadi, "
            "尾牙=pesta akhir tahun, 春酒=pesta tahun baru, 員工旅遊=wisata karyawan, "
            "便當費=biaya makan siang, 伴手禮=oleh-oleh, 禮券=voucher hadiah, "
            "補休=libur pengganti, 公出=dinas luar, "
            "【量測/設備】"
            "量測=mengukur, 尺寸=diameter/dimensi, 量測尺寸=ukur diameter, "
            "手動量測=ukur secara manual, 三點式=3 titik, "
            "雷射=laser, 設備=peralatan/mesin, "
            "故障=rusak/error, 拋光=polishing, 拋光棒=batang polishing, "
            "切割=cutting/potong, 模具=cetakan/mold, "
            "公差=toleransi, 校正=kalibrasi, 游標卡尺=jangka sorong, "
            "千分尺=mikrometer, 測量儀=alat ukur, "
            "紀錄=catat, 清洗=cuci, 輕調輕放=handle dengan hati-hati, "
            "每捆=setiap bundel, 包裝站=stasiun packing, "
            "C行套環=C-ring, 補上=lengkapi, "
            "【安全/環境】"
            "安全=keselamatan, 戴手套=pakai sarung tangan, 戴口罩=pakai masker, "
            "護目鏡=kacamata pelindung, 安全帽=helm, 安全鞋=sepatu safety, "
            "消防=pemadam kebakaran, 滅火器=alat pemadam, "
            "打掃=bersih-bersih, 清潔=kebersihan, 整理=rapikan, "
            "5S=5S, 垃圾=sampah, 回收=daur ulang, "
            "廠內=di dalam pabrik, 禁止=dilarang, 宣導=sosialisasi, "
            "【宿舍/生活】"
            "宿舍=asrama, 房間=kamar, 室友=teman sekamar, "
            "門禁=jam malam, 熄燈=lampu mati, 洗衣=cuci baju, "
            "煮飯=masak nasi, 餐廳=kantin, 便當=bekal makan, "
            "餵狗=kasih makan anjing, "
            "【薪資/人事】"
            "薪水=gaji, 底薪=gaji pokok, 加班費=uang lembur, "
            "全勤獎金=bonus kehadiran penuh, 獎金=bonus, 扣薪=potong gaji, "
            "績效=penilaian kinerja, 增加績效=tambah penilaian kinerja, "
            "依情節=sesuai tingkat pelanggaran, "
            "匯款=kirim uang/transfer, 發薪=bayar gaji, "
            "續約=perpanjang kontrak, 合約=kontrak, 體檢=medical check-up, "
            "居留證=ARC/kartu izin tinggal, 護照=paspor, "
            "【溝通/其他】"
            "開會=rapat, 集合=kumpul, 公告=pengumuman, "
            "報告=laporan, 表格=formulir, 簽名=tanda tangan, "
            "聽不懂=tidak mengerti, 慢慢來=pelan-pelan, 快一點=cepat sedikit, "
            "小心=hati-hati, 注意=perhatian, 禁止=dilarang, "
            "做得好=kerja bagus, 辛苦了=terima kasih atas kerja kerasnya, "
            "確實=pastikan, 防止=mencegah. "
            "10. CRITICAL FACTORY CONTEXT RULES: "
            "a) When numbers + 米(meter) appear (三米,六米,3米,6米,12米), they refer to BAR LENGTHS (panjang batang), not distance. "
            "Always translate as 'batang X meter'. E.g. 三米上面放六米 = batang 3 meter ditaruh di atas batang 6 meter. "
            "b) When numbers + 把/捆 appear, they are BUNDLE counters. E.g. 包2把 = packing 2 bundel. "
            "c) When 包(bāo) is used as a VERB, it means packing/kemas, NOT wrapping. E.g. 高侑的今天包2把都這樣 = Yang di-packing Gao You hari ini 2 bundel semuanya kayak gini. "
            "d) Person names (高侑,十元,小麥,啊堂,秋情,政軒,碩凱,汶錡,武駿,凱銘,小趙,阿澤,法比恩,山多,EggEgg,fang,Dato潘 etc.) are nicknames - keep them as-is, do NOT translate. "
            "e) Customer/company names (DACAPO, CASTLE, LOTUS, METALINOX, 田華榕, 佳東 etc.) are customer names - keep them as-is, do NOT translate. "
            "f) R+number = round bar diameter (batang bulat). E.g. R28.57=batang bulat diameter 28.57mm. Non-R = hex/special shape bar (異型棒). E.g. H26=batang hex 26mm. "
            "g) S/B = straight bar (直棒). E.g. S/B 16 = straight bar 16mm, S/B 38.5 = straight bar 38.5mm. "
            "h) E1~E11 = cold drawing production line numbers (nomor lini produksi cold drawing). Keep as-is. "
            "i) I1~I21 = grinding/polishing machine numbers (nomor mesin grinding/polishing). Keep as-is. "
            "j) BF2, BF3, BF5 = polishing machine numbers. Keep as-is. "
            "k) 5F/5L/5N/6S/6T/6U/6W/6X/7E/7F/7G + numbers = product work order ID. Keep as-is, never translate. "
            "l) 1CD, H26, R22.2 etc. in scheduling context = process code + size. 1CD=process code. "
            "m) 課料=material yang ditunjuk oleh kepala seksi (section chief's designated material). "
            "n) 速差=selisih kecepatan/speed difference. 速差標準=standar selisih kecepatan. "
            "o) G包=tipe packing G (packaging method code). Keep as-is. "
            "p) 不留線=tidak menyisakan kawat di mesin (do not leave wire on machine). "
            "q) AP=after process/proses lanjutan (finishing equipment name). "
            "11. TRANSLATION EXAMPLES (follow these patterns strictly): "
            "【台灣口語→印尼文】"
            "乾 需不需要提報一下 → Aduh, perlu dilaporkan gak nih? "
            "UT囤一堆料了 → UT udah numpuk banyak material. "
            "品保還在下班 誇張 → QC udah pulang, keterlaluan. "
            "三米上面放六米 → Batang 3 meter ditaruh di atas batang 6 meter. "
            "麻煩他們不要這樣放料 → Tolong bilang ke mereka jangan taruh material kayak gini. "
            "高侑的今天包2把都這樣 → Yang di-packing 高侑 hari ini 2 bundel semuanya kayak gini. "
            "來料都短少4-5公斤 → Material yang masuk semuanya kurang 4-5 kilogram. "
            "已轉達 → Sudah disampaikan. "
            "趕快去處理 → Buruan ditangani. "
            "這批料有問題 → Lot material ini ada masalah. "
            "幫我盯一下 → Tolong awasin ya. "
            "怎麼搞的啦 → Kok bisa kayak gini sih. "
            "搞什麼啊 → Ngapain sih. "
            "做到哪了 → Udah sampai mana kerjaannya? "
            "還沒好喔 → Belum selesai ya? "
            "快好了沒 → Mau selesai belum? "
            "人咧 → Orangnya mana? "
            "誰做的 → Siapa yang bikin? "
            "先不要動 → Jangan diapa-apain dulu. "
            "等我一下 → Tunggu sebentar. "
            "辛苦了 → Makasih kerja kerasnya. "
            "不是這樣用 → Bukan gitu caranya. "
            "有夠誇張 → Keterlaluan banget. "
            "靠 又壞了 → Astaga, rusak lagi. "
            "笑死 → Ngakak. "
            "累死了 → Capek banget. "
            "機台怪怪的 → Mesinnya agak aneh. "
            "先這樣 → Segitu dulu ya. "
            "我看看再說 → Aku lihat dulu nanti. "
            "叫他快點 → Suruh dia cepatan. "
            "跟他說小心一點 → Bilang ke dia hati-hati. "
            "這個要重工 → Yang ini harus rework. "
            "不合格退回去 → NG, kembalikan. "
            "砂輪要換了 → Batu gerinda harus diganti. "
            "冷卻液不夠 → Cairan pendinginnya kurang. "
            "尺寸量一下 → Ukur diameternya. "
            "公差超過了 → Toleransinya udah lewat. "
            "【台灣口語→印尼文 (製程/帳務/包裝)】"
            "這6把再麻煩今晚入庫 → 6 bundel ini tolong masukin gudang malam ini. "
            "明早業務要抓資料 謝謝 → Besok pagi sales perlu ambil data, makasih. "
            "再幫忙設個急單備註 → Tolong tambahkan catatan order urgent. "
            "包裝會看備註安排處理 → Bagian packing akan lihat catatan dan atur pengerjaannya. "
            "下製程異常 再麻煩一下 → Proses selanjutnya ada masalah, tolong ditangani. "
            "好了 再麻煩試試看 → Udah, tolong coba lagi ya. "
            "BF2拋光機維修中 → Mesin polishing BF2 sedang diperbaiki. "
            "上面這捆生產完會換 → Setelah bundel ini selesai produksi akan diganti. "
            "不過2.5明天應該拋不完 → Tapi diameter 2.5 besok sepertinya gak selesai polishing. "
            "44.45前天有跟妳說超產，業務回覆了嗎 → Diameter 44.45 kemarin sudah bilang over produksi, sales udah balas belum? "
            "業務目前延到8號 我再改時間 → Sales ditunda sampai tanggal 8, aku ubah waktunya. "
            "還沒 我問問 → Belum, aku tanya dulu. "
            "有消息說一下 我再安排套紙管 → Kalau ada kabar kabarin ya, aku atur pasang tabung kertas. "
            "他說還沒問 今天給答案 XD → Dia bilang belum tanya, hari ini kasih jawaban XD. "
            "拖了三天，看來業務不急 → Udah ditunda 3 hari, kayaknya sales gak buru-buru. "
            "噴漆後照訂單量拆包 → Setelah spray paint, bagi packing sesuai jumlah order. "
            "這個田華榕退庫UT，實物退貨後已經變成三米 → Yang ini 田華榕 data dipindahkan dari gudang ke station UT (470), setelah barang dikembalikan sudah jadi batang 3 meter. "
            "幫忙問看看一樣要給品保UT嗎 → Tolong tanya, ini juga perlu dikasih ke QC UT gak? "
            "我確認一下 → Aku konfirmasi dulu. "
            "這捆沒法發料存檔，其他把都可以 → Bundel ini gak bisa issue material dan simpan data, yang lain bisa semua. "
            "已填需求單請資訊確認 → Sudah isi formulir permintaan, minta IT konfirmasi. "
            "可以全收 謝謝 → Bisa diterima semua, makasih. "
            "已套紙管入庫 → Sudah pasang tabung kertas dan masuk gudang. "
            "找嘉駿確認中 → Sedang konfirmasi dengan 嘉駿. "
            "這把短尺暫存無法回490在處理一下 → Bundel ini ukuran pendek, disimpan sementara, gak bisa balik ke station 490, tolong diproses lagi. "
            "NOTE: In casual chat 在 is often used as 再(lagi/again). E.g. 在處理一下=再處理一下=tolong proses lagi. "
            "什麼意思@@? 我看ID現況在490 → Maksudnya apa? Aku lihat status ID sekarang di station 490. "
            "回490秤重站無工單資訊 → Dikembalikan ke station 490 timbang, tapi gak ada info work order. "
            "已提需求單，待資訊處理中 → Sudah ajukan formulir permintaan, menunggu IT proses. "
            "等處理 → Tunggu diproses. "
            "7G966304B 此捆明天出貨拋光後再請優先包裝入庫 → 7G966304B bundel ini besok kirim, setelah polishing tolong prioritas packing dan masuk gudang. "
            "已入庫 → Sudah masuk gudang. "
            "這個幫忙確認要給哪一站 → Tolong konfirmasi ini harus ke stasiun mana. "
            "這個尺寸削皮應該沒法做 → Ukuran ini sepertinya gak bisa di-peeling. "
            "好 確認後回覆 → OK, setelah konfirmasi aku balas. "
            "品保點錯製程，麻煩退回400-無主 → QC salah pilih proses, tolong kembalikan ke station 400 tanpa pemilik. "
            "重新原單改制退火冷抽 從401開始 → Buat ulang work order, ubah proses jadi annealing + cold drawing, mulai dari station 401. "
            "請協助帳務490站回400站(無主) → Tolong bantu administrasi dari station 490 ke station 400 (tanpa pemilik). "
            "料在矯直機那 會重掛單 → Materialnya di mesin straightening, akan pasang ulang work order. "
            "7E584311 再幫忙回400 謝謝 → 7E584311 tolong kembalikan ke station 400, makasih. "
            "帳已回400、料要回去那一個單位？ → Data sudah dikembalikan ke 400, materialnya mau ke unit mana? "
            "去削皮退火 感溫 → Ke proses peeling dan annealing, makasih. "
            "490急單再麻煩幫忙包裝 → Order urgent station 490, tolong bantu packing. "
            "R2.5 再麻煩優先安排 → R2.5 tolong diprioritaskan. "
            "3200 500明天入廠 → Kotak kayu panjang 3200 kapasitas 500kg besok masuk pabrik. "
            "沒木箱 → Gak ada kotak kayu. "
            "【台灣口語→印尼文 (排程/出貨/客戶)】"
            "7F414020 請幫放至480轉用收回400，要改制去化，謝謝 → 7F414020 tolong pindahkan ke station 480, lalu kembalikan ke station 400, mau ubah proses produksi, makasih. "
            "這在480了 → Ini sudah di station 480. "
            "暫留過久再麻煩協助包裝 謝謝 → Kalau disimpan terlalu lama, tolong bantu packing ya, makasih. "
            "業務說收～ 請包～ → Sales bilang terima, tolong di-packing. "
            "班長～ 7F656502A 這把溢量請再入無主～ 謝謝! → Kepala shift, 7F656502A bundel ini kelebihan produksi, tolong masukkan ke status tanpa pemilik, makasih! "
            "客需求支數7支、不收短 來料只有6支、其中一支短、剔除掉剩5支、能包嘛？ → Pelanggan minta 7 batang, gak terima ukuran pendek. Material masuk cuma 6 batang, 1 batang pendek, dibuang sisa 5 batang, bisa di-packing gak? "
            "我問問業務 → Aku tanya sales dulu. "
            "請問短尺長度多少～ → Berapa panjang yang ukuran pendek? "
            "我要找料一下 → Aku mau cari materialnya dulu. "
            "好喔 → OK. "
            "因為櫃子在路上 9點到 這樣可能可以等一下入庫 → Karena kontainer sedang di jalan, sampai jam 9, jadi mungkin bisa tunggu sebentar baru masuk gudang. "
            "DACAPO入了 → DACAPO sudah masuk gudang. "
            "DACAPO都入完了 → DACAPO semuanya sudah masuk gudang. "
            "7G605100 對不對～ 謝謝!! → 7G605100 bener kan? Makasih!! "
            "沒木箱可裝箱 → Gak ada kotak kayu untuk packing. "
            "班長～ 請用2700大的木箱裝，再麻煩幫我抓一下幾點會好，業務下午要出，謝謝 → Kepala shift, tolong pakai kotak kayu ukuran besar 2700, tolong cek jam berapa selesai, sales sore ini mau kirim, makasih. "
            "12點前 → Sebelum jam 12. "
            "已經入完了 → Sudah selesai semua masuk gudang. "
            "那就是帳沒入到 → Berarti datanya belum masuk ke sistem. "
            "資料異常，凱銘在處理了 → Data ada masalah, 凱銘 sedang urus. "
            "研磨排程已更新，急單再麻煩安排洗料拋光 謝謝 → Jadwal grinding sudah diupdate, order urgent tolong atur cuci material dan polishing, makasih. "
            "7G751312B 請麻煩優先洗料上機研磨，謝謝! → 7G751312B tolong prioritas cuci material dan naik mesin grinding, makasih! "
            "7G755315 請協助安排洗料拋光，業務有出貨需求，謝謝! → 7G755315 tolong bantu atur cuci material dan polishing, sales ada kebutuhan kirim, makasih! "
            "粗拋完已放行 → Rough polishing selesai, sudah di-release. "
            "感謝~~~~如果可以包裝完更讚 → Makasih, kalau bisa selesai packing lebih bagus lagi. "
            "今日出貨差 DACAPO 7G63837在490 7G687108A在420 OL → Hari ini pengiriman masih kurang: DACAPO 7G63837 di station 490, 7G687108A di station 420 sedang produksi. "
            "METALINOX 差2噸等等K4會在出料 可以的在幫包裝 感謝 → METALINOX masih kurang 2 ton, nanti mesin K4 akan keluarkan material, kalau bisa tolong bantu packing, makasih. "
            "7G962110A再麻煩安排拋光後和7G962209併包，業務有出貨需求～謝謝 → 7G962110A tolong atur polishing, setelah itu gabung packing dengan 7G962209, sales ada kebutuhan kirim, makasih. "
            "7G830007 退庫拆包再麻煩協助接收～謝謝! → 7G830007 keluarkan dari gudang dan bongkar packing, tolong bantu terima, makasih! "
            "7G538313這把退庫拆包的也先幫忙拆包感謝 → 7G538313 bundel ini yang dikeluarkan dari gudang juga tolong bongkar packingnya dulu, makasih. "
            "7G108519D 請幫收回400，有單去化 謝謝 → 7G108519D tolong kembalikan ke station 400, ada order baru untuk serap material, makasih. "
            "洗給E7拋了 → Sudah dicuci dan dikasih ke mesin E7 untuk polishing. "
            "這兩把11.1再麻煩協助包裝 謝謝！ → 2 bundel diameter 11.1 ini tolong bantu packing, makasih! "
            "今日3米木箱進來再先幫包 → Hari ini kotak kayu 3 meter datang, tolong packing duluan. "
            "【台灣口語→印尼文 (訂單/品質/出勤/管理)】"
            "包裝遇到常州眾山再注意這個料號，剛接單後續才會投料生產，此訂單不收短尺需將短尺分捆 → Kalau packing ketemu 常州眾山 perhatikan nomor material ini, baru terima order nanti baru masukkan material ke produksi, order ini gak terima ukuran pendek, yang pendek harus pisah bundel. "
            "常州眾山除了有寄信通知不收短尺的，其餘如果工單不是允收0，短尺符合訂長可以包入庫 → 常州眾山 selain yang sudah ada surat pemberitahuan gak terima ukuran pendek, sisanya kalau work order bukan nol toleransi, ukuran pendek yang sesuai panjang pesanan bisa di-packing masuk gudang. "
            "KANGRUI只要工單允收0支就不收短尺 → KANGRUI kalau work order nol toleransi berarti gak terima ukuran pendek. "
            "現況分出來的短尺生計都會掛在KANGRUI同一張訂單，只要符合訂尺，可以不同爐號合併（一包最多十支） → Saat ini ukuran pendek yang dipisahkan, production planning akan masukkan ke order KANGRUI yang sama, asal sesuai panjang pesanan, boleh gabung heat number berbeda (1 packing maksimal 10 batang). "
            "這張單注意一下哦，今天開始到料，短尺確認長度後分出來先放待併包，年假之後生計會統一掛單給能收的訂單 → Perhatikan order ini ya, mulai hari ini material datang, ukuran pendek cek panjangnya lalu pisahkan, simpan dulu untuk gabung packing nanti, setelah libur Imlek production planning akan atur work order ke order yang bisa terima. "
            "18/19號有需要休假的再跟我說一下，當天可以休三個 → Tanggal 18/19 kalau ada yang mau libur bilang ke aku, hari itu bisa libur 3 orang. "
            "剛剛開會決議過年不停機，如果A班D班出勤人數不夠12人，想賺紅包可以代班 → Baru rapat, keputusannya Imlek tidak berhenti produksi, kalau shift A dan shift D jumlah hadir kurang dari 12 orang, yang mau dapat angpao bisa gantikan shift. "
            "人事有通知一份他們自己排的堆高機複訓課程，1/29 1700-2000三樓會議室 → HRD ada pemberitahuan jadwal pelatihan ulang forklift yang mereka atur sendiri, 29/1 jam 17:00-20:00 di ruang rapat lantai 3. "
            "當天這時段來上課就好，加班時數改天用忘卡補 → Hari itu datang ikut pelatihan di jam itu aja, jam lembur diinput lewat sistem lupa kartu di hari lain. "
            "處長走了 → Kepala divisi sudah pergi. "
            "A夢走了 → A夢 sudah pergi. "
            "有壓日期的急單再幫忙處理一下，很多未到站，拋光會一邊產出 → Order urgent yang ada deadline tolong diproses, banyak yang belum sampai di stasiun, polishing akan produksi sambil jalan. "
            "班股跟削皮撞日，我們改天再開，明天正常上班時間就好 → Rapat shift bentrok dengan bagian peeling, kita ganti hari, besok masuk kerja seperti biasa aja. "
            "儲運今天反應一些異常TAG，早班已經換完了 → Bagian gudang hari ini lapor ada beberapa label yang error, shift pagi sudah ganti semua. "
            "TAG列印如果儲區顯示異常通常是轉檔未成功，再等一陣子重印儲區就正確了 → Kalau cetak label area penyimpanan tampil error, biasanya konversi data belum berhasil, tunggu sebentar lalu cetak ulang pasti benar. "
            "包裝電腦如果沒問題，順便幫忙測試標籤機功能 → Kalau komputer packing gak ada masalah, sekalian tolong tes fungsi mesin label. "
            "退火爐壞掉，要到下週二才會重新投料，各站料源如果不足再請收料人員分流 → Tungku annealing rusak, baru minggu depan Selasa bisa masukkan material lagi, kalau material di tiap stasiun kurang minta petugas penerima material bagi rata. "
            "上面要求2/14-2/22年假期間出勤都要造冊，這期間有要請假的人，今天抽空在白板的班表畫一下 → Atasan minta tanggal 14-22/2 selama libur Imlek, kehadiran harus dibuat daftar, yang mau izin di periode ini, hari ini sempat-sempatin gambar di jadwal shift papan tulis. "
            "包裝材那類的廢棄物以後丟太空包就好了 → Sampah bahan packing mulai sekarang buang ke jumbo bag aja. "
            "噴漆罐一定要打洞才能丟棄在太空包，本週被查核兩次缺失，再互相提醒一下 → Kaleng spray HARUS dilubangi baru boleh buang ke jumbo bag, minggu ini kena audit 2 kali ada temuan, tolong saling ingatkan. "
            "本月入庫目標2950，異型棒不擋，其餘非本月不入庫 → Target masuk gudang bulan ini 2950, batang bentuk khusus gak dibatasi, sisanya yang bukan bulan ini jangan masuk gudang. "
            "除了出貨急單優先處理外，非本月穩穩的包就好，之後訂單慢慢恢復，下個月3200起跳上看3500 → Selain order urgent kirim yang diprioritaskan, order bukan bulan ini packing pelan-pelan aja, nanti order perlahan pulih, bulan depan mulai 3200 target naik ke 3500. "
            "急單跟異型棒再幫忙優先處理 → Order urgent dan batang bentuk khusus tolong diprioritaskan. "
            "本月入庫目標量已達標，目前只入急單、異型棒跟二月以前的遞延單，以上麻煩優先處理，謝謝 → Target masuk gudang bulan ini sudah tercapai, sekarang hanya masukkan order urgent, batang bentuk khusus, dan order yang ditunda dari sebelum Februari, tolong prioritaskan, makasih. "
            "十二點後異型棒也檔非本月 → Setelah jam 12 batang bentuk khusus juga ditahan, bukan bulan ini jangan masuk gudang. "
            "接下來只入標記急單、本月份單跟遞延單 → Selanjutnya hanya masukkan yang ditandai urgent, order bulan ini, dan order yang ditunda. "
            "今天沒點名，昨天來過了 → Hari ini gak ada inspeksi, kemarin sudah datang. "
            "非本月入20噸，留一些明天入 → Order bukan bulan ini masukkan 20 ton, sisakan sebagian besok masuk. "
            "非本月我先移四把急單共兩噸 → Order bukan bulan ini aku pindahkan dulu 4 bundel urgent total 2 ton. "
            "應該是上週四D班，傍晚要注意一下小趙跟處長行蹤，免得凱銘被釘 → Harusnya shift D hari Kamis kemarin, sore nanti perhatikan kemana 小趙 dan kepala divisi pergi, supaya 凱銘 gak kena tegur. "
            "晚點去大門口套量六十週年紀念衫，尺寸再上傳群組登記 → Nanti pergi ke gerbang utama ambil dan ukur baju peringatan 60 tahun, ukurannya upload ke grup untuk daftar. "
            "記得量哦 版型真的比較小 → Ingat ukur ya, modelnya memang agak kecil. "
            "自己稍微看一下設備的料源，有料就是要生產。月底我們不可能是停機的單位 → Cek sendiri material di mesin masing-masing, ada material ya harus produksi. Akhir bulan kita gak boleh jadi unit yang mesin berhenti. "
            "這個比較髒 → Yang ini agak kotor. "
            "幫追料 → Tolong kejar materialnya. "
            "放了 → Sudah ditaruh. "
            "【台灣口語→印尼文 (生產管理/安全/行政)】"
            "非本月只有異型棒不管控，其他麻煩不要入了，昨天早班沒管控被檢討 → Order bukan bulan ini cuma batang bentuk khusus yang bebas, sisanya jangan masuk gudang, shift pagi kemarin gak kontrol kena tegur. "
            "包裝備註的急單再幫忙優先處理一下 → Order urgent yang ada catatan di packing tolong prioritaskan. "
            "訂單量超過10%要問再包，包了一定拆 → Kalau jumlah melebihi 10% dari order tanya dulu baru packing, kalau keburu di-packing pasti dibongkar. "
            "7/31急單蠻多的，不用刻意挖料 盡量這兩天消化一下 → Tanggal 31/7 order urgent lumayan banyak, gak usah cari material dari tempat lain, usahakan 2 hari ini selesaikan. "
            "開天車務必遵守規定目視吊掛物 → Operasi crane WAJIB lihat beban gantung sesuai aturan. "
            "綠卡抽空補到十月 → Kalau sempat lengkapi kartu hijau sampai bulan Oktober. "
            "中午前大成趕結帳，當站有大成再幫忙優先包裝入庫 → Sebelum siang 大成 harus tutup buku, yang ada 大成 di stasiun tolong prioritas packing masuk gudang. "
            "幫忙收801 TAG丟掉 → Tolong ambil dari station 801, label buang. "
            "短尺或U料收料後儲區DJ20 DJ21都要當班交削皮 → Ukuran pendek atau material U setelah diterima area DJ20 DJ21 harus diserahkan ke bagian peeling di shift itu juga. "
            "佳東/SUNGEUN不管控交期，其他次月交期先包不入庫 → 佳東/SUNGEUN deadline tidak dikontrol, sisanya yang deadline bulan depan packing dulu tapi jangan masuk gudang. "
            "入庫目標3450噸 → Target masuk gudang 3450 ton. "
            "4～6點中間快兩個小時沒入庫，間隔時間要看一下 → Antara jam 4-6 hampir 2 jam gak ada masuk gudang, jarak waktunya tolong diperhatikan. "
            "上面釘入庫時間跟合理量 → Atasan tekankan waktu masuk gudang dan jumlah yang wajar. "
            "以後任何有夾帶樣品需求的客戶，包裝完拍一下ID跟樣品的照片給我掛檔 → Mulai sekarang semua pelanggan yang minta sertakan sampel, setelah packing foto ID dan sampelnya kirim ke aku untuk arsip. "
            "U料可削皮去化，再幫收料回400站一下 → Material U bisa dialihkan ke proses peeling, tolong kembalikan material ke station 400. "
            "316LJ如果品保給我們的料沒有按照規定套套環，就先不要包了 → 316LJ kalau material dari QC belum dipasang ring pelindung sesuai aturan, jangan di-packing dulu. "
            "客訴噴漆罐沒搖均勻，麻煩使用前搖一搖 → Ada komplain pelanggan kaleng spray belum dikocok rata, tolong sebelum pakai kocok dulu. "
            "輪晚班要注意一下常被拍的缺失，拍幾張整理環境的照片給我掛檔 → Giliran shift malam perhatikan temuan yang sering difoto, foto beberapa gambar lingkungan yang sudah rapi kirim ke aku untuk arsip. "
            "今天有稽核，報表注意一下 → Hari ini ada audit, perhatikan laporan produksi. "
            "下班前來拿冬季制服跟本月棉手套 → Sebelum pulang ambil seragam musim dingin dan sarung tangan katun bulan ini. "
            "排程更新，請留意交接項目，謝謝 → Jadwal produksi diupdate, tolong perhatikan hal serah terima, makasih. "
            "小趙巡廠了 → 小趙 sedang keliling inspeksi pabrik. "
            "處長走了 下來了 → Kepala divisi sudah pergi, sudah turun. "
            "下班記得拿伴手禮 → Pulang ingat ambil oleh-oleh. "
            "便當費有空可以跟我結清哦 → Biaya makan siang kalau sempat bayar ke aku ya. "
            "幫追帳 → Tolong kejar data administrasinya. "
            "已2900別入帳了噢 → Sudah 2900 jangan masukkan data lagi ya. "
            "有到站優先處理 → Yang sudah sampai di stasiun prioritas proses. "
            "【台灣口語→印尼文 (設備/管理)】"
            "拋光機interlock都不要拿東西擋著，上面會查 → Pengunci keamanan mesin polishing jangan ditahan pakai benda, atasan akan periksa. "
            "外勞當月沒超過80小時的可以加班 → Pekerja asing yang bulan ini belum lebih 80 jam bisa lembur. "
            "BF3降速生產寫一下交接紀錄簿 不然他們會問 → BF3 produksi turunkan kecepatan, tulis di buku catatan serah terima, nanti mereka tanya. "
            "環境交接簿要記得簽名，他們現在每個禮拜都會檢討 → Buku serah terima lingkungan ingat tanda tangan, sekarang setiap minggu dievaluasi. "
            "護罩跟外勞宣導一下要蓋好 → Sosialisasi ke pekerja Indonesia pelindung mesin harus ditutup rapat. "
            "印勞打錯系統有提示 可是他們看不懂把他按掉了 → Pekerja Indonesia salah input, sistem ada peringatan tapi mereka gak ngerti jadi ditutup aja. "
            "人力出勤要記得掛，昨天沒掛 → Ingat input kehadiran tenaga kerja, kemarin belum diinput. "
            "八月遞延幫忙處理一下 → Order yang ditunda dari Agustus tolong diproses. "
            "要入到2850噸哦，再幫忙一下 → Harus masuk gudang sampai 2850 ton ya, tolong bantu. "
            "幫忙i16重跑雷射 → Tolong ulang laser marking di mesin I16. "
            "先排除砂輪問題 → Singkirkan dulu masalah batu gerinda. "
            "【台灣口語→印尼文 (品質/異常料)】"
            "來料自由端偏小 → Material masuk ujung bebasnya under size. "
            "先不要拋光等削皮班長會驗後再生產 → Jangan polishing dulu, tunggu kepala shift peeling inspeksi bersama baru produksi. "
            "一支端部有異常不收短尺一支分捆轉用 → Satu batang ujung ada abnormal gak terima ukuran pendek, satu batang pisah bundel dialihkan. "
            "品保驗出螺紋幫忙了解一下 → QC menemukan ulir, tolong cek penyebabnya. "
            "單點還是整支性偏小 → Satu titik under size atau seluruh batang under size? "
            "殺光痕嚴重但表粗有過 → Bekas grinding parah tapi surface roughness lulus. "
            "表粗有過目視沒過 → Surface roughness lulus tapi visual tidak lulus. "
            "請協助粗拋重工，感謝 → Tolong bantu rework rough polishing, makasih. "
            "客戶不允收，請協助重工拋光 → Pelanggan tidak terima, tolong bantu rework polishing. "
            "涉及軋輥印痕的批次，請協助開立重工研磨至尺寸下限 → Lot yang kena roll mark, tolong buat WO rework grinding sampai batas bawah ukuran. "
            "不要低於公差下限 → Jangan sampai di bawah batas toleransi bawah. "
            "車刀痕過深研磨未去除 → Bekas pisau bubut terlalu dalam, grinding belum bisa hilangkan. "
            "部分棒材單面無手感車刀痕 導致外觀無一致性 → Sebagian batang satu sisi tidak terasa bekas pisau bubut, menyebabkan tampilan tidak seragam. "
            "拋光色差，布輪修整完會重拋 → Warna polishing tidak rata, setelah dressing roda kain akan polish ulang. "
            "盤元涉及軋輥的風險批，請480站協調入470走ET檢測確認 → Lot wire rod yang kena masalah roll, minta station 480 koordinasi masuk 470 untuk pengujian ET. "
            "護罩要隨時關閉，卡料需關閉電源後再取料 → Pelindung mesin harus selalu ditutup, material macet HARUS matikan listrik dulu baru ambil. "
            "嚴禁運轉中設備直接以手搬動棒材 → DILARANG memindahkan batang baja dengan tangan langsung saat mesin sedang jalan. "
            "報表要記得確實填寫，尤其是雷射校正部分 → Laporan produksi ingat diisi dengan benar, terutama bagian kalibrasi laser. "
            "【台灣口語→印尼文 (冷抽/設備/紀律)】"
            "修磨人員須配戴護目鏡、口罩、手套、耳塞，未依規定者劣項 → Petugas repair grinding WAJIB pakai kacamata pelindung, masker, sarung tangan, earplug, yang melanggar dicatat pelanggaran. "
            "不可側磨已宣導多次，納入劣項 → Larangan side grinding sudah disosialisasi berkali-kali, dicatat sebagai pelanggaran. "
            "飲料瓶以宣導多次，人員扣績效處理 → Soal botol minuman sudah disosialisasi berkali-kali, yang melanggar potong penilaian kinerja. "
            "矯直機前壓輪故障，卡死無法上昇，已請修護協助處理 → Roda tekan depan mesin straightening rusak, macet tidak bisa naik, sudah minta bagian maintenance bantu perbaiki. "
            "儀電sensor異常導致無法上升，查修後說是氣壓缸的問題 → Sensor instrumen listrik error menyebabkan tidak bisa naik, setelah diperiksa ternyata masalah silinder pneumatik. "
            "氣壓缸更換備品回裝完成，測試OK正常生產 → Silinder pneumatik ganti spare part dan pasang kembali selesai, tes OK produksi normal. "
            "產量有落後請幫忙趕上去 → Jumlah produksi tertinggal, tolong bantu kejar. "
            "來料盤元不佳退回線外修磨 → Wire rod masuk kualitas buruk, dikembalikan untuk offline repair grinding. "
            "壓光機跳異常，復歸無效，需叫修儀電查看 → Mesin press polish error, reset gagal, perlu panggil instrumen listrik untuk cek. "
            "請每週掃一次設備上的粉塵，尤其是護蓋 → Tolong bersihkan debu di mesin seminggu sekali, terutama penutup pelindung. "
            "各站不要物逐項清除 → Barang tidak terpakai di tiap stasiun buang satu per satu. "
            "地面漏油、生鏽、掉漆要改善 → Lantai bocor oli, berkarat, cat mengelupas harus diperbaiki. "
            "積水已清完，退火爐冷卻水馬達目前未處理好 → Genangan air sudah dibersihkan, motor air pendingin tungku annealing belum selesai diperbaiki. "
            "以上缺失請逐項改善，明日0700前完成 → Temuan di atas tolong diperbaiki satu per satu, selesai besok sebelum jam 07:00. "
            "E5線速是否過慢，僅2.4～3.6m/min → Kecepatan lini E5 terlalu lambat ya, cuma 2.4-3.6 m/min? "
            "安全圍籬裝置跳異常設備無法啟動，已請儀電人員進廠查修 → Safety fence alarm error mesin gak bisa nyala, sudah minta instrumen listrik masuk pabrik untuk cek. "
            "E1矯直機前壓輪擠料，計長器底座焊接點斷開 → Roda tekan depan mesin straightening E1 material macet keluar, sambungan las dudukan length counter putus. "
            "接線後至切斷機，請以慢速生產，線速10m/min以下 → Setelah sambung kawat sampai mesin potong, tolong produksi pelan, kecepatan lini di bawah 10m/min. "
            "眼模刮傷整修一次，無法改善，更換眼模 → Die tergores, sudah perbaiki sekali tidak membaik, ganti die. "
            "直度不佳無法達到入壓光機的標準 → Straightness buruk tidak memenuhi standar masuk mesin press polish. "
            "砂光機集塵管接頭破損 → Sambungan pipa dust collector mesin sanding rusak. "
            "E11已抽完，要回精整，請放行過帳 → E11 sudah selesai drawing, harus kembali ke finishing, tolong release dan input data. "
            "煙蒂、檳榔渣現先處理 → Puntung rokok dan sisa pinang bersihkan sekarang. "
            "光輝退火爐爐溫表跳亂碼，等請儀電人員一併查修 → Termometer furnace bright annealing tampil error code, tunggu instrumen listrik cek sekalian. "
            "B1主機手績效快歸零了，班長請今日完成缺失改善 → Penilaian kinerja operator utama B1 hampir habis, kepala shift tolong hari ini selesaikan perbaikan temuan. "
            "班長請落實線速要求，目前線速已超過速差標準 → Kepala shift tolong patuhi aturan kecepatan lini, saat ini kecepatan sudah melebihi batas standar. "
            "更換備品後已恢復生產 → Setelah ganti spare part sudah kembali produksi. "
            "在製品管制表已修改上線，請通知有使用之人員重開 → Tabel kontrol WIP sudah diupdate dan online, tolong beritahu pengguna untuk restart. "
            "【印尼文→中文】"
            "Saya mau izin besok → 我明天要請假 "
            "Mesinnya rusak → 機台壞了 "
            "Materialnya udah habis → 料用完了 "
            "Kapan gajinya keluar? → 薪水什麼時候發？ "
            "Saya gak ngerti → 我聽不懂 "
            "Ini harus diganti ya? → 這個要換嗎？ "
            "Boleh pulang duluan? → 可以先下班嗎？ "
            "Lembur sampai jam berapa? → 加班到幾點？ "
            "Bos, ini udah selesai → 老闆，這個好了 "
            "Mau makan dulu → 先去吃飯 "
            "Siap, saya kerjakan → 好的，我來做 "
            "Tadi ada yang jatuh → 剛剛有東西掉了 "
            "Ukurannya gak pas → 尺寸不對 "
            "Stoknya masih ada? → 庫存還有嗎？ "
            "Tolong ajarin saya → 請教我一下"
            + extra_rule +
            " Only output the translation. No quotes, no explanation, no prefix."
        )

        if repair_mode and bad_result:
            msg = (
                "Original text (source language): " + protected + "\n\n"
                "Bad translation that leaked source-language words: " + bad_result + "\n\n"
                "Rewrite the bad translation into pure " + tgt_name +
                ". Preserve names and __MENTION__ placeholders exactly. Translate every remaining source-language word."
            )
        else:
            msg = "Translate from " + src_name + " to " + tgt_name + ": " + protected

        r = oai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": msg}
            ],
            temperature=0.1 if strict_no_source_script or repair_mode else 0.2,
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


def cache_get(text, src, tgt):
    """Get translation from cache if exists and not expired."""
    key = (text.strip(), src, tgt)
    if key in translation_cache:
        result, ts = translation_cache[key]
        if time.time() - ts < CACHE_TTL:
            logger.info("Cache hit: %s -> %s", src, tgt)
            return result
        else:
            del translation_cache[key]
    return None


def cache_set(text, src, tgt, result):
    """Store translation in cache, evict oldest if full."""
    if len(translation_cache) >= CACHE_MAX_SIZE:
        oldest_key = min(translation_cache, key=lambda k: translation_cache[k][1])
        del translation_cache[oldest_key]
    key = (text.strip(), src, tgt)
    translation_cache[key] = (result, time.time())


def translate_with_retry(func, text, src, tgt, max_retries=2):
    """Call a translation function with retry on failure."""
    for attempt in range(max_retries + 1):
        result = func(text, src, tgt)
        if result:
            return result
        if attempt < max_retries:
            wait = 1 * (attempt + 1)
            logger.warning("Retry %d/%d after %ds for %s", attempt + 1, max_retries, wait, func.__name__)
            time.sleep(wait)
    return None


def translate(text, src, tgt):
    # Check cache first
    cached = cache_get(text, src, tgt)
    if cached:
        return cached

    result = translate_with_retry(translate_openai, text, src, tgt, max_retries=2)

    # If source-language leakage is detected, retry with strict mode.
    if result and not is_translation_valid(result, src, tgt):
        logger.warning("Source-language leakage detected in translation, retrying with stricter prompt")
        strict_result = translate_openai(text, src, tgt, strict_no_source_script=True)
        if strict_result and is_translation_valid(strict_result, src, tgt):
            result = strict_result
        else:
            repaired = translate_openai(
                text,
                src,
                tgt,
                strict_no_source_script=True,
                repair_mode=True,
                bad_result=(strict_result or result)
            )
            if repaired and is_translation_valid(repaired, src, tgt):
                result = repaired

    if result and is_translation_valid(result, src, tgt):
        cache_set(text, src, tgt, result)
        return result

    # Fallback to Google with retry.
    result = translate_with_retry(translate_google, text, src, tgt, max_retries=1)
    if result and is_translation_valid(result, src, tgt):
        cache_set(text, src, tgt, result)
        return result

    # Last chance: ask OpenAI to repair the latest output instead of returning a leaked translation.
    if result:
        repaired = translate_openai(
            text,
            src,
            tgt,
            strict_no_source_script=True,
            repair_mode=True,
            bad_result=result
        )
        if repaired and is_translation_valid(repaired, src, tgt):
            cache_set(text, src, tgt, repaired)
            return repaired

    return None


def ocr_image_openai(image_base64):
    """Use OpenAI Vision to extract text from image."""
    if not oai:
        return None
    try:
        r = oai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an OCR assistant. Extract ALL text visible in the image. "
                        "Output ONLY the extracted text, preserving line breaks. "
                        "If there is no text in the image, output exactly: NO_TEXT_FOUND"
                    )
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/jpeg;base64," + image_base64,
                                "detail": "high"
                            }
                        },
                        {
                            "type": "text",
                            "text": "Extract all text from this image."
                        }
                    ]
                }
            ],
            temperature=0.1,
            max_tokens=2000,
        )
        result = r.choices[0].message.content.strip()
        if result == "NO_TEXT_FOUND" or not result:
            return None
        return result
    except Exception as e:
        logger.error("OpenAI Vision OCR error: %s", e)
        return None


def ocr_and_translate_image(image_base64, tgt_lang):
    """OCR + translate image text in one API call, preserving layout."""
    if not oai:
        return None, None
    tgt_name = LANG_NAMES.get(tgt_lang, tgt_lang)
    tgt_flag = LANG_FLAGS.get(tgt_lang, "")
    try:
        r = oai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are an OCR + translation assistant for a factory work group chat.\n"
                        "Task: Extract ALL text from the image, then translate each section.\n\n"
                        "OUTPUT FORMAT - translate each section/paragraph separately:\n"
                        "For each distinct section in the image, output:\n"
                        "【original section title or first line】\n"
                        "original text...\n"
                        + tgt_flag + " translated text...\n"
                        "(blank line before next section)\n\n"
                        "EXAMPLE:\n"
                        "【交辦事項】\n"
                        "1.研磨來料前需紀錄來料三點式尺寸\n"
                        + tgt_flag + " 1.Sebelum grinding material masuk, catat dimensi 3 titik\n\n"
                        "RULES:\n"
                        "1. Keep the SAME structure, numbering, and line breaks as the original.\n"
                        "2. Each section shows original first, then translation with " + tgt_flag + " flag.\n"
                        "3. If there are numbered items (1. 2. 3.), keep the same numbering.\n"
                        "4. Translate naturally, casual daily language for factory workers.\n"
                        "5. Target Traditional Chinese = Taiwan style.\n"
                        "6. NEVER translate person names or company names.\n"
                        "7. If no text found, output exactly: NO_TEXT_FOUND\n"
                        "8. Factory vocabulary: "
                        "交辦事項=hal yang harus dikerjakan, "
                        "研磨=grinding, 拋光=polishing, 來料=material masuk, "
                        "量測=mengukur, 尺寸=diameter/dimensi, 三點式=3 titik, "
                        "雷射=laser, 設備=peralatan, 故障=rusak, "
                        "紀錄=catat, 佳東=Jia Dong, 拋光棒=batang polishing, "
                        "清洗=cuci, 輕調輕放=handle dengan hati-hati, "
                        "環狀擦傷=goresan melingkar, "
                        "重工=rework, 料回削皮=material kembali kupas/peeling, "
                        "補上=lengkapi, C行套環=C-ring, "
                        "廠內=di dalam pabrik, 禁止=dilarang, 餵狗=kasih makan anjing, "
                        "宣導=sosialisasi, "
                        "包裝站=stasiun packing, 啟動=mulai, "
                        "PMI全檢=inspeksi penuh PMI, 抽查機制=sistem sampling, "
                        "每捆=setiap bundel, 鋼種=jenis baja, "
                        "棒材=batang baja, 混料=tercampur material, "
                        "出貨=pengiriman, 依情節=sesuai tingkat pelanggaran, "
                        "增加績效=tambah penilaian kinerja, "
                        "確實=pastikan, 防止=mencegah\n"
                        "9. Only output the result. No extra explanation."
                    )
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/jpeg;base64," + image_base64,
                                "detail": "high"
                            }
                        },
                        {
                            "type": "text",
                            "text": "Extract and translate all text from this image to " + tgt_name + ". Keep the same layout structure."
                        }
                    ]
                }
            ],
            temperature=0.2,
            max_tokens=3000,
        )
        result = r.choices[0].message.content.strip()
        if result == "NO_TEXT_FOUND" or not result:
            return None, None
        return result, None
    except Exception as e:
        logger.error("OpenAI Vision OCR+translate error: %s", e)
        return None, str(e)



def download_line_image(message_id):
    """Download image from LINE and return (base64_string, raw_bytes)."""
    try:
        with ApiClient(configuration) as api_client:
            blob_api = MessagingApiBlob(api_client)
            content = blob_api.get_message_content(message_id)
            img_base64 = base64.b64encode(content).decode("utf-8")
            return img_base64, content
    except Exception as e:
        logger.error("LINE image download error: %s", e)
        return None, None


def download_line_audio(message_id):
    """Download audio from LINE and return bytes."""
    try:
        with ApiClient(configuration) as api_client:
            blob_api = MessagingApiBlob(api_client)
            content = blob_api.get_message_content(message_id)
            return content
    except Exception as e:
        logger.error("LINE audio download error: %s", e)
        return None


def transcribe_audio_openai(audio_bytes):
    """Use OpenAI Whisper to transcribe audio to text."""
    if not oai:
        return None
    try:
        with tempfile.NamedTemporaryFile(suffix=".m4a", delete=True) as tmp:
            tmp.write(audio_bytes)
            tmp.flush()
            tmp.seek(0)
            r = oai.audio.transcriptions.create(
                model="whisper-1",
                file=tmp,
            )
            text = r.text.strip() if r.text else None
            return text
    except Exception as e:
        logger.error("OpenAI Whisper error: %s", e)
        return None


def make_notice(content, target="id"):
    tgt_text = translate(content, "zh", target)
    if not tgt_text:
        tgt_text = "(translation failed)"
    lines = []
    lines.append("\U0001f4e2 \u516c\u544a / Pengumuman")
    lines.append("====================")
    lines.append("\U0001f1f9\U0001f1fc " + content)
    lines.append(LANG_FLAGS.get(target, "") + " " + tgt_text)
    lines.append("====================")
    return "\n".join(lines)


def make_notice_from_other(content, src, target="zh"):
    zh_text = translate(content, src, "zh")
    if not zh_text:
        zh_text = "(translation failed)"
    lines = []
    lines.append("\U0001f4e2 \u516c\u544a / Pengumuman")
    lines.append("====================")
    lines.append("\U0001f1f9\U0001f1fc " + zh_text)
    lines.append(LANG_FLAGS.get(src, "") + " " + content)
    lines.append("====================")
    return "\n".join(lines)


def get_help_text(group_id):
    tgt = group_target_lang.get(group_id, "id")
    tgt_zh = LANG_NAMES_ZH.get(tgt, tgt)
    tgt_flag = LANG_FLAGS.get(tgt, "")
    lines = []
    lines.append("\U0001f310 \u7ffb\u8b6f\u6a5f\u5668\u4eba / Bot Penerjemah")
    lines.append("====================")
    lines.append("/on  - \u958b\u555f\u7ffb\u8b6f / Aktifkan")
    lines.append("/off - \u95dc\u9589\u7ffb\u8b6f / Nonaktifkan")
    lines.append("/img on  - \u958b\u555f\u5716\u7247\u7ffb\u8b6f / Aktifkan terjemahan gambar")
    lines.append("/img off - \u95dc\u9589\u5716\u7247\u7ffb\u8b6f / Nonaktifkan terjemahan gambar")
    lines.append("/voice on  - \u958b\u555f\u8a9e\u97f3\u7ffb\u8b6f / Aktifkan terjemahan suara")
    lines.append("/voice off - \u95dc\u9589\u8a9e\u97f3\u7ffb\u8b6f / Nonaktifkan terjemahan suara")
    lines.append("/skip - \u4e0d\u7ffb\u8b6f\u6211\u7684\u8a0a\u606f / Jangan terjemahkan saya")
    lines.append("/unskip - \u6062\u5fa9\u7ffb\u8b6f\u6211\u7684\u8a0a\u606f / Terjemahkan saya lagi")
    lines.append("/skiplist - \u67e5\u770b\u767d\u540d\u55ae / Lihat daftar skip")
    lines.append("/status - \u67e5\u770b\u72c0\u614b / Cek status")
    lines.append("/lang \u4ee3\u78bc - \u5207\u63db\u76ee\u6a19\u8a9e\u8a00")
    lines.append("/notice \u5167\u5bb9 - \u96d9\u8a9e\u516c\u544a")
    lines.append("/help - \u8aaa\u660e / Bantuan")
    lines.append("====================")
    lines.append("\u8a9e\u8a00\u4ee3\u78bc / Kode bahasa:")
    lines.append("id = \U0001f1ee\U0001f1e9 \u5370\u5c3c\u6587 / Indonesia")
    lines.append("en = \U0001f1ec\U0001f1e7 \u82f1\u6587 / English")
    lines.append("vi = \U0001f1fb\U0001f1f3 \u8d8a\u5357\u6587 / Vietnam")
    lines.append("th = \U0001f1f9\U0001f1ed \u6cf0\u6587 / Thai")
    lines.append("ja = \U0001f1ef\U0001f1f5 \u65e5\u6587 / Jepang")
    lines.append("ko = \U0001f1f0\U0001f1f7 \u97d3\u6587 / Korea")
    lines.append("ms = \U0001f1f2\U0001f1fe \u99ac\u4f86\u6587 / Melayu")
    lines.append("tl = \U0001f1f5\U0001f1ed \u83f2\u5f8b\u8cd3\u6587 / Filipina")
    lines.append("====================")
    lines.append("\u76ee\u524d\u8a2d\u5b9a / Saat ini:")
    lines.append("\u4e2d\u6587 \u2192 " + tgt_flag + " " + tgt_zh)
    lines.append("\u5176\u4ed6\u8a9e\u8a00 \u2192 \U0001f1f9\U0001f1fc \u4e2d\u6587")
    lines.append("====================")
    lines.append("\u7bc4\u4f8b / Contoh:")
    lines.append("/lang en \u2192 \u4e2d\u6587\u7ffb\u82f1\u6587")
    lines.append("/lang id \u2192 \u4e2d\u6587\u7ffb\u5370\u5c3c\u6587")
    return "\n".join(lines)


def handle_lang_command(text, group_id):
    parts = text.strip().split()
    if len(parts) < 2:
        # Show current setting
        tgt = group_target_lang.get(group_id, "id")
        tgt_zh = LANG_NAMES_ZH.get(tgt, tgt)
        tgt_flag = LANG_FLAGS.get(tgt, "")
        lines = []
        lines.append("\u76ee\u524d\u4e2d\u6587\u7ffb\u8b6f\u76ee\u6a19\uff1a" + tgt_flag + " " + tgt_zh)
        lines.append("")
        lines.append("\u5207\u63db\u8acb\u8f38\u5165 / Ketik:")
        lines.append("/lang id \u2192 \u5370\u5c3c\u6587")
        lines.append("/lang en \u2192 \u82f1\u6587")
        lines.append("/lang vi \u2192 \u8d8a\u5357\u6587")
        lines.append("/lang th \u2192 \u6cf0\u6587")
        lines.append("/lang ja \u2192 \u65e5\u6587")
        lines.append("/lang ko \u2192 \u97d3\u6587")
        lines.append("/lang ms \u2192 \u99ac\u4f86\u6587")
        lines.append("/lang tl \u2192 \u83f2\u5f8b\u8cd3\u6587")
        return "\n".join(lines)
    code = parts[1].lower().strip()
    if code not in VALID_TARGETS:
        return "\u26a0\ufe0f \u7121\u6548\u4ee3\u78bc\uff01\u8acb\u7528: id, en, vi, th, ja, ko, ms, tl"
    group_target_lang[group_id] = code
    tgt_zh = LANG_NAMES_ZH.get(code, code)
    tgt_flag = LANG_FLAGS.get(code, "")
    return "\u2705 \u5df2\u5207\u63db\uff1a\u4e2d\u6587 \u2192 " + tgt_flag + " " + tgt_zh + "\n\u5176\u4ed6\u8a9e\u8a00 \u2192 \U0001f1f9\U0001f1fc \u4e2d\u6587"


def handle_command(text, group_id, user_id=None):
    cmd = text.strip().lower()
    if cmd == "/help":
        return get_help_text(group_id)
    elif cmd == "/on":
        group_settings[group_id] = True
        return "\u2705 \u7ffb\u8b6f\u5df2\u958b\u555f / Penerjemah aktif"
    elif cmd == "/off":
        group_settings[group_id] = False
        return "\u274c \u7ffb\u8b6f\u5df2\u95dc\u9589 / Penerjemah nonaktif"
    elif cmd == "/img on":
        group_img_settings[group_id] = True
        return "\u2705 \u5716\u7247\u7ffb\u8b6f\u5df2\u958b\u555f / Terjemahan gambar aktif"
    elif cmd == "/img off":
        group_img_settings[group_id] = False
        return "\u274c \u5716\u7247\u7ffb\u8b6f\u5df2\u95dc\u9589 / Terjemahan gambar nonaktif"
    elif cmd == "/voice on":
        group_audio_settings[group_id] = True
        return "\u2705 \u8a9e\u97f3\u7ffb\u8b6f\u5df2\u958b\u555f / Terjemahan suara aktif"
    elif cmd == "/voice off":
        group_audio_settings[group_id] = False
        return "\u274c \u8a9e\u97f3\u7ffb\u8b6f\u5df2\u95dc\u9589 / Terjemahan suara nonaktif"
    elif cmd == "/skip":
        if not user_id:
            return "\u26a0\ufe0f \u7121\u6cd5\u8b58\u5225\u4f60\u7684\u8eab\u4efd"
        if group_id not in group_skip_users:
            group_skip_users[group_id] = set()
        group_skip_users[group_id].add(user_id)
        return "\u2705 \u5df2\u5c07\u4f60\u52a0\u5165\u767d\u540d\u55ae\uff0c\u4f60\u7684\u8a0a\u606f\u4e0d\u6703\u88ab\u7ffb\u8b6f\nAnda ditambahkan ke daftar skip"
    elif cmd == "/unskip":
        if not user_id:
            return "\u26a0\ufe0f \u7121\u6cd5\u8b58\u5225\u4f60\u7684\u8eab\u4efd"
        if group_id in group_skip_users:
            group_skip_users[group_id].discard(user_id)
        return "\u2705 \u5df2\u5c07\u4f60\u79fb\u51fa\u767d\u540d\u55ae\uff0c\u4f60\u7684\u8a0a\u606f\u6703\u88ab\u7ffb\u8b6f\nAnda dihapus dari daftar skip"
    elif cmd == "/skiplist":
        skipped = group_skip_users.get(group_id, set())
        if not skipped:
            return "\u76ee\u524d\u767d\u540d\u55ae\u662f\u7a7a\u7684 / Daftar skip kosong"
        return "\u23ed\ufe0f \u767d\u540d\u55ae / Daftar skip:\n" + str(len(skipped)) + " \u4eba\u5df2\u8df3\u904e / orang di-skip"
    elif cmd == "/status":
        is_on = group_settings.get(group_id, True)
        tgt = group_target_lang.get(group_id, "id")
        tgt_zh = LANG_NAMES_ZH.get(tgt, tgt)
        tgt_flag = LANG_FLAGS.get(tgt, "")
        if is_on:
            img_on = group_img_settings.get(group_id, True)
            img_status = "\u2705 \u958b\u555f" if img_on else "\u274c \u95dc\u9589"
            audio_on = group_audio_settings.get(group_id, True)
            audio_status = "\u2705 \u958b\u555f" if audio_on else "\u274c \u95dc\u9589"
            return "\u2705 \u7ffb\u8b6f\uff1a\u958b\u555f\u4e2d / Aktif\n\u4e2d\u6587 \u2192 " + tgt_flag + " " + tgt_zh + "\n\U0001f5bc\ufe0f \u5716\u7247\u7ffb\u8b6f\uff1a" + img_status + "\n\U0001f3a4 \u8a9e\u97f3\u7ffb\u8b6f\uff1a" + audio_status
        else:
            return "\u274c \u7ffb\u8b6f\uff1a\u5df2\u95dc\u9589 / Nonaktif"
    elif cmd.startswith("/lang"):
        return handle_lang_command(text, group_id)
    elif text.strip().startswith("/notice ") or text.strip().startswith("/notice\u3000"):
        content = text.strip()[8:].strip()
        if not content:
            return "\u26a0\ufe0f \u8acb\u8f38\u5165\u516c\u544a\u5167\u5bb9\n\u4f8b\u5982 / Contoh: /notice \u660e\u5929\u653e\u5047\u4e00\u5929"
        tgt = group_target_lang.get(group_id, "id")
        if has_chinese(content):
            return make_notice(content, tgt)
        else:
            src = detect_language(content)
            if src and src != "zh":
                return make_notice_from_other(content, src)
            return make_notice(content, tgt)
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
    is_dm = not getattr(source, 'group_id', None) and not getattr(source, 'room_id', None)
    group_id = getattr(source, 'group_id', None) or getattr(source, 'room_id', None) or getattr(source, 'user_id', None)
    user_id = getattr(source, 'user_id', None)

    # --- DM (private message) mode ---
    if is_dm and user_id:
        # DM commands
        cmd = text.strip().lower()
        if cmd == "/help":
            tgt = dm_target_lang.get(user_id, "id")
            tgt_zh = LANG_NAMES_ZH.get(tgt, tgt) if tgt != "zh" else "\u4e2d\u6587"
            tgt_flag = LANG_FLAGS.get(tgt, "")
            lines = []
            lines.append("\U0001f310 \u79c1\u8a0a\u7ffb\u8b6f\u6a21\u5f0f / Mode Terjemahan Pribadi")
            lines.append("====================")
            lines.append("\u50b3\u8a0a\u606f\u7d66\u6211\uff0c\u6211\u6703\u81ea\u52d5\u7ffb\u8b6f\uff01")
            lines.append("Kirim pesan ke saya, akan diterjemahkan!")
            lines.append("")
            lines.append("/to \u4ee3\u78bc - \u8a2d\u5b9a\u7ffb\u8b6f\u76ee\u6a19\u8a9e\u8a00")
            lines.append("/help - \u8aaa\u660e")
            lines.append("====================")
            lines.append("\u8a9e\u8a00\u4ee3\u78bc / Kode bahasa:")
            lines.append("zh = \U0001f1f9\U0001f1fc \u4e2d\u6587")
            lines.append("id = \U0001f1ee\U0001f1e9 \u5370\u5c3c\u6587")
            lines.append("en = \U0001f1ec\U0001f1e7 \u82f1\u6587")
            lines.append("vi = \U0001f1fb\U0001f1f3 \u8d8a\u5357\u6587")
            lines.append("th = \U0001f1f9\U0001f1ed \u6cf0\u6587")
            lines.append("ja = \U0001f1ef\U0001f1f5 \u65e5\u6587")
            lines.append("ko = \U0001f1f0\U0001f1f7 \u97d3\u6587")
            lines.append("ms = \U0001f1f2\U0001f1fe \u99ac\u4f86\u6587")
            lines.append("tl = \U0001f1f5\U0001f1ed \u83f2\u5f8b\u8cd3\u6587")
            lines.append("====================")
            lines.append("\u76ee\u524d\u76ee\u6a19 / Target: " + tgt_flag + " " + tgt_zh)
            lines.append("\u7bc4\u4f8b: /to en \u2192 \u5168\u90e8\u7ffb\u6210\u82f1\u6587")
            with ApiClient(configuration) as api_client:
                api = MessagingApi(api_client)
                api.reply_message(ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="\n".join(lines))]
                ))
            return
        if cmd.startswith("/to"):
            parts = text.strip().split()
            dm_valid = ["zh", "id", "en", "vi", "th", "ja", "ko", "ms", "tl"]
            if len(parts) < 2:
                tgt = dm_target_lang.get(user_id, "id")
                tgt_zh = LANG_NAMES_ZH.get(tgt, tgt) if tgt != "zh" else "\u4e2d\u6587"
                tgt_flag = LANG_FLAGS.get(tgt, "")
                with ApiClient(configuration) as api_client:
                    api = MessagingApi(api_client)
                    api.reply_message(ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="\u76ee\u524d\u76ee\u6a19\uff1a" + tgt_flag + " " + tgt_zh + "\n\u7bc4\u4f8b: /to en")]
                    ))
                return
            code = parts[1].lower().strip()
            if code not in dm_valid:
                with ApiClient(configuration) as api_client:
                    api = MessagingApi(api_client)
                    api.reply_message(ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text="\u26a0\ufe0f \u7121\u6548\u4ee3\u78bc\uff01\u8acb\u7528: zh, id, en, vi, th, ja, ko, ms, tl")]
                    ))
                return
            dm_target_lang[user_id] = code
            tgt_zh = LANG_NAMES_ZH.get(code, code) if code != "zh" else "\u4e2d\u6587"
            tgt_flag = LANG_FLAGS.get(code, "")
            with ApiClient(configuration) as api_client:
                api = MessagingApi(api_client)
                api.reply_message(ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text="\u2705 \u79c1\u8a0a\u7ffb\u8b6f\u76ee\u6a19\uff1a" + tgt_flag + " " + tgt_zh + "\n\u50b3\u8a0a\u606f\u7d66\u6211\u5c31\u6703\u7ffb\u8b6f\uff01")]
                ))
            return
        # DM: skip other / commands
        if text.startswith("/"):
            return

        # DM translation: detect language, translate to target
        lang = detect_language(text)
        tgt = dm_target_lang.get(user_id, "id")
        if lang is None:
            # Cannot detect, just translate to target anyway using OpenAI
            result = translate(text, "auto", tgt)
            if not result:
                return
            reply = LANG_FLAGS.get(tgt, "") + " " + result
        elif lang == tgt:
            # Same language, skip
            return
        else:
            result = translate(text, lang, tgt)
            if not result:
                return
            reply = LANG_FLAGS.get(tgt, "") + " " + result

        with ApiClient(configuration) as api_client:
            api = MessagingApi(api_client)
            api.reply_message(ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply)]
            ))
        return

    # --- Group mode (original logic) ---
    if text.startswith("/"):
        cmd_result = handle_command(text, group_id, user_id)
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

    # Check skip list
    sender_id = getattr(source, 'user_id', None)
    if sender_id and sender_id in group_skip_users.get(group_id, set()):
        return

    if text.startswith("!"):
        return

    lang = detect_language(text)
    if lang is None:
        return

    tgt = group_target_lang.get(group_id, "id")

    reply = None
    if lang == "zh":
        result = translate(text, "zh", tgt)
        if result:
            reply = LANG_FLAGS.get(tgt, "") + " " + result
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


@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image(event):
    """Handle image messages: OCR + translate with layout-preserving text."""
    source = event.source
    group_id = getattr(source, 'group_id', None) or getattr(source, 'room_id', None) or getattr(source, 'user_id', None)

    # Check if translation is on
    is_on = group_settings.get(group_id, True)
    if not is_on:
        return

    # Check skip list
    sender_id = getattr(source, 'user_id', None)
    if sender_id and sender_id in group_skip_users.get(group_id, set()):
        return

    # Check if image translation is on
    img_on = group_img_settings.get(group_id, True)
    if not img_on:
        return

    # Need OpenAI for image OCR
    if not oai:
        logger.warning("No OpenAI key, cannot do image OCR")
        return

    # Download image from LINE
    message_id = event.message.id
    img_base64, img_raw = download_line_image(message_id)
    if not img_base64:
        return

    # Determine target language
    tgt = group_target_lang.get(group_id, "id")

    # Quick OCR to check if there's text and detect language
    extracted = ocr_image_openai(img_base64)
    if not extracted or len(extracted.strip()) < 2:
        return

    lang = detect_language(extracted)
    if lang is None:
        return

    # Determine actual translation target
    if lang == "zh":
        actual_tgt = tgt
    else:
        actual_tgt = "zh"

    # OCR + translate with layout preserved
    result, err = ocr_and_translate_image(img_base64, actual_tgt)
    if not result:
        # Fallback: use plain text translation
        if lang == "zh":
            plain = translate(extracted, "zh", tgt)
        else:
            plain = translate(extracted, lang, "zh")
        if plain:
            result = plain
        else:
            return

    reply = "\U0001f5bc\ufe0f " + LANG_FLAGS.get(actual_tgt, "") + "\n" + result

    # LINE message limit is 5000 chars
    if len(reply) > 5000:
        reply = reply[:4990] + "\n..."

    with ApiClient(configuration) as api_client:
        api = MessagingApi(api_client)
        api.reply_message(ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text=reply)]
        ))


@handler.add(MessageEvent, message=AudioMessageContent)
def handle_audio(event):
    """Handle audio/voice messages: Whisper STT + detect language + translate."""
    source = event.source
    group_id = getattr(source, 'group_id', None) or getattr(source, 'room_id', None) or getattr(source, 'user_id', None)

    # Check if translation is on
    is_on = group_settings.get(group_id, True)
    if not is_on:
        return

    # Check skip list
    sender_id = getattr(source, 'user_id', None)
    if sender_id and sender_id in group_skip_users.get(group_id, set()):
        return

    # Check if audio translation is on
    audio_on = group_audio_settings.get(group_id, True)
    if not audio_on:
        return

    # Need OpenAI for Whisper
    if not oai:
        logger.warning("No OpenAI key, cannot do audio transcription")
        return

    # Download audio from LINE
    message_id = event.message.id
    audio_bytes = download_line_audio(message_id)
    if not audio_bytes:
        return

    # Transcribe with Whisper
    transcribed = transcribe_audio_openai(audio_bytes)
    if not transcribed or len(transcribed.strip()) < 2:
        return

    # Detect language
    lang = detect_language(transcribed)
    if lang is None:
        return

    tgt = group_target_lang.get(group_id, "id")

    reply = None
    if lang == "zh":
        result = translate(transcribed, "zh", tgt)
        if result:
            reply = "\U0001f3a4 " + LANG_FLAGS.get(tgt, "") + "\n\U0001f4ac " + transcribed + "\n\U0001f4dd " + result
    else:
        result = translate(transcribed, lang, "zh")
        if result:
            reply = "\U0001f3a4 " + LANG_FLAGS.get("zh", "") + "\n\U0001f4ac " + transcribed + "\n\U0001f4dd " + result

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
