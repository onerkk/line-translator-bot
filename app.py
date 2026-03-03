"""
LINE Bot - 繁體中文 ↔ 印尼文 自動翻譯機器人
當群組中有人發送繁體中文，自動翻譯成印尼文
當群組中有人發送印尼文，自動翻譯成繁體中文
"""

import os
import re
import json
import hashlib
import hmac
import base64
import logging
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError
from openai import OpenAI

# ===== 設定 =====
app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# LINE Bot 設定
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")

# OpenAI API 設定 (用於高品質翻譯)
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

# 初始化 LINE Bot
configuration = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# 初始化 OpenAI
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


# ===== 語言偵測 =====
def contains_chinese(text):
    """偵測文字是否包含中文字元"""
    chinese_pattern = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')
    matches = chinese_pattern.findall(text)
    # 至少要有2個中文字，或中文字佔比超過30%才算中文
    if len(matches) >= 2:
        return True
    if len(text) > 0 and len(matches) / len(text) > 0.3:
        return True
    return False


def contains_indonesian(text):
    """
    偵測文字是否為印尼文
    印尼文使用拉丁字母，常見特徵詞彙判斷
    """
    # 先排除含有中文的文字
    if contains_chinese(text):
        return False

    # 如果全是 ASCII / 拉丁字母為主
    latin_pattern = re.compile(r'[a-zA-Zàáâãäåèéêëìíîïòóôõöùúûüýÿ]')
    latin_chars = latin_pattern.findall(text)
    total_alpha = len(latin_chars)

    if total_alpha < 3:
        return False

    text_lower = text.lower()

    # 印尼文常見詞彙和特徵（高頻詞）
    indonesian_markers = [
        # 常見詞
        'yang', 'dan', 'ini', 'itu', 'ada', 'untuk', 'dengan', 'dari',
        'tidak', 'akan', 'sudah', 'bisa', 'juga', 'saya', 'kami', 'kita',
        'mereka', 'dia', 'apa', 'bagaimana', 'kenapa', 'mengapa', 'kapan',
        'dimana', 'siapa', 'belum', 'sudah', 'sedang', 'telah', 'harus',
        'boleh', 'mau', 'ingin', 'perlu', 'bukan', 'jangan', 'tolong',
        'terima kasih', 'selamat', 'pagi', 'siang', 'sore', 'malam',
        'baik', 'bagus', 'benar', 'salah', 'besar', 'kecil',
        'makan', 'minum', 'tidur', 'kerja', 'pulang', 'pergi',
        'rumah', 'kantor', 'uang', 'harga', 'berapa', 'banyak',
        'sedikit', 'semua', 'setiap', 'antara', 'seperti', 'karena',
        'tetapi', 'tapi', 'atau', 'jika', 'kalau', 'supaya', 'agar',
        'sampai', 'masih', 'lagi', 'saja', 'dulu', 'nanti', 'sekarang',
        'hari', 'minggu', 'bulan', 'tahun', 'waktu', 'jam',
        # 印尼文特有前綴/後綴組合
        'meng', 'mem', 'men', 'meny', 'ber', 'per', 'ter', 'kan', 'nya',
        # 口語
        'gak', 'nggak', 'udah', 'gimana', 'gitu', 'dong', 'sih', 'nih',
        'loh', 'kok', 'yuk', 'ayo', 'banget', 'bgt',
    ]

    # 計算匹配的印尼文標記詞數量
    words = re.findall(r'\b\w+\b', text_lower)
    match_count = 0
    for word in words:
        if word in indonesian_markers:
            match_count += 1
        # 檢查前綴後綴
        for marker in ['meng', 'mem', 'men', 'meny', 'ber', 'per', 'ter']:
            if word.startswith(marker) and len(word) > len(marker) + 2:
                match_count += 0.5
                break
        if word.endswith('kan') or word.endswith('nya') or word.endswith('lah'):
            match_count += 0.3

    # 如果匹配詞數 >= 2 或佔比超過 20%，判定為印尼文
    if match_count >= 2:
        return True
    if len(words) > 0 and match_count / len(words) > 0.2:
        return True

    return False


def detect_language(text):
    """
    偵測語言，回傳 'zh' (中文), 'id' (印尼文), 或 None (無法判斷)
    """
    # 去除空白和表情符號
    clean_text = text.strip()
    if not clean_text or len(clean_text) < 2:
        return None

    # 先檢查中文
    if contains_chinese(clean_text):
        return 'zh'

    # 再檢查印尼文
    if contains_indonesian(clean_text):
        return 'id'

    return None


# ===== 翻譯功能 =====
def translate_with_openai(text, source_lang, target_lang):
    """使用 OpenAI GPT 進行高品質翻譯"""
    if not openai_client:
        return None

    if source_lang == 'zh' and target_lang == 'id':
        prompt = f"""Terjemahkan teks Mandarin berikut ke Bahasa Indonesia yang natural dan mudah dipahami.
Jangan tambahkan penjelasan, cukup terjemahan saja.

Teks: {text}"""
    else:
        prompt = f"""請將以下印尼文翻譯成自然流暢的繁體中文，要讓一般台灣人都能看懂。
不要加任何解釋，只要翻譯結果。

文字：{text}"""

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {
                    "role": "system",
                    "content": "你是專業的中文-印尼文翻譯員。翻譯要自然流暢，像母語者說話一樣，避免生硬的直譯。"
                },
                {"role": "user", "content": prompt}
            ],
            temperature=0.3,
            max_tokens=2000,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"OpenAI 翻譯錯誤: {e}")
        return None


def translate_with_google(text, source_lang, target_lang):
    """
    使用 Google Translate 免費 API 作為備用方案
    注意：這是非官方 API，大量使用可能被限制
    """
    import urllib.request
    import urllib.parse

    try:
        encoded_text = urllib.parse.quote(text)
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl={source_lang}&tl={target_lang}&dt=t&q={encoded_text}"

        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0')

        with urllib.request.urlopen(req, timeout=10) as response:
            result = json.loads(response.read().decode('utf-8'))
            translated = ''.join([item[0] for item in result[0] if item[0]])
            return translated
    except Exception as e:
        logger.error(f"Google 翻譯錯誤: {e}")
        return None


def translate(text, source_lang, target_lang):
    """
    翻譯主函式，優先使用 OpenAI，備用 Google Translate
    """
    # 優先用 OpenAI（品質較好）
    result = translate_with_openai(text, source_lang, target_lang)
    if result:
        return result

    # 備用：Google Translate
    sl = 'zh-TW' if source_lang == 'zh' else 'id'
    tl = 'id' if target_lang == 'id' else 'zh-TW'
    result = translate_with_google(text, sl, tl)
    if result:
        return result

    return None


# ===== LINE Bot Webhook =====
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data(as_text=True)
    logger.info(f"收到 Webhook 請求")

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        logger.error("簽名驗證失敗")
        abort(400)

    return "OK"


@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    """處理收到的文字訊息"""
    text = event.message.text.strip()

    # 忽略太短的訊息
    if len(text) < 2:
        return

    # 忽略指令訊息（以 / 或 ! 開頭）
    if text.startswith('/') or text.startswith('!'):
        return

    # 偵測語言
    lang = detect_language(text)

    if lang is None:
        # 無法判斷語言，不翻譯
        return

    # 決定翻譯方向
    if lang == 'zh':
        translated = translate(text, 'zh', 'id')
        if translated:
            reply_text = f"🇮🇩 {translated}"
        else:
            return
    elif lang == 'id':
        translated = translate(text, 'id', 'zh')
        if translated:
            reply_text = f"🇹🇼 {translated}"
        else:
            return
    else:
        return

    # 回覆翻譯結果
    with ApiClient(configuration) as api_client:
        messaging_api = MessagingApi(api_client)
        messaging_api.reply_message(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=reply_text)]
            )
        )


@app.route("/health", methods=["GET"])
def health_check():
    """健康檢查端點"""
    return {"status": "ok", "service": "line-translator-bot"}


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
