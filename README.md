# LINE Bot 繁體中文 ↔ 印尼文 自動翻譯機器人

群組裡有人打中文 → 自動翻譯成印尼文 🇮🇩
群組裡有人打印尼文 → 自動翻譯成繁體中文 🇹🇼

---

## 運作方式

- 自動偵測語言（中文 / 印尼文）
- 翻譯引擎：使用設定中的 OpenAI 翻譯模型，並依現有備援策略處理失敗
- 文字與圖片 OCR 共用同一套工廠術語、情境知識、翻譯記憶與品質閘門
- 工廠詞庫使用字首樹（Trie）最長詞索引，只注入本句實際命中的詞條
- 支援工廠單位簡稱與 ERP 股別，例如「一課」=`Seksi 1`、「一股」=`Bagian Cold Drawing 1`、「一股股長」=`kepala bagian Cold Drawing 1`；`Regu` 僅用於班／工作小組
- 太短的訊息（< 2 字）自動忽略，避免洗版
- 翻譯結果前面會加國旗 emoji 方便辨識

---

## 你需要準備的東西

### 1. LINE Bot（免費）

1. 到 [LINE Developers Console](https://developers.line.biz/console/) 登入
2. 建立一個 **Provider**
3. 建立一個 **Messaging API Channel**
4. 在 Channel 設定頁面取得：
   - **Channel Secret**（在 Basic settings 頁面）
   - **Channel Access Token**（在 Messaging API 頁面，點「Issue」產生）
5. 關閉「Auto-reply messages」（在 LINE Official Account Manager → 回應設定）

### 2. OpenAI API Key

1. 到 [OpenAI Platform](https://platform.openai.com/api-keys) 申請
2. 建立一組 API Key
3. 儲值一點額度（預設使用 gpt-5.4-mini；需要最低成本可改用 gpt-5.4-nano）

### 3. 一台伺服器（以下任選一個）

推薦免費/便宜的方案：

| 平台 | 費用 | 難度 |
|------|------|------|
| [Railway](https://railway.app) | 每月 $5 美金有免費額度 | ⭐ 最簡單 |
| [Render](https://render.com) | 免費方案可用 | ⭐ 簡單 |
| [Fly.io](https://fly.io) | 免費額度 | ⭐⭐ 中等 |
| [Google Cloud Run](https://cloud.google.com) | 免費額度 | ⭐⭐⭐ 進階 |
| 自己的 VPS | 看方案 | ⭐⭐⭐ 進階 |

---

## 部署教學

### 方法一：Railway（最推薦，最簡單）

```bash
# 1. 安裝 Railway CLI
npm install -g @railway/cli

# 2. 登入
railway login

# 3. 在專案資料夾初始化
cd line-translator-bot
railway init

# 4. 設定環境變數
railway variables set LINE_CHANNEL_ACCESS_TOKEN=你的token
railway variables set LINE_CHANNEL_SECRET=你的secret
railway variables set OPENAI_API_KEY=你的key

# 5. 部署
railway up
```

部署完成後 Railway 會給你一個網址，例如 `https://xxx.up.railway.app`

### 方法二：Render

1. 把程式碼推到 GitHub
2. 到 Render Dashboard 建立 New Web Service
3. 連結你的 GitHub repo
4. 設定：
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn --bind 0.0.0.0:8080 --workers 1 --threads 4 --timeout 180 app:app`
5. 在 Environment 頁面加入三個環境變數
6. 部署完成後取得網址

### 方法三：本地測試（用 ngrok）

```bash
# 1. 安裝套件
pip install -r requirements.txt

# 2. 設定環境變數
export LINE_CHANNEL_ACCESS_TOKEN=你的token
export LINE_CHANNEL_SECRET=你的secret
export OPENAI_API_KEY=你的key

# 3. 啟動伺服器
python app.py

# 4. 另開一個終端，用 ngrok 建立公開網址
ngrok http 8080
```

ngrok 會給你一個網址，例如 `https://xxxx.ngrok-free.app`

---

## 設定 LINE Webhook

1. 回到 [LINE Developers Console](https://developers.line.biz/console/)
2. 進入你的 Channel → Messaging API 頁面
3. 設定 **Webhook URL**：`https://你的網址/callback`
4. 打開 **Use webhook**
5. 點 **Verify** 測試連線

---

## 把 Bot 加入群組

1. 在 LINE 加 Bot 為好友（掃描 LINE Developers 上的 QR Code）
2. 把 Bot 邀請進你的群組
3. 開始聊天，Bot 會自動翻譯！

---

## 使用效果範例

```
阿明：今天加班到幾點？
🇮🇩 Bot：Hari ini lembur sampai jam berapa?

Sari：Mungkin sampai jam 8 malam
🇹🇼 Bot：大概到晚上8點

阿明：好，辛苦了
🇮🇩 Bot：Baik, terima kasih atas kerja kerasnya

Dewi：Terima kasih, bos
🇹🇼 Bot：謝謝，老闆
```

---

## 常見問題

### Bot 沒有回覆？
- 確認 Webhook URL 設定正確（結尾要有 `/callback`）
- 確認環境變數都有設定
- 確認 LINE Official Account 的自動回覆已關閉
- 檢查伺服器 log 看有沒有錯誤

### 翻譯品質不好？
- 確認 OPENAI_API_KEY 有設定且有餘額
- 沒有 OpenAI Key 的話會退回用 Google Translate，品質會差一些

### 有些訊息沒翻譯？
- 太短的訊息（少於 2 字）會自動跳過
- 純英文不會翻譯（因為無法判斷是中文還是印尼文的情境）
- 純表情符號或貼圖不會翻譯

### 費用大概多少？
- LINE Bot：免費
- OpenAI：`gpt-5.4-mini` 為預設翻譯模型；`gpt-5.4-nano` 適合大量簡短訊息
  - 實際費用依訊息長度、圖片、快取命中與背景品檢次數計算
- 伺服器：看你選的平台，Railway 免費額度夠小群組用

---

## 進階自訂

- `glossary_data.json`：工廠標準詞、中文／印尼文別名、優先序、OCR 提示與反向安全設定。
- `factory_knowledge.json`：需要上下文判斷的工廠流程、禁用譯法與必要概念。
- `factory_terminology.py`：共用字首樹索引、組織單位解析與 OCR 安全正規化。
- `app.py`：語言偵測、翻譯模型、LINE 訊息流程與管理介面。

大量詞庫維護方式與欄位格式請參閱 `ROOT_FIX_2026-07-18_FACTORY_TERMINOLOGY_ENGINE.md`。

---

## 檔案結構

```
line-translator-bot/
├── app.py                         # 主程式與標準翻譯管線
├── factory_terminology.py         # 大量工廠術語 Trie、別名與單位解析
├── glossary_data.json             # 標準詞庫
├── glossary_enforcement.py        # 雙向術語合規與反向安全索引
├── factory_knowledge.json         # 工廠上下文知識與禁用譯法
├── requirements.txt               # Python 套件
├── Dockerfile                     # Docker 部署用
└── README.md                      # 這份說明
```
