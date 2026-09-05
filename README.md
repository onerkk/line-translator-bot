## 2026-09-05 第二版：錯字、近義句與工廠操作狀態

這次 ZIP 是相對最初上傳專案的**累積更新**，包含上一版修正；不論是否已套用上一包，都可解壓覆蓋專案根目錄後重新部署。ZIP 僅放變更／新增檔案，保留既有詞庫、設定與資料庫。新增模組 `factory_source_understanding.py`、`translation_adaptive_memory.py` 必須與本包其他檔案一起部署。

### 這次修正的 Bug

- 印尼文→中文的人工修正案例，以前在相似句搜尋時一律拿不到中文專用的「內容錨點」，因此被排除。現在使用印尼文內容詞、工廠概念及字元特徵，讓 `cek bahan` 能參考 `periksa material` 的既有案例。
- 舊 SQLite／向量記憶查詢只有「本群組優先排序」，未排除其他群組。現在只讀本群組及明確全域資料；人工修正的舊介面相容路徑也同樣隔離。
- token-set 分數 100 或向量很相似，不表示句意相同。以前「可以使用」可能直接套給「不可以使用」；現在普通近似命中只作 AI 參考，直接重用必須符合更嚴格的來源與驗證條件。
- 翻譯記憶中的案例原文、理由與譯文會做 XML 文字跳脫，避免案例中的標籤截斷系統提示。
- 向量記憶沒有本群組可用資料、或僅有舊 embedding 模型的資料時，不再先付費產生查詢向量。
- 新增可辨識操作的局部狀態檢查，保護「尚未修理／已修理」「禁止使用／可以使用」等差異。此檢查涵蓋最終交付、快取及人工修正驗證，並保留疑問句和自然譯法的合理差異。

### 新增與補強的功能

| 功能 | 工廠使用方式 | API 與品質處理 |
| --- | --- | --- |
| 錯字與口語縮寫理解 | `稱重→秤重`、`標纖→標籤`、`blm→belum`、`jgn→jangan` 等可辨識變體 | 本機處理，原始訊息不被覆寫；人名、mention、代碼及連結受保護 |
| 雙向近義句案例檢索 | 用詞、詞形或語序不同時，仍查找相關工廠案例與核准修正 | 本機特徵與有上限的快取，不新增付費 embedding；一般改寫由現有 AI 依本句翻譯 |
| 新錯字提示 | 依工廠詞彙及目前詞庫找出唯一近似拼字，例如 `perbaikna` | 只給同一次翻譯呼叫作判斷；有多種可能時不擅改設備、數字或人名 |
| 核准變體快速翻譯 | 已核准句只出現明確同義／拼字變化時，嘗試沿用譯文 | 須通過當前資料及語義檢查；命中不呼叫翻譯 API；衝突的核准結果不任意擇一 |
| 數量模板 | 「請搬 3 把到 B 區」改成「請搬 5 把到 B 區」 | 只在句型、動作、單位、位置及雙語數值對應明確時替換；重複數值、換算、推算總數或不明千分位交由 AI |
| 案例差異提示 | 將本句與參考句變動的部分提示給 AI | 提醒採用本次數量、時間、設備與狀態，避免照抄舊句 |
| 操作狀態一致性檢查 | 對可辨識的修理、使用、秤重、包裝等動作檢查明示的否定／完成狀態 | 本機檢查，不以固定目標句作答案；設備明確時一併比對設備歸屬 |

### 業界功能查核與取捨

查核日期：2026-09-05。以下是官方文件中的功能做法；本版是在現有程式內補強相應能力，沒有新增第三方付費帳號或預設啟用新供應商。

- [Google Adaptive Translation](https://docs.cloud.google.com/translate/docs/advanced/adaptive-translation)：用相近的雙語例句調整翻譯。本版補強已有案例庫的雙向檢索與本句差異提示，保留既有供應商。
- [Phrase 翻譯記憶匹配](https://support.phrase.com/hc/en-us/articles/5709665578908-Net-Rate-Schemes-TMS)：區分重複、情境一致、完全相符與近似匹配。本版修正「近似分數高就照抄」的行為，並加入受控的數量模板。
- [Phrase 品質檢查](https://support.phrase.com/hc/en-us/articles/5709703799324-Quality-Assurance-QA-TMS)：包含數字、術語、未翻譯內容及標籤等檢查。本專案原有多項檢查，本版補上操作狀態反轉及案例標籤處理。
- [DeepL 上下文說明](https://developers.deepl.com/docs/learning-how-tos/examples-and-guides/how-to-use-context-parameter)：以相關上下文處理短句歧義。本專案已支援引用訊息／圖片上下文，本次保留其來源優先與快取隔離原則。

### 驗證與適用範圍

本次完整離線驗證：598 項測試、438 項子測試通過；30 個正式工廠回歸案例全部通過，29 個禁止譯法探針全部被拒絕。

新增 `test_factory_adaptive_translation.py`，包含錯字、縮寫、近義句、數量及位置對應、否定反例、群組隔離、向量呼叫及完整翻譯入口測試。測試以本機假供應商執行，不消耗翻譯 API；CI 會執行全部根目錄測試及工廠案例驗證。

一般未見句子仍由 AI 依上下文翻譯，不要求原文逐字相同；本機快速路徑只處理能驗證的變體。模糊設備代碼、矛盾指令或無法判定的錯字，不靠相似度擅自改成另一個意思。實際線上延遲、帳單及未知句子的品質，需要部署後以真實訊息觀察；離線測試不代表所有句子永遠零錯誤。

---

## 2026-09-05 翻譯流程修正

將更新 ZIP 內的檔案解壓到專案根目錄並覆蓋同名檔案，然後重新部署／重啟服務。
請一併放入新增的 `translation_source_identity.py` 與 `translation_request_guard.py`；啟動時會核對模組版本。既有詞庫、設定與資料庫保留，人工修正索引會自動遷移。

- 精確案例比對保留小數、比較符號、單字邊界、問號及狀態符號，避免不同條件錯用同一譯文。
- 已核准原文在站別正規化與 LINE mention 保護前查詢；人工修正優先於內建案例，但仍須通過目前驗證。
- 區分「每把」與「逐把依序」，接受正確的印尼文代詞詞尾，避免既有 PMI／事故回報案例被錯誤拒絕。
- 硬性驗證失敗不再被交付回退放行，也不再把缺失資料附加在句尾冒充完整翻譯。未成功交付的工作保留供後續重試。
- 同一份案例只傳送一次；保留本句的必要語義、單位與圖片上下文。提示編譯版本也納入快取指紋。
- 一般句子以一次模型生成為主；一次翻譯協調最多取得兩份模型候選。已使用第二份候選時，停止額外複核。網路錯誤重試與後續佇列重試另計。
- 在隨附 Docker 的單程序多執行緒部署中，同群組、語氣及原文相同且不依賴上下文的並行請求共用已驗證快取；不同群組與上下文不共用。
- 工廠主路徑不再為不會讀取的向量記憶支付 embedding 費用；保留 SQLite 精確翻譯記憶。
- 圖片工單查詢關閉／無結果時繼續翻譯 OCR；成功 OCR 會保存，重試不重複辨識。LINE 尚未接收的工單回覆不會誤標為已完成。

離線驗證（不需要翻譯 API 金鑰）：

```bash
python -m pip install -r requirements.txt pytest
python validate_factory_translation_assets.py --json
python -m pytest -q test_*.py
```

測試涵蓋正式案例的完整翻譯入口、已知錯譯、數值條件、並行請求、資料遷移與圖片重試。實際線上延遲、帳單節省比例及未見句子的翻譯品質，仍取決於供應商、輸入與命中率；離線測試不代表已驗證所有未知句子。

---

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

### 2026-08-30 受控持續學習：越用越精準、不讓錯誤自我繁殖

- **核准前自動驗證**：一般成員可用 `/wrong 正確譯文` 回報，但資料先進入 `pending`；修正版必須通過目前的語言、不可變資料、角色／動作／移動與工廠語義檢查，才可核准。系統不會把模型自己的輸出或未審回饋當成正確答案。
- **精確句＋相似句同步學習**：核准修正會立即成為版本化精確 TM，並自動進入不需 embedding 的語意案例庫；完全相同的來源可安全直用，相似句則只把修正當對照證據並啟動來源複核，不整句硬貼。
- **錯誤形狀風險學習**：品質閘門實際攔截／修復的來源會累積成群組隔離、時間衰減的風險模式。相似訊息再次出現時會自動提高複核等級；錯誤候選本身永遠不會被升格成譯文。
- **版本與回滾**：同一來源的新核准修正會取代舊 revision，但舊版保留為 `superseded`；駁回或刪除最新版時會先用當前規則重驗，再安全恢復上一版。政策升級後也可由 `/api/admin/al/audit` 重新稽核並隔離失效修正。
- **範圍隔離**：群組專用修正與風險模式只影響原群組；只有明確的全域修正可跨群組使用。
- **外部供應商敏感資料遮罩**：電子郵件、台灣／印尼手機、台灣身分證格式、具明確標籤的 NIK／KTP／ARC／護照／員編／銀行帳號，以及設定中的保護名，會先在本機換成可逆 placeholder；供應商回覆後再於本機還原。若主翻譯漏掉 placeholder，候選會被拒收。此功能不增加 API 呼叫。
- **混合語句處理**：中文夾印尼文／英文等自然語句會在本機標記 code-switching，再用同一次 LLM 呼叫完整翻成目標語言；不切段、不增加 API 次數，機台碼與人名不會被誤判為第二語言。
- **所有入口共用驗證**：LINE `/wrong`、後台翻譯日誌修正、後台自訂範例與正面表情共識都走同一驗證邊界；舊版自訂範例在每次內容變更後也會重新驗證，不合格資料只保留供管理員查看，不會進提示、精確命中或案例庫。
- **成本安全預設**：精確 TM 與相似句案例庫都會同步；付費 embedding 的向量加速仍預設關閉。正面表情回饋必須同時達兩票、包含管理員核可並通過本機驗證才可升格，這個管理員門檻不可由環境變數關閉。高成本的全量複核不會被偷偷開啟，只在已學風險或高後果內容上增加一次來源複核。

可選環境變數：

```bash
# 預設 mask；只有明確接受原始敏感資料送往外部供應商時才設 off
TRANSLATION_PRIVACY_MODE=mask

# 預設 0：相似句已有本機案例庫；設 1 才額外建立付費向量加速
ACTIVE_LEARNING_VECTOR_SYNC=0

# 品質事件最多保留筆數；風險模式另以聚合值保存
ACTIVE_LEARNING_EVENT_RETENTION=10000

```

注意：文字遮罩無法先讀取圖片內的資料；若開啟遠端 Vision/OCR，原圖仍會送往所選 AI 供應商。需要完全不外送圖片時，應停用遠端圖片翻譯。

### 2026-08-31 作業語意根治：檢查關係，不只檢查單字

- **主詞與事件綁定**：機台代碼、機油與滴漏會組成「哪一台機台漏油」；晚班人員、否定與倒垃圾會組成一個完整責任事件，口語拼法 `malem`／`tida` 也會先正規化。
- **流程方向不再翻反**：`三把會陸續拋光過去` 會解析成三捆材料逐步送往拋光站；`拋光` 在方向補語前是目的地，不會再誤成「三捆會被拋光」。捆數與製程名稱皆從當次來源抽取，不綁死單一句子。
- **保留原句語氣**：只有來源明寫「不要／禁止」才可輸出 `jangan`；`喝完亂丟`、`沒短尺亂維護` 這類陳述／抱怨不會再被擅自改成命令。`短尺維護` 固定依工廠詞義處理為 `penanganan material pendek`，不是設備 maintenance。
- **客戶名的生產省略語**：具備包裝／訂單證據時，`今天剩 A、B、C` 會明確翻成尚餘這些客戶的訂單，同時逐字保留客戶識別名；一般物品清單不會觸發此規則。
- **多段通知逐項核對**：本月訂單優先、藍底提醒、MES 停止時間、異動資料完成時間、包裝出貨急單與異型站分流會分成獨立關係驗證，少一段、時間對調或站別接錯都不能通過學習與快取。
- **所有出口共用同一驗收**：來源完整時先由本機語意框架直接產生翻譯；來源含額外未解析內容時禁止局部直翻，改交模型並注入關係提示。模型結果、快取、TM、OCR、回饋核准與持續學習都通過相同驗收，避免錯誤答案反覆回流。
- **發布即自我檢查**：語意模組 API／build 與 `app.py` 綁定；啟動時執行包含正例、已回報錯譯、變數化句型、反例及「不得漏掉額外子句」的本機自測，版本不一致或規則失效會直接拒絕啟動。

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
├── active_learning.py             # 驗證、版本、回滾、品質事件與自適應複核風險
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
4. `translation_casebook.py`：核准修正可對來源完全相同的句子直接命中；相似句只注入對照案例並要求來源複核，且遵守群組範圍。
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
