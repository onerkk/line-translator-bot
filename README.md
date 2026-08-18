# LINE Bot 繁體中文 ↔ 印尼文 自動翻譯機器人

群組裡有人打中文 → 自動翻譯成印尼文 🇮🇩
群組裡有人打印尼文 → 自動翻譯成繁體中文 🇹🇼

---

## 運作方式

- 自動偵測語言（中文 / 印尼文）
- 翻譯引擎：使用設定中的 AI 翻譯供應商；中印互譯預設禁止未受工廠規則約束的通用 NMT 靜默降級
- 文字與圖片 OCR 共用同一套工廠術語、情境知識、翻譯記憶與品質閘門
- `Depan/Tengah/Belakang/Kebulatan` 等完整設備量測表會由本機來源欄位直接翻譯，不依賴第二次 AI 複核
- 工廠詞庫使用字首樹（Trie）最長詞索引，只注入本句實際命中的詞條
- 支援工廠單位簡稱與 ERP 股別，例如「一課」=`Seksi 1`、「一股」=`Bagian Cold Drawing 1`、「一股股長」=`kepala bagian Cold Drawing 1`；`Regu` 僅用於班／工作小組
- 太短的訊息（< 2 字）自動忽略，避免洗版
- 翻譯結果前面會加國旗 emoji 方便辨識

### 2026-08-18 業界功能整合：品質、效率與費用

- **人工修正審核制**：一般成員可用 `/wrong 正確譯文` 回報，但資料先進入 `pending`；只有管理後台核准的內容會進入案例庫與精確 TM。駁回或刪除已核准修正時，會比對來源、譯文與 provenance 後安全回滾，避免錯誤回饋污染後續翻譯。
- **外部供應商敏感資料遮罩**：電子郵件、台灣／印尼手機、台灣身分證格式、具明確標籤的 NIK／KTP／ARC／護照／員編／銀行帳號，以及設定中的保護名，會先在本機換成可逆 placeholder；供應商回覆後再於本機還原。若主翻譯漏掉 placeholder，候選會被拒收。此功能不增加 API 呼叫。
- **混合語句處理**：中文夾印尼文／英文等自然語句會在本機標記 code-switching，再用同一次 LLM 呼叫完整翻成目標語言；不切段、不增加 API 次數，機台碼與人名不會被誤判為第二語言。
- **成本安全預設**：人工修正只寫精確 TM；付費 embedding 的向量同步預設關閉。正面表情回饋必須包含管理員核可才可升格為訓練範例。高成本的全量複核、串流口譯、完整影片字幕與版面保留文件翻譯不會被此次更新偷偷開啟。

可選環境變數：

```bash
# 預設 mask；只有明確接受原始敏感資料送往外部供應商時才設 off
TRANSLATION_PRIVACY_MODE=mask

# 預設 0：精確 TM 已可處理同句；設 1 才為每筆核准修正建立付費向量
ACTIVE_LEARNING_VECTOR_SYNC=0

# 預設 1：表情共識仍需至少一名管理員核可才可進入範例
REACTION_PROMOTION_REQUIRES_ADMIN=1
```

注意：文字遮罩無法先讀取圖片內的資料；若開啟遠端 Vision/OCR，原圖仍會送往所選 AI 供應商。需要完全不外送圖片時，應停用遠端圖片翻譯。

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
- 至少要設定一個可用的 AI 翻譯供應商。中印工廠翻譯預設不會退回一般 Google Translate；若供應商失敗，系統會拒絕學習或回傳未驗證的通用譯文

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
├── translation_privacy.py         # 外部供應商敏感資料遮罩與本機還原
├── active_learning.py             # 待審／核准／駁回人工修正工作流
├── factory_translation_policy.py  # 中印互譯統一工廠路由、複核與失敗封鎖政策
├── factory_translation_guard.py   # 統一驗收、精確案例、不可變資料與禁用錯譯檢查
├── factory_terminology.py         # 大量工廠術語 Trie、別名與單位解析
├── glossary_data.json             # 標準詞庫、標準譯法與禁用譯法
├── glossary_policy.py             # 詞庫標準化與舊資料遷移規則
├── glossary_enforcement.py        # 雙向術語合規與反向安全索引
├── factory_knowledge.json         # 工廠上下文知識、流程與已確認修正案例
├── factory_translation_regression.json # 30 組正式歷史工廠翻譯回歸案例
├── validate_factory_translation_assets.py # 無需 Flask/LINE/API 的離線發布驗證器
├── requirements.txt               # Python 套件
├── Dockerfile                     # Docker 部署用
└── README.md                      # 這份說明
```

## 統一工廠翻譯路由（2026-07-25）

本專案的繁體中文 ↔ 印尼文翻譯預設全部進入同一套工廠語義管線，不再先把訊息當成一般生活用語。文字訊息與圖片 OCR 會依序使用：

1. `factory_translation_policy.py`：統一決定工廠路由、是否必須來源複核、複核失敗是否封鎖，以及是否允許通用 NMT 備援。
2. `glossary_data.json`／`glossary_policy.py`：提供唯一標準詞、禁用譯法與舊資料遷移規則。
3. `factory_knowledge.json`：保存需要整句語境判斷的流程、角色、設備與已確認修正案例。
4. `translation_casebook.py`：只允許來源完全相同且通過工廠語義驗證的人工修正直接命中。
5. `translation_quality_gate.py`：一般且本地驗證通過的訊息維持一次模型呼叫；高風險、語義不完整或本地驗證失敗時才做一次來源複核。
6. `factory_translation_guard.py`：在模型輸出、最終交付、快取、TM、主動學習、表情裝飾與 OCR 路徑上使用同一套驗收邊界。
7. `factory_translation_regression.json`：保存 30 組正式歷史案例及禁用錯譯探針，防止改版回歸。

生產預設：

```bash
FACTORY_TRANSLATION_MODE=always
FACTORY_TRANSLATION_REVIEW_MODE=adaptive
FACTORY_TRANSLATION_REQUIRE_REVIEW_SUCCESS=0
FACTORY_ALLOW_GENERIC_NMT_FALLBACK=0
```

`FACTORY_TRANSLATION_MODE=auto` 僅適合臨時測試；`off` 會停用統一工廠路由。`adaptive` 會讓一般乾淨訊息只呼叫一次模型，重大安全、品質、衝突指示或本地驗證失敗才複核；確定性的職稱校正與術語檢查仍在每次翻譯執行。若確實需要逐筆複核，必須同時設定 `FACTORY_TRANSLATION_REVIEW_MODE=always` 與 `FACTORY_ALLOW_ALWAYS_REVIEW=1`，避免舊環境變數無意間把時間與費用加倍。未通過驗證的結果不會寫入快取或學習資料。

發布前執行：

```bash
python validate_factory_translation_assets.py --json
```

完整變更與驗證方式請參閱 `ROOT_FIX_2026-07-25_UNIFIED_FACTORY_TRANSLATION.md`。
