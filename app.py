"""
翻譯小助手 — Discord Bot
Ported from LINE translator bot (Walsin Lihwa / 華新麗華 鹽水廠)
"""
import os
import re
import json
import logging
import urllib.request
import urllib.parse
import time
import asyncio
import io
import base64

import discord
from discord import app_commands
from discord.ext import commands
from openai import OpenAI
from aiohttp import web

# ─── Config ─────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("discord_bot")

DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "changeme")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "onerkk/discord-translator-bot")

oai = OpenAI(api_key=OPENAI_KEY) if OPENAI_KEY else None

# ─── Per-channel settings ───────────────────────────────
channel_settings = {}       # {channel_id: True/False} translation on/off
channel_target_lang = {}    # {channel_id: "id"/"en"/...}
channel_skip_users = {}     # {channel_id: set(user_ids)}
channel_img_settings = {}   # {channel_id: True/False} image OCR on/off
channel_audio_settings = {} # {channel_id: True/False} voice on/off
channel_wo_settings = {}    # {channel_id: True/False} work order detection on/off

# Translation cache
translation_cache = {}
CACHE_MAX_SIZE = 500
CACHE_TTL = 3600

# ─── Language constants ─────────────────────────────────
LANG_FLAGS = {
    "zh": "\U0001f1f9\U0001f1fc", "id": "\U0001f1ee\U0001f1e9",
    "en": "\U0001f1ec\U0001f1e7", "vi": "\U0001f1fb\U0001f1f3",
    "th": "\U0001f1f9\U0001f1ed", "ja": "\U0001f1ef\U0001f1f5",
    "ko": "\U0001f1f0\U0001f1f7", "ms": "\U0001f1f2\U0001f1fe",
    "tl": "\U0001f1f5\U0001f1ed",
}
LANG_NAMES = {
    "zh": "Traditional Chinese", "id": "Indonesian", "en": "English",
    "vi": "Vietnamese", "th": "Thai", "ja": "Japanese",
    "ko": "Korean", "ms": "Malay", "tl": "Filipino/Tagalog",
}
LANG_NAMES_ZH = {
    "id": "印尼文", "en": "英文", "vi": "越南文", "th": "泰文",
    "ja": "日文", "ko": "韓文", "ms": "馬來文", "tl": "菲律賓文",
}
VALID_TARGETS = ["id", "en", "vi", "th", "ja", "ko", "ms", "tl"]

# ─── Hard replacement tables ────────────────────────────
ZH_TO_ID_HARD = {
    "爐號標籤": "label heat number", "爐號": "heat number",
    "無心研磨": "centerless grinding", "光輝退火爐": "furnace bright annealing",
    "光輝退火": "bright annealing", "退火爐": "tungku annealing",
    "過帳": "input data ke sistem", "放行": "release data",
    "殺光痕": "bekas grinding mark", "車刀痕": "bekas pisau bubut",
    "砂光痕": "bekas sanding mark", "軋輥印痕": "bekas roll mark",
    "環狀擦傷": "goresan melingkar", "表粗": "surface roughness",
    "偏小": "under size", "偏大": "over size", "風險批": "lot berisiko",
    "走ET檢測": "jalankan pengujian ET", "開立重工": "buat work order rework",
    "不允收": "pelanggan tidak terima",
    "矯直機": "mesin straightening", "壓光機": "mesin press polish",
    "砂光機": "mesin sanding", "拋光機": "mesin polishing",
    "眼模": "die/cetakan", "引拔座": "drawing bench", "皮膜槽": "coating tank",
    "氣壓缸": "silinder pneumatik", "安全圍籬": "safety fence",
    "集塵設備": "dust collector", "計長器": "length counter", "冷水機": "chiller",
    "馬蹄環": "shackle", "吊掛物": "beban gantung", "護罩": "pelindung mesin",
    "interlock": "pengunci keamanan", "標籤機": "mesin label",
    "品保": "QC", "儲運": "bagian gudang", "生計": "production planning",
    "業務": "bagian sales", "營業": "bagian sales", "人事": "HRD",
    "處長": "kepala divisi", "稼動率": "utilization rate", "線速": "kecepatan lini",
    "速差": "selisih kecepatan", "主機手": "operator utama", "印勞": "pekerja Indonesia",
    "在製品管制表": "tabel kontrol WIP",
    "套紙管": "pasang tabung kertas", "太空包": "jumbo bag",
    "噴漆罐": "kaleng spray", "木箱": "kotak kayu", "櫃子": "kontainer",
    "允收": "toleransi terima", "訂尺": "panjang pesanan", "短尺": "ukuran pendek",
    "異型棒": "batang bentuk khusus", "遞延單": "order ditunda", "急單": "order urgent",
    "不擋非本月": "order bukan bulan ini boleh masuk gudang", "不擋": "tidak dibatasi",
    "溢量": "kelebihan produksi", "併包": "gabung packing",
    "出貨差": "kekurangan pengiriman",
    "忘卡補": "input lewat sistem lupa kartu", "造冊": "buat daftar absensi",
    "班股": "rapat shift", "堆高機複訓": "pelatihan ulang forklift",
    "天車複訓": "pelatihan ulang crane", "扣績效": "potong penilaian kinerja",
    "劣項": "pelanggaran", "納入劣項": "dicatat pelanggaran",
    "提報懲處": "laporkan untuk sanksi", "三定": "3 tetap",
    "不要物": "barang tidak terpakai", "被釘": "kena tegur",
    "綠卡": "kartu hijau",
    "煙蒂": "puntung rokok", "檳榔渣": "sisa pinang", "廚餘": "sisa makanan",
    "漏油": "bocor oli", "積水": "genangan air", "粉塵": "debu",
    "感溫": "terima kasih", "有夠": "sangat", "母湯": "jangan",
}

ID_POST_FIX = {
    "nomor panas": "heat number", "label nomor panas": "label heat number",
    "nomor tungku": "heat number", "label nomor tungku": "label heat number",
    "nomor oven": "heat number", "label nomor oven": "label heat number",
    "paket datang ke": "kalau ada packing untuk",
    "saat paket datang ke": "kalau ada packing untuk",
    "Mohon diperhatikan saat paket datang ke": "Nanti kalau ada packing untuk",
    "Mohon diperhatikan saat kalau ada packing untuk": "Nanti kalau ada packing untuk",
    "tiga meter di atas enam meter": "batang 3 meter ditaruh di atas batang 6 meter",
    "Tiga meter di atas enam meter": "Batang 3 meter ditaruh di atas batang 6 meter",
    "3 meter di atas 6 meter": "batang 3 meter ditaruh di atas batang 6 meter",
    "jaminan kualitas": "QC", "penjaminan mutu": "QC",
    "panggilan nama": "inspeksi pengawas", "absen nama": "inspeksi pengawas",
    "roll call": "inspeksi pengawas",
    "suhu perasaan": "terima kasih", "merasakan suhu": "terima kasih",
    "Polymetal": "寶麗金屬", "Bao Li Metal": "寶麗金屬", "Bao Li Logam": "寶麗金屬",
    "Changzhou Zhongshan": "常州眾山", "Da Shun": "大順", "Da Cheng": "大成",
    "Bei Ze": "北澤", "Hong Yun": "鴻運", "Tian Hua Rong": "田華榕",
    "Jia Dong": "佳東",
    "bagian operasional": "bagian sales", "operasional perlu": "sales perlu",
}

# ─── Storage data (embedded) ────────────────────────────
_STORAGE_JSON = '{"6C422209": [["<=3200", "EH28"], [">4200", "EG38"], [">3200<=4200", "EH26"]], "ABE": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EG34"]], "AIK": [[">3200<=4200", "EG14"], [">4200", "EH33"], ["<=3200", "EH28"]], "ALCONIX JP": [["<=3200", "EG14"], [">4200", "EH33"], [">3200<=4200", "EG14"]], "AMERICAN STAINLESS": [[">3200<=4200", "EG14"], [">4200", "EG34"], ["<=3200", "EH28"]], "AMS": [[">3200<=4200", "EG14"], [">4200", "EG34"], ["<=3200", "EH28"]], "ANCHOR": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EG34"]], "ANIL METALS": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EH33"]], "APEX METAL": [["<=3200", "EH28"], [">4200", "EH33"], [">3200<=4200", "EG14"]], "AWACS": [[">3200<=4200", "EG14"], [">4200", "EG34"], ["<=3200", "EH28"]], "B&B": [[">4200", "EH33"], ["<=3200", "EH22"], [">3200<=4200", "EG14"]], "B&J": [["<=3200", "EC40"], [">4200", "EC40"], [">3200<=4200", "EC45"]], "BOBCO": [["<=3200", "EH28"], [">3200<=4200", "EG14"], [">4200", "EH34"]], "BOLLINGHAUS": [[">3200<=4200", "EC43"], ["<=3200", "EC43"], [">4200", "EC43"]], "CA-ASD": [[">4200", "EH11"], ["<=3200", "EH12"], [">3200<=4200", "EH12"]], "CA-AUSTRAL": [[">3200<=4200", "EH12"], ["<=3200", "EH12"], [">4200", "EH11"]], "CA-DALSTEEL": [[">4200", "EH11"], ["<=3200", "EH12"], [">3200<=4200", "EH12"]], "CA-FLETCHER": [[">3200<=4200", "EH12"], [">4200", "EH11"], ["<=3200", "EH28"]], "CA-M&S": [["<=3200", "EH12"], [">3200<=4200", "EH12"], [">4200", "EH11"]], "CA-MICO": [["<=3200", "EH12"], [">3200<=4200", "EH12"], [">4200", "EH11"]], "CA-MIDWAY": [["<=3200", "EH12"], [">3200<=4200", "EH12"], [">4200", "EH11"]], "CA-S&T": [["<=3200", "EH12"], [">3200<=4200", "EH12"], [">4200", "EH11"]], "CA-VAN LEEUWEN": [["<=3200", "EH12"], [">4200", "EH11"], [">3200<=4200", "EH12"]], "CA-VES": [["<=3200", "EH12"], [">3200<=4200", "EH12"], [">4200", "EH11"]], "CA-VULCAN": [[">4200", "EH11"], ["<=3200", "EH12"], [">3200<=4200", "EH12"]], "CA-VULCAN NZ": [["<=3200", "EH12"], [">3200<=4200", "EH12"], [">4200", "EH11"]], "CA-WAKEFIELD": [[">4200", "EH11"], [">3200<=4200", "EH12"], ["<=3200", "EH12"]], "CAMELLIA": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EG34"]], "CASTLE": [[">3200<=4200", "EH12"], ["<=3200", "EH28"], [">4200", "EH11"]], "CHANDAN": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EH33"]], "CHANG HSIN": [["<=3200", "EH28"], [">3200<=4200", "EG14"], [">4200", "EG34"]], "CONTINENTAL": [[">3200<=4200", "EG14"], [">4200", "EG34"], ["<=3200", "EH28"]], "DACAPO": [["<=3200", "EH22"], [">3200<=4200", "EG14"], [">4200", "EG34"]], "DK METAL": [[">3200<=4200", "EG14"], [">4200", "EH33"], ["<=3200", "EH28"]], "ESTEELINDO": [[">3200<=4200", "EG14"], [">4200", "EH33"], ["<=3200", "EH28"]], "EURO INOX": [[">3200<=4200", "EG14"], [">4200", "EH33"], ["<=3200", "EH28"]], "FIX": [["<=3200", "EH28"], [">4200", "EG34"], [">3200<=4200", "EG14"]], "FORTUNE METAL": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EG34"]], "GD METAL": [[">3200<=4200", "EG14"], [">4200", "EH33"], ["<=3200", "EH28"]], "GLH": [[">4200", "EG34"], ["<=3200", "EH28"], [">3200<=4200", "EG14"]], "GLOBAL STAINLESS": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EH33"]], "GP SYRMA": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EG34"]], "H.Y.S.": [["<=3200", "EH22"], [">3200<=4200", "EG14"], [">4200", "EH33"]], "HANSAE": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EH33"]], "HIDAYAT": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EH33"]], "ISTANA KARANG LAUT": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EH33"]], "JAYESH": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EH33"]], "JIGNESH": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EH33"]], "KANGRUI": [[">3200<=4200", "EG14"], [">4200", "EG34"], ["<=3200", "EH28"]], "KARTHIK": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EH33"]], "KJ": [[">3200<=4200", "EG14"], [">4200", "EH33"], ["<=3200", "EH28"]], "LEMONA": [["<=3200", "EH28"], [">3200<=4200", "EG14"], [">4200", "EH33"]], "LOGAM MAS": [[">3200<=4200", "EG14"], [">4200", "EH33"], ["<=3200", "EH28"]], "MANGAL": [[">3200<=4200", "EG14"], [">4200", "EH33"], ["<=3200", "EH28"]], "METALINOX": [[">3200<=4200", "EG14"], [">4200", "EG34"], ["<=3200", "EH28"]], "MICROSTEEL": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EG34"]], "PANCHMAHAL": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EH33"]], "PLUTUS": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EH33"]], "PT.COGNE": [[">3200<=4200", "EC43"], ["<=3200", "EC43"], [">4200", "EC43"]], "SAMRAT": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EH33"]], "SAMWON": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EH33"]], "SHANDONG GUANGDA": [[">3200<=4200", "EG14"], [">4200", "EH33"], ["<=3200", "EH28"]], "SHREE AMBIKA": [[">3200<=4200", "EG14"], [">4200", "EH33"], ["<=3200", "EH28"]], "STEELINC": [[">3200<=4200", "EG14"], [">4200", "EG34"], ["<=3200", "EH28"]], "SUNGEUN": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EG34"]], "SUPRA": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EH33"]], "TATASTEEL": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EH33"]], "TCI": [["<=3200", "EC43"], [">4200", "EC43"], [">3200<=4200", "EC43"]], "THREE STAR": [[">3200<=4200", "EG14"], [">4200", "EH33"], ["<=3200", "EH28"]], "TOKYO STAINLESS": [[">3200<=4200", "EG14"], [">4200", "EH33"], ["<=3200", "EH28"]], "TOPGAIN": [["<=3200", "EH28"], [">3200<=4200", "EG14"], [">4200", "EH33"]], "VIRAJ": [[">3200<=4200", "EG14"], ["<=3200", "EH28"], [">4200", "EH33"]], "ZHEJIANG": [[">3200<=4200", "EG14"], [">4200", "EH33"], ["<=3200", "EH28"]], "三寶": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "三陽": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "上暉": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "久鑫": [["<=3200", "EH79"], [">4200", "EG38"], [">3200<=4200", "EH78"]], "元台": [[">3200<=4200", "EH78"], [">4200", "EG38"], ["<=3200", "EH79"]], "全聯": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "六星": [["<=3200", "EH79"], [">3200<=4200", "EH78"], [">4200", "EG38"]], "冠升": [["<=3200", "EH79"], [">3200<=4200", "EH78"], [">4200", "EG38"]], "利泓": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "加倍力": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "北澤": [["<=3200", "EH79"], [">3200<=4200", "EH78"], [">4200", "EG38"]], "協和": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "協奇": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "台芝": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "右勝": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "名威": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "和鍵": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "唐榮": [["<=3200", "EC47"], [">3200<=4200", "EC40"], [">4200", "EC40"]], "嘉泰": [["<=3200", "EH79"], [">3200<=4200", "EH78"], [">4200", "EG38"]], "四維": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "堡辰": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "大成": [["<=3200", "EH79"], [">3200<=4200", "EH78"], [">4200", "EG38"]], "大洋": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "大順": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "天基": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "奧森": [["<=3200", "EH79"], [">3200<=4200", "EH78"], [">4200", "EG38"]], "寶穎": [[">3200<=4200", "EH78"], [">4200", "EG38"], ["<=3200", "EH79"]], "巨昌": [["<=3200", "EH79"], [">3200<=4200", "EH78"], [">4200", "EG38"]], "常州眾山": [[">3200<=4200", "EG14"], [">4200", "EH33"], ["<=3200", "EH28"]], "廉錩": [["<=3200", "EH79"], [">3200<=4200", "EH78"], [">4200", "EG38"]], "弘森": [["<=3200", "EH79"], [">4200", "EG38"], [">3200<=4200", "EH78"]], "志盛": [["<=3200", "EH79"], [">3200<=4200", "EH78"], [">4200", "EG38"]], "慶達": [["<=3200", "EH79"], [">3200<=4200", "EH78"], [">4200", "EG38"]], "擎億": [["<=3200", "EH79"], [">3200<=4200", "EH78"], [">4200", "EG38"]], "新光": [["<=3200", "EH79"], [">3200<=4200", "EH78"], [">4200", "EG38"]], "旗勝": [[">3200<=4200", "EH78"], [">4200", "EG38"], ["<=3200", "EH79"]], "明憲": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "普利擎": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "永吉": [["<=3200", "EH79"], [">3200<=4200", "EH78"], [">4200", "EG38"]], "津展": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "洪福": [["<=3200", "EH79"], [">3200<=4200", "EH78"], [">4200", "EG38"]], "源盛傑": [["<=3200", "EH79"], [">3200<=4200", "EH78"], [">4200", "EG38"]], "漢翊": [["<=3200", "EH79"], [">3200<=4200", "EH78"], [">4200", "EG38"]], "百堅": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "皇銘": [[">3200<=4200", "EH27"], ["<=3200", "EH27"], [">4200", "EG38"]], "盛英": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "知行": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "磐石": [["<=3200", "EH79"], [">3200<=4200", "EH78"], [">4200", "EG38"]], "誼山": [["<=3200", "EH29"], [">3200<=4200", "EH78"], [">4200", "EH38"]], "頭份": [[">4200", "EG38"], [">3200<=4200", "EH78"], ["<=3200", "EH79"]], "優普洛": [["<=3200", "EH79"], [">3200<=4200", "EH78"], [">4200", "EG38"]], "營三備庫(內)": [[">3200<=4200", "EC40"], ["<=3200", "EC47"], [">4200", "EC40"]], "營三備庫(外)": [[">3200<=4200", "EC40"], [">4200", "EC40"], ["<=3200", "EC47"]], "營業庫存": [[">4200", "EH99"], ["<=3200", "EH99"], [">3200<=4200", "EH99"]], "環友": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "聯岱": [[">4200", "EG38"], ["<=3200", "EH79"], [">3200<=4200", "EH78"]], "聯祥": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "邁達斯": [[">4200", "EG38"], ["<=3200", "EH79"], [">3200<=4200", "EH78"]], "鴻運": [[">3200<=4200", "EH27"], ["<=3200", "EH27"], [">4200", "EG38"]], "雙和": [[">3200<=4200", "EG14"], ["<=3200", "EH26"], [">4200", "EG34"]], "麒譯": [["<=3200", "EH79"], [">4200", "EG38"], [">3200<=4200", "EH78"]], "町洋": [["<=3200", "EH79"], [">4200", "EG38"], [">3200<=4200", "EH78"]], "晟田": [["<=3200", "EH79"], [">4200", "EG38"], [">3200<=4200", "EH78"]], "畯圓": [["<=3200", "EH19"], [">4200", "EG38"], [">3200<=4200", "EH78"]], "鐿順發": [["<=3200", "EH79"], [">4200", "EG38"], [">3200<=4200", "EH78"]], "鑫誠鐵材": [["<=3200", "EH79"], [">4200", "EG38"], [">3200<=4200", "EH78"]], "恒耀": [["<=3200", "EH79"], [">4200", "EG38"], [">3200<=4200", "EH78"]], "暉": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]], "頂": [[">3200<=4200", "EH78"], ["<=3200", "EH79"], [">4200", "EG38"]]}'
STORAGE_LOOKUP = json.loads(_STORAGE_JSON)

# Try loading external storage_data.json
_storage_json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "storage_data.json")
if os.path.exists(_storage_json_path):
    try:
        with open(_storage_json_path, "r", encoding="utf-8") as _f:
            STORAGE_LOOKUP = json.load(_f)
            logger.info("Loaded storage_data.json: %d customers", len(STORAGE_LOOKUP))
    except Exception as _e:
        logger.warning("Failed to load storage_data.json: %s", _e)

EXTRA_CUSTOMERS = [
    "寶麗金屬", "田華榕", "蘋果", "賽利金屬", "盛昌遠", "曜麟",
    "LOTUS", "LOTUS METAL", "shinko", "wing keung",
]
CUSTOMER_NAMES = sorted(
    list(set(list(STORAGE_LOOKUP.keys()) + EXTRA_CUSTOMERS)),
    key=lambda x: -len(x)
)


# ─── Mention handling (Discord style) ──────────────────
def extract_mentions_discord(text):
    """Extract Discord <@id> mentions."""
    return re.findall(r'<@!?\d+>', text)


def protect_mentions(text):
    mentions = extract_mentions_discord(text)
    protected = text
    placeholders = {}
    for i, m in enumerate(mentions):
        ph = f"__MENTION_{i}__"
        placeholders[ph] = m
        protected = protected.replace(m, ph, 1)
    return protected, placeholders


def restore_mentions(text, placeholders):
    restored = text or ""
    for ph, original in placeholders.items():
        idx = ph.replace("__MENTION_", "").replace("__", "")
        variants = [ph, ph.replace("_", " "), f"MENTION_{idx}", f"MENTION {idx}",
                    f"__MENTION {idx}__", f"[[MENTION_{idx}]]"]
        for v in variants:
            restored = restored.replace(v, original)
    missing = [orig for orig in placeholders.values() if orig not in restored]
    if missing:
        restored = " ".join(missing) + " " + restored
    return restored.strip()


# ─── Language detection ─────────────────────────────────
def has_chinese(text):
    return len(re.findall(r'[\u4e00-\u9fff]', text)) >= 2

def has_japanese(text):
    return (len(re.findall(r'[\u3040-\u309f]', text)) + len(re.findall(r'[\u30a0-\u30ff]', text))) >= 2

def has_korean(text):
    return len(re.findall(r'[\uac00-\ud7af]', text)) >= 2

def has_thai(text):
    return len(re.findall(r'[\u0e00-\u0e7f]', text)) >= 2

def has_vietnamese(text):
    vi_special = re.findall(r'[\u01a0\u01a1\u01af\u01b0\u0110\u0111]', text)
    vi_chars = re.findall(r'[\u00e0-\u00ff\u1ea0-\u1ef9]', text.lower())
    words = text.lower().split()
    vi_markers = {'cua','nhung','trong','duoc','khong','nhu','mot','toi','ban','anh','chi','em',
                  'la','va','cac','cho','voi','tai','nay','khi','con','roi','lam','biet','muon',
                  'den','di','xin','cam','chao','dep','ngon','tot','xau'}
    marker_count = sum(1 for w in words if w in vi_markers)
    if len(vi_special) >= 1:
        return True
    if len(vi_chars) >= 3 and marker_count >= 1:
        return True
    return False

def has_indonesian(text):
    if has_chinese(text) or has_thai(text) or has_korean(text) or has_japanese(text):
        return False
    words = re.findall(r'[a-zA-Z]+', text.lower())
    if len(words) < 2:
        return False
    id_words = {
        'yang','dan','ini','itu','ada','untuk','dengan','dari','tidak','akan','sudah','bisa',
        'juga','saya','kami','kita','mereka','dia','apa','bagaimana','kenapa','kapan','dimana',
        'siapa','belum','sedang','harus','boleh','mau','ingin','bukan','jangan','tolong',
        'terima','kasih','selamat','pagi','siang','sore','malam','baik','bagus','benar',
        'salah','besar','kecil','makan','minum','tidur','kerja','pulang','pergi','rumah',
        'kantor','uang','harga','berapa','banyak','sedikit','semua','karena','tetapi','tapi',
        'atau','jika','kalau','sampai','masih','lagi','saja','dulu','nanti','sekarang',
        'hari','minggu','bulan','tahun','gak','nggak','udah','gimana','dong','sih','nih',
        'kok','yuk','ayo','banget','orang','baru','lembur','cuti','gaji','minta','ambil',
        'kirim','tunggu','cepat','lambat','susah','gampang','senang','sedih','marah',
        'takut','capek','lapar','haus','sakit','sehat','di','ke','jam','ruang','baca',
        'soal','ujian','terakhir','kamu','jadi','harap','ukur','secara','manual','rusak',
        'saat','mohon','pakai',
    }
    id_count = sum(1 for w in words if w in id_words)
    return id_count >= 2

def has_english(text):
    if has_chinese(text) or has_thai(text) or has_korean(text) or has_japanese(text):
        return False
    words = re.findall(r'[a-zA-Z]+', text.lower())
    if len(words) < 2:
        return False
    en_words = {
        'the','is','are','was','were','have','has','had','do','does','did','will','would',
        'can','could','should','shall','may','might','must','been','being','what','where',
        'when','who','how','why','which','this','that','these','those','with','from',
        'about','into','through','after','before','between','under','over','again','then',
        'here','there','very','just','also','not','but','and','for','all','each','every',
        'both','few','more','most','other','some','such','only','same','than','too',
    }
    id_words_check = {
        'yang','dan','ini','itu','ada','untuk','dengan','dari','tidak','sudah','bisa',
        'saya','kami','mereka','apa','bagaimana','kenapa','belum','harus','boleh','mau',
        'jangan','tolong','terima','kasih','selamat','pagi','siang','sore','malam',
    }
    en_count = sum(1 for w in words if w in en_words)
    id_count = sum(1 for w in words if w in id_words_check)
    return en_count > id_count and en_count >= 2

def detect_language(text):
    clean = re.sub(r'<@!?\d+>', ' ', text)
    clean = re.sub(r'https?://\S+', ' ', clean)
    clean = clean.strip()
    if not clean:
        return None
    if has_japanese(clean) and not has_chinese(clean):
        return "ja"
    if has_korean(clean):
        return "ko"
    if has_thai(clean):
        return "th"
    if has_chinese(clean):
        latin_words = re.findall(r'[a-zA-Z]+', clean.lower())
        id_words = {
            'yang','dan','ini','itu','ada','untuk','dengan','dari','tidak','akan','sudah',
            'bisa','juga','saya','kami','kita','mereka','dia','apa','bagaimana','kenapa',
            'kapan','dimana','siapa','belum','sedang','harus','boleh','mau','ingin','bukan',
            'jangan','tolong','terima','kasih','selamat','pagi','siang','sore','malam',
            'baik','bagus','benar','salah','besar','kecil','makan','minum','tidur','kerja',
            'pulang','pergi','rumah','kantor','uang','harga','berapa','banyak','sedikit',
            'semua','karena','tetapi','tapi','atau','jika','kalau','sampai','masih','lagi',
            'saja','dulu','nanti','sekarang','hari','minggu','bulan','tahun','gak','nggak',
            'udah','gimana','dong','sih','nih','kok','yuk','ayo','banget','orang','baru',
            'lembur','cuti','gaji','minta','ambil','kirim','tunggu','cepat','lambat',
            'susah','gampang','senang','sedih','marah','takut','capek','lapar','haus',
            'sakit','sehat','di','ke','jam','ruang','baca','soal','ujian','terakhir',
            'kamu','jadi','harap','ukur','secara','manual','rusak','saat','mohon','pakai',
        }
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


# ─── Translation pipeline ──────────────────────────────
def pre_replace_zh(text):
    result = text
    cust_ph = {}
    for i, name in enumerate(CUSTOMER_NAMES):
        if name in result:
            ph = f"__CUST_{i}__"
            cust_ph[ph] = name
            result = result.replace(name, ph)
    for zh, replacement in sorted(ZH_TO_ID_HARD.items(), key=lambda x: -len(x[0])):
        if zh in result:
            result = result.replace(zh, f"[{replacement}]")
    return result, cust_ph

def restore_customers(text, cust_ph):
    if not text or not cust_ph:
        return text
    result = text
    for ph, name in cust_ph.items():
        idx = ph.replace("__CUST_", "").replace("__", "")
        variants = [ph, ph.replace("_", " "), f"CUST_{idx}", f"CUST {idx}",
                    f"__CUST {idx}__", f"[CUST_{idx}]"]
        for v in variants:
            if v in result:
                result = result.replace(v, name)
    result = re.sub(r'__CUST_(\d+)__',
                    lambda m: cust_ph.get(f"__CUST_{m.group(1)}__", m.group(0)), result)
    return result

def post_fix_translation(text):
    if not text:
        return text
    result = text
    for wrong, correct in sorted(ID_POST_FIX.items(), key=lambda x: -len(x[0])):
        result = result.replace(wrong, correct)
    result = re.sub(r'\[([a-zA-Z /&]+)\]', r'\1', result)
    result = re.sub(r'\s{2,}', ' ', result).strip()
    return result

def contains_source_script(text, src):
    cleaned = re.sub(r'__MENTION_\d+__', ' ', text or '')
    cleaned = re.sub(r'__CUST_\d+__', ' ', cleaned)
    for name in CUSTOMER_NAMES:
        if name in cleaned:
            cleaned = cleaned.replace(name, ' ')
    patterns = {"zh": r'[\u4e00-\u9fff]', "ja": r'[\u3040-\u30ff\u4e00-\u9fff]',
                "ko": r'[\uac00-\ud7af]', "th": r'[\u0e00-\u0e7f]'}
    pattern = patterns.get(src)
    if not pattern:
        return False
    return len(re.findall(pattern, cleaned)) >= 2

def is_translation_valid(result, src, tgt):
    if not result or not result.strip():
        return False
    if src != tgt and contains_source_script(result, src):
        return False
    return True

def build_system_prompt(extra_rule=""):
    return (
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
        "【製程/Process】無心研磨=centerless grinding, 研磨=grinding, 砂輪=batu gerinda, 調整輪=roda pengatur, 刀板=work rest blade, 冷卻液=cairan pendingin, "
        "不鏽鋼=stainless steel, 棒鋼=steel bar, 盤元=wire rod, 削皮棒=peeled bar, 冷精棒=cold-drawn bar, "
        "熱軋=hot rolling, 退火=annealing, 酸洗=pickling, 削皮=peeling, 冷抽=cold drawing, "
        "鋼種=jenis baja, PMI=uji material, 來料=material masuk, 棒材=batang baja, 混料=tercampur material(SERIOUS), 料號=nomor material, "
        "拋光=polishing, 粗拋=rough polishing, 噴漆=spray paint, 洗料=cuci material, "
        "倒角=chamfer, 修磨=repair grinding, 壓光=press polish, 矯直=straightening, 精整=finishing, AP=mesin finishing, "
        "光輝退火=bright annealing, 回爐=kirim kembali ke furnace, "
        "側磨=side grinding(DILARANG/prohibited), 不可側磨=dilarang side grinding, "
        "【班次/出勤】點名=ada pengawas yang datang(inspection, NOT roll call), 早班=shift pagi, 夜班=shift malam, 中班=shift siang, "
        "加班=lembur, 請假=izin, 病假=izin sakit, 事假=izin pribadi, 特休=cuti tahunan, "
        "【設備】天車=overhead crane, 台車=trolley, 吊秤=timbangan gantung, 馬蹄環=shackle, "
        "稼動率=utilization rate, 線速=line speed, 主機手=operator utama, 印勞=pekerja Indonesia, "
        "【包裝】套紙管=pasang tabung kertas, 入庫=masuk gudang, 櫃子=kontainer, 木箱=kotak kayu, "
        "把=bundel, 捆=bundel/ikat, 支/根=batang, 批=lot/batch, "
        "包(verb)=packing, 秤重=timbang, 貼標=tempel label, "
        "【訂單】允收=jumlah yang boleh diterima, 訂尺=panjang sesuai pesanan, 爐號=heat number, "
        "不擋=tidak dibatasi(ALLOWED), 不擋非本月=order bukan bulan ini BOLEH masuk gudang, "
        "溢量=kelebihan produksi, 併包=gabung packing, 出貨差=kekurangan pengiriman, "
        "過帳=input data ke sistem, 放行=release data, "
        "【品質】品保=QC, 客訴=komplain pelanggan, 偏小=under size, 偏大=over size, 表粗=surface roughness, "
        "開立重工=buat WO rework, 風險批=lot berisiko, "
        "【部門】業務=sales, 營業=sales, 生計=production planning, 品保=QC, 儲運=gudang&logistik, 人事=HRD, "
        "處長=kepala divisi, "
        "10. CRITICAL CONTEXT RULES: "
        "a) X米=bar LENGTH. 三米上面放六米=batang 3m di atas 6m. "
        "b) 把/捆=BUNDLE counters. 包2把=packing 2 bundel. "
        "c) 包(verb)=packing NOT wrapping. "
        "d) 爐號=heat number(NEVER 'nomor panas'). "
        "e) 放=POLYSEMY: 放+把/單/批=RELEASE; 放+地點=PUT/PLACE; 放+料=FEED. "
        "f) 再=POLYSEMY: X再Y(condition)=hanya X yang Y; 再+verb(alone)=lagi/again. "
        "g) 不擋=NOT blocked=ALLOWED. "
        "h) Customer names=keep as-is, do NOT translate. "
        + extra_rule +
        " Only output the translation. No quotes, no explanation, no prefix."
    )

def translate_openai(text, src, tgt, strict=False, repair_mode=False, bad_result=None):
    if not oai:
        return None
    try:
        src_name = LANG_NAMES.get(src, src)
        tgt_name = LANG_NAMES.get(tgt, tgt)
        input_text = text
        cust_placeholders = {}
        if src == "zh":
            input_text, cust_placeholders = pre_replace_zh(text)
        protected, placeholders = protect_mentions(input_text)

        extra_rule = ""
        if strict and src != tgt:
            if src == "zh":
                extra_rule = " IMPORTANT: Do not leave any Chinese words untranslated unless they are a person's name or placeholder."

        sys_prompt = build_system_prompt(extra_rule)

        if repair_mode and bad_result:
            msg = (f"Original text (source language): {protected}\n\n"
                   f"Bad translation that leaked source-language words: {bad_result}\n\n"
                   f"Rewrite the bad translation into pure {tgt_name}. "
                   "Preserve names and __MENTION__ placeholders exactly.")
        else:
            msg = f"Translate from {src_name} to {tgt_name}: {protected}"

        r = oai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "system", "content": sys_prompt},
                      {"role": "user", "content": msg}],
            temperature=0.1 if strict or repair_mode else 0.2,
            max_tokens=2000,
        )
        result = r.choices[0].message.content.strip()
        result = restore_mentions(result, placeholders)
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
        lang_map = {"zh": "zh-TW", "id": "id", "en": "en", "vi": "vi",
                    "th": "th", "ja": "ja", "ko": "ko", "ms": "ms", "tl": "tl"}
        sl = lang_map.get(src, src)
        tl = lang_map.get(tgt, tgt)
        q = urllib.parse.quote(protected)
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={sl}&tl={tl}&dt=t&q={q}"
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            parts = [item[0] for item in data[0] if item[0]]
            result = "".join(parts)
            result = restore_mentions(result, placeholders)
            result = post_fix_translation(result)
            return result
    except Exception as e:
        logger.error("Google translate error: %s", e)
        return None

def cache_get(text, src, tgt):
    key = (text.strip(), src, tgt)
    if key in translation_cache:
        result, ts = translation_cache[key]
        if time.time() - ts < CACHE_TTL:
            return result
        del translation_cache[key]
    return None

def cache_set(text, src, tgt, result):
    if len(translation_cache) >= CACHE_MAX_SIZE:
        oldest = min(translation_cache, key=lambda k: translation_cache[k][1])
        del translation_cache[oldest]
    translation_cache[(text.strip(), src, tgt)] = (result, time.time())

def safe_embed_text(text, max_len=4090):
    """Truncate text to fit Discord embed limits (4096 chars)."""
    if not text:
        return text
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text

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
    cached = cache_get(text, src, tgt)
    if cached:
        return cached

    result = translate_with_retry(translate_openai, text, src, tgt, max_retries=2)

    # Retry with strict mode if source leakage detected
    if result and not is_translation_valid(result, src, tgt):
        logger.warning("Source leakage detected, retrying strict")
        strict_result = translate_openai(text, src, tgt, strict=True)
        if strict_result and is_translation_valid(strict_result, src, tgt):
            result = strict_result
        else:
            repaired = translate_openai(text, src, tgt, strict=True,
                                        repair_mode=True, bad_result=(strict_result or result))
            if repaired and is_translation_valid(repaired, src, tgt):
                result = repaired

    if result and is_translation_valid(result, src, tgt):
        cache_set(text, src, tgt, result)
        return result

    # Fallback: Google Translate with retry
    result = translate_with_retry(translate_google, text, src, tgt, max_retries=1)
    if result and is_translation_valid(result, src, tgt):
        cache_set(text, src, tgt, result)
        return result

    # Last chance repair
    if result:
        repaired = translate_openai(text, src, tgt, strict=True, repair_mode=True, bad_result=result)
        if repaired and is_translation_valid(repaired, src, tgt):
            cache_set(text, src, tgt, repaired)
            return repaired

    return None


# ─── Storage query ──────────────────────────────────────
def format_length_zh(code):
    if code == "<=3200": return "未滿3200"
    elif code == ">4200": return "超過4200"
    elif code == ">3200<=4200": return "3200～4200"
    elif code == ">4000": return "超過4000"
    else:
        return code.replace("<=", "未滿").replace(">=", "超過").replace(">", "超過").replace("<", "未滿")

def query_storage(customer_name):
    """Look up storage zone for a customer. Returns embed-ready data or None."""
    entries = STORAGE_LOOKUP.get(customer_name)
    if not entries:
        for key in STORAGE_LOOKUP:
            if key.lower() == customer_name.lower() or customer_name in key or key in customer_name:
                entries = STORAGE_LOOKUP[key]
                customer_name = key
                break
    if not entries:
        return None, None
    return customer_name, entries


# ─── OCR (image text extraction) ────────────────────────
def ocr_and_translate_image(image_base64, tgt_lang):
    """OCR + translate image text in one API call."""
    if not oai:
        return None
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
                        "RULES:\n"
                        "1. Keep the SAME structure, numbering, and line breaks as the original.\n"
                        "2. Each section: original text first, then translation with " + tgt_flag + " flag.\n"
                        "3. Translate naturally, casual daily language for factory workers.\n"
                        "4. Target Traditional Chinese = Taiwan style.\n"
                        "5. NEVER translate or romanize person names. Keep Chinese names in original characters.\n"
                        "6. NEVER translate customer/company names. Keep them EXACTLY as-is: "
                        "賽利金屬, 寶麗金屬, 田華榕, 佳東, 蘋果, 常州眾山, 大順, 大成, 巨昌, 北澤, 鴻運, 畯圓, 名威, 右勝, "
                        "貝克休斯, 皇銘, 台芝, 百堅, 津展, 曜麟, 廉錩, 盛昌遠, 永吉, 光輝, "
                        "DACAPO, CASTLE, LOTUS, METALINOX, KANGRUI, SUNGEUN, STEELINC, GLH, SHINKO, WING KEUNG, "
                        "BOLLINGHAUS, COGNE, TCI, PLUTUS, SAMWON, DK METAL, KJ.\n"
                        "7. If there is no text in the image, output exactly: NO_TEXT_FOUND"
                    )
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image_base64, "detail": "high"}},
                        {"type": "text", "text": "Extract all text from this image and translate to " + tgt_name + "."}
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
        logger.error("OCR error: %s", e)
        return None


def ocr_image_only(image_base64):
    """Extract text only from image (for work order detection)."""
    if not oai:
        return None
    try:
        r = oai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an OCR assistant. Extract ALL text visible in the image. Output ONLY the extracted text, preserving line breaks. If no text, output: NO_TEXT_FOUND"},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image_base64, "detail": "high"}},
                    {"type": "text", "text": "Extract all text from this image."}
                ]}
            ],
            temperature=0.1, max_tokens=2000,
        )
        result = r.choices[0].message.content.strip()
        if result == "NO_TEXT_FOUND" or not result:
            return None
        return result
    except Exception as e:
        logger.error("OCR-only error: %s", e)
        return None


# ─── Work order detection ───────────────────────────────
def detect_work_order(ocr_text):
    """Detect if OCR text is from a factory work order (製造指示書)."""
    if not ocr_text:
        return None
    wo_keywords = ["冷精棒製造指示書", "製造指示書", "訂單編號", "客戶名稱", "成品尺寸",
                   "FINAL流程", "FINAL", "MIC_NO", "ID_NO", "HRITABPDIL", "退火代碼",
                   "冷精棒", "收貨人", "短尺", "品保", "特殊", "削皮", "訂單資訊",
                   "成品尺寸MIN", "成品尺寸MAX", "製造指示"]
    keyword_count = sum(1 for kw in wo_keywords if kw in ocr_text)
    if keyword_count < 2:
        return None
    patterns = [r'客戶名稱[:\s：]*([^\s\n|,，]+)', r'客戶[:\s：]*([^\s\n|,，]+)',
                r'客[户戶]名[称稱][:\s：]*([^\s\n|,，]+)']
    for pat in patterns:
        m = re.search(pat, ocr_text)
        if m:
            customer = m.group(1).strip()
            if customer and len(customer) >= 2:
                return customer
    for name in CUSTOMER_NAMES:
        if len(name) >= 2 and name in ocr_text:
            return name
    return None


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
    lines = [f"📋 工單偵測\n客戶：{customer_name}\n", "📦 儲區查詢", "=" * 18]
    for length, area in entries:
        zh = format_length_zh(length)
        lines.append(f"{zh} → {area}")
    lines.append("=" * 18)
    return "\n".join(lines)


# ─── Voice transcription (Whisper) ──────────────────────
def transcribe_audio(audio_bytes, filename="audio.ogg"):
    """Transcribe audio using OpenAI Whisper."""
    if not oai:
        return None
    try:
        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = filename
        r = oai.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
        )
        text = r.text.strip()
        return text if text else None
    except Exception as e:
        logger.error("Whisper error: %s", e)
        return None


# ─── Usage stats tracking ───────────────────────────────
usage_stats = {
    "text_translations": 0,
    "image_translations": 0,
    "voice_translations": 0,
    "work_orders_detected": 0,
    "slash_commands": 0,
    "reaction_translations": 0,
    "context_translations": 0,
    "start_time": time.time(),
}

# ─── Per-user language preference ───────────────────────
user_lang_prefs = {}  # {user_id: "zh"/"id"/"en"/...}

# ─── Auto-role language mapping ─────────────────────────
LANG_ROLE_NAMES = {
    "zh": "🇹🇼 中文",
    "id": "🇮🇩 Indonesia",
    "en": "🇬🇧 English",
    "vi": "🇻🇳 Tiếng Việt",
    "th": "🇹🇭 ไทย",
    "ja": "🇯🇵 日本語",
    "ko": "🇰🇷 한국어",
}

# ─── Flag emoji to language mapping ─────────────────────
FLAG_TO_LANG = {
    "🇹🇼": "zh", "🇨🇳": "zh", "🇮🇩": "id", "🇬🇧": "en", "🇺🇸": "en",
    "🇻🇳": "vi", "🇹🇭": "th", "🇯🇵": "ja", "🇰🇷": "ko", "🇲🇾": "ms", "🇵🇭": "tl",
}

# ─── Discord Bot ────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)


# ─── Translate Button View ──────────────────────────────
class TranslateView(discord.ui.View):
    """Buttons under translation results."""
    def __init__(self, original_text, src_lang, current_tgt):
        super().__init__(timeout=300)
        self.original_text = original_text
        self.src_lang = src_lang
        self.current_tgt = current_tgt

    @discord.ui.button(label="🇮🇩 ID", style=discord.ButtonStyle.secondary)
    async def btn_id(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._retranslate(interaction, "id")

    @discord.ui.button(label="🇹🇼 中文", style=discord.ButtonStyle.secondary)
    async def btn_zh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._retranslate(interaction, "zh")

    @discord.ui.button(label="🇬🇧 EN", style=discord.ButtonStyle.secondary)
    async def btn_en(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._retranslate(interaction, "en")

    @discord.ui.button(label="🇻🇳 VI", style=discord.ButtonStyle.secondary)
    async def btn_vi(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._retranslate(interaction, "vi")

    async def _retranslate(self, interaction, tgt):
        if tgt == self.src_lang:
            await interaction.response.send_message("⚠️ 來源語言和目標語言相同", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        result = translate(self.original_text, self.src_lang, tgt)
        if result:
            flag = LANG_FLAGS.get(tgt, "🌐")
            usage_stats["text_translations"] += 1
            await interaction.followup.send(f"{flag} {result}", ephemeral=True)
        else:
            await interaction.followup.send("❌ 翻譯失敗", ephemeral=True)


# ─── Handover Template View ─────────────────────────────
class HandoverView(discord.ui.View):
    """Buttons for shift handover template."""
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.button(label="A班 → B班", style=discord.ButtonStyle.primary)
    async def shift_ab(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_template(interaction, "A", "B")

    @discord.ui.button(label="B班 → C班", style=discord.ButtonStyle.primary)
    async def shift_bc(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_template(interaction, "B", "C")

    @discord.ui.button(label="C班 → D班", style=discord.ButtonStyle.primary)
    async def shift_cd(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_template(interaction, "C", "D")

    @discord.ui.button(label="D班 → A班", style=discord.ButtonStyle.primary)
    async def shift_da(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_template(interaction, "D", "A")

    async def _send_template(self, interaction, from_shift, to_shift):
        zh = (f"📋 **{from_shift}班 → {to_shift}班 交班紀錄**\n"
              f"━━━━━━━━━━━━━━━━━━\n"
              f"📅 日期：\n"
              f"👤 交班人：\n"
              f"👤 接班人：\n"
              f"━━━━━━━━━━━━━━━━━━\n"
              f"🔧 **設備狀態：**\n"
              f"• \n"
              f"📦 **生產進度：**\n"
              f"• \n"
              f"⚠️ **待處理事項：**\n"
              f"• \n"
              f"📝 **備註：**\n"
              f"• \n"
              f"━━━━━━━━━━━━━━━━━━")
        id_text = (f"📋 **Serah terima shift {from_shift} → {to_shift}**\n"
                   f"━━━━━━━━━━━━━━━━━━\n"
                   f"📅 Tanggal:\n"
                   f"👤 Shift keluar:\n"
                   f"👤 Shift masuk:\n"
                   f"━━━━━━━━━━━━━━━━━━\n"
                   f"🔧 **Status mesin:**\n"
                   f"• \n"
                   f"📦 **Progress produksi:**\n"
                   f"• \n"
                   f"⚠️ **Yang harus ditangani:**\n"
                   f"• \n"
                   f"📝 **Catatan:**\n"
                   f"• \n"
                   f"━━━━━━━━━━━━━━━━━━")
        embed = discord.Embed(title=f"📋 交班 Serah Terima | {from_shift}→{to_shift}", color=0x06C755)
        embed.add_field(name="🇹🇼 中文", value=zh, inline=False)
        embed.add_field(name="🇮🇩 Indonesia", value=id_text, inline=False)
        await interaction.response.send_message(embed=embed)


# ─── Admin permission check ─────────────────────────────
def is_admin(interaction: discord.Interaction) -> bool:
    """Check if user has admin/manage_guild permission."""
    if not interaction.guild:
        return True
    perms = interaction.user.guild_permissions
    return perms.administrator or perms.manage_guild


# ─── Report Modal (popup form) ──────────────────────────
class ReportModal(discord.ui.Modal, title="🚨 異常回報 / Laporan Masalah"):
    location = discord.ui.TextInput(
        label="位置/機台 (Lokasi/Mesin)",
        placeholder="例：I5研磨機 / Station 420",
        max_length=100,
    )
    description = discord.ui.TextInput(
        label="異常描述 (Deskripsi masalah)",
        style=discord.TextStyle.paragraph,
        placeholder="詳細描述異常狀況...",
        max_length=1000,
    )
    action_taken = discord.ui.TextInput(
        label="已採取措施 (Tindakan)",
        style=discord.TextStyle.paragraph,
        placeholder="目前已做了什麼處理...",
        max_length=500,
        required=False,
    )
    urgency = discord.ui.TextInput(
        label="緊急程度 (1=輕微 2=一般 3=緊急)",
        placeholder="1, 2, 或 3",
        max_length=1,
        default="2",
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        level_map = {"1": ("🟢", "輕微", "Ringan", 0x00AA00),
                     "2": ("🟡", "一般", "Normal", 0xFFAA00),
                     "3": ("🔴", "緊急", "Darurat", 0xFF0000)}
        lvl = level_map.get(str(self.urgency).strip(), level_map["2"])

        # Translate description
        src = detect_language(str(self.description)) or "zh"
        tgt = "id" if src == "zh" else "zh"
        translated_desc = translate(str(self.description), src, tgt)
        translated_action = ""
        if str(self.action_taken).strip():
            translated_action = translate(str(self.action_taken), src, tgt) or ""

        embed = discord.Embed(
            title=f"🚨 異常回報 / Laporan Masalah {lvl[0]}",
            color=lvl[3]
        )
        embed.add_field(name=f"{lvl[0]} 等級 / Level", value=f"{lvl[1]} / {lvl[2]}", inline=True)
        embed.add_field(name="📍 位置 / Lokasi", value=str(self.location), inline=True)
        embed.add_field(name=f"{LANG_FLAGS.get(src, '')} 描述", value=str(self.description), inline=False)
        if translated_desc:
            embed.add_field(name=f"{LANG_FLAGS.get(tgt, '')} 翻譯", value=translated_desc, inline=False)
        if str(self.action_taken).strip():
            embed.add_field(name=f"{LANG_FLAGS.get(src, '')} 已處理", value=str(self.action_taken), inline=False)
            if translated_action:
                embed.add_field(name=f"{LANG_FLAGS.get(tgt, '')} Tindakan", value=translated_action, inline=False)
        embed.set_footer(text=f"回報人：{interaction.user.display_name} | {time.strftime('%Y-%m-%d %H:%M')}")
        usage_stats["slash_commands"] += 1
        await interaction.followup.send(embed=embed)


# ─── Language Dropdown Select ───────────────────────────
class LangSelectView(discord.ui.View):
    """Dropdown to select personal language preference."""
    def __init__(self):
        super().__init__(timeout=60)

    @discord.ui.select(
        placeholder="選擇你的語言 / Pilih bahasa...",
        options=[
            discord.SelectOption(label="🇹🇼 中文 (繁體)", value="zh", description="Traditional Chinese"),
            discord.SelectOption(label="🇮🇩 Bahasa Indonesia", value="id", description="Indonesian"),
            discord.SelectOption(label="🇬🇧 English", value="en", description="English"),
            discord.SelectOption(label="🇻🇳 Tiếng Việt", value="vi", description="Vietnamese"),
            discord.SelectOption(label="🇹🇭 ภาษาไทย", value="th", description="Thai"),
            discord.SelectOption(label="🇯🇵 日本語", value="ja", description="Japanese"),
            discord.SelectOption(label="🇰🇷 한국어", value="ko", description="Korean"),
            discord.SelectOption(label="🇲🇾 Bahasa Melayu", value="ms", description="Malay"),
            discord.SelectOption(label="🇵🇭 Filipino", value="tl", description="Filipino/Tagalog"),
        ]
    )
    async def lang_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        lang = select.values[0]
        user_lang_prefs[interaction.user.id] = lang
        flag = LANG_FLAGS.get(lang, "")
        name = LANG_NAMES_ZH.get(lang, LANG_NAMES.get(lang, lang))

        # Auto-assign language role
        await auto_assign_role(interaction.user, interaction.guild, lang)

        await interaction.response.send_message(
            f"✅ 你的語言已設為 {flag} **{name}**\n"
            f"Bot 會根據你的語言偏好翻譯",
            ephemeral=True
        )


# ─── Poll View (bilingual voting) ──────────────────────
class PollView(discord.ui.View):
    """Interactive bilingual poll buttons."""
    def __init__(self, options_zh, options_tgt, tgt_flag):
        super().__init__(timeout=None)
        self.votes = {}  # {option_index: set(user_ids)}
        self.options_zh = options_zh
        self.options_tgt = options_tgt
        for i, (zh, tgt) in enumerate(zip(options_zh, options_tgt)):
            self.votes[i] = set()
            button = discord.ui.Button(
                label=f"{zh} / {tgt}" if len(zh) + len(tgt) < 70 else zh,
                style=discord.ButtonStyle.primary,
                custom_id=f"poll_{i}",
            )
            button.callback = self._make_callback(i)
            self.add_item(button)

    def _make_callback(self, index):
        async def callback(interaction: discord.Interaction):
            uid = interaction.user.id
            # Remove from other options
            for idx in self.votes:
                self.votes[idx].discard(uid)
            # Add to selected
            self.votes[index].add(uid)
            # Build results
            lines = []
            for i, (zh, tgt) in enumerate(zip(self.options_zh, self.options_tgt)):
                count = len(self.votes[i])
                bar = "█" * count + "░" * max(0, 10 - count)
                lines.append(f"{zh} / {tgt}: {bar} **{count}**")
            await interaction.response.send_message(
                f"✅ 已投票：**{self.options_zh[index]}**\n\n" + "\n".join(lines),
                ephemeral=True
            )
        return callback


# ─── Auto-role assignment helper ────────────────────────
async def auto_assign_role(member, guild, lang):
    """Auto-assign a language role to a member."""
    if not guild or not member:
        return
    role_name = LANG_ROLE_NAMES.get(lang)
    if not role_name:
        return
    try:
        # Find or create role
        role = discord.utils.get(guild.roles, name=role_name)
        if not role:
            role = await guild.create_role(name=role_name, mentionable=True)
        # Remove other language roles
        for rn in LANG_ROLE_NAMES.values():
            existing = discord.utils.get(guild.roles, name=rn)
            if existing and existing in member.roles and existing != role:
                await member.remove_roles(existing)
        # Add new role
        if role not in member.roles:
            await member.add_roles(role)
    except Exception as e:
        logger.error(f"Auto-role error: {e}")


@bot.event
async def on_ready():
    logger.info(f"Bot online: {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} slash commands")
    except Exception as e:
        logger.error(f"Sync error: {e}")
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching, name="翻譯中... | /help"
    ))


# ─── Flag emoji reaction translation ────────────────────
@bot.event
async def on_raw_reaction_add(payload):
    """React with a flag emoji to translate a message to that language."""
    if payload.member and payload.member.bot:
        return
    emoji = str(payload.emoji)
    tgt = FLAG_TO_LANG.get(emoji)
    if not tgt:
        return
    try:
        channel = bot.get_channel(payload.channel_id)
        if not channel:
            return
        message = await channel.fetch_message(payload.message_id)
        if not message or message.author.bot:
            return
        text = message.content.strip()
        if not text:
            return
        src = detect_language(text)
        if not src or src == tgt:
            return
        result = translate(text, src, tgt)
        if not result:
            return
        flag = LANG_FLAGS.get(tgt, "🌐")
        embed = discord.Embed(description=safe_embed_text(f"{flag} {result}"), color=0x06C755)
        embed.set_footer(text=f"反應翻譯 {emoji} | {LANG_NAMES.get(src, src)[:2]} → {LANG_NAMES.get(tgt, tgt)[:2]}")
        await message.reply(embed=embed, mention_author=False)
        usage_stats["reaction_translations"] += 1
    except Exception as e:
        logger.error(f"Reaction translate error: {e}")


# ─── Right-click context menu translation ────────────────
@bot.tree.context_menu(name="翻譯這段")
async def ctx_translate(interaction: discord.Interaction, message: discord.Message):
    """Right-click a message → Apps → 翻譯這段"""
    text = message.content.strip()
    if not text:
        await interaction.response.send_message("❌ 這則訊息沒有文字", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    src = detect_language(text)
    if not src:
        src = "id"
    ch_id = interaction.channel_id
    tgt_lang = channel_target_lang.get(ch_id, "id")
    tgt = tgt_lang if src == "zh" else "zh"
    if src == tgt:
        tgt = "id" if src == "zh" else "zh"
    result = translate(text, src, tgt)
    if not result:
        await interaction.followup.send("❌ 翻譯失敗", ephemeral=True)
        return
    flag = LANG_FLAGS.get(tgt, "🌐")
    usage_stats["context_translations"] += 1
    await interaction.followup.send(
        f"**原文：** {text}\n{flag} **翻譯：** {result}",
        ephemeral=True
    )


# ─── Welcome new members (bilingual) ────────────────────
@bot.event
async def on_member_join(member):
    """Send bilingual welcome message when a new member joins."""
    guild = member.guild
    # Try to find a general/welcome channel
    target_channel = None
    for ch in guild.text_channels:
        if ch.permissions_for(guild.me).send_messages:
            if any(kw in ch.name.lower() for kw in ['一般', 'general', 'welcome', '歡迎']):
                target_channel = ch
                break
    if not target_channel:
        for ch in guild.text_channels:
            if ch.permissions_for(guild.me).send_messages:
                target_channel = ch
                break
    if not target_channel:
        return
    embed = discord.Embed(
        title=f"👋 歡迎 / Selamat Datang!",
        description=(
            f"歡迎 **{member.display_name}** 加入 **{guild.name}**！\n"
            f"Selamat datang **{member.display_name}** di **{guild.name}**!\n\n"
            f"💬 直接打字會自動翻譯 / Ketik langsung otomatis diterjemahkan\n"
            f"📦 輸入 `/help` 查看所有指令 / Ketik `/help` untuk lihat semua perintah"
        ),
        color=0x06C755
    )
    embed.set_thumbnail(url=member.display_avatar.url if member.display_avatar else None)
    await target_channel.send(embed=embed)


# ─── Auto-translate on message ──────────────────────────
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    await bot.process_commands(message)

    # ── DM translation (private message to bot) ──
    if isinstance(message.channel, discord.DMChannel):
        text = message.content.strip()
        if not text:
            return
        src = detect_language(text)
        if not src:
            await message.reply("❌ 無法偵測語言 / Bahasa tidak terdeteksi")
            return
        tgt = "id" if src == "zh" else "zh"
        result = translate(text, src, tgt)
        if result:
            flag = LANG_FLAGS.get(tgt, "🌐")
            embed = discord.Embed(description=safe_embed_text(f"{flag} {result}"), color=0x06C755)
            embed.set_footer(text=f"私訊翻譯 | {LANG_NAMES.get(src, src)[:2]} → {LANG_NAMES.get(tgt, tgt)[:2]}")
            view = TranslateView(text, src, tgt)
            await message.reply(embed=embed, view=view)
            usage_stats["text_translations"] += 1
        return

    ch_id = message.channel.id
    if not channel_settings.get(ch_id, True):
        return
    if message.author.id in channel_skip_users.get(ch_id, set()):
        return

    tgt_lang = channel_target_lang.get(ch_id, "id")

    # ── Handle image attachments ──
    for attachment in message.attachments:
        if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp']):
            # Check if image translation is on
            if not channel_img_settings.get(ch_id, True):
                continue
            try:
                img_bytes = await attachment.read()
                img_b64 = base64.b64encode(img_bytes).decode("utf-8")

                # Check for work order first (if wo enabled)
                if channel_wo_settings.get(ch_id, True):
                    ocr_text = ocr_image_only(img_b64)
                    if ocr_text:
                        customer = detect_work_order(ocr_text)
                        if customer:
                            storage_info = format_storage_for_work_order(customer)
                            if storage_info:
                                embed = discord.Embed(description=storage_info, color=0xFFD700)
                                embed.set_footer(text="📋 工單自動偵測")
                                await message.reply(embed=embed, mention_author=False)
                                usage_stats["work_orders_detected"] += 1
                                continue

                # OCR + translate
                result = ocr_and_translate_image(img_b64, tgt_lang)
                if result:
                    if len(result) > 4000:
                        result = result[:4000] + "..."
                    embed = discord.Embed(title="🖼️ 圖片翻譯", description=result, color=0x06C755)
                    embed.set_footer(text="OCR + 翻譯")
                    await message.reply(embed=embed, mention_author=False)
                    usage_stats["image_translations"] += 1
            except Exception as e:
                logger.error(f"Image processing error: {e}")
            continue

        # ── Handle audio/voice attachments ──
        if any(attachment.filename.lower().endswith(ext) for ext in ['.ogg', '.mp3', '.wav', '.m4a', '.opus', '.flac']):
            # Check if voice translation is on
            if not channel_audio_settings.get(ch_id, True):
                continue
            try:
                audio_bytes = await attachment.read()
                text = transcribe_audio(audio_bytes, attachment.filename)
                if not text:
                    continue

                src = detect_language(text) or "zh"
                tgt = tgt_lang if src == "zh" else "zh"

                result = translate(text, src, tgt) if src != tgt else None
                src_flag = LANG_FLAGS.get(src, "🎤")
                tgt_flag = LANG_FLAGS.get(tgt, "🌐")

                embed = discord.Embed(color=0x06C755)
                embed.add_field(name=f"🎤 語音辨識 {src_flag}", value=text, inline=False)
                if result:
                    embed.add_field(name=f"💬 翻譯 {tgt_flag}", value=result, inline=False)
                embed.set_footer(text="Whisper 語音辨識 + 翻譯")
                await message.reply(embed=embed, mention_author=False)
                usage_stats["voice_translations"] += 1
            except Exception as e:
                logger.error(f"Audio processing error: {e}")
            continue

    # ── Handle text messages ──
    text = message.content.strip()
    if not text or text.startswith("/") or text.startswith("!"):
        return
    if len(text) < 2:
        return

    src = detect_language(text)
    if not src:
        return

    # Auto-assign language role on first detected message
    if message.guild and message.author.id not in user_lang_prefs:
        user_lang_prefs[message.author.id] = src
        await auto_assign_role(message.author, message.guild, src)

    # Determine target: use user's personal pref if set, else channel default
    user_pref = user_lang_prefs.get(message.author.id)
    if src == "zh":
        tgt = tgt_lang
    elif src == tgt_lang:
        tgt = "zh"
    elif user_pref and src == user_pref:
        # User's language detected, translate to opposite
        tgt = "zh" if user_pref != "zh" else tgt_lang
    else:
        tgt = "zh"

    if src == tgt:
        return

    result = translate(text, src, tgt)
    if not result:
        return

    flag = LANG_FLAGS.get(tgt, "🌐")
    embed = discord.Embed(description=safe_embed_text(f"{flag} {result}"), color=0x06C755)
    embed.set_footer(text=f"翻譯 {LANG_NAMES.get(src, src)[:2]} → {LANG_NAMES.get(tgt, tgt)[:2]}")
    view = TranslateView(text, src, tgt)

    try:
        await message.reply(embed=embed, view=view, mention_author=False)
        usage_stats["text_translations"] += 1
    except Exception as e:
        logger.error(f"Reply error: {e}")


# ─── Slash Commands ─────────────────────────────────────

@bot.tree.command(name="help", description="顯示機器人指令說明")
async def cmd_help(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🤖 翻譯小助手",
        description="華新麗華鹽水廠 — 多語翻譯機器人",
        color=0x06C755
    )
    embed.add_field(name="💬 自動翻譯", value="頻道打字自動翻譯＋按鈕切換語言", inline=False)
    embed.add_field(name="🏳️ 旗幟反應翻譯", value="對訊息加 🇮🇩🇹🇼🇬🇧🇻🇳🇹🇭🇯🇵🇰🇷 翻譯", inline=False)
    embed.add_field(name="📋 右鍵翻譯", value="長按訊息 → 應用程式 → **翻譯這段**", inline=False)
    embed.add_field(name="💬 私訊翻譯", value="直接私訊 bot 翻譯", inline=False)
    embed.add_field(name="🖼️ 圖片翻譯", value="上傳圖片自動 OCR 翻譯", inline=False)
    embed.add_field(name="🎤 語音翻譯", value="上傳語音檔 Whisper 辨識翻譯", inline=False)
    embed.add_field(name="📋 工單偵測", value="上傳製造指示書自動查儲區", inline=False)
    embed.add_field(name="👋 歡迎訊息", value="新成員加入自動雙語歡迎", inline=False)
    embed.add_field(name="🏷️ 自動角色", value="偵測語言自動分配語言角色", inline=False)
    embed.add_field(name="📦 /qry <客戶>", value="儲區查詢（自動補全）", inline=True)
    embed.add_field(name="🔍 /search <字>", value="模糊搜尋客戶儲區", inline=True)
    embed.add_field(name="📖 /term <字>", value="工廠術語查詢", inline=True)
    embed.add_field(name="📢 /notice <訊息>", value="雙語公告", inline=True)
    embed.add_field(name="📌 /pin <訊息>", value="雙語公告釘選 🔒", inline=True)
    embed.add_field(name="📝 /handover", value="交班雙語模板", inline=True)
    embed.add_field(name="🚨 /report", value="異常回報表單（Modal）", inline=True)
    embed.add_field(name="📊 /poll", value="雙語投票", inline=True)
    embed.add_field(name="🌐 /mylang", value="個人語言偏好（下拉選單）", inline=True)
    embed.add_field(name="🌐 /lang 🔒", value="頻道語言（管理員）", inline=True)
    embed.add_field(name="🔇 /skip 🔒", value="跳過翻譯（管理員）", inline=True)
    embed.add_field(name="⏸️ /toggle 🔒", value="開關翻譯（管理員）", inline=True)
    embed.add_field(name="🖼️ /img 🔒", value="開關圖片翻譯", inline=True)
    embed.add_field(name="🎤 /voice 🔒", value="開關語音翻譯", inline=True)
    embed.add_field(name="📋 /wo 🔒", value="開關工單偵測", inline=True)
    embed.add_field(name="📃 /skiplist", value="查看跳過名單", inline=True)
    embed.add_field(name="📊 /stats", value="使用統計", inline=True)
    embed.add_field(name="ℹ️ /status", value="頻道設定", inline=True)
    embed.set_footer(text="🔒=管理員限定 | 華新麗華鹽水廠 不鏽鋼事業部")
    usage_stats["slash_commands"] += 1
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="qry", description="查詢客戶儲區位置")
@app_commands.describe(customer="客戶名稱")
async def cmd_qry(interaction: discord.Interaction, customer: str):
    name, entries = query_storage(customer)
    if not entries:
        await interaction.response.send_message(
            f"❌ 找不到客戶「{customer}」\n💡 試試 `/search {customer}` 模糊搜尋",
            ephemeral=True
        )
        return
    embed = discord.Embed(title=f"📦 {name} — 儲區查詢", color=0x06C755)
    for length, area in entries:
        zh = format_length_zh(length)
        embed.add_field(name=zh, value=f"**{area}**", inline=True)
    embed.set_footer(text="儲區資料 | 華新麗華鹽水廠")
    usage_stats["slash_commands"] += 1
    await interaction.response.send_message(embed=embed)


@cmd_qry.autocomplete("customer")
async def qry_autocomplete(interaction: discord.Interaction, current: str):
    if not current:
        choices = [app_commands.Choice(name=k, value=k) for k in sorted(STORAGE_LOOKUP.keys())[:25]]
    else:
        matches = [k for k in STORAGE_LOOKUP.keys() if current.lower() in k.lower()]
        choices = [app_commands.Choice(name=m, value=m) for m in sorted(matches)[:25]]
    return choices


@bot.tree.command(name="search", description="模糊搜尋多個客戶儲區")
@app_commands.describe(keyword="客戶名稱關鍵字")
async def cmd_search(interaction: discord.Interaction, keyword: str):
    kw = keyword.strip().lower()
    matches = [(k, v) for k, v in STORAGE_LOOKUP.items() if kw in k.lower()]
    if not matches:
        await interaction.response.send_message(f"❌ 找不到包含「{keyword}」的客戶", ephemeral=True)
        return
    embed = discord.Embed(title=f"🔍 搜尋結果：{keyword}（{len(matches)} 筆）", color=0x06C755)
    for name, entries in matches[:15]:
        zones = " | ".join(f"{format_length_zh(l)}: {a}" for l, a in entries)
        embed.add_field(name=name, value=zones, inline=False)
    if len(matches) > 15:
        embed.set_footer(text=f"還有 {len(matches) - 15} 筆未顯示，請縮小搜尋範圍")
    usage_stats["slash_commands"] += 1
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="notice", description="發送雙語公告")
@app_commands.describe(message="公告內容（中文或印尼文）")
async def cmd_notice(interaction: discord.Interaction, message: str):
    await interaction.response.defer()
    src = detect_language(message) or "zh"
    ch_id = interaction.channel_id
    tgt_lang = channel_target_lang.get(ch_id, "id")
    tgt = tgt_lang if src == "zh" else "zh"
    result = translate(message, src, tgt)
    if not result:
        await interaction.followup.send("❌ 翻譯失敗，請稍後再試")
        return
    src_flag = LANG_FLAGS.get(src, "")
    tgt_flag = LANG_FLAGS.get(tgt, "")
    embed = discord.Embed(title="📢 公告 / Pengumuman", color=0xFFD700)
    embed.add_field(name=f"{src_flag} 原文", value=message, inline=False)
    embed.add_field(name=f"{tgt_flag} 翻譯", value=result, inline=False)
    embed.set_footer(text=f"由 {interaction.user.display_name} 發送")
    usage_stats["slash_commands"] += 1
    await interaction.followup.send(embed=embed)


@bot.tree.command(name="pin", description="發送雙語公告並釘選（管理員）")
@app_commands.describe(message="公告內容")
async def cmd_pin(interaction: discord.Interaction, message: str):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 需要管理員權限 / Perlu izin admin", ephemeral=True)
        return
    await interaction.response.defer()
    src = detect_language(message) or "zh"
    ch_id = interaction.channel_id
    tgt_lang = channel_target_lang.get(ch_id, "id")
    tgt = tgt_lang if src == "zh" else "zh"
    result = translate(message, src, tgt)
    if not result:
        await interaction.followup.send("❌ 翻譯失敗")
        return
    src_flag = LANG_FLAGS.get(src, "")
    tgt_flag = LANG_FLAGS.get(tgt, "")
    embed = discord.Embed(title="📌 重要公告 / Pengumuman Penting", color=0xFF4444)
    embed.add_field(name=f"{src_flag} 原文", value=message, inline=False)
    embed.add_field(name=f"{tgt_flag} 翻譯", value=result, inline=False)
    embed.set_footer(text=f"由 {interaction.user.display_name} 釘選")
    msg = await interaction.followup.send(embed=embed)
    try:
        await msg.pin()
    except Exception:
        pass
    usage_stats["slash_commands"] += 1


@bot.tree.command(name="handover", description="交班雙語模板")
async def cmd_handover(interaction: discord.Interaction):
    usage_stats["slash_commands"] += 1
    await interaction.response.send_message("選擇交班方向：", view=HandoverView(), ephemeral=True)


@bot.tree.command(name="report", description="異常回報（彈出表單填寫）")
async def cmd_report(interaction: discord.Interaction):
    usage_stats["slash_commands"] += 1
    await interaction.response.send_modal(ReportModal())


@bot.tree.command(name="poll", description="建立雙語投票")
@app_commands.describe(
    question="投票問題",
    option1="選項1", option2="選項2",
    option3="選項3（可選）", option4="選項4（可選）",
)
async def cmd_poll(interaction: discord.Interaction, question: str,
                   option1: str, option2: str,
                   option3: str = None, option4: str = None):
    await interaction.response.defer()
    options = [option1, option2]
    if option3:
        options.append(option3)
    if option4:
        options.append(option4)

    # Translate question and options
    src = detect_language(question) or "zh"
    tgt = "id" if src == "zh" else "zh"

    q_translated = translate(question, src, tgt) or question
    options_translated = []
    for opt in options:
        t = translate(opt, src, tgt) or opt
        options_translated.append(t)

    src_flag = LANG_FLAGS.get(src, "")
    tgt_flag = LANG_FLAGS.get(tgt, "")

    embed = discord.Embed(
        title=f"📊 投票 / Voting",
        color=0x5865F2
    )
    embed.add_field(name=f"{src_flag} {question}", value=f"{tgt_flag} {q_translated}", inline=False)
    for i, (zh, tgt_opt) in enumerate(zip(options, options_translated)):
        embed.add_field(name=f"選項 {i+1}", value=f"{zh} / {tgt_opt}", inline=True)
    embed.set_footer(text=f"由 {interaction.user.display_name} 發起 | 點按鈕投票")

    if src == "zh":
        view = PollView(options, options_translated, tgt_flag)
    else:
        view = PollView(options_translated, options, src_flag)

    usage_stats["slash_commands"] += 1
    await interaction.followup.send(embed=embed, view=view)


@bot.tree.command(name="mylang", description="設定你的個人語言偏好（下拉選單）")
async def cmd_mylang(interaction: discord.Interaction):
    usage_stats["slash_commands"] += 1
    await interaction.response.send_message(
        "🌐 選擇你的語言 / Pilih bahasa kamu:",
        view=LangSelectView(),
        ephemeral=True
    )


@bot.tree.command(name="lang", description="設定頻道目標語言（管理員）")
@app_commands.describe(language="目標語言代碼")
@app_commands.choices(language=[
    app_commands.Choice(name="🇮🇩 印尼文", value="id"),
    app_commands.Choice(name="🇬🇧 英文", value="en"),
    app_commands.Choice(name="🇻🇳 越南文", value="vi"),
    app_commands.Choice(name="🇹🇭 泰文", value="th"),
    app_commands.Choice(name="🇯🇵 日文", value="ja"),
    app_commands.Choice(name="🇰🇷 韓文", value="ko"),
    app_commands.Choice(name="🇲🇾 馬來文", value="ms"),
    app_commands.Choice(name="🇵🇭 菲律賓文", value="tl"),
])
async def cmd_lang(interaction: discord.Interaction, language: app_commands.Choice[str]):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 需要管理員權限 / Perlu izin admin", ephemeral=True)
        return
    channel_target_lang[interaction.channel_id] = language.value
    zh_name = LANG_NAMES_ZH.get(language.value, language.value)
    usage_stats["slash_commands"] += 1
    await interaction.response.send_message(f"✅ 本頻道目標語言已設為 **{language.name}**（{zh_name}）")


@bot.tree.command(name="skip", description="切換使用者翻譯跳過狀態（管理員）")
@app_commands.describe(user="要跳過翻譯的使用者")
async def cmd_skip(interaction: discord.Interaction, user: discord.Member):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 需要管理員權限 / Perlu izin admin", ephemeral=True)
        return
    ch_id = interaction.channel_id
    if ch_id not in channel_skip_users:
        channel_skip_users[ch_id] = set()
    if user.id in channel_skip_users[ch_id]:
        channel_skip_users[ch_id].discard(user.id)
        await interaction.response.send_message(f"✅ **{user.display_name}** 的訊息將恢復翻譯")
    else:
        channel_skip_users[ch_id].add(user.id)
        await interaction.response.send_message(f"🔇 **{user.display_name}** 的訊息將不再翻譯")
    usage_stats["slash_commands"] += 1


@bot.tree.command(name="toggle", description="開關本頻道的自動翻譯（管理員）")
async def cmd_toggle(interaction: discord.Interaction):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 需要管理員權限 / Perlu izin admin", ephemeral=True)
        return
    ch_id = interaction.channel_id
    current = channel_settings.get(ch_id, True)
    channel_settings[ch_id] = not current
    status = "開啟 ✅" if not current else "關閉 ❌"
    usage_stats["slash_commands"] += 1
    await interaction.response.send_message(f"翻譯已{status}")


@bot.tree.command(name="img", description="開關本頻道的圖片翻譯（管理員）")
@app_commands.choices(switch=[
    app_commands.Choice(name="開啟", value="on"),
    app_commands.Choice(name="關閉", value="off"),
])
async def cmd_img(interaction: discord.Interaction, switch: app_commands.Choice[str]):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 需要管理員權限", ephemeral=True)
        return
    channel_img_settings[interaction.channel_id] = (switch.value == "on")
    status = "開啟 ✅" if switch.value == "on" else "關閉 ❌"
    usage_stats["slash_commands"] += 1
    await interaction.response.send_message(f"🖼️ 圖片翻譯已{status}")


@bot.tree.command(name="voice", description="開關本頻道的語音翻譯（管理員）")
@app_commands.choices(switch=[
    app_commands.Choice(name="開啟", value="on"),
    app_commands.Choice(name="關閉", value="off"),
])
async def cmd_voice(interaction: discord.Interaction, switch: app_commands.Choice[str]):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 需要管理員權限", ephemeral=True)
        return
    channel_audio_settings[interaction.channel_id] = (switch.value == "on")
    status = "開啟 ✅" if switch.value == "on" else "關閉 ❌"
    usage_stats["slash_commands"] += 1
    await interaction.response.send_message(f"🎤 語音翻譯已{status}")


@bot.tree.command(name="wo", description="開關本頻道的工單偵測（管理員）")
@app_commands.choices(switch=[
    app_commands.Choice(name="開啟", value="on"),
    app_commands.Choice(name="關閉", value="off"),
])
async def cmd_wo(interaction: discord.Interaction, switch: app_commands.Choice[str]):
    if not is_admin(interaction):
        await interaction.response.send_message("❌ 需要管理員權限", ephemeral=True)
        return
    channel_wo_settings[interaction.channel_id] = (switch.value == "on")
    status = "開啟 ✅" if switch.value == "on" else "關閉 ❌"
    usage_stats["slash_commands"] += 1
    await interaction.response.send_message(f"📋 工單偵測已{status}")


@bot.tree.command(name="skiplist", description="查看本頻道被跳過翻譯的使用者")
async def cmd_skiplist(interaction: discord.Interaction):
    ch_id = interaction.channel_id
    skipped = channel_skip_users.get(ch_id, set())
    if not skipped:
        await interaction.response.send_message("✅ 目前沒有被跳過的使用者", ephemeral=True)
        return
    lines = []
    for uid in skipped:
        member = interaction.guild.get_member(uid) if interaction.guild else None
        name = member.display_name if member else str(uid)
        lines.append(f"• {name}")
    embed = discord.Embed(title="🔇 跳過翻譯名單", description="\n".join(lines), color=0x06C755)
    usage_stats["slash_commands"] += 1
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="stats", description="查看翻譯使用統計")
async def cmd_stats(interaction: discord.Interaction):
    uptime_sec = time.time() - usage_stats["start_time"]
    hours = int(uptime_sec // 3600)
    minutes = int((uptime_sec % 3600) // 60)
    embed = discord.Embed(title="📊 翻譯小助手 使用統計", color=0x06C755)
    embed.add_field(name="⏱️ 運行時間", value=f"{hours}h {minutes}m", inline=True)
    embed.add_field(name="💬 文字翻譯", value=str(usage_stats["text_translations"]), inline=True)
    embed.add_field(name="🖼️ 圖片翻譯", value=str(usage_stats["image_translations"]), inline=True)
    embed.add_field(name="🎤 語音翻譯", value=str(usage_stats["voice_translations"]), inline=True)
    embed.add_field(name="📋 工單偵測", value=str(usage_stats["work_orders_detected"]), inline=True)
    embed.add_field(name="🏳️ 反應翻譯", value=str(usage_stats["reaction_translations"]), inline=True)
    embed.add_field(name="📋 右鍵翻譯", value=str(usage_stats["context_translations"]), inline=True)
    embed.add_field(name="⌨️ 斜線指令", value=str(usage_stats["slash_commands"]), inline=True)
    embed.add_field(name="📦 快取數量", value=str(len(translation_cache)), inline=True)
    embed.add_field(name="👥 儲區客戶", value=str(len(STORAGE_LOOKUP)), inline=True)
    embed.add_field(name="📖 術語詞條", value=str(len(ZH_TO_ID_HARD)), inline=True)
    embed.add_field(name="🤖 GPT 模型", value="gpt-4o-mini", inline=True)
    usage_stats["slash_commands"] += 1
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="status", description="查看本頻道翻譯設定")
async def cmd_status(interaction: discord.Interaction):
    ch_id = interaction.channel_id
    on = channel_settings.get(ch_id, True)
    lang = channel_target_lang.get(ch_id, "id")
    zh_name = LANG_NAMES_ZH.get(lang, lang)
    skip_count = len(channel_skip_users.get(ch_id, set()))
    embed = discord.Embed(title="⚙️ 頻道翻譯設定", color=0x06C755)
    embed.add_field(name="翻譯狀態", value="✅ 開啟" if on else "❌ 關閉", inline=True)
    embed.add_field(name="目標語言", value=f"{LANG_FLAGS.get(lang, '')} {zh_name}", inline=True)
    embed.add_field(name="跳過人數", value=str(skip_count), inline=True)
    usage_stats["slash_commands"] += 1
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="term", description="查詢工廠術語翻譯")
@app_commands.describe(keyword="中文或印尼文關鍵字")
async def cmd_term(interaction: discord.Interaction, keyword: str):
    results = []
    kw = keyword.strip().lower()
    for zh, id_text in ZH_TO_ID_HARD.items():
        if kw in zh.lower() or kw in id_text.lower():
            results.append(f"**{zh}** → {id_text}")
    if not results:
        await interaction.response.send_message(f"❌ 找不到「{keyword}」相關術語", ephemeral=True)
        return
    display = results[:20]
    if len(results) > 20:
        display.append(f"... 還有 {len(results) - 20} 筆")
    embed = discord.Embed(title=f"📖 術語查詢：{keyword}", description="\n".join(display), color=0x06C755)
    usage_stats["slash_commands"] += 1
    await interaction.response.send_message(embed=embed)


# ─── Admin Panel HTML ────────────────────────────────────
ADMIN_HTML = '''<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>翻譯Bot 管理後台 (Discord)</title>
<meta name="theme-color" content="#5865F2">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="DC Bot管理">
<link rel="manifest" href="/manifest.json">
<link rel="apple-touch-icon" href="/icon-192.png">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0a0a0a;color:#e0e0e0;min-height:100vh}
.header{background:linear-gradient(135deg,#5865F2,#7289DA);padding:16px;font-size:18px;font-weight:700;display:flex;align-items:center;gap:8px}
#loginPage{padding:20px;max-width:400px;margin:40px auto}
#loginPage h2{margin-bottom:16px;text-align:center}
#mainPage{display:none}
input[type=password],input[type=text],textarea{width:100%;padding:12px;border:1px solid #333;border-radius:8px;background:#1a1a1a;color:#fff;font-size:15px;margin-bottom:12px}
textarea{min-height:80px;resize:vertical}
.btn{padding:10px 20px;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;display:inline-block}
.btn-purple{background:#5865F2;color:#fff}
.btn-green{background:#06C755;color:#fff}
.btn-red{background:#d93025;color:#fff}
.btn-sm{padding:6px 12px;font-size:12px}
.tabs{display:flex;overflow-x:auto;background:#111;border-bottom:2px solid #222;-webkit-overflow-scrolling:touch}
.tab{padding:12px 14px;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap;color:#888;border-bottom:2px solid transparent;margin-bottom:-2px;flex-shrink:0}
.tab.active{color:#5865F2;border-bottom-color:#5865F2}
.panel{display:none;padding:12px}
.panel.active{display:block}
.card{background:#161616;border-radius:10px;padding:14px;margin-bottom:10px;border:1px solid #222}
.card-title{font-weight:600;font-size:14px;margin-bottom:4px;display:flex;justify-content:space-between;align-items:center}
.card-sub{font-size:12px;color:#888;margin-top:2px}
.badge{font-size:11px;padding:2px 8px;border-radius:10px;font-weight:600;cursor:pointer}
.badge-on{background:#06C75522;color:#06C755}
.badge-off{background:#d9302522;color:#d93025}
.badge-info{background:#5865F222;color:#5865F2}
.stat-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.stat-box{background:#161616;border-radius:8px;padding:12px;text-align:center;border:1px solid #222}
.stat-num{font-size:24px;font-weight:700;color:#5865F2}
.stat-label{font-size:11px;color:#888;margin-top:4px}
.empty{text-align:center;padding:20px;color:#666;font-size:13px}
.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#333;color:#fff;padding:10px 20px;border-radius:8px;font-size:13px;opacity:0;transition:opacity .3s;z-index:999}
.toast.show{opacity:1}
.toggle{position:relative;display:inline-block;width:44px;height:24px}
.toggle input{opacity:0;width:0;height:0}
.slider{position:absolute;cursor:pointer;inset:0;background:#444;border-radius:24px;transition:.3s}
.slider::before{content:"";position:absolute;height:18px;width:18px;left:3px;bottom:3px;background:#fff;border-radius:50%;transition:.3s}
.toggle input:checked+.slider{background:#5865F2}
.toggle input:checked+.slider::before{transform:translateX(20px)}
.wl-item{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #222}
select{width:100%;padding:10px;border-radius:8px;background:#1a1a1a;color:#fff;border:1px solid #333;font-size:14px;margin-bottom:8px}
.inline-select{display:inline;width:auto;padding:2px 6px;font-size:11px;margin:0}
.tag{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:600;margin:2px}
.tag-green{background:#06C75533;color:#06C755}
.tag-blue{background:#5865F233;color:#5865F2}
.tag-red{background:#d9302533;color:#d93025}
.note-card{background:#1a1a1a;border-radius:8px;padding:12px;margin-bottom:8px;border-left:3px solid #5865F2}
.note-time{font-size:11px;color:#666;margin-top:4px}
.note-content{font-size:13px;white-space:pre-wrap}
</style>
</head>
<body>
<div class="header">🤖 翻譯Bot 管理後台 <span style="font-size:12px;color:#ddd;font-weight:400">Discord</span></div>

<div id="loginPage">
<h2>🔐 登入</h2>
<input type="password" id="pwInput" placeholder="輸入管理密碼" onkeydown="if(event.key==='Enter')doLogin()">
<button class="btn btn-purple" style="width:100%" onclick="doLogin()">登入</button>
</div>

<div id="mainPage">
<div class="tabs">
<div class="tab active" onclick="switchTab('dash')">總覽</div>
<div class="tab" onclick="switchTab('channels')">頻道</div>
<div class="tab" onclick="switchTab('notes')">相簿&筆記</div>
<div class="tab" onclick="switchTab('skip')">白名單</div>
<div class="tab" onclick="switchTab('users')">使用者</div>
<div class="tab" onclick="switchTab('storage')">儲區</div>
</div>

<!-- Dashboard -->
<div class="panel active" id="panel-dash">
<div id="statsGrid" class="stat-grid"><div class="empty">載入中...</div></div>
</div>

<!-- Channels -->
<div class="panel" id="panel-channels">
<div id="channelList"><div class="empty">載入中...</div></div>
</div>

<!-- Notes -->
<div class="panel" id="panel-notes">
<div class="card">
<div class="card-title">📝 新增筆記 / 公告</div>
<textarea id="noteInput" placeholder="輸入筆記內容..."></textarea>
<div style="display:flex;gap:8px">
<button class="btn btn-purple btn-sm" onclick="addNote()">新增筆記</button>
<button class="btn btn-green btn-sm" onclick="addNote(true)">📌 釘選公告</button>
</div>
</div>
<div id="notesList"><div class="empty">尚無筆記</div></div>
</div>

<!-- Skip/Whitelist -->
<div class="panel" id="panel-skip">
<select id="skipChannelSelect" onchange="loadSkipList()">
<option value="">選擇頻道...</option>
</select>
<div class="card-sub" style="padding:0 4px 8px;font-size:12px">開啟 = 不翻譯該成員訊息</div>
<div id="skipListContent"><div class="empty">請先選擇頻道</div></div>
</div>

<!-- Users -->
<div class="panel" id="panel-users">
<div id="userList"><div class="empty">載入中...</div></div>
</div>

<!-- Storage -->
<div class="panel" id="panel-storage">
<div class="card">
<div class="card-title">📦 儲區資料更新</div>
<div class="card-sub">上傳 Excel 檔案自動更新儲區查詢資料</div>
<div style="margin-top:12px">
<input type="file" id="storageFile" accept=".xlsx,.xls" style="display:none" onchange="previewStorage()">
<button class="btn btn-purple" onclick="document.getElementById('storageFile').click()">選擇 Excel 檔案</button>
<div id="storageFileName" style="margin-top:8px;font-size:13px;color:#888"></div>
</div>
</div>
<div id="storagePreview"></div>
<div id="storageActions" style="display:none;margin-top:12px">
<button class="btn btn-green" onclick="uploadStorage()">確認更新</button>
</div>
<div class="card" style="margin-top:12px">
<div class="card-title">目前資料</div>
<div id="storageStats"><div class="empty">載入中...</div></div>
<div style="margin-top:10px">
<button class="btn btn-sm" style="background:#333;color:#fff" onclick="downloadJson()">下載 JSON</button>
</div>
</div>
</div>
</div>

<div class="toast" id="toast"></div>

<script>
let KEY='';
const API=window.location.origin+'/api/admin';

function toast(msg){const t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2000)}

async function api(path,method='GET',body=null){
  try{
    const opts={method,headers:{'X-Admin-Key':KEY,'Content-Type':'application/json'}};
    if(body)opts.body=JSON.stringify(body);
    const r=await fetch(API+path,opts);
    if(r.status===403){toast('密碼錯誤');return null}
    return r.json();
  }catch(e){toast('連線失敗，請重試');return null}
}

function doLogin(){
  KEY=document.getElementById('pwInput').value.trim();
  if(!KEY){toast('請輸入密碼');return}
  api('/status').then(d=>{
    if(!d)return;
    document.getElementById('loginPage').style.display='none';
    document.getElementById('mainPage').style.display='block';
    localStorage.setItem('dc_admin_key',KEY);
    loadAll();
  });
}

function switchTab(name){
  const labels={'dash':'總覽','channels':'頻道','notes':'相簿','skip':'白名單','users':'使用者','storage':'儲區'};
  document.querySelectorAll('.tab').forEach(t=>{t.classList.toggle('active',t.textContent.includes(labels[name]))});
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.getElementById('panel-'+name).classList.add('active');
}

function loadAll(){loadStats();loadChannels();loadUsers();loadStorage();loadNotes();loadChannelSelect()}

// ── Dashboard ──
async function loadStats(){
  const d=await api('/stats');
  if(!d)return;
  const el=document.getElementById('statsGrid');
  const labels={'uptime':'⏱️ 運行時間','text_translations':'💬 文字翻譯','image_translations':'🖼️ 圖片翻譯','voice_translations':'🎤 語音翻譯','work_orders_detected':'📋 工單偵測','slash_commands':'⌨️ 指令','reaction_translations':'🏳️ 反應翻譯','context_translations':'📋 右鍵翻譯','cache_count':'📦 快取','customer_count':'👥 客戶','guilds':'🏠 伺服器','channels_active':'📢 頻道','users_known':'👤 使用者'};
  el.innerHTML=Object.entries(d).map(([k,v])=>{
    if(k==='start_time')return '';
    return '<div class="stat-box"><div class="stat-num">'+v+'</div><div class="stat-label">'+(labels[k]||k)+'</div></div>';
  }).join('');
}

// ── Channels ──
async function loadChannels(){
  const d=await api('/channels');
  if(!d)return;
  const el=document.getElementById('channelList');
  if(!d.channels||!d.channels.length){el.innerHTML='<div class="empty">尚無頻道紀錄</div>';return}
  el.innerHTML=d.channels.map(c=>{
    const imgOn=c.img_on!==false;
    const voiceOn=c.voice_on!==false;
    const woOn=c.wo_on!==false;
    return '<div class="card">'+
      '<div class="card-title"><span>#'+c.name+' <span style="color:#666;font-size:11px">'+c.guild+'</span></span>'+
      '<span class="badge '+(c.translation_on?'badge-on':'badge-off')+'" onclick="toggleCh(\''+c.id+'\')">翻譯'+(c.translation_on?'開':'關')+'</span></div>'+
      '<div class="card-sub">語言: <select class="inline-select" onchange="setChLang(\''+c.id+'\',this.value)">'+
      '<option value="id" '+(c.target_lang==='id'?'selected':'')+'>印尼</option>'+
      '<option value="en" '+(c.target_lang==='en'?'selected':'')+'>英文</option>'+
      '<option value="vi" '+(c.target_lang==='vi'?'selected':'')+'>越南</option>'+
      '<option value="th" '+(c.target_lang==='th'?'selected':'')+'>泰文</option>'+
      '<option value="ja" '+(c.target_lang==='ja'?'selected':'')+'>日文</option>'+
      '<option value="ko" '+(c.target_lang==='ko'?'selected':'')+'>韓文</option>'+
      '</select> ｜跳過: '+c.skip_count+'人</div>'+
      '<div style="margin-top:6px">'+
      '<span class="tag '+(imgOn?'tag-green':'tag-red')+'" onclick="toggleChSetting(\''+c.id+'\',\'img\')">'+(imgOn?'🖼️ 圖片開':'🖼️ 圖片關')+'</span> '+
      '<span class="tag '+(voiceOn?'tag-green':'tag-red')+'" onclick="toggleChSetting(\''+c.id+'\',\'voice\')">'+(voiceOn?'🎤 語音開':'🎤 語音關')+'</span> '+
      '<span class="tag '+(woOn?'tag-green':'tag-red')+'" onclick="toggleChSetting(\''+c.id+'\',\'wo\')">'+(woOn?'📋 工單開':'📋 工單關')+'</span>'+
      '</div>'+
      '<div style="margin-top:8px"><button class="btn btn-red btn-sm" onclick="leaveGuild(\''+c.guild_id+'\',\''+c.guild+'\')">退出伺服器: '+c.guild+'</button></div>'+
    '</div>';
  }).join('');
}

async function toggleCh(chId){
  const d=await api('/channel/toggle','POST',{channel_id:chId});
  if(d&&d.ok){toast(d.translation_on?'翻譯已開':'翻譯已關');loadChannels()}
}
async function setChLang(chId,lang){
  const d=await api('/channel/lang','POST',{channel_id:chId,lang:lang});
  if(d&&d.ok)toast('語言已切換: '+lang);
}
async function toggleChSetting(chId,setting){
  const d=await api('/channel/setting','POST',{channel_id:chId,setting:setting});
  if(d&&d.ok){toast(setting+' 已切換');loadChannels()}
}
async function leaveGuild(guildId,name){
  if(!confirm('確定退出「'+name+'」伺服器？'))return;
  const d=await api('/guild/leave','POST',{guild_id:guildId});
  if(d)toast(d.message||'已退出');loadChannels();
}

// ── Notes ──
async function loadNotes(){
  const d=await api('/notes');
  if(!d)return;
  const el=document.getElementById('notesList');
  if(!d.notes||!d.notes.length){el.innerHTML='<div class="empty">尚無筆記</div>';return}
  el.innerHTML=d.notes.map((n,i)=>'<div class="note-card">'+(n.pinned?'📌 ':'')+'<div class="note-content">'+n.content+'</div><div class="note-time">'+n.time+' <span style="cursor:pointer;color:#d93025" onclick="deleteNote('+i+')">刪除</span></div></div>').join('');
}
async function addNote(pin=false){
  const content=document.getElementById('noteInput').value.trim();
  if(!content){toast('請輸入內容');return}
  const d=await api('/notes','POST',{content:content,pinned:pin});
  if(d&&d.ok){toast(pin?'已釘選公告':'已新增筆記');document.getElementById('noteInput').value='';loadNotes()}
}
async function deleteNote(idx){
  if(!confirm('確定刪除？'))return;
  const d=await api('/notes/delete','POST',{index:idx});
  if(d&&d.ok){toast('已刪除');loadNotes()}
}

// ── Skip/Whitelist ──
async function loadChannelSelect(){
  const d=await api('/channels');
  if(!d)return;
  const sel=document.getElementById('skipChannelSelect');
  sel.innerHTML='<option value="">選擇頻道...</option>';
  (d.channels||[]).forEach(c=>{
    const opt=document.createElement('option');
    opt.value=c.id;opt.textContent='#'+c.name+' ('+c.guild+')';
    sel.appendChild(opt);
  });
}
async function loadSkipList(){
  const chId=document.getElementById('skipChannelSelect').value;
  const el=document.getElementById('skipListContent');
  if(!chId){el.innerHTML='<div class="empty">請先選擇頻道</div>';return}
  const d=await api('/channel/members?channel_id='+chId);
  if(!d)return;
  if(!d.members||!d.members.length){el.innerHTML='<div class="empty">尚無成員紀錄</div>';return}
  el.innerHTML=d.members.map(u=>'<div class="wl-item"><span>'+u.name+'</span><label class="toggle"><input type="checkbox" '+(u.skipped?'checked':'')+' onchange="toggleSkipUser(\''+chId+'\',\''+u.id+'\',this.checked)"><span class="slider"></span></label></div>').join('');
}
async function toggleSkipUser(chId,uid,on){
  const d=await api('/user/skip','POST',{channel_id:chId,user_id:uid,skip:on});
  if(d&&d.ok)toast(on?'已跳過翻譯':'已恢復翻譯');
}

// ── Users ──
async function loadUsers(){
  const d=await api('/users');
  if(!d)return;
  const el=document.getElementById('userList');
  if(!d.users||!d.users.length){el.innerHTML='<div class="empty">尚無使用者紀錄</div>';return}
  el.innerHTML=d.users.map(u=>'<div class="card"><div class="card-title"><span>'+u.name+'</span><span class="badge '+(u.lang?'badge-info':'badge-off')+'">'+(u.lang||'未設定')+'</span></div><div class="card-sub">ID: '+u.id+(u.is_admin?' <span style="color:#5865F2">🔑 管理員</span>':'')+'</div></div>').join('');
}

// ── Storage ──
async function loadStorage(){
  const d=await api('/storage');
  if(!d)return;
  document.getElementById('storageStats').innerHTML='<div style="font-size:14px">客戶數: <b>'+d.count+'</b></div>';
}
let storageFileData=null;
function previewStorage(){
  const f=document.getElementById('storageFile').files[0];
  if(!f)return;
  document.getElementById('storageFileName').textContent='📄 '+f.name;
  storageFileData=f;
  document.getElementById('storageActions').style.display='block';
  document.getElementById('storagePreview').innerHTML='<div class="card"><div class="card-sub">點「確認更新」上傳並解析</div></div>';
}
async function uploadStorage(){
  if(!storageFileData){toast('請先選擇檔案');return}
  const fd=new FormData();fd.append('file',storageFileData);
  try{
    const r=await fetch(API+'/storage/upload',{method:'POST',headers:{'X-Admin-Key':KEY},body:fd});
    const d=await r.json();
    if(d.error){toast(d.error);return}
    toast(d.message||'更新成功');
    document.getElementById('storageActions').style.display='none';
    document.getElementById('storagePreview').innerHTML='<div class="card"><div style="color:#06C755;font-weight:600">✅ 已更新 '+d.count+' 筆客戶資料</div></div>';
    loadStorage();
  }catch(e){toast('上傳失敗: '+e)}
}
async function downloadJson(){
  try{
    const r=await fetch(API+'/storage/json',{headers:{'X-Admin-Key':KEY}});
    const blob=await r.blob();const url=URL.createObjectURL(blob);
    const a=document.createElement('a');a.href=url;a.download='storage_data.json';a.click();
    URL.revokeObjectURL(url);toast('JSON 已下載');
  }catch(e){toast('下載失敗')}
}

window.addEventListener('load',()=>{
  const k=localStorage.getItem('dc_admin_key');
  if(k){document.getElementById('pwInput').value=k;doLogin()}
});
if('serviceWorker' in navigator){navigator.serviceWorker.register('/sw.js').catch(()=>{})}
</script>
</body>
</html>'''


# ─── PWA resources ───────────────────────────────────────
DC_MANIFEST = json.dumps({
    "name": "翻譯Bot 管理後台 (Discord)",
    "short_name": "DC Bot管理",
    "start_url": "/admin",
    "display": "standalone",
    "background_color": "#0a0a0a",
    "theme_color": "#5865F2",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}
    ]
}, ensure_ascii=False)

DC_SW_JS = '''const CACHE='dc-bot-admin-v1';
const URLS=['/admin'];
self.addEventListener('install',e=>{e.waitUntil(caches.open(CACHE).then(c=>c.addAll(URLS)))});
self.addEventListener('fetch',e=>{e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)))});'''

def generate_icon_png(size=192):
    """Generate a nice bot icon PNG."""
    import struct, zlib
    width = height = size
    raw = b''
    center = size / 2
    r = size * 0.44  # main radius
    br = size * 0.08  # border radius for rounding

    for y in range(height):
        raw += b'\x00'  # PNG filter: none
        for x in range(width):
            # Normalized coordinates
            nx = (x - center) / (size / 2)
            ny = (y - center) / (size / 2)

            # Rounded square mask
            ax = abs(nx) * 1.1
            ay = abs(ny) * 1.1
            in_shape = max(ax, ay) < 0.88 and (ax + ay) < 1.5

            if not in_shape:
                raw += b'\x0a\x0a\x0a'  # transparent/dark bg
                continue

            # Gradient: top purple #5865F2 → bottom blue #3B44C4
            t = (ny + 1) / 2  # 0 to 1 top to bottom
            pr = int(0x58 * (1 - t) + 0x3B * t)
            pg = int(0x65 * (1 - t) + 0x44 * t)
            pb = int(0xF2 * (1 - t) + 0xC4 * t)

            # Draw chat bubble shape (white)
            bx = nx * 1.3
            by = (ny - 0.05) * 1.5
            in_bubble = (bx * bx + by * by) < 0.35
            # Small triangle at bottom
            in_tri = (abs(bx) < 0.12 and by > 0.28 and by < 0.52)

            # Draw "中" text area (simplified)
            tx = nx + 0.22
            ty = ny - 0.02
            in_left_char = abs(tx) < 0.18 and abs(ty) < 0.2

            # Draw "ID" text area
            rx = nx - 0.22
            ry = ny - 0.02
            in_right_char = abs(rx) < 0.16 and abs(ry) < 0.18

            # Divider line
            in_divider = abs(nx) < 0.015 and abs(ny - 0.0) < 0.22

            if in_bubble or in_tri:
                if in_divider:
                    raw += bytes([pr, pg, pb])  # divider in gradient color
                elif in_left_char:
                    # Left side slightly tinted
                    raw += b'\xF0\xF0\xFF'
                elif in_right_char:
                    raw += b'\xFF\xF0\xF0'
                else:
                    raw += b'\xFF\xFF\xFF'  # white bubble
            else:
                raw += bytes([pr, pg, pb])  # gradient background

    compressed = zlib.compress(raw, 9)
    def make_chunk(chunk_type, data):
        chunk = chunk_type + data
        return struct.pack('>I', len(data)) + chunk + struct.pack('>I', zlib.crc32(chunk) & 0xffffffff)
    ihdr_data = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    png = b'\x89PNG\r\n\x1a\n'
    png += make_chunk(b'IHDR', ihdr_data)
    png += make_chunk(b'IDAT', compressed)
    png += make_chunk(b'IEND', b'')
    return png

# Cache generated icons to avoid regenerating on every request
_icon_cache = {}
def get_icon(size):
    if size not in _icon_cache:
        _icon_cache[size] = generate_icon_png(size)
    return _icon_cache[size]


# ─── Web server + Admin API ──────────────────────────────
async def health_handler(request):
    return web.Response(text='{"status":"ok"}', content_type="application/json")

async def admin_page_handler(request):
    return web.Response(text=ADMIN_HTML, content_type="text/html")

def check_admin_key(request):
    key = request.headers.get("X-Admin-Key", "")
    return key == ADMIN_KEY

async def api_admin_status(request):
    if not check_admin_key(request):
        return web.json_response({"error": "forbidden"}, status=403)
    return web.json_response({"ok": True})

async def api_admin_stats(request):
    if not check_admin_key(request):
        return web.json_response({"error": "forbidden"}, status=403)
    uptime_sec = time.time() - usage_stats["start_time"]
    hours = int(uptime_sec // 3600)
    minutes = int((uptime_sec % 3600) // 60)
    data = {
        "uptime": f"{hours}h {minutes}m",
        "text_translations": usage_stats["text_translations"],
        "image_translations": usage_stats["image_translations"],
        "voice_translations": usage_stats["voice_translations"],
        "work_orders_detected": usage_stats["work_orders_detected"],
        "slash_commands": usage_stats["slash_commands"],
        "reaction_translations": usage_stats["reaction_translations"],
        "context_translations": usage_stats["context_translations"],
        "cache_count": len(translation_cache),
        "customer_count": len(STORAGE_LOOKUP),
        "guilds": len(bot.guilds) if bot.is_ready() else 0,
        "channels_active": len(channel_settings),
        "users_known": len(user_lang_prefs),
    }
    return web.json_response(data)

async def api_admin_channels(request):
    if not check_admin_key(request):
        return web.json_response({"error": "forbidden"}, status=403)
    channels = []
    if bot.is_ready():
        for guild in bot.guilds:
            for ch in guild.text_channels:
                ch_id = ch.id
                if ch_id in channel_settings or ch_id in channel_target_lang or True:
                    channels.append({
                        "id": str(ch_id),
                        "name": ch.name,
                        "guild": guild.name,
                        "guild_id": str(guild.id),
                        "translation_on": channel_settings.get(ch_id, True),
                        "target_lang": channel_target_lang.get(ch_id, "id"),
                        "skip_count": len(channel_skip_users.get(ch_id, set())),
                        "img_on": channel_img_settings.get(ch_id, True),
                        "voice_on": channel_audio_settings.get(ch_id, True),
                        "wo_on": channel_wo_settings.get(ch_id, True),
                    })
    return web.json_response({"channels": channels})

async def api_admin_users(request):
    if not check_admin_key(request):
        return web.json_response({"error": "forbidden"}, status=403)
    users = []
    if bot.is_ready():
        seen = set()
        for guild in bot.guilds:
            for member in guild.members:
                if member.bot or member.id in seen:
                    continue
                seen.add(member.id)
                lang = user_lang_prefs.get(member.id)
                is_admin = member.guild_permissions.administrator or member.guild_permissions.manage_guild
                users.append({
                    "id": str(member.id),
                    "name": member.display_name,
                    "lang": lang or "",
                    "is_admin": is_admin,
                })
    users.sort(key=lambda x: x["name"])
    return web.json_response({"users": users})

async def api_admin_storage(request):
    if not check_admin_key(request):
        return web.json_response({"error": "forbidden"}, status=403)
    return web.json_response({"count": len(STORAGE_LOOKUP)})

async def api_admin_storage_json(request):
    if not check_admin_key(request):
        return web.json_response({"error": "forbidden"}, status=403)
    json_str = json.dumps(STORAGE_LOOKUP, ensure_ascii=False, indent=2)
    return web.Response(
        text=json_str, content_type="application/json",
        headers={"Content-Disposition": "attachment; filename=storage_data.json"}
    )

async def api_admin_storage_upload(request):
    global STORAGE_LOOKUP, CUSTOMER_NAMES
    if not check_admin_key(request):
        return web.json_response({"error": "forbidden"}, status=403)
    try:
        reader = await request.multipart()
        field = await reader.next()
        if not field or field.name != 'file':
            return web.json_response({"error": "沒有檔案"}, status=400)
        data = await field.read()
        if not data:
            return web.json_response({"error": "空的檔案"}, status=400)

        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return web.json_response({"error": "空的 Excel"}, status=400)

        header = [str(c).strip() if c else "" for c in rows[0]]
        new_data = {}

        # Auto-detect format
        len_cols = {}
        for i, h in enumerate(header):
            hl = h.replace(" ", "")
            if "<=3200" in hl and ">3200" not in hl:
                len_cols["<=3200"] = i
            elif ">3200" in hl and "<=4200" in hl:
                len_cols[">3200<=4200"] = i
            elif ">4200" in hl:
                len_cols[">4200"] = i

        if len(len_cols) >= 2:
            cust_col = 0
            for _, row in enumerate(rows[1:], 1):
                if not row or not row[cust_col]:
                    continue
                cust = str(row[cust_col]).strip()
                if not cust:
                    continue
                entries = []
                for length_key, col_idx in len_cols.items():
                    if col_idx < len(row) and row[col_idx]:
                        zone = str(row[col_idx]).strip()
                        if zone:
                            entries.append([length_key, zone])
                if entries:
                    new_data[cust] = entries
        else:
            for row in rows[1:]:
                if not row or len(row) < 3:
                    continue
                cust = str(row[0]).strip() if row[0] else ""
                length_key = str(row[1]).strip() if row[1] else ""
                zone = str(row[2]).strip() if row[2] else ""
                if cust and length_key and zone:
                    if cust not in new_data:
                        new_data[cust] = []
                    new_data[cust].append([length_key, zone])

        if not new_data:
            return web.json_response({"error": "無法解析 Excel，請確認格式"}, status=400)

        STORAGE_LOOKUP = new_data
        CUSTOMER_NAMES = sorted(list(set(list(STORAGE_LOOKUP.keys()) + EXTRA_CUSTOMERS)), key=lambda x: -len(x))
        logger.info("Storage updated via admin: %d customers", len(new_data))

        # Auto-commit to GitHub
        json_str = json.dumps(new_data, ensure_ascii=False, indent=2)
        gh_ok = commit_storage_to_github(json_str)
        msg = f"已更新 {len(new_data)} 筆客戶"
        if gh_ok:
            msg += "（已自動推送 GitHub）"
        else:
            msg += "（GitHub 推送失敗，僅暫時生效）"

        return web.json_response({"ok": True, "count": len(new_data), "github": gh_ok, "message": msg})
    except Exception as e:
        logger.error("Storage upload error: %s", e)
        return web.json_response({"error": f"解析失敗: {str(e)}"}, status=400)


def commit_storage_to_github(json_data):
    """Auto-commit storage_data.json to GitHub repo."""
    if not GITHUB_TOKEN:
        logger.warning("No GITHUB_TOKEN, skipping GitHub commit")
        return False
    try:
        api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/storage_data.json"
        req = urllib.request.Request(api_url, headers={
            "Authorization": "token " + GITHUB_TOKEN,
            "Accept": "application/vnd.github.v3+json"
        })
        sha = None
        try:
            with urllib.request.urlopen(req) as resp:
                existing = json.loads(resp.read().decode())
                sha = existing.get("sha")
        except urllib.error.HTTPError as e:
            if e.code != 404:
                raise
        content_b64 = base64.b64encode(json_data.encode("utf-8")).decode("utf-8")
        body = {"message": "Update storage data via admin panel", "content": content_b64, "branch": "main"}
        if sha:
            body["sha"] = sha
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(api_url, data=data, method="PUT", headers={
            "Authorization": "token " + GITHUB_TOKEN,
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json"
        })
        with urllib.request.urlopen(req) as resp:
            logger.info("Storage committed to GitHub successfully")
            return True
    except Exception as e:
        logger.error("GitHub commit failed: %s", e)
        return False


# ─── Notes storage (in-memory) ───────────────────────────
admin_notes = []  # [{content: str, pinned: bool, time: str}]


# ─── Admin API: channel/user management ──────────────────
async def api_admin_channel_toggle(request):
    if not check_admin_key(request):
        return web.json_response({"error": "forbidden"}, status=403)
    data = await request.json()
    ch_id = int(data.get("channel_id", 0))
    if not ch_id:
        return web.json_response({"error": "missing channel_id"}, status=400)
    current = channel_settings.get(ch_id, True)
    channel_settings[ch_id] = not current
    return web.json_response({"ok": True, "translation_on": not current})

async def api_admin_channel_lang(request):
    if not check_admin_key(request):
        return web.json_response({"error": "forbidden"}, status=403)
    data = await request.json()
    ch_id = int(data.get("channel_id", 0))
    lang = data.get("lang", "id")
    if not ch_id:
        return web.json_response({"error": "missing channel_id"}, status=400)
    channel_target_lang[ch_id] = lang
    return web.json_response({"ok": True, "lang": lang})

async def api_admin_channel_setting(request):
    """Toggle img/voice/wo per channel."""
    if not check_admin_key(request):
        return web.json_response({"error": "forbidden"}, status=403)
    data = await request.json()
    ch_id = int(data.get("channel_id", 0))
    setting = data.get("setting", "")
    if not ch_id or setting not in ("img", "voice", "wo"):
        return web.json_response({"error": "invalid"}, status=400)
    settings_map = {"img": channel_img_settings, "voice": channel_audio_settings, "wo": channel_wo_settings}
    store = settings_map[setting]
    current = store.get(ch_id, True)
    store[ch_id] = not current
    return web.json_response({"ok": True, "value": not current})

async def api_admin_channel_members(request):
    """Get members of a channel for skip list."""
    if not check_admin_key(request):
        return web.json_response({"error": "forbidden"}, status=403)
    ch_id = int(request.query.get("channel_id", 0))
    if not ch_id or not bot.is_ready():
        return web.json_response({"members": []})
    skipped = channel_skip_users.get(ch_id, set())
    members = []
    for guild in bot.guilds:
        channel = guild.get_channel(ch_id)
        if channel:
            for member in guild.members:
                if not member.bot:
                    members.append({
                        "id": str(member.id),
                        "name": member.display_name,
                        "skipped": member.id in skipped,
                    })
            break
    members.sort(key=lambda x: x["name"])
    return web.json_response({"members": members})

async def api_admin_user_skip(request):
    if not check_admin_key(request):
        return web.json_response({"error": "forbidden"}, status=403)
    data = await request.json()
    uid = int(data.get("user_id", 0))
    ch_id = int(data.get("channel_id", 0))
    skip = data.get("skip", True)
    if not uid or not ch_id:
        return web.json_response({"error": "missing params"}, status=400)
    if ch_id not in channel_skip_users:
        channel_skip_users[ch_id] = set()
    if skip:
        channel_skip_users[ch_id].add(uid)
    else:
        channel_skip_users[ch_id].discard(uid)
    return web.json_response({"ok": True, "skipped": skip})

async def api_admin_guild_leave(request):
    """Leave a Discord guild/server."""
    if not check_admin_key(request):
        return web.json_response({"error": "forbidden"}, status=403)
    data = await request.json()
    guild_id = int(data.get("guild_id", 0))
    if not guild_id or not bot.is_ready():
        return web.json_response({"error": "invalid"}, status=400)
    guild = bot.get_guild(guild_id)
    if guild:
        try:
            await guild.leave()
            return web.json_response({"ok": True, "message": f"已退出 {guild.name}"})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
    return web.json_response({"error": "找不到伺服器"}, status=404)

async def api_admin_notes_get(request):
    if not check_admin_key(request):
        return web.json_response({"error": "forbidden"}, status=403)
    return web.json_response({"notes": admin_notes})

async def api_admin_notes_add(request):
    if not check_admin_key(request):
        return web.json_response({"error": "forbidden"}, status=403)
    data = await request.json()
    content = data.get("content", "").strip()
    pinned = data.get("pinned", False)
    if not content:
        return web.json_response({"error": "empty"}, status=400)
    note = {"content": content, "pinned": pinned, "time": time.strftime("%Y-%m-%d %H:%M")}
    if pinned:
        admin_notes.insert(0, note)  # Pinned at top
    else:
        admin_notes.append(note)
    return web.json_response({"ok": True})

async def api_admin_notes_delete(request):
    if not check_admin_key(request):
        return web.json_response({"error": "forbidden"}, status=403)
    data = await request.json()
    idx = data.get("index", -1)
    if 0 <= idx < len(admin_notes):
        admin_notes.pop(idx)
        return web.json_response({"ok": True})
    return web.json_response({"error": "invalid index"}, status=400)

async def manifest_handler(request):
    return web.Response(text=DC_MANIFEST, content_type="application/manifest+json")

async def sw_handler(request):
    return web.Response(text=DC_SW_JS, content_type="application/javascript")

async def icon_handler(request):
    # Serve uploaded icon.png from repo, fallback to generated
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icon.png")
    if os.path.exists(icon_path):
        with open(icon_path, "rb") as f:
            return web.Response(body=f.read(), content_type="image/png",
                                headers={"Cache-Control": "public, max-age=86400"})
    size = 512 if "512" in request.path else 192
    return web.Response(body=get_icon(size), content_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})

async def start_web_server():
    app = web.Application()
    app.router.add_get("/health", health_handler)
    app.router.add_get("/", health_handler)
    app.router.add_get("/admin", admin_page_handler)
    app.router.add_get("/manifest.json", manifest_handler)
    app.router.add_get("/sw.js", sw_handler)
    app.router.add_get("/icon-192.png", icon_handler)
    app.router.add_get("/icon-512.png", icon_handler)
    app.router.add_get("/api/admin/status", api_admin_status)
    app.router.add_get("/api/admin/stats", api_admin_stats)
    app.router.add_get("/api/admin/channels", api_admin_channels)
    app.router.add_get("/api/admin/users", api_admin_users)
    app.router.add_get("/api/admin/storage", api_admin_storage)
    app.router.add_get("/api/admin/storage/json", api_admin_storage_json)
    app.router.add_post("/api/admin/storage/upload", api_admin_storage_upload)
    app.router.add_post("/api/admin/channel/toggle", api_admin_channel_toggle)
    app.router.add_post("/api/admin/channel/lang", api_admin_channel_lang)
    app.router.add_post("/api/admin/channel/setting", api_admin_channel_setting)
    app.router.add_get("/api/admin/channel/members", api_admin_channel_members)
    app.router.add_post("/api/admin/user/skip", api_admin_user_skip)
    app.router.add_post("/api/admin/guild/leave", api_admin_guild_leave)
    app.router.add_get("/api/admin/notes", api_admin_notes_get)
    app.router.add_post("/api/admin/notes", api_admin_notes_add)
    app.router.add_post("/api/admin/notes/delete", api_admin_notes_delete)
    port = int(os.environ.get("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Web server started on port {port} (admin: /admin)")

# ─── Run ────────────────────────────────────────────────
async def main():
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN not set!")
        exit(1)
    await start_web_server()
    async with bot:
        await bot.start(DISCORD_TOKEN)

if __name__ == "__main__":
    asyncio.run(main())
