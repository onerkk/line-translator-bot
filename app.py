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
# Work order photo detection toggle per group, default True
group_wo_settings = {}
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
    # Strip @mentions: English names with optional Chinese nickname
    clean = re.sub(r'@[A-Za-z0-9][A-Za-z0-9 _.-]*(?:\s+[\u4e00-\u9fff]{1,4})?(?=(?:\s|[\n,，。!！?？:：;；()（）\[\]{}<>"“”]|$))', ' ', text)
    # Strip @mentions: Chinese names with optional parenthesized title e.g. @小麥（研磨股班長）
    clean = re.sub(r'@[\u4e00-\u9fff]+(?:\s*[（(][^）)]*[）)])?', ' ', clean)
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
    cleaned = re.sub(r'__CUST_\d+__', ' ', cleaned)
    # Also strip known customer names (they are kept in original language intentionally)
    for name in CUSTOMER_NAMES:
        if name in cleaned:
            cleaned = cleaned.replace(name, ' ')
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


# === Hard replacement tables ===
# These bypass GPT entirely - applied BEFORE sending to GPT (zh->id)
# and AFTER receiving from GPT (id->zh result post-processing)

ZH_TO_ID_HARD = {
    # 製程/站別
    "爐號標籤": "label heat number",
    "爐號": "heat number",
    "無心研磨": "centerless grinding",
    "光輝退火爐": "furnace bright annealing",
    "光輝退火": "bright annealing",
    "退火爐": "tungku annealing",
    "過帳": "input data ke sistem",
    "放行": "release data",
    # 品質/缺陷
    "殺光痕": "bekas grinding mark",
    "車刀痕": "bekas pisau bubut",
    "砂光痕": "bekas sanding mark",
    "軋輥印痕": "bekas roll mark",
    "環狀擦傷": "goresan melingkar",
    "表粗": "surface roughness",
    "偏小": "under size",
    "偏大": "over size",
    "風險批": "lot berisiko",
    "走ET檢測": "jalankan pengujian ET",
    "開立重工": "buat work order rework",
    "不允收": "pelanggan tidak terima",
    # 設備
    "矯直機": "mesin straightening",
    "壓光機": "mesin press polish",
    "砂光機": "mesin sanding",
    "拋光機": "mesin polishing",
    "眼模": "die/cetakan",
    "引拔座": "drawing bench",
    "皮膜槽": "coating tank",
    "氣壓缸": "silinder pneumatik",
    "安全圍籬": "safety fence",
    "集塵設備": "dust collector",
    "計長器": "length counter",
    "冷水機": "chiller",
    "馬蹄環": "shackle",
    "吊掛物": "beban gantung",
    "護罩": "pelindung mesin",
    "interlock": "pengunci keamanan",
    "標籤機": "mesin label",
    # 管理
    "品保": "QC",
    "儲運": "bagian gudang",
    "生計": "production planning",
    "業務": "bagian sales",
    "營業": "bagian sales",
    "人事": "HRD",
    "處長": "kepala divisi",
    "稼動率": "utilization rate",
    "線速": "kecepatan lini",
    "速差": "selisih kecepatan",
    "主機手": "operator utama",
    "印勞": "pekerja Indonesia",
    "在製品管制表": "tabel kontrol WIP",
    # 包裝/入庫
    "套紙管": "pasang tabung kertas",
    "太空包": "jumbo bag",
    "噴漆罐": "kaleng spray",
    "木箱": "kotak kayu",
    "櫃子": "kontainer",
    # 訂單
    "允收": "toleransi terima",
    "訂尺": "panjang pesanan",
    "短尺": "ukuran pendek",
    "異型棒": "batang bentuk khusus",
    "遞延單": "order ditunda",
    "急單": "order urgent",
    "溢量": "kelebihan produksi",
    "併包": "gabung packing",
    "出貨差": "kekurangan pengiriman",
    # HR/紀律
    "忘卡補": "input lewat sistem lupa kartu",
    "造冊": "buat daftar absensi",
    "班股": "rapat shift",
    "堆高機複訓": "pelatihan ulang forklift",
    "天車複訓": "pelatihan ulang crane",
    "扣績效": "potong penilaian kinerja",
    "劣項": "pelanggaran",
    "納入劣項": "dicatat pelanggaran",
    "提報懲處": "laporkan untuk sanksi",
    "三定": "3 tetap",
    "不要物": "barang tidak terpakai",
    "被釘": "kena tegur",
    "綠卡": "kartu hijau",
    # 環境
    "煙蒂": "puntung rokok",
    "檳榔渣": "sisa pinang",
    "廚餘": "sisa makanan",
    "漏油": "bocor oli",
    "積水": "genangan air",
    "粉塵": "debu",
    # 口語
    "感溫": "terima kasih",
    "有夠": "sangat",
    "母湯": "jangan",
}

# Post-replacement: fix common GPT mistakes in output
ID_POST_FIX = {
    # 爐號 corrections
    "nomor panas": "heat number",
    "label nomor panas": "label heat number",
    "nomor tungku": "heat number",
    "label nomor tungku": "label heat number",
    "nomor oven": "heat number",
    "label nomor oven": "label heat number",
    # 有包到 corrections
    "paket datang ke": "kalau ada packing untuk",
    "saat paket datang ke": "kalau ada packing untuk",
    "Mohon diperhatikan saat paket datang ke": "Nanti kalau ada packing untuk",
    "Mohon diperhatikan saat kalau ada packing untuk": "Nanti kalau ada packing untuk",
    # 三米六米 corrections
    "tiga meter di atas enam meter": "batang 3 meter ditaruh di atas batang 6 meter",
    "Tiga meter di atas enam meter": "Batang 3 meter ditaruh di atas batang 6 meter",
    "3 meter di atas 6 meter": "batang 3 meter ditaruh di atas batang 6 meter",
    # 品保 corrections
    "jaminan kualitas": "QC",
    "penjaminan mutu": "QC",
    # 點名 corrections (NOT roll call)
    "panggilan nama": "inspeksi pengawas",
    "absen nama": "inspeksi pengawas",
    "roll call": "inspeksi pengawas",
    # 感溫 - should not be translated literally
    "suhu perasaan": "terima kasih",
    "merasakan suhu": "terima kasih",
    # Common GPT errors
    "Polymetal": "寶麗金屬",
    "Bao Li Metal": "寶麗金屬",
    "Bao Li Logam": "寶麗金屬",
    "Changzhou Zhongshan": "常州眾山",
    "Da Shun": "大順",
    "Da Cheng": "大成",
    "Bei Ze": "北澤",
    "Hong Yun": "鴻運",
    "Tian Hua Rong": "田華榕",
    "Jia Dong": "佳東",
    # 營業 common mistranslation
    "bagian operasional": "bagian sales",
    "operasional perlu": "sales perlu",
}

# Customer names - protect from translation by wrapping
# Storage area lookup data (from 儲區查詢.xlsx)
_STORAGE_JSON = '{"6C422209":[["<=3200","EH28"],[">4200","EG38"],[">3200<=4200","EH26"]],"ABE":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EG34"]],"AIK":[[">3200<=4200","EG14"],[">4200","EH33"],["<=3200","EH28"]],"ALCONIX JP":[["<=3200","EG14"],[">4200","EH33"],[">3200<=4200","EG14"]],"AMERICAN STAINLESS":[[">3200<=4200","EG14"],[">4200","EG34"],["<=3200","EH28"]],"AMS":[[">3200<=4200","EG14"],[">4200","EG34"],["<=3200","EH28"]],"ANCHOR":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EG34"]],"ANIL METALS":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EH33"]],"APEX METAL":[["<=3200","EH28"],[">4200","EH33"],[">3200<=4200","EG14"]],"AWACS":[[">3200<=4200","EG14"],[">4200","EG34"],["<=3200","EH28"]],"B&B":[[">4200","EH33"],["<=3200","EH22"],[">3200<=4200","EG14"]],"B&J":[["<=3200","EC40"],[">4200","EC40"],[">3200<=4200","EC45"]],"BOBCO":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH34"]],"BOLLINGHAUS":[[">3200<=4200","EC43"],["<=3200","EC43"],[">4200","EC43"]],"CA-ASD":[[">4200","EH11"],["<=3200","EH12"],[">3200<=4200","EH12"]],"CA-AUSTRAL":[[">3200<=4200","EH12"],["<=3200","EH12"],[">4200","EH11"]],"CA-DALSTEEL":[[">4200","EH11"],["<=3200","EH12"],[">3200<=4200","EH12"]],"CA-FLETCHER":[[">3200<=4200","EH12"],[">4200","EH11"],["<=3200","EH28"]],"CA-M&S":[["<=3200","EH12"],[">3200<=4200","EH12"],[">4200","EH11"]],"CA-MICO":[["<=3200","EH12"],[">3200<=4200","EH12"],[">4200","EH11"]],"CA-MIDWAY":[["<=3200","EH12"],[">3200<=4200","EH12"],[">4200","EH11"]],"CA-S&T":[["<=3200","EH12"],[">3200<=4200","EH12"],[">4200","EH11"]],"CA-VAN LEEUWEN":[["<=3200","EH12"],[">4200","EH11"],[">3200<=4200","EH12"]],"CA-VES":[["<=3200","EH12"],[">3200<=4200","EH12"],[">4200","EH11"]],"CA-VULCAN":[[">4200","EH11"],["<=3200","EH12"],[">3200<=4200","EH12"]],"CA-VULCAN NZ":[["<=3200","EH12"],[">3200<=4200","EH12"],[">4200","EH11"]],"CA-WAKEFIELD":[[">4200","EH11"],[">3200<=4200","EH12"],["<=3200","EH12"]],"CAMELLIA":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EG34"]],"CASTLE":[[">3200<=4200","EH12"],["<=3200","EH28"],[">4200","EH11"]],"CHANDAN":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EH33"]],"CHANG HSIN":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH33"]],"CHANGSU":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EH33"]],"COGNE AOSTA":[[">3200<=4200","EG34"],["<=3200","EH28"],[">4200","EG14"]],"COGNE CELIK":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EG34"]],"COGNE DE":[[">3200<=4200","EG14"],[">4200","EH34"],["<=3200","EH28"]],"COGNE DG":[[">3200<=4200","EC47"],["<=3200","EC47"],[">4200","EC41"]],"COGNE FR":[[">4200","EH33"],[">3200<=4200","EG14"],["<=3200","EH28"]],"COGNE KR":[["<=3200","EH26"],[">3200<=4200","EG14"],[">4200","EG34"]],"COGNE UK":[[">3200<=4200","EG14"],[">4200","EH34"],["<=3200","EH28"]],"COMINOX":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH33"]],"COMPRINOX":[[">4200","EH33"],[">3200<=4200","EG14"],["<=3200","EH28"]],"CSMU":[["<=3200","EH28"],[">4200","EH33"],[">3200<=4200","EG14"]],"DACAPO":[["<=3200","EH25"],[">3200<=4200","EG14"],[">4200","EH31"]],"DACAPO-K STOCK":[["<=3200","EH25"],[">3200<=4200","EG14"],[">4200","EH31"]],"DAECHANG":[[">4200","EG34"],[">3200<=4200","EG14"],["<=3200","EH28"]],"DAMSTAHL":[[">3200<=4200","EG14"],[">4200","EG34"],["<=3200","EH28"]],"DAVER":[[">3200<=4200","EG14"],[">4200","EG34"],["<=3200","EH28"]],"DK METAL":[[">4200","EG35"],[">3200<=4200","EG14"],["<=3200","EC47"]],"DUFU":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EG34"]],"EGMO":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EH33"]],"EIAM":[[">3200<=4200","EG14"],["<=3200","EG14"],[">4200","EH33"]],"ESP":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH33"]],"EURO STEEL":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH33"]],"FASTENAL":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EH33"]],"FINE METAL TRADE":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH33"]],"FSS":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EH33"]],"G HWA":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EG34"]],"GIC":[[">4200","EH33"],["<=3200","EH28"],[">3200<=4200","EG14"]],"GLH":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EH33"]],"GS METAL":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EH33"]],"HADCO":[[">4200","EH33"],[">3200<=4200","EG14"],["<=3200","EH28"]],"HAKUDO":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EG34"]],"HAMATECH":[[">4200","EG34"],["<=3200","EH28"],[">3200<=4200","EG14"]],"HANWA":[["<=3200","EH21"],[">3200<=4200","EG14"],[">4200","EH33"]],"HEAP SING HUAT":[[">4200","EH33"],["<=3200","EH28"],[">3200<=4200","EG14"]],"HH":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH33"]],"HRMETAL":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EH34"]],"HUA GUAN METAL":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EH33"]],"HWA GUAN METAL":[[">4200","EH33"],[">3200<=4200","EG14"],["<=3200","EH28"]],"IM":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH33"]],"INTEGRITY STAINLESS":[[">4200","EG34"],[">3200<=4200","EG14"],["<=3200","EH28"]],"IPE":[[">4200","EH33"],["<=3200","EH28"],[">3200<=4200","EG14"]],"ISE":[[">4200","EH33"],[">3200<=4200","EG14"],["<=3200","EH28"]],"IWATANI":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EH33"]],"JANG ANN":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH33"]],"JFE SHOJI":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH33"]],"KANGRUI":[[">3200<=4200","EG14"],[">4200","EC45"],["<=3200","EH28"]],"KANSAI":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EH33"]],"KDK":[[">3200<=4200","EG14"],[">4200","EG34"],["<=3200","EH28"]],"KIAN":[[">3200<=4200","EG14"],[">4200","EG34"],["<=3200","EH28"]],"KIM ANN":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EG34"]],"KJ":[[">4200","EG32"],["<=3200","EC47"],[">3200<=4200","EG14"]],"KJ PRECISION":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EH33"]],"KOMINOX AB":[[">4200","EG34"],["<=3200","EH28"],[">3200<=4200","EG14"]],"LAI KING":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH33"]],"LAURIE":[["<=3200","EG14"],[">3200<=4200","EH28"],[">4200","EH33"]],"LE":[[">3200<=4200","EG14"],[">4200","EG34"],["<=3200","EH28"]],"LEE & STEEL":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH33"]],"LIM MENG SENG":[[">3200<=4200","EG14"],[">4200","EG34"],["<=3200","EH28"]],"LINSTER":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EG34"]],"LOTUS METAL":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EG34"]],"LTM":[["<=3200","EG15"],[">3200<=4200","EG15"],[">4200","EG34"]],"M.R. STEEL":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EG34"]],"MAINCHAIN":[[">4200","EG34"],[">3200<=4200","EG14"],["<=3200","EH28"]],"MAN TAK":[[">4200","EG34"],["<=3200","EG15"],[">3200<=4200","EG15"]],"MARINE":[["<=3200","EG14"],[">3200<=4200","EH28"],[">4200","EH33"]],"MCB":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH33"]],"MENAM":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EG34"]],"METAL ESTABLISH":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EG34"]],"METALINOX":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EG34"]],"METALLSERVIS":[[">3200<=4200","EH14"],["<=3200","EH28"],[">4200","EG35"]],"NAKAYAMA":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH33"]],"NDE":[["<=3200","EH28"],[">4000","EG34"],[">4200","EG34"],[">3200<=4200","EG14"]],"NM":[[">3200<=4200","EG14"],["<=3200","EH22"],[">4200","EG34"]],"NMSK":[[">4200","EG34"],["<=3200","EH28"]],"NOVA TRADING":[["<=3200","EH27"],[">4200","EG34"],[">3200<=4200","EG14"]],"NOXFAP":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EG34"]],"NS METAL":[[">3200<=4200","EG14"],["<=3200","EG14"],[">4200","EH18"]],"NSC":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EG34"]],"OKAYA":[[">4200","EG34"],["<=3200","EH28"],[">3200<=4200","EH14"]],"OLYMPIC STEEL":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EG34"]],"OME":[[">4200","EG34"],["<=3200","EH28"],[">3200<=4200","EG14"]],"PACKER(ISRAEL)":[["<=3200","EH28"],[">4200","EG34"],[">3200<=4200","EH14"]],"PASCAL":[[">3200<=4200","EH14"],["<=3200","EH28"],[">4200","EG34"]],"PF":[["<=3200","EH28"],[">3200<=4200","EH14"],[">4200","EG34"]],"PLUTUS":[[">3200<=4200","EI30"],["<=3200","EI25"],[">4200","EI40"]],"PRECISION":[["<=3200","EH28"],[">4200","EH33"],[">3200<=4200","EH14"]],"PRECISION METAL":[[">4200","EH33"],["<=3200","EH28"],[">3200<=4200","EH14"]],"PRECISION METALS":[[">3200<=4200","EH14"],["<=3200","EH28"],[">4200","EH33"]],"QPLUS":[["<=3200","EH28"],[">4200","EH33"],[">3200<=4200","EG14"]],"RAAJRATNA":[[">4200","EG34"],["<=3200","EH28"],[">3200<=4200","EG14"]],"RHS":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EG34"]],"RINO":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EH34"]],"RISEBM":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EG34"]],"SAGAMI":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EG34"]],"SAMWON":[["<=3200","EC47"],[">3200<=4200","EG14"],[">4200","EG32"]],"SCM":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EG34"]],"SCOT":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EI40"]],"SD-BK":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EG34"]],"SD-BKL":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH33"]],"SD-KHS":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EH33"]],"SD-LIM METAL":[[">4200","EG34"],["<=3200","EH28"],[">3200<=4200","EG14"]],"SD-METALPHILE":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EG34"]],"SD-METHA":[[">4200","EH33"],["<=3200","EH28"],[">3200<=4200","EG14"]],"SD-TPS":[[">4200","EH33"],["<=3200","EH28"],[">3200<=4200","EG14"]],"SENG HUAT":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH33"]],"SENG HUAT METALPLEX":[[">4200","EH33"],["<=3200","EH28"],[">3200<=4200","EG14"]],"SGH":[["<=3200","EH28"],[">4200","EH33"],[">3200<=4200","EG14"]],"SHIMIZU MATERIAL":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH34"]],"SHINKO":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH34"]],"SHINKO TH":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH34"]],"SING LEONG-雙馬":[["<=3200","EH28"],[">4200","EH34"],[">3200<=4200","EG14"]],"SLA":[["<=3200","EH28"],[">4200","EH33"],[">3200<=4200","EG14"]],"SMG":[["<=3200","EH28"],[">4200","EG33"],[">3200<=4200","EG14"]],"SPECTROMATRIX":[["<=3200","EH28"],[">4200","EH33"],[">3200<=4200","EG14"]],"STEELINC":[["<=3200","EH28"],[">4200","EG34"],[">3200<=4200","EG14"]],"STEWART":[[">3200<=4200","EG14"],[">4200","EH33"],["<=3200","EH28"]],"STIRLINGS":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EH34"]],"STIRLINGS(5%)":[[">3200<=4200","EG14"],[">4200","EH34"],["<=3200","EH28"]],"STKSTAINLESS":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH33"]],"SUNGEUN":[[">4200","EG33"],["<=3200","EG37"],[">3200<=4200","EG14"]],"SUNGSIL METAL":[[">4200","EG35"],["<=3200","EC47"],[">3200<=4200","EG14"]],"SUPERFIX":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EG34"]],"SUPREME":[[">4200","EG34"],["<=3200","EH28"],[">3200<=4200","EG14"]],"TAN VIET":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH33"]],"TCI":[["<=3200","EH32"],[">3200<=4200","EH32"],[">4200","EH32"]],"TEKPOINT":[[">3200<=4200","EG14"],["<=3200","EG14"],[">4200","EG34"]],"TITAN METALS":[[">4200","EH33"],[">3200<=4200","EG14"],["<=3200","EH28"]],"TK-SCHULTE":[[">4200","EH33"],[">3200<=4200","EG14"],["<=3200","EH22"]],"TKMP":[[">3200<=4200","EG14"],[">4200","EH34"],["<=3200","EH26"]],"TMC":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EG34"]],"TOP SUNNY":[["<=3200","EH28"],[">4200","EG34"],[">3200<=4200","EG14"]],"TOZZHIN THAILAND":[["<=3200","EH28"],[">4200","EG34"],[">3200<=4200","EG14"]],"TSA":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EG34"]],"TSM":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EG34"]],"TUBE SUPPLY":[[">4200","EG34"],[">3200<=4200","EG14"],["<=3200","EH28"]],"TUSCO":[[">3200<=4200","EG15"],[">4200","EG34"],["<=3200","EH28"]],"WESCO":[[">4200","EG34"],[">3200<=4200","EG15"],["<=3200","EH28"]],"WEST COAST":[[">4200","EH33"],["<=3200","EH28"],[">3200<=4200","EG14"]],"WING KEUNG":[[">3200<=4200","EG14"],[">4200","EH33"],["<=3200","EH29"]],"WPS":[[">4200","EH33"],["<=3200","EH28"],[">3200<=4200","EG14"]],"YGS":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH33"]],"YIEH CORP LTD(HK)":[["<=3200","EH28"],[">4200","EG34"],[">3200<=4200","EG14"]],"YONGTA":[[">4200","EH33"],["<=3200","EH28"],[">3200<=4200","EG14"]],"YOSHU":[[">4200","EH33"],["<=3200","EH28"],[">3200<=4200","EG14"]],"YOUCHANG":[[">4200","EG34"],[">3200<=4200","EG14"],["<=3200","EH28"]],"YOUNG DONG":[[">3200<=4200","EG15"],["<=3200","EG15"],[">4200","EH33"]],"？頂":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"？暉":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"力常(觀音)":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"三大興":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"三卯鍛壓":[[">3200<=4200","EH78"],[">4200","EG38"],["<=3200","EH79"]],"三利":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"上晉":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"上海凡斯":[["<=3200","EC47"],[">4200","EC40"],[">3200<=4200","EC45"]],"上海坤成":[["<=3200","EC47"],[">3200<=4200","EC40"],[">4200","EC40"]],"上海億科":[[">3200<=4200","EC40"],[">4200","EC40"],["<=3200","EC47"]],"上海町芃":[["<=3200","EH10"],[">4200","EH10"],[">3200<=4200","EH10"]],"上銀":[["<=3200","EH99"],[">4200","EC40"],[">3200<=4200","EH99"]],"凡立":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"千里眼":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"大甲永和":[[">3200<=4200","EH78"],[">4200","EG38"],["<=3200","EH79"]],"大成":[[">4200","EH32"],[">3200<=4200","EH32"],["<=3200","EH32"]],"大連德邁仕":[["<=3200","EC47"],[">3200<=4200","EC47"],[">4200","EC40"]],"大順":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"中國防蝕":[[">4200","EH35"],[">3200<=4200","EH78"],["<=3200","EH79"]],"元盈":[[">4200","EG38"],["<=3200","EH79"],[">3200<=4200","EH78"]],"元偉勝":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"升暘":[["<=3200","EH79"],[">4200","EG38"],[">3200<=4200","EG38"]],"天津隆德":[[">4200","EC40"],["<=3200","EC47"],[">3200<=4200","EC40"]],"方鉦":[[">3200<=4200","EH72"],[">4200","EH72"],["<=3200","EH79"]],"世廷":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"世華":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"功億":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"北澤":[[">4200","EG38"],[">3200<=4200","EG39"],["<=3200","EG39"]],"北澤一廠":[["<=3200","EG39"],[">3200<=4200","EG39"],[">4200","EG38"]],"北澤二廠":[[">4200","EG38"],["<=3200","EG39"],[">3200<=4200","EG38"]],"北澤三廠":[["<=3200","EG39"],[">3200<=4200","EG38"],[">4200","EG38"]],"右勝鋼鐵":[[">3200<=4200","EH78"],["<=3200","EG39"],[">4200","EH71"]],"台芝":[[">4200","EH10"],["<=3200","EH10"],[">3200<=4200","EH10"]],"台灣亞錁":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"台灣林吉":[[">4200","EG38"],[">3200<=4200","EH78"],["<=3200","EH79"]],"台灣矽微":[[">4200","EG38"],["<=3200","EH79"],[">3200<=4200","EH78"]],"巨昌":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"巨頻":[[">3200<=4200","EG14"],[">4200","EG38"],["<=3200","EH79"]],"永川泰":[[">3200<=4200","EH78"],[">4200","EG38"],["<=3200","EH79"]],"永村":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"禾桀":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EH38"]],"光翔":[["<=3200","EH79"],[">4200","EG38"],[">3200<=4200","EH78"]],"全利金屬":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"全敏尖端":[[">3200<=4200","EH78"],[">4200","EG38"],["<=3200","EH79"]],"向春":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"名威":[[">4200","EG38"],[">3200<=4200","EH78"],["<=3200","EH79"]],"合順":[[">4200","EG38"],["<=3200","EH79"],[">3200<=4200","EH78"]],"宇隆":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"宇慶":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"有光":[[">4200","EG38"],["<=3200","EH79"],[">3200<=4200","EH78"]],"江陰外庫":[["<=3200","EC47"],[">3200<=4200","EC47"],[">4200","EC40"]],"江陰華新":[[">4200","EC40"],[">3200<=4200","EC40"],["<=3200","EC47"]],"江蘇迪威":[[">4200","EC40"],[">3200<=4200","EC47"],["<=3200","EC47"]],"汎新":[[">3200<=4200","EH78"],[">4200","EG38"],["<=3200","EH79"]],"百呈":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"百堅":[[">3200<=4200","EG37"],[">4200","EH33"],["<=3200","EG14"]],"西邁金屬":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EH33"]],"君立":[["<=3200","EH79"],[">4200","EH36"],[">3200<=4200","EH78"]],"壯安":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"宏盈":[[">4200","EG38"],["<=3200","EH79"],[">3200<=4200","EH78"]],"宏荃":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"志典":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EH33"]],"志聯":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"甫剛":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"貝加":[[">4200","EG38"],[">3200<=4200","EH78"],["<=3200","EH79"]],"貝克休斯":[[">3200<=4200","EG38"],[">4200","EG38"],["<=3200","EG38"]],"京碼":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"京鋼":[[">4200","EC41"],[">3200<=4200","EH78"],["<=3200","EH79"]],"佳東":[[">3200<=4200","EH76"],["<=3200","EH76"],[">4200","EH70"]],"佳東-台中":[[">4200","EH70"],["<=3200","EH76"],[">3200<=4200","EH78"]],"佳東-台北":[[">4200","EH70"],[">3200<=4200","EH78"],["<=3200","EH76"]],"佳東-高雄":[[">3200<=4200","EH78"],[">4200","EH70"],["<=3200","EH76"]],"協崎":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"坤泰":[[">4200","EG38"],[">3200<=4200","EH78"],["<=3200","EH79"]],"奇賓":[[">4200","EG38"],["<=3200","EH79"],[">3200<=4200","EG38"]],"孟駿":[[">3200<=4200","EH78"],[">4200","EG38"],["<=3200","EH79"]],"尚智":[["<=3200","EH79"],[">4200","EG38"],[">3200<=4200","EH78"]],"岡山東穎":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"承總":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"易隆":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"昆山金富盈":[[">3200<=4200","EC40"],["<=3200","EC47"],[">4200","EC40"]],"明石":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"東栗":[[">3200<=4200","EH78"],[">4200","EG38"],["<=3200","EH79"]],"東莞峰作":[["<=3200","EC47"],[">3200<=4200","EC40"],[">4200","EC40"]],"東萊":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"東徽":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EH33"]],"武漢機械":[[">4200","EG38"],["<=3200","EH79"],[">3200<=4200","EH78"]],"金大":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"金利山":[[">4200","EG38"],[">3200<=4200","EH78"],["<=3200","EH79"]],"金亞洲":[[">3200<=4200","EH78"],[">4200","EG38"],["<=3200","EH79"]],"金城":[[">4200","EG38"],["<=3200","EH79"],[">3200<=4200","EH78"]],"金耘":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EH71"]],"金耘-南營所":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EH71"]],"金煜":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EH71"]],"長盈":[[">4200","EG38"],[">3200<=4200","EG14"],["<=3200","EH79"]],"長圓":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"俊來(蘆洲)":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"俊益":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"厚群":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"威孚高科技":[["<=3200","EC47"],[">3200<=4200","EC47"],[">4200","EC40"]],"建新":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"柏緯":[[">3200<=4200","EC47"],[">4200","EC42"],["<=3200","EC47"]],"津展":[["<=3200","EH75"],[">4200","EH71"],[">3200<=4200","EH71"]],"津展-台中":[[">4200","EH72"],["<=3200","EH75"],[">3200<=4200","EH72"]],"津展-台北":[[">3200<=4200","EH72"],[">4200","EH72"],["<=3200","EH75"]],"津展-台南":[[">3200<=4200","EH72"],["<=3200","EH75"],[">4200","EH72"]],"皇銘":[[">3200<=4200","EH78"],[">4200","EG38"],["<=3200","EH79"]],"科威聯":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"英鈿":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"重慶九勝":[[">4200","EC40"],["<=3200","EC47"],[">3200<=4200","EC47"]],"重慶九環":[[">4200","EC40"],["<=3200","EC47"],[">3200<=4200","EC40"]],"展舵":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"峰作金屬":[["<=3200","EH74"],[">3200<=4200","EH78"],[">4200","EH71"]],"峰勝":[[">3200<=4200","EH78"],[">4200","EG38"],["<=3200","EH79"]],"振家":[[">4200","EG38"],["<=3200","EH79"],[">3200<=4200","EH78"]],"振華興":[[">4200","EG38"],["<=3200","EH79"],[">3200<=4200","EH78"]],"時哲":[["<=3200","EH79"],[">4200","EG38"],[">3200<=4200","EH78"]],"晉易":[[">4200","EH38"],[">3200<=4200","EH78"],["<=3200","EH79"]],"晉椿":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EH38"]],"晉椿(鹿港)":[[">4200","EH38"],[">3200<=4200","EH78"],["<=3200","EH79"]],"浙江三花":[[">4200","EG34"],["<=3200","EH28"],[">3200<=4200","EG14"]],"益陽":[["<=3200","EH79"],[">4200","EG38"],[">3200<=4200","EH78"]],"高立熱處理":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"高銪":[[">4200","EG38"],["<=3200","EH79"],[">3200<=4200","EH78"]],"商旺":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"域鑫科技":[[">4200","EC40"]],"常州眾山":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EC43"]],"強淞":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"強實":[["<=3200","EH79"],[">4200","EG38"],[">3200<=4200","EH78"]],"捷流":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"淳康":[[">3200<=4200","EH10"],["<=3200","EH10"],[">4200","EH10"]],"眾山":[[">3200<=4200","EH78"],[">4200","EG35"],["<=3200","EH79"]],"祥日達":[[">3200<=4200","EH78"],[">4200","EG38"],["<=3200","EH79"]],"祥英":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"笠源":[[">4200","EG38"],[">3200<=4200","EH78"],["<=3200","EH79"]],"頂翔勝":[[">4200","EG38"],[">3200<=4200","EH78"],["<=3200","EH79"]],"麥億":[[">3200<=4200","EH78"],[">4200","EG38"],["<=3200","EH79"]],"凱記":[["<=3200","EH79"],[">4200","EG38"],[">3200<=4200","EH78"]],"勝初":[[">3200<=4200","EH78"],[">4200","EG38"],["<=3200","EH79"]],"勝新":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"勝盟":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"富億鑫":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"尊茂":[[">4200","EG38"],[">3200<=4200","EH78"],["<=3200","EH79"]],"復盛應用":[[">3200<=4200","EH78"],[">4200","EG38"],["<=3200","EH78"]],"敦壹":[[">4200","EG38"],[">3200<=4200","EH78"],["<=3200","EH79"]],"朝盟":[[">4200","EG38"],[">3200<=4200","EH78"],["<=3200","EH79"]],"無錫永雋":[["<=3200","EH28"],[">3200<=4200","EG14"],[">4200","EH33"]],"舜欽":[[">4200","EG38"],["<=3200","EH79"],[">3200<=4200","EH78"]],"華友(外)":[[">3200<=4200","EG14"],["<=3200","EH28"],[">4200","EH34"]],"華纜":[[">4200","EH10"],[">3200<=4200","EH10"],["<=3200","EH10"]],"詠勗":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"詠晟":[["<=3200","EH79"],[">3200<=4200","EC47"],[">4200","EC40"]],"進達":[[">4200","EG38"],[">3200<=4200","EH78"],["<=3200","EH79"]],"開滋":[["<=3200","EG39"],[">3200<=4200","EH78"],[">4200","EG38"]],"隆明":[[">3200<=4200","EH78"],[">4200","EG38"],["<=3200","EH79"]],"隆門":[[">4200","EG38"],[">3200<=4200","EH28"],["<=3200","EH28"]],"隆順發":[[">4200","EG38"],["<=3200","EH79"],[">3200<=4200","EH78"]],"雅信億":[[">3200<=4200","EH14"],["<=3200","EH79"],[">4200","EH33"]],"廉喬":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"廉錩":[[">4200","EG38"],[">3200<=4200","EH78"],["<=3200","EH77"]],"廉錩-台北":[[">3200<=4200","EH78"],["<=3200","EH77"],[">4200","EG38"]],"廉錩-台南":[[">4200","EG38"],[">3200<=4200","EH78"],["<=3200","EH77"]],"慈溪龍華":[[">4200","EC40"],[">3200<=4200","EC40"],["<=3200","EC47"]],"新創捷":[["<=3200","EH79"],[">3200<=4200","EH14"],[">4200","EH33"]],"新華特聯":[["<=3200","EH79"],[">3200<=4200","EH14"],[">4200","EH35"]],"新萊應材":[[">3200<=4200","EH78"],[">4200","EG38"],["<=3200","EH79"]],"瑞鋼":[[">4200","EC40"],[">3200<=4200","EC40"],["<=3200","EC47"]],"盟鉦":[[">4200","EG38"],[">3200<=4200","EH78"],["<=3200","EH79"]],"萬揚":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EH33"]],"經捷":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"經貿":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"群鎰":[[">4200","EG38"],["<=3200","EH79"],[">3200<=4200","EH78"]],"聖泰":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"路竹新益":[[">4200","EG38"],["<=3200","EH79"],[">3200<=4200","EH78"]],"鉅泰昇":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EH36"]],"鉅銅":[["<=3200","EH79"],[">4200","EG38"],[">3200<=4200","EH78"]],"鉅豐":[["<=3200","EH79"]],"鼎崴":[[">3200<=4200","EH78"],[">4200","EG38"],["<=3200","EH79"]],"嘉冠":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"嘉碁":[[">4200","EG38"],[">3200<=4200","EH78"],["<=3200","EH79"]],"寧波東葛":[[">3200<=4200","EC40"],["<=3200","EC47"],[">4200","EC40"]],"慷倫":[[">3200<=4200","EH78"],[">4200","EG38"],["<=3200","EH79"]],"睿緻佳":[["<=3200","EH78"],[">4200","EG38"],[">3200<=4200","EH78"]],"福泉":[[">4200","EG38"],[">3200<=4200","EH78"],["<=3200","EH79"]],"聚祥":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"銓宥":[[">4200","EH10"],[">3200<=4200","EH10"],["<=3200","EH10"]],"廣泰":[[">4200","EH10"],[">3200<=4200","EH10"],["<=3200","EH10"]],"慶鋐":[[">3200<=4200","EH78"],[">4200","EG38"],["<=3200","EH79"]],"歐承":[[">4200","EG38"],["<=3200","EH79"],[">3200<=4200","EH78"]],"毅鋼":[[">4200","EG38"],["<=3200","EH79"],[">3200<=4200","EH78"]],"磐石":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"誼山":[["<=3200","EH29"],[">3200<=4200","EH78"],[">4200","EH38"]],"頭份":[[">4200","EG38"],[">3200<=4200","EH78"],["<=3200","EH79"]],"優普洛":[["<=3200","EH79"],[">3200<=4200","EH78"],[">4200","EG38"]],"環友":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"聯岱":[[">4200","EG38"],["<=3200","EH79"],[">3200<=4200","EH78"]],"聯祥":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"邁達斯":[[">4200","EG38"],["<=3200","EH79"],[">3200<=4200","EH78"]],"鴻運":[[">3200<=4200","EH27"],["<=3200","EH27"],[">4200","EG38"]],"雙和":[[">3200<=4200","EG14"],["<=3200","EH26"],[">4200","EG34"]],"麒譯":[["<=3200","EH79"],[">4200","EG38"],[">3200<=4200","EH78"]],"町洋":[["<=3200","EH79"],[">4200","EG38"],[">3200<=4200","EH78"]],"晟田":[["<=3200","EH79"],[">4200","EG38"],[">3200<=4200","EH78"]],"畯圓":[["<=3200","EH19"],[">4200","EG38"],[">3200<=4200","EH78"]],"鐿順發":[["<=3200","EH79"],[">4200","EG38"],[">3200<=4200","EH78"]],"鑫誠鐵材":[["<=3200","EH79"],[">4200","EG38"],[">3200<=4200","EH78"]],"恒耀":[["<=3200","EH79"],[">4200","EG38"],[">3200<=4200","EH78"]],"暉":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]],"頂":[[">3200<=4200","EH78"],["<=3200","EH79"],[">4200","EG38"]]}'
STORAGE_LOOKUP = json.loads(_STORAGE_JSON)
# Extra customers not in storage Excel but appear in factory chat
EXTRA_CUSTOMERS = [
    "寶麗金屬", "田華榕", "蘋果", "賽利金屬", "盛昌遠", "曜麟",
    "LOTUS", "LOTUS METAL", "shinko", "wing keung",
]
CUSTOMER_NAMES = sorted(list(set(list(STORAGE_LOOKUP.keys()) + EXTRA_CUSTOMERS)), key=lambda x: -len(x))


def pre_replace_zh(text):
    """Apply hard replacements to Chinese text before GPT translation.
    Returns (modified_text, customer_placeholders_dict)."""
    result = text
    # Protect customer names with placeholders (these survive GPT translation)
    cust_ph = {}
    for i, name in enumerate(CUSTOMER_NAMES):
        if name in result:
            ph = f"__CUST_{i}__"
            cust_ph[ph] = name
            result = result.replace(name, ph)
    # Apply hard replacements (longest first to avoid partial matches)
    for zh, replacement in sorted(ZH_TO_ID_HARD.items(), key=lambda x: -len(x[0])):
        if zh in result:
            result = result.replace(zh, f"[{replacement}]")
    return result, cust_ph


def restore_customers(text, cust_ph):
    """Restore customer name placeholders back to original names."""
    if not text or not cust_ph:
        return text
    result = text
    for ph, name in cust_ph.items():
        # GPT might mangle placeholders, try variants
        idx = ph.replace("__CUST_", "").replace("__", "")
        variants = [
            ph, ph.replace("_", " "), f"CUST_{idx}", f"CUST {idx}",
            f"__CUST {idx}__", f"[CUST_{idx}]",
        ]
        for v in variants:
            if v in result:
                result = result.replace(v, name)
    # Safety: if any customer name placeholder pattern remains, try regex
    result = re.sub(r'__CUST_(\d+)__', lambda m: cust_ph.get(f"__CUST_{m.group(1)}__", m.group(0)), result)
    return result


def post_fix_translation(text):
    """Fix known GPT translation mistakes in output."""
    if not text:
        return text
    result = text
    # Fix specific wrong translations (longest match first)
    for wrong, correct in sorted(ID_POST_FIX.items(), key=lambda x: -len(x[0])):
        result = result.replace(wrong, correct)
    # Remove bracketed hints that leaked through from pre_replace
    result = re.sub(r'\[([a-zA-Z /&]+)\]', r'\1', result)
    # Clean up double spaces
    result = re.sub(r'\s{2,}', ' ', result).strip()
    return result


def translate_openai(text, src, tgt, strict_no_source_script=False, repair_mode=False, bad_result=None):
    if not oai:
        return None
    try:
        src_name = LANG_NAMES.get(src, src)
        tgt_name = LANG_NAMES.get(tgt, tgt)

        # Apply hard replacements before GPT for zh->other
        input_text = text
        cust_placeholders = {}
        if src == "zh":
            input_text, cust_placeholders = pre_replace_zh(text)

        protected, placeholders = protect_mentions(input_text)

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
            "1. NEVER translate @mentions and NEVER translate or romanize person names. Keep all Chinese names in ORIGINAL CHINESE CHARACTERS. "
            "For example: 徐嘉騰 stays as 徐嘉騰, NOT Xu Jiateng. 陳弘林 stays as 陳弘林, NOT Chen Honglin. "
            "Chinese nicknames for people must stay unchanged. Do NOT translate them literally. "
            "2. Any text like __MENTION_0__, __MENTION_1__ etc are placeholders - keep them exactly as is. "
            "3. Translate all other content completely and naturally like real people talk at work. Use casual daily language. "
            "4. Indonesian slang: gak=tidak, udah=sudah, gimana=bagaimana, bgt=banget, org=orang, yg=yang, tdk=tidak, dg=dengan, krn=karena, blm=belum, hrs=harus, bs=bisa, lg=lagi, gw=saya, lu=kamu. "
            "5. TAIWANESE MANDARIN COLLOQUIAL (very important): "
            "乾/干=aduh/astaga, 靠=astaga/waduh, 幹=sial/buset, 傻眼=gak percaya, 扯/誇張=keterlaluan, 笑死=ngakak, 氣死=kesel banget, 累死=capek banget, "
            "啦=lah/dong, 喔/哦=ya/lho, 耶=dong/nih, 嘛=dong/kan, 蛤=hah?/apa?, 厚=ya kan, "
            "醬/降=begitu/gitu(=這樣), 母湯=jangan/gak boleh(=不要), 超/有夠=banget(=非常), 感溫=terima kasih(台語感恩), "
            "CRITICAL: Taiwanese rhetorical questions SUGGEST doing something: 需不需要X=perlu X gak nih(suggesting X should be done), 要不要X=gimana kalau X, 還在X=masih X(often implies criticism). "
            "搞什麼=ngapain sih, 搞定=beres, 人咧=orangnya mana, 怎麼搞的=kenapa bisa begini, 出包=ada masalah, 先這樣=segitu dulu ya, 再說=nanti aja, "
            "X到不行/X得要死/X到爆=X banget, 怎麼這麼X=kok X banget, 有夠X=X banget, "
            "ㄏㄏ=haha, QQ=sedih, 3Q=terima kasih, GG=tamat, XD=haha, @@=bingung. "
            "6. Target Traditional Chinese = Taiwan style, not mainland. "
            "7. Target Indonesian = simple clear daily language for factory workers. "
            "8. Context: factory work - shifts, overtime, orders, tasks, meals, breaks, meetings, exams. "
            "9. FACTORY VOCABULARY: "
            "【製程/Process】"
            "無心研磨=centerless grinding, 研磨=grinding, 砂輪=batu gerinda, 調整輪=roda pengatur, 刀板=work rest blade, 冷卻液=cairan pendingin, "
            "不鏽鋼=stainless steel, 棒鋼=steel bar, 盤元=wire rod, 削皮棒=peeled bar, 冷精棒=cold-drawn bar, "
            "熱軋=hot rolling, 退火=annealing, 酸洗=pickling, 削皮=peeling, 冷抽=cold drawing, "
            "鋼種=jenis baja, PMI=uji material, 來料=material masuk, 棒材=batang baja, 混料=tercampur material(SERIOUS), 料號=nomor material, "
            "拋光=polishing, 粗拋=rough polishing, 噴漆=spray paint, 洗料=cuci material, "
            "倒角=chamfer, 修磨=repair grinding, 盤元修磨=repair grinding wire rod, 線外修磨=offline repair grinding, "
            "壓光=press polish, 矯直=straightening, 重矯=straightening ulang, 精整=finishing, AP=mesin finishing, "
            "光輝退火=bright annealing, 回爐=kirim kembali ke furnace, "
            "側磨=side grinding(DILARANG/prohibited), 不可側磨=dilarang side grinding, "
            "【站別/Stations - numbers are STATION NUMBERS】"
            "400站=station 400, 401站=station 401, 420站=station 420, "
            "470站=station 470(UT station), UT=mesin UT(di station 470), 480站=station 480, "
            "490站=station 490(秤重站/timbang), 801站=station 801, "
            "OL=sedang produksi/online, 回400=kembalikan ke station 400, "
            "無主=tanpa pemilik/unassigned, 入無主=masukkan ke status tanpa pemilik, "
            "掛單/工單=work order, 重掛單=pasang ulang work order, 無工單資訊=tidak ada info work order, "
            "改制=ubah proses, 去化=ada order baru mau terima, 有單去化=ada order baru untuk serap material, 改制去化=ubah proses produksi, "
            "帳/帳務=data administrasi(ERP), 帳已回400=data sudah dikembalikan ke station 400, "
            "過帳=input data produksi(jumlah&berat)ke sistem tanpa release ke stasiun berikutnya, "
            "放行=release data ke stasiun berikutnya(setelah QC lulus), "
            "退庫=kembalikan ke gudang, 退庫拆包=keluarkan dari gudang bongkar packing untuk dibagi ulang, "
            "發料=issue material, 存檔=simpan data, 暫存=simpan sementara, 短尺=ukuran pendek, "
            "溢量=kelebihan produksi melebihi permintaan, 併包=gabung packing dari lot berbeda dalam order sama, "
            "出貨差=kekurangan pengiriman hari ini, 轉用=dialihkan untuk order lain, 跳無主轉用=pindah ke tanpa pemilik lalu dialihkan, "
            "【班次/出勤】"
            "點名=ada pengawas yang datang(inspection, NOT roll call), 早班=shift pagi, 夜班=shift malam, 中班=shift siang, "
            "加班=lembur, 排班=jadwal shift, 調班=tukar shift, 上班=masuk kerja, 下班=pulang kerja, 打卡=absen, "
            "請假=izin, 病假=izin sakit, 事假=izin pribadi, 特休=cuti tahunan, 代班=gantikan shift, "
            "忘卡補=lupa kartu ID, pakai sistem input waktu, 造冊=buat daftar absensi, "
            "班股=rapat shift, 堆高機複訓=pelatihan ulang forklift, 天車複訓=pelatihan ulang crane, "
            "紅包=angpao, 年終獎金=bonus akhir tahun, 過年不停機=Imlek tidak berhenti produksi, "
            "【產線/設備】"
            "產線=lini produksi, 機台=mesin, 開機=nyalakan mesin, 停機=mesin berhenti, 調機=setting mesin, "
            "上料=isi material, 備料=siapkan material, 產量=jumlah produksi, 目標=target, 達標=capai target, 超產=over production, "
            "訂單=order, 出貨=kirim barang, 交期=deadline, 趕貨=kejar order, 急單=order urgent, 急單備註=catatan order urgent, "
            "下製程=proses selanjutnya, 異常=abnormal/ada masalah, 維修中=sedang diperbaiki, "
            "天車=overhead crane, 台車=trolley, 吊秤=timbangan gantung, 馬蹄環=shackle, 鋼索=sling baja, 吊掛物=beban gantung, "
            "稼動率=utilization rate, 線速=line speed(m/min), 限速=batas kecepatan, 降速=turunkan kecepatan, 提速=naikkan kecepatan, 速差=selisih kecepatan, "
            "撥料=feed material, 過機=lewatkan mesin, 線外=offline, 印勞=pekerja Indonesia, "
            "砂光機=sanding machine, 眼模=die/cetakan drawing, 引拔座=drawing bench, 皮膜槽=coating tank, "
            "查修=investigasi&perbaiki, 修護=maintenance, 儀電=instrumen listrik, 備品=spare part, "
            "跳異常=error muncul, 復歸=reset, 復歸無效=reset gagal, 跳機=mesin trip, 恢復生產=kembali produksi, "
            "叫修=panggil teknisi, 進廠查修=teknisi masuk pabrik cek, 電聯儀電=hubungi instrumen listrik, "
            "斷料=material putus, 卡料=material macet, 擠料=material terjepit keluar, "
            "主機手=operator utama, 上料人員=petugas pengisian material, 點檢=cek rutin, 護罩=pelindung mesin/safety guard, "
            "interlock=pengunci keamanan(jangan ditahan pakai benda), "
            "【包裝/入庫】"
            "套紙管=pasang tabung kertas, 入庫=masuk gudang, 優先包裝入庫=prioritas packing masuk gudang, "
            "需求單=formulir permintaan, 可以全收=bisa diterima semua, "
            "櫃子=kontainer(shipping container), 櫃子在路上=kontainer sedang di jalan, "
            "木箱=kotak kayu, 裝箱=masukkan ke kotak kayu, 2700大的木箱=kotak kayu ukuran besar 2700mm, "
            "NOTE: 木箱 context: 3200/2400=box LENGTH mm, 500/1000=weight CAPACITY kg. "
            "把=bundel(bundle), 捆=bundel/ikat, 支/根=batang(piece/rod), 批=lot/batch, "
            "NOTE: X米(三米,六米)=batang X meter(bar LENGTH not distance). 三米上面放六米=batang 3m ditaruh di atas batang 6m. "
            "包(verb)=packing/kemas(NOT wrapping). 秤重=timbang, 貼標=tempel label, 綁鐵=ikat besi, "
            "【訂單管理】"
            "允收=jumlah yang boleh diterima pelanggan, 允收0支=zero tolerance, 不收短尺=tidak terima ukuran pendek, "
            "訂尺=panjang sesuai pesanan, 爐號=heat number/nomor furnace(NEVER translate as 'panas'), 爐號標籤=label heat number, "
            "分捆=pisah bundel, 遞延單=delayed order, 非本月=bukan order bulan ini, "
            "非本月不入庫=order bukan bulan ini jangan masuk gudang, 檔非本月=tahan order bukan bulan ini, "
            "異型棒=batang bentuk khusus, 異型棒不擋=batang khusus tidak dibatasi, "
            "入庫目標=target masuk gudang, 壓日期=ada deadline ketat, "
            "管控=kontrol, 不管控=tidak dikontrol(bebas), "
            "【品質/缺陷】"
            "品保=QC, 會驗=joint inspection, 暫留=hold sementara, HOLD=tahan, "
            "客訴=komplain pelanggan, 夾帶樣品=sertakan sampel, 掛檔=simpan ke arsip, 稽核=audit, "
            "螺紋=thread mark, 車刀痕=turning tool mark, 砂光痕=sanding mark, 殺光痕=grinding mark, "
            "剝片=flaking, 軋輥印痕=roll mark, 碰傷=luka benturan, 黑皮=unfinished surface, "
            "偏小=under size, 偏大=over size, 表粗=surface roughness, 目視=visual inspection, "
            "開立重工=buat WO rework, 重工研磨至尺寸下限=rework grinding sampai batas bawah ukuran, "
            "不允收=pelanggan tidak terima, 風險批=lot berisiko, 走ET檢測=jalankan pengujian ET, "
            "卡料需關閉電源後再取料=material macet HARUS matikan listrik dulu baru ambil, "
            "【部門/人員】"
            "業務=sales, 營業=sales(=業務), 生計=production planning, 資訊=IT department, 品保=QC, 儲運=gudang&logistik, 人事=HRD, 工安=safety officer, "
            "處長=kepala divisi, 抓資料=ambil data, "
            "【標籤/系統】"
            "TAG=label, 儲區=area penyimpanan di sistem, 轉檔=konversi data, "
            "MES=MES(sistem produksi), 報表=laporan produksi, 條碼=barcode, "
            "標籤機=mesin label, 包裝電腦=komputer packing, "
            "在製品管制表=WIP control sheet, "
            "【安全/環境/紀律】"
            "太空包=jumbo bag/FIBC, 噴漆罐一定要打洞才能丟棄在太空包=kaleng spray HARUS dilubangi sebelum buang ke jumbo bag, "
            "扣績效=potong kinerja(sanksi), 劣項=pelanggaran, 納入劣項=dicatat pelanggaran, "
            "三定=3 tetap(tempat/barang/jumlah tetap), 不要物=barang tidak terpakai, "
            "漏油=bocor oli, 生鏽=berkarat, 掉漆=cat mengelupas, 積水=genangan air, 粉塵=debu, "
            "煙蒂=puntung rokok, 檳榔渣=sisa pinang, 被釘=kena tegur atasan, "
            "提報懲處=laporkan untuk sanksi, 會嚴罰=dihukum berat, "
            "綠卡=kartu hijau(catatan safety), KYT=pelatihan prediksi bahaya, 防火演練=latihan pemadam kebakaran, "
            "調班單=formulir tukar shift, 簽核=tanda tangan persetujuan, "
            "【生活/薪資】"
            "宿舍=asrama, 便當=bekal makan, 餵狗=kasih makan anjing, "
            "薪水=gaji, 加班費=uang lembur, 績效=penilaian kinerja, 匯款=transfer, "
            "尾牙=pesta akhir tahun, 春酒=pesta tahun baru, 伴手禮=oleh-oleh, 便當費=biaya makan siang, "
            "量測=mengukur, 尺寸=diameter, 公差=toleransi, 校正=kalibrasi, "
            "【客戶 - NEVER translate】"
            "DACAPO, CASTLE, LOTUS, METALINOX, KANGRUI, SUNGEUN, STEELINC, GLH, shinko, wing keung, "
            "田華榕, 佳東, 蘋果, 常州眾山, 大順, 大成, 巨昌, 北澤, 鴻運, 畯圓, 名威, 右勝, 貝克休斯, 皇銘, "
            "台芝, 百堅, 津展, 曜麟, 廉錩, 盛昌遠, 永吉, 光輝, 寶麗金屬. "
            "NOTE: 蘋果=customer NOT fruit. 光輝=customer OR 光輝退火(bright annealing), context determines. "
            "10. CRITICAL CONTEXT RULES: "
            "a) X米(三米,六米)=bar LENGTH. 三米上面放六米=batang 3m ditaruh di atas batang 6m. "
            "b) 把/捆=BUNDLE counters. 包2把=packing 2 bundel. "
            "c) 包(verb)=packing NOT wrapping. 高侑的今天包2把都這樣=Yang di-packing 高侑 hari ini 2 bundel semuanya kayak gini. "
            "d) Names(高侑,十元,小麥,啊堂,秋情,政軒,碩凱,汶錡,武駿,凱銘,小趙,阿澤,法比恩,山多,EggEgg,fang,Dato潘)=keep as-is. "
            "e) Customer names=keep as-is, do NOT translate. "
            "f) R+number=round bar diameter(R28.57=bulat 28.57mm). Non-R=hex/special(H26=hex 26mm). "
            "g) S/B=straight bar. E1~E11=cold drawing lines. I1~I21=grinding machines. BF2/3/5=polishing machines. "
            "h) 5F/5L/6S/6T/6U/6W/7E/7F/7G+numbers=work order ID, keep as-is. "
            "i) 課料=section chief designated material. G包=packing method code. AP=finishing equipment. "
            "j) 爐號=heat number(NEVER 'nomor panas'). 有包到X=kalau ada packing untuk X(NOT 'paket datang ke X'). "
            "11. TRANSLATION EXAMPLES (follow strictly): "
            "【中→印尼】"
            "乾 需不需要提報一下 → Aduh, perlu dilaporkan gak nih? "
            "UT囤一堆料了 → UT udah numpuk banyak material. "
            "品保還在下班 誇張 → QC udah pulang, keterlaluan. "
            "三米上面放六米 → Batang 3 meter ditaruh di atas batang 6 meter. "
            "麻煩他們不要這樣放料 → Tolong bilang ke mereka jangan taruh material kayak gini. "
            "高侑的今天包2把都這樣 → Yang di-packing 高侑 hari ini 2 bundel semuanya kayak gini. "
            "來料都短少4-5公斤 → Material masuk semuanya kurang 4-5 kilogram. "
            "已轉達 → Sudah disampaikan. "
            "這批料有問題 → Lot material ini ada masalah. "
            "幫我盯一下 → Tolong awasin ya. "
            "怎麼搞的啦 → Kok bisa kayak gini sih. "
            "人咧 → Orangnya mana? "
            "辛苦了 → Makasih kerja kerasnya. "
            "靠 又壞了 → Astaga, rusak lagi. "
            "先這樣 → Segitu dulu ya. "
            "叫他快點 → Suruh dia cepatan. "
            "砂輪要換了 → Batu gerinda harus diganti. "
            "公差超過了 → Toleransinya udah lewat. "
            "這6把再麻煩今晚入庫 → 6 bundel ini tolong masukin gudang malam ini. "
            "明早業務要抓資料 謝謝 → Besok pagi sales perlu ambil data, makasih. "
            "BF2拋光機維修中 → Mesin polishing BF2 sedang diperbaiki. "
            "44.45前天有跟妳說超產，業務回覆了嗎 → Diameter 44.45 kemarin sudah bilang over produksi, sales udah balas belum? "
            "噴漆後照訂單量拆包 → Setelah spray paint, bagi packing sesuai jumlah order. "
            "品保點錯製程，麻煩退回400-無主 → QC salah pilih proses, tolong kembalikan ke station 400 tanpa pemilik. "
            "帳已回400、料要回去那一個單位？ → Data sudah dikembalikan ke 400, materialnya mau ke unit mana? "
            "去削皮退火 感溫 → Ke proses peeling dan annealing, makasih. "
            "7F414020 請幫放至480轉用收回400，要改制去化，謝謝 → 7F414020 tolong pindahkan ke station 480, lalu kembalikan ke 400, mau ubah proses, makasih. "
            "業務說收～ 請包～ → Sales bilang terima, tolong di-packing. "
            "班長～ 7F656502A 這把溢量請再入無主～ 謝謝! → Kepala shift, 7F656502A bundel ini kelebihan, tolong masukkan ke tanpa pemilik, makasih! "
            "客需求支數7支、不收短 來料只有6支、其中一支短、剔除掉剩5支、能包嘛？ → Pelanggan minta 7 batang, gak terima pendek. Masuk cuma 6, 1 pendek dibuang sisa 5, bisa packing gak? "
            "因為櫃子在路上 9點到 這樣可能可以等一下入庫 → Karena kontainer sedang di jalan, sampai jam 9, mungkin bisa tunggu sebentar baru masuk gudang. "
            "DACAPO都入完了 → DACAPO semuanya sudah masuk gudang. "
            "班長～ 請用2700大的木箱裝，再麻煩幫我抓一下幾點會好，業務下午要出，謝謝 → Kepala shift, tolong pakai kotak kayu 2700, cek jam berapa selesai, sales sore mau kirim, makasih. "
            "那就是帳沒入到 → Berarti datanya belum masuk ke sistem. "
            "資料異常，凱銘在處理了 → Data ada masalah, 凱銘 sedang urus. "
            "研磨排程已更新，急單再麻煩安排洗料拋光 謝謝 → Jadwal grinding diupdate, order urgent tolong atur cuci material dan polishing, makasih. "
            "粗拋完已放行 → Rough polishing selesai, sudah di-release. "
            "今日出貨差 DACAPO 7G63837在490 7G687108A在420 OL → Hari ini pengiriman kurang: DACAPO 7G63837 di 490, 7G687108A di 420 sedang produksi. "
            "METALINOX 差2噸等等K4會在出料 可以的在幫包裝 感謝 → METALINOX kurang 2 ton, nanti K4 keluarkan material, kalau bisa tolong packing, makasih. "
            "7G108519D 請幫收回400，有單去化 謝謝 → 7G108519D tolong kembalikan ke 400, ada order baru untuk serap material, makasih. "
            "洗給E7拋了 → Sudah dicuci dan dikasih ke E7 untuk polishing. "
            "包裝遇到常州眾山再注意這個料號，剛接單後續才會投料生產，此訂單不收短尺需將短尺分捆 → Kalau packing ketemu 常州眾山 perhatikan nomor material ini, baru terima order nanti baru produksi, order ini gak terima pendek harus pisah bundel. "
            "剛剛開會決議過年不停機，如果A班D班出勤人數不夠12人，想賺紅包可以代班 → Rapat keputusan Imlek tidak stop, shift A D kurang 12 orang, mau angpao bisa gantikan shift. "
            "人事有通知堆高機複訓課程，1/29 1700-2000三樓會議室。當天來上課就好，加班時數改天用忘卡補 → HRD info pelatihan forklift, 29/1 jam 17-20 ruang rapat lt.3. Datang ikut aja, jam lembur diinput lewat sistem lupa kartu di hari lain. "
            "處長走了 → Kepala divisi sudah pergi. "
            "有壓日期的急單再幫忙處理一下，很多未到站，拋光會一邊產出 → Order urgent deadline tolong diproses, banyak belum sampai, polishing produksi sambil jalan. "
            "噴漆罐一定要打洞才能丟棄在太空包，本週被查核兩次缺失 → Kaleng spray HARUS dilubangi baru buang ke jumbo bag, minggu ini kena audit 2 kali. "
            "本月入庫目標2950，異型棒不擋，其餘非本月不入庫 → Target gudang 2950, batang khusus bebas, sisanya bukan bulan ini jangan masuk. "
            "本月入庫目標量已達標，目前只入急單、異型棒跟二月以前的遞延單 → Target tercapai, sekarang hanya urgent, batang khusus, dan order ditunda sebelum Feb. "
            "今天沒點名，昨天來過了 → Hari ini gak ada inspeksi, kemarin sudah datang. "
            "應該是上週四D班，傍晚要注意一下小趙跟處長行蹤，免得凱銘被釘 → Harusnya shift D Kamis kemarin, sore perhatikan 小趙 dan kepala divisi, supaya 凱銘 gak kena tegur. "
            "自己稍微看一下設備的料源，有料就是要生產。月底我們不可能是停機的單位 → Cek material di mesin masing-masing, ada material ya produksi. Akhir bulan kita gak boleh mesin berhenti. "
            "之後有包到寶麗金屬注意一下，有一批訂單會備註客戶不要爐號標籤 → Nanti kalau ada packing untuk 寶麗金屬 perhatikan, ada order dicatat pelanggan tidak mau label heat number. "
            "非本月只有異型棒不管控，其他麻煩不要入了，昨天早班沒管控被檢討 → Bukan bulan ini cuma batang khusus bebas, sisanya jangan masuk, shift pagi kemarin gak kontrol kena tegur. "
            "開天車務必遵守規定目視吊掛物 → Operasi crane WAJIB lihat beban gantung sesuai aturan. "
            "護罩跟外勞宣導一下要蓋好 → Sosialisasi ke pekerja Indonesia pelindung mesin harus ditutup rapat. "
            "印勞打錯系統有提示 可是他們看不懂把他按掉了 → Pekerja Indonesia salah input, sistem ada peringatan tapi mereka gak ngerti jadi ditutup. "
            "拋光機interlock都不要拿東西擋著，上面會查 → Pengunci keamanan polishing jangan ditahan pakai benda, atasan akan periksa. "
            "來料自由端偏小 → Material masuk ujung bebasnya under size. "
            "殺光痕嚴重但表粗有過 → Bekas grinding parah tapi surface roughness lulus. "
            "表粗有過目視沒過 → Surface roughness lulus tapi visual tidak lulus. "
            "涉及軋輥印痕的批次，請協助開立重工研磨至尺寸下限 → Lot kena roll mark, tolong buat WO rework grinding sampai batas bawah ukuran. "
            "護罩要隨時關閉，卡料需關閉電源後再取料 → Pelindung mesin harus ditutup, material macet HARUS matikan listrik dulu baru ambil. "
            "嚴禁運轉中設備直接以手搬動棒材 → DILARANG pindahkan batang baja dengan tangan saat mesin jalan. "
            "矯直機前壓輪故障，卡死無法上昇，已請修護協助處理 → Roda tekan straightening rusak macet, sudah minta maintenance bantu. "
            "氣壓缸更換備品回裝完成，測試OK正常生產 → Silinder pneumatik ganti spare part selesai, tes OK produksi normal. "
            "來料盤元不佳退回線外修磨 → Wire rod masuk kualitas buruk, dikembalikan offline repair grinding. "
            "不可側磨已宣導多次，納入劣項 → Larangan side grinding sudah disosialisasi berkali-kali, dicatat pelanggaran. "
            "E5線速是否過慢，僅2.4～3.6m/min → Kecepatan lini E5 terlalu lambat, cuma 2.4-3.6 m/min? "
            "眼模刮傷整修一次，無法改善，更換眼模 → Die tergores, perbaiki sekali tidak membaik, ganti die. "
            "E11已抽完，要回精整，請放行過帳 → E11 selesai drawing, harus kembali ke finishing, tolong release dan input data. "
            "更換備品後已恢復生產 → Setelah ganti spare part sudah kembali produksi. "
            "報表要記得確實填寫，尤其是雷射校正部分 → Laporan produksi ingat diisi benar, terutama bagian kalibrasi laser. "
            "幫追料 → Tolong kejar materialnya. "
            "幫追帳 → Tolong kejar data administrasinya. "
            "已2900別入帳了噢 → Sudah 2900 jangan masukkan data lagi ya. "
            "【印尼→中文】"
            "Saya mau izin besok → 我明天要請假 "
            "Mesinnya rusak → 機台壞了 "
            "Materialnya udah habis → 料用完了 "
            "Kapan gajinya keluar? → 薪水什麼時候發？ "
            "Saya gak ngerti → 我聽不懂 "
            "Boleh pulang duluan? → 可以先下班嗎？ "
            "Lembur sampai jam berapa? → 加班到幾點？ "
            "Bos, ini udah selesai → 老闆，這個好了 "
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
        # Fix known GPT translation mistakes and restore customer names
        if src == "zh":
            result = post_fix_translation(result)
            result = restore_customers(result, cust_placeholders)
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
            # Fix known translation mistakes
            result = post_fix_translation(result)
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


def detect_work_order(ocr_text):
    """Detect if OCR text is from a factory work order (製造指示書).
    Returns customer name if detected, None otherwise."""
    if not ocr_text:
        return None
    wo_keywords = ["冷精棒製造指示書", "製造指示書", "訂單編號", "客戶名稱", "成品尺寸",
                   "FINAL流程", "FINAL", "MIC_NO", "ID_NO", "HRITABPDIL", "退火代碼",
                   "冷精棒", "收貨人", "短尺", "品保", "特殊", "削皮", "訂單資訊",
                   "成品尺寸MIN", "成品尺寸MAX", "製造指示"]
    keyword_count = sum(1 for kw in wo_keywords if kw in ocr_text)
    logger.info("Work order detection: %d keywords matched in OCR text (%d chars)", keyword_count, len(ocr_text))
    if keyword_count < 2:
        return None
    # Try multiple patterns to extract customer name
    patterns = [
        r'客戶名稱[:\s：]*([^\s\n|,，]+)',
        r'客戶[:\s：]*([^\s\n|,，]+)',
        r'客[户戶]名[称稱][:\s：]*([^\s\n|,，]+)',
    ]
    for pat in patterns:
        m = re.search(pat, ocr_text)
        if m:
            customer = m.group(1).strip()
            if customer and len(customer) >= 2:
                logger.info("Work order customer detected: %s", customer)
                return customer
    # Fallback: try to match any known customer name in the text
    for name in CUSTOMER_NAMES:
        if len(name) >= 2 and name in ocr_text:
            logger.info("Work order customer matched from list: %s", name)
            return name
    logger.info("Work order detected but no customer name found")
    return None


def format_length_zh(code):
    """Convert length code to Chinese."""
    if code == "<=3200":
        return "未滿3200"
    elif code == ">4200":
        return "超過4200"
    elif code == ">3200<=4200":
        return "3200～4200"
    elif code == ">4000":
        return "超過4000"
    else:
        c = code.replace("<=", "未滿").replace(">=", "超過").replace(">", "超過").replace("<", "未滿")
        return c


def format_storage_for_work_order(customer_name):
    """Format storage lookup for work order image detection."""
    entries = STORAGE_LOOKUP.get(customer_name)
    if not entries:
        for key in STORAGE_LOOKUP:
            if key.lower() == customer_name.lower() or customer_name in key or key in customer_name:
                entries = STORAGE_LOOKUP[key]
                customer_name = key
                break
    if not entries:
        return None
    lines = []
    lines.append("\U0001f4cb \u5de5\u55ae\u5075\u6e2c")
    lines.append("\u5ba2\u6236\uff1a" + customer_name)
    lines.append("")
    lines.append("\U0001f4e6 \u5132\u5340\u67e5\u8a62")
    lines.append("=" * 18)
    for length, area in entries:
        zh = format_length_zh(length)
        lines.append(zh + " \u2192 " + area)
    lines.append("=" * 18)
    return "\n".join(lines)


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
                        "OUTPUT FORMAT:\n"
                        "For each distinct section/paragraph in the image, output:\n"
                        "original text...\n"
                        + tgt_flag + " translated text...\n"
                        "(blank line before next section)\n\n"
                        "EXAMPLE:\n"
                        "1.研磨來料前需紀錄來料三點式尺寸\n"
                        + tgt_flag + " 1.Sebelum grinding material masuk, catat dimensi 3 titik\n\n"
                        "2.拋光棒需清洗\n"
                        + tgt_flag + " 2.Batang polishing harus dicuci\n\n"
                        "RULES:\n"
                        "1. Keep the SAME structure, numbering, and line breaks as the original.\n"
                        "2. Each section: original text first, then translation with " + tgt_flag + " flag. Do NOT add section titles or brackets.\n"
                        "3. If there are numbered items (1. 2. 3.), keep the same numbering.\n"
                        "4. Do NOT repeat the original text. Show it only ONCE then show the translation.\n"
                        "5. Translate naturally, casual daily language for factory workers.\n"
                        "6. Target Traditional Chinese = Taiwan style.\n"
                        "7. NEVER translate or romanize person names. Keep Chinese names in original Chinese characters (e.g. 陳弘林 stays as 陳弘林, NOT Chen Honglin). Do NOT convert to pinyin.\n"
                        "7b. NEVER translate customer/company names. Keep them EXACTLY as-is: "
                        "賽利金屬, 寶麗金屬, 田華榕, 佳東, 蘋果, 常州眾山, 大順, 大成, 巨昌, 北澤, 鴻運, 畯圓, 名威, 右勝, "
                        "貝克休斯, 皇銘, 台芝, 百堅, 津展, 曜麟, 廉錩, 盛昌遠, 永吉, 光輝, "
                        "DACAPO, CASTLE, LOTUS, METALINOX, KANGRUI, SUNGEUN, STEELINC, GLH, SHINKO, WING KEUNG, "
                        "BOLLINGHAUS, COGNE, TCI, PLUTUS, SAMWON, DK METAL, KJ. "
                        "If you see ANY company name in the image, keep it unchanged. Do NOT translate 金屬=metal, 鋼鐵=steel etc. when part of a company name.\n"
                        "8. If no text found, output exactly: NO_TEXT_FOUND\n"
                        "9. TABLES/SPREADSHEETS: If the image is a table or spreadsheet, output it as a COMPACT table. "
                        "Only translate column headers and labels. Keep person names as-is in original characters. "
                        "Keep numbers as-is. Use a simple format like:\n"
                        "姓名/Nama | 3/17止/Hingga 3/17\n"
                        "陳弘林 | -600\n"
                        "蔡佳佳 | 200\n"
                        "Do NOT output each cell as a separate translated section. Keep it compact.\n"
                        "10. Factory vocabulary: "
                        "交辦事項=hal yang harus dikerjakan, "
                        "研磨=grinding, 無心研磨=centerless grinding, 拋光=polishing, 來料=material masuk, "
                        "量測=mengukur, 尺寸=diameter/dimensi, 三點式=3 titik, "
                        "雷射=laser, 設備=peralatan, 故障=rusak, "
                        "紀錄=catat, 拋光棒=batang polishing, "
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
                        "確實=pastikan, 防止=mencegah, "
                        "精整=finishing, AP=mesin finishing, 矯直=straightening, 壓光=press polish, "
                        "退火=annealing, 光輝退火=bright annealing, 酸洗=pickling, 削皮=peeling, 冷抽=cold drawing, "
                        "熱軋=hot rolling, 煉鋼=steelmaking/peleburan baja, 碳廠=pabrik karbon, "
                        "職安署=Dinas K3(inspeksi keselamatan kerja), 查核=audit/inspeksi, "
                        "品保=QC, 儲運=gudang&logistik, 生計=production planning, 業務=sales, 營業=sales, 人事=HRD, "
                        "處長=kepala divisi, 點名=inspeksi pengawas(NOT roll call), "
                        "加班=lembur, 排班=jadwal shift, 早班=shift pagi, 夜班=shift malam, "
                        "砂輪=batu gerinda, 天車=overhead crane, 堆高機=forklift, "
                        "油桶=drum oli, 太空包=jumbo bag, 噴漆罐=kaleng spray, "
                        "入庫=masuk gudang, 退庫=kembalikan ke gudang, 出貨差=kekurangan pengiriman, "
                        "掛單/工單=work order, 重掛單=pasang ulang work order, 取樣=ambil sampel, "
                        "二道門=pintu kedua(gate 2), 捐血=donor darah, "
                        "爐號=heat number(NEVER nomor panas), 過帳=input data ke sistem, 放行=release data\n"
                        "11. Only output the result. No extra explanation."
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
    sep = "=" * 18
    lines = []
    lines.append("\U0001f4e2 \u516c\u544a / Pengumuman")
    lines.append(sep)
    lines.append("\U0001f1f9\U0001f1fc " + content)
    lines.append(LANG_FLAGS.get(target, "") + " " + tgt_text)
    lines.append(sep)
    return "\n".join(lines)


def make_notice_from_other(content, src, target="zh"):
    zh_text = translate(content, src, "zh")
    if not zh_text:
        zh_text = "(translation failed)"
    sep = "=" * 18
    lines = []
    lines.append("\U0001f4e2 \u516c\u544a / Pengumuman")
    lines.append(sep)
    lines.append("\U0001f1f9\U0001f1fc " + zh_text)
    lines.append(LANG_FLAGS.get(src, "") + " " + content)
    lines.append(sep)
    return "\n".join(lines)


def get_help_text(group_id):
    tgt = group_target_lang.get(group_id, "id")
    tgt_zh = LANG_NAMES_ZH.get(tgt, tgt)
    tgt_flag = LANG_FLAGS.get(tgt, "")
    sep = "=" * 18
    lines = []
    lines.append("\U0001f310 \u7ffb\u8b6f\u6a5f\u5668\u4eba")
    lines.append(sep)
    lines.append("\u3010\u958b\u95dc\u3011")
    lines.append("/on \u30fb /off \u7ffb\u8b6f")
    lines.append("/img on\u30fboff \u5716\u7247")
    lines.append("/voice on\u30fboff \u8a9e\u97f3")
    lines.append("/wo on\u30fboff \u62cd\u5de5\u55ae\u67e5\u5132\u5340")
    lines.append("\u3010\u500b\u4eba\u3011")
    lines.append("/skip \u4e0d\u7ffb\u8b6f\u6211")
    lines.append("/unskip \u6062\u5fa9\u7ffb\u8b6f")
    lines.append("/skiplist \u767d\u540d\u55ae")
    lines.append("\u3010\u529f\u80fd\u3011")
    lines.append("/lang \u4ee3\u78bc \u5207\u63db\u8a9e\u8a00")
    lines.append("/notice \u5167\u5bb9 \u96d9\u8a9e\u516c\u544a")
    lines.append("/qry \u5ba2\u6236 \u67e5\u5132\u5340")
    lines.append("/status \u67e5\u770b\u72c0\u614b")
    lines.append("\U0001f4f7 \u62cd\u5de5\u55ae\u2192\u81ea\u52d5\u67e5\u5132\u5340")
    lines.append(sep)
    lines.append("\u8a9e\u8a00\u4ee3\u78bc:")
    lines.append("id\u5370\u5c3c en\u82f1 vi\u8d8a th\u6cf0")
    lines.append("ja\u65e5 ko\u97d3 ms\u99ac\u4f86 tl\u83f2")
    lines.append(sep)
    lines.append("\u76ee\u524d: \u4e2d\u6587\u2192" + tgt_flag + tgt_zh)
    lines.append("\u7bc4\u4f8b: /lang en")
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


def handle_qry_command(text):
    """Handle /qry <customer_name> command to lookup storage area."""
    parts = text.strip().split(None, 1)
    if len(parts) < 2:
        return "\u26a0\ufe0f \u8acb\u8f38\u5165\u5ba2\u6236\u540d\u7a31\n\u7bc4\u4f8b: /qry ABE\n\u7bc4\u4f8b: /qry \u4f73\u6771"
    query = parts[1].strip()
    # Try exact match first
    entries = STORAGE_LOOKUP.get(query)
    # Try case-insensitive match
    if not entries:
        for key in STORAGE_LOOKUP:
            if key.lower() == query.lower():
                entries = STORAGE_LOOKUP[key]
                query = key
                break
    # Try partial match
    if not entries:
        matches = [k for k in STORAGE_LOOKUP if query.lower() in k.lower() or query in k]
        if len(matches) == 1:
            query = matches[0]
            entries = STORAGE_LOOKUP[query]
        elif len(matches) > 1:
            result = "\U0001f50d \u627e\u5230\u591a\u7b46\u7b26\u5408:\n"
            for m in matches[:10]:
                result += "  \u2022 " + m + "\n"
            if len(matches) > 10:
                result += "  ...(\u5171" + str(len(matches)) + "\u7b46)\n"
            result += "\n\u8acb\u8f38\u5165\u5b8c\u6574\u5ba2\u6236\u540d\u7a31"
            return result
    if not entries:
        return "\u274c \u627e\u4e0d\u5230\u5ba2\u6236: " + query + "\n\u8acb\u78ba\u8a8d\u540d\u7a31\u662f\u5426\u6b63\u78ba"
    # Build response
    lines = []
    lines.append("\U0001f4e6 " + query + " \u5132\u5340\u67e5\u8a62")
    lines.append("=" * 18)
    for length, area in entries:
        zh = format_length_zh(length)
        lines.append(zh + " \u2192 " + area)
    lines.append("=" * 18)
    return "\n".join(lines)


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
    elif cmd == "/wo on":
        group_wo_settings[group_id] = True
        return "\u2705 \u62cd\u5de5\u55ae\u67e5\u5132\u5340\u5df2\u958b\u555f"
    elif cmd == "/wo off":
        group_wo_settings[group_id] = False
        return "\u274c \u62cd\u5de5\u55ae\u67e5\u5132\u5340\u5df2\u95dc\u9589"
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
            wo_on = group_wo_settings.get(group_id, True)
            wo_status = "\u2705 \u958b\u555f" if wo_on else "\u274c \u95dc\u9589"
            return "\u2705 \u7ffb\u8b6f\uff1a\u958b\u555f\u4e2d / Aktif\n\u4e2d\u6587 \u2192 " + tgt_flag + " " + tgt_zh + "\n\U0001f5bc\ufe0f \u5716\u7247\u7ffb\u8b6f\uff1a" + img_status + "\n\U0001f3a4 \u8a9e\u97f3\u7ffb\u8b6f\uff1a" + audio_status + "\n\U0001f4cb \u62cd\u5de5\u55ae\u67e5\u5132\u5340\uff1a" + wo_status
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
    elif text.strip().lower().startswith("/qry"):
        return handle_qry_command(text)
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
            sep = "=" * 18
            lines = []
            lines.append("\U0001f310 \u79c1\u8a0a\u7ffb\u8b6f\u6a21\u5f0f")
            lines.append(sep)
            lines.append("\u50b3\u8a0a\u606f\u7d66\u6211\u5c31\u6703\u7ffb\u8b6f\uff01")
            lines.append("")
            lines.append("/to \u4ee3\u78bc \u8a2d\u5b9a\u76ee\u6a19\u8a9e\u8a00")
            lines.append("/qry \u5ba2\u6236 \u67e5\u5132\u5340")
            lines.append("\U0001f4f7 \u62cd\u5de5\u55ae\u2192\u81ea\u52d5\u67e5\u5132\u5340")
            lines.append(sep)
            lines.append("\u8a9e\u8a00: zh\u4e2d id\u5370\u5c3c en\u82f1")
            lines.append("vi\u8d8a th\u6cf0 ja\u65e5 ko\u97d3 ms\u99ac tl\u83f2")
            lines.append(sep)
            lines.append("\u76ee\u524d: " + tgt_flag + tgt_zh)
            lines.append("\u7bc4\u4f8b: /to en")
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
        # DM: handle /qry command
        if text.strip().lower().startswith("/qry"):
            qry_result = handle_qry_command(text)
            if qry_result:
                with ApiClient(configuration) as api_client:
                    api = MessagingApi(api_client)
                    api.reply_message(ReplyMessageRequest(
                        reply_token=event.reply_token,
                        messages=[TextMessage(text=qry_result)]
                    ))
            return
        # DM: skip other / commands
        if text.startswith("/"):
            return

        # DM translation: strip mentions, detect language, translate
        text_clean = strip_mentions_for_detect(text).strip()
        if not text_clean or len(text_clean) < 2:
            return

        lang = detect_language(text_clean)
        tgt = dm_target_lang.get(user_id, "id")
        if lang is None:
            result = translate(text_clean, "auto", tgt)
            if not result:
                return
            reply = LANG_FLAGS.get(tgt, "") + " " + result
        elif lang == tgt:
            return
        else:
            result = translate(text_clean, lang, tgt)
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

    # Strip @mentions - pure mentions without content should not be translated
    text_clean = strip_mentions_for_detect(text).strip()
    if not text_clean or len(text_clean) < 2:
        return

    lang = detect_language(text_clean)
    if lang is None:
        return

    tgt = group_target_lang.get(group_id, "id")

    reply = None
    if lang == "zh":
        result = translate(text_clean, "zh", tgt)
        if result:
            reply = LANG_FLAGS.get(tgt, "") + " " + result
    else:
        result = translate(text_clean, lang, "zh")
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
    logger.info("Image received from %s", group_id)

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
        logger.warning("Failed to download image %s", message_id)
        return
    logger.info("Image downloaded: %d bytes", len(img_raw) if img_raw else 0)

    # Determine target language
    tgt = group_target_lang.get(group_id, "id")

    # Quick OCR to check if there's text and detect language
    extracted = ocr_image_openai(img_base64)
    logger.info("Image OCR result: %s chars, text: %s", len(extracted) if extracted else 0, (extracted[:100] + "...") if extracted and len(extracted) > 100 else extracted)
    if not extracted or len(extracted.strip()) < 2:
        return

    # === Check if this is a work order (製造指示書) ===
    wo_on = group_wo_settings.get(group_id, True)
    if wo_on:
        try:
            wo_customer = detect_work_order(extracted)
            if wo_customer:
                reply = format_storage_for_work_order(wo_customer)
                if reply:
                    with ApiClient(configuration) as api_client:
                        api = MessagingApi(api_client)
                        api.reply_message(ReplyMessageRequest(
                            reply_token=event.reply_token,
                            messages=[TextMessage(text=reply)]
                        ))
                    return
        except Exception as e:
            logger.error("Work order detection error: %s", e)
    # === End work order check ===

    lang = detect_language(extracted)
    if lang is None:
        return

    # Determine actual translation target
    if lang == "zh":
        actual_tgt = tgt
    else:
        actual_tgt = "zh"

    # Translate OCR text using the same translation engine as text messages
    if lang == "zh":
        result = translate(extracted, "zh", tgt)
    else:
        result = translate(extracted, lang, "zh")

    if not result:
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
