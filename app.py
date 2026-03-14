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
            "5. Chinese slang and abbreviations too. "
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
            "產量=jumlah produksi, 目標=target, 達標=capai target, "
            "訂單=order/pesanan, 出貨=kirim barang, 交期=deadline pengiriman, "
            "趕貨=kejar order, 急單=order urgent, "
            "交辦事項=hal yang harus dikerjakan/tugas, "
            "【品質/檢查】"
            "品質=kualitas, 品管=QC, 巡查=inspeksi, 檢查=periksa/cek, "
            "抽檢=sampling check, 全檢=periksa semua/inspeksi penuh, "
            "抽查機制=sistem sampling, "
            "合格=lulus/OK, 不合格=tidak lulus/NG, "
            "重工=rework, 返修=perbaiki ulang, "
            "環狀擦傷=goresan melingkar, 刮傷=goresan, 瑕疵=cacat, "
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
            "確實=pastikan, 防止=mencegah."
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
