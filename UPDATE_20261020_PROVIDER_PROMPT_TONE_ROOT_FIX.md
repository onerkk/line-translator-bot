# 2026-10-20 三家 AI 供應商、提示詞快取與語氣判定根治

本 ZIP 只含相對本次上傳專案的變動／新增檔案，依原路徑覆蓋到專案根目錄後重新部署。
未修改線上服務、未呼叫付費模型、未發 LINE 訊息。

## 截圖判讀

「蘇比 你今日體檢沒有採集檢…請記得先準備好放陰涼處等司機，謝謝」的印尼文語意正確。
兩個瑕疵都來自程式，不是模型：

1. 結尾「謝謝」讓本機語氣分析把整句判成 `gratitude 0.96`，提示詞因此要求模型「用溫暖語氣」，
   後端再把感謝類表情（🙏 💛 😊）貼到多個句子上。
2. 擷取實際送出的請求後確認，三家供應商的提示詞都有多餘或矛盾內容（見下）。

## 根因與修正（三家同時）

| 根因 | 影響 | 修正 |
| --- | --- | --- |
| 10/16 精簡提示詞進到 ai_provider 後，被舊的 XML 外層再包一次：第二個 `<role>`、第二個 `<output_format>`（一個要純文字、一個要 `<translation>` 標籤）、`<thinking_protocol>`、`<success_criteria>` | Claude 路徑每則多 1,400 字且指令矛盾 | 偵測到 `<translation_principles>` 的直接組裝提示詞即不再包裝 |
| 詞庫 grounding 用「整個請求含 system」比對術語 | 70 字提醒被附上 11 筆無關術語 search_result | 只比對使用者原文 |
| Claude 快取區塊把每則不同的語氣分析一起放進 1 小時快取 | 每則付 2 倍寫入價、幾乎不命中 | 穩定區固定切在群組語氣預設 `</translation_preset>` 之後（與 OpenAI 路徑相同邊界）；動態區不快取 |
| 停止序列固定取前四個中文前綴 | 中→印輸出無保護 | 依翻譯方向排序 |
| 模型過時：Haiku 4.5（$1/$5）、Sonnet 5（$3/$15）、gpt-5.6 Luna/Terra | 單價 10–20 倍 | 預設改 `claude-haiku-5-5`（$0.10/$0.50）、`claude-sonnet-5-5`（$2/$10）、`gpt-6-luna`（$0.10/$0.50）、`gpt-6.1-sol`（$2/$10）；舊 ID 在設定載入、mapping、請求邊界三層自動前進遷移 |
| 5.5 系列參數契約不同 | 直接換模型會 400 | 不送 temperature/top_p/top_k、禁 prefill、thinking 改 adaptive + `output_config.effort`、Haiku 5.5 快取門檻 512 tokens |
| app 算好的每句 `reasoning_effort` 沒傳到 Claude | 10/16「依內容配置推理」對 Claude 無效 | `_dispatch_provider` 傳入；none→思考關閉、low/medium→adaptive |
| 短句一遇「操作風險」就升級品質層 | 30 字提醒付 Sonnet 價格 | 風險句留在常規層並把 effort 提到 medium；只有長度 ≥ 字數門檻才用品質層 |
| `_tl.tone` 未設定時掉到 `casual`（口語俚語） | 與 principles「標準印尼文」矛盾 | 回到後台全域預設（factory） |
| 結尾謝謝主導語氣；感謝表情含 💛 | 指令被當感謝；工廠對話出現愛心 | 結尾禮貌語只作收尾，不主導全句；一個中文長句展開成多個印尼句時最多一個功能標記；移除 💛 |

Gemini 路徑原本就不包裝、不 grounding；本次只更新模型對照（gpt-6 層級 → gemini-3.1-flash-lite / 2.5-flash）。

## 實測（離線擷取真實請求，固定模型回覆）

同一則截圖訊息、工廠語氣預設：

- Claude：system 由兩個區塊 3,068 + 1,378 字（casual 預設）／約 9,000 字（factory 預設）＋ 11 筆無關術語，
  改為 6,848 字穩定區（可快取、每則相同）＋ 738 字動態區，0 筆無關術語；模型 `claude-haiku-5-5`、
  `thinking adaptive / effort medium`（風險句）、無 temperature、無 prefill。
- OpenAI：`gpt-6-luna`、`reasoning_effort medium`、6,939 字穩定前綴帶明確快取斷點 + 941 字動態區。
- Gemini：`gemini-3.1-flash-lite`、`reasoning_effort minimal`、單一 system。

依官方單價推算，常規短句每則輸入費用由約 $0.009（Haiku 4.5 / 5.6 Luna，無快取）降到約 $0.0002
（Haiku 5.5 / GPT-6 Luna，穩定區命中快取）。這是單價推算，不是帳單；正式速度與費用須部署後實測。

## 第三段：作業確認、圖片 OCR、後台

- 作業確認（/ack、/確認、提醒、收據、停止）：594 項測試連跑 5 次全通過；指令解析、點名範圍、提醒 lease、
  通知翻譯走同一條翻譯管線（含群組語氣）。程式邏輯未發現需要修改的缺陷。第二段整包執行時偶發的
  `test_only_scheduled_deadline_posts…` 在第三段整包執行已通過；它是提醒排程到期的時間邊界測試，
  不是功能缺陷。
- 圖片 OCR：Claude Haiku 5.5 改用高解析度影像層級（長邊 2,576 px 才縮圖），同一張手機照片的視覺
  token 約為 Haiku 4.5 的 2.5 倍。送到 Claude 前把長邊限制在 1,568 px（過去所有 Claude 模型的服務上限），
  OCR 品質不變、影像費用不漲；小於上限的圖片原樣送出。OpenAI／Gemini 路徑不受影響。
- 後台：模型選單、價格表、說明文字、前端模型家族判斷（gpt-6 → reasoning family）同步更新；
  儲存舊 Claude ID 時自動遷移。

## 測試

- 新增 `test_provider_refresh_20261020.py`（34 項）：ID 遷移、5.5 參數契約、不二次包裝、快取邊界、
  grounding 範圍、停止序列、分流、語氣。
- 更新 `test_model_refresh.py`、`test_translation_cp_extreme.py`、`test_translation_cp_value_root_fix.py`
  以反映新模型政策；版本字串測試同步到 `v3.50.0`。
- 全套 `python run_translation_offline_checks.py`：4,292 項通過、450 子測試通過。

## 版本連動

- `app.py` VERSION → `v3.50.0-claude-5-5-gpt-6-prompt-cache-root-fix-2026-10-20`
- `translation_extras.TRANSLATION_EXTRAS_VERSION` 與 `app._EXPECTED_TRANSLATION_EXTRAS_VERSION` → `2026-10-20.1-closing-courtesy-tone`

## 官方查核

- Claude Haiku 5.5 概覽與遷移指南（platform.claude.com/docs/zh-CN/models/haiku-5-5/…）：ID、價格、512 快取門檻、
  移除採樣參數、adaptive thinking、禁 prefill。
- OpenAI Pricing（developers.openai.com/api/docs/pricing）：gpt-6-luna $0.10/$0.50、gpt-6.1-sol $2/$10、
  GPT-5.6 Sol 優惠價至 2026-11-21。

## 2026-10-21 /help 重新設計（ui105，`line_command_catalog.py`）

- 第一張卡「常用指令」直接列出 /確認、/交班摘要、/我的語言、/標錯、/工廠、/狀態、/說明。
- 之後每張卡就是一個分類，卡內直接列出該分類全部指令：左側分類色塊圖示、中英指令名、一句用途、
  管理員標記；點任一列開啟該指令的說明卡（用法、範例、雙語說明），無參數指令有「送出」按鈕一鍵執行。
- 不再有「先選分類、再翻頁」的中間層；圖片／按鈕兩類維持圖文教學。
- 指令列的 postback 改用 `topic=cmd:<key>`，解決 /ack 既是指令又是分類而無法開啟說明的問題；
  `/說明 確認` 等文字用法不變。
- 版本：`BUILD_ID` → `2026-10-21.ui105`；新增 `test_help_ui105.py`（4 項），`test_ui104_commands.py` 維持通過。

### 2026-10-21 補：/help 完全沒反應的根因與修正
- 根因：ui105 第一版把全部指令列在一則訊息，JSON 達 65 KB；LINE Flex Message 上限 50 KB，LINE 回 400，
  聊天室看起來就是「沒反應」（翻譯本身正常，截圖「測試 → Tes」可證）。
- 修正：指令列改用 span 單一文字節點，移除圖示色塊；成員選單約 31 KB、管理員約 43 KB；
  另加保護：組裝後超過 46 KB 會先捨去「常用」卡再從尾端裁減，永遠不送超限訊息。
- 測試：`test_help_ui105.py` 新增 50 KB 與每卡 10 KB 的尺寸測試，所有語言／身分／主題組合都檢查。

## 學習閉環第一塊：`learning_loop_eval.py`
- 更正前一段的說法：核准過的 /標錯 修正**已經**會進 casebook 當對照式 few-shot（`translation_casebook._case_from_correction`），
  不只精準命中。缺的是「有沒有變聰明」的量測與錯誤類型的累積。
- 這支工具把全部核准修正當保留評測集，對真正的翻譯管線跑兩種模式：baseline（把該筆修正藏起來，看其他修正和規則能否推廣）、
  learned（修正可被檢索）；用相似度加評審模型（預設 gpt-6-luna）打 1–5 分並標錯誤類型（術語／否定／數字單位／站別／語氣／漏譯）。
- 輸出 JSON 與 Markdown 報告，含錯誤類型統計、退步案例與 gate（pass／review）。改提示詞或換模型前後各跑一次，數字沒升就不上線。
- 用法：`python learning_loop_eval.py --provider openai --limit 40 --out eval_report`；`--dry-run` 不花 API。
- 下一塊（未做）：錯誤類型累積 ≥3 自動產生規則草稿、管理員核准後進穩定提示詞區。

## 2026-10-21 b：/help 沒反應的根因與修正；學習閉環第一塊

- **根因**：新版 /help 一次列出全部指令，Flex JSON 達 65 KB；LINE 對單一 Flex 訊息的上限是 50 KB，
  API 回 400，所以 /help 完全沒有回覆。修正後：成員選單約 31 KB、管理員選單約 43 KB、每張卡 < 10 KB，
  並新增測試鎖住 45 KB／10 KB 上限（指令列改用 span 文字、移除圖示方塊）。
- **其他情況**：若整個機器人（含一般翻譯）都沒反應，表示部署時沒有一起覆蓋 app.py、translation_extras.py、
  ai_provider.py、line_command_catalog.py；app.py 啟動時會故意失敗並在 Render 日誌印出
  「deployment mismatch」。請把 ZIP 內所有檔案一起覆蓋。
- **學習閉環現況（更正前面的說法）**：核准過的 /標錯 修正本來就會進 casebook，作為「錯例→正例」對照範本依相似度檢索，
  不只是同句重用。真正缺的是量測：沒有任何數字能證明核准修正讓新句子變好。
- **新增 `learning_loop_eval.py`**：把核准修正當保留評測集，對每一筆跑正式翻譯管線兩次 —
  baseline（隱藏這一筆修正，leave-one-out）與 learned（保留），比較與核准譯文的相似度，
  並可用 gpt-6-luna 評審打 1–5 分及錯誤類型（術語／否定／數字單位／站別設備／語氣／漏譯）。
  輸出 JSON 與 Markdown 報告與 gate（pass／review）。用法：
  `python learning_loop_eval.py --provider openai --limit 40`；`--dry-run` 不花 API。
  這是閉環的第三塊（量測）；第二塊（錯誤類型累積→規則草稿→管理員核准）下一段做，評審給的
  error category 就是它的輸入。

## 2026-10-21 學習閉環第二塊：截圖審核 → 機器人自動學習（`translation_reviews.py`）

從現在起，截圖審核的產出是**資料**（`translation_reviews/*.json`），不是程式碼。每個 bundle 在開機時進三層：
1. corrections → 核准修正（全域）→ casebook 對照式 few-shot，相似訊息會看到「錯的 vs 對的」。
2. glossary → 併入術語表（不覆蓋後台已有詞）→ factory_terminology 索引、提示詞術語區。
3. rule → factory_knowledge 卡片（情境說明、範例、禁用句）→ 檢索進提示詞；
   若模型仍輸出審核過的禁用句，**只有這種情況**會多做一次修正呼叫（每則最多一次，修正結果須通過全部卡片與品質閘門才採用）。

實測（固定模型回覆）：10/20 截圖那句的 bundle 放進去後，一句改寫過的新訊息「阿迪 你今天體檢沒有採集檢…」
提示詞已含卡片、術語與修正範例；模型故意重複舊錯時第二次呼叫把它修成 sampel … belum dikumpulkan。

- 第一個 bundle：`translation_reviews/2026-10-20-health-check-sample.json`（採集檢主詞、陰涼處 teduh）。
- 驗證：`python translation_reviews.py validate`；手動灌入：`python translation_reviews.py ingest`；
  開機由 worker 啟動流程自動灌入（可重複、不重複寫入）。
- 新增 `test_translation_reviews.py`（6 項）；`test_translation_source_first.py` 調整為容許全域審核修正。
- 之後每次截圖審核，我回覆一個 bundle JSON 檔，你放進 `translation_reviews/` 部署即可；
  累積後用 `learning_loop_eval.py` 量測。

### 2026-10-21 補：既有餵養資料全部接到同一套學習邏輯
- 既有四處資料本來就會進提示詞（52 張情境卡、323 術語、30 筆回歸句、Redis 備份的後台核准修正）；
  但它們被違反時只是「記錄」，不改結果。現在統一：任何情境卡的禁用句、任何核准修正的已知錯誤寫法再出現，
  都觸發同一個「每則最多一次」的修正呼叫；一般卡片要求維持建議性，費用不變。
- 13 筆 2026-10-07／10-10 的截圖審核案例（以前只是測試）轉成 bundle
  `translation_reviews/2026-10-07-and-10-10-screenshot-reviews.json`，開機後成為對照範例。
- `test_translation_reviews.py` 7 項；全套 4,306 通過。

## 2026-10-21 全面檢查（功能、技術、文件）

檢查方法與結果（都可重跑）：
- 靜態分析（ruff F821/F811/B 系列）全部正式模組：1 處脆弱寫法修正（`line_ack_reminders` 失敗回呼引用 except 變數，改為明確綁定）；其餘為 benchmark 腳本與即時呼叫的閉包，非錯誤。
- Python 3.11 語法相容（Render 用 3.11.0，開發環境 3.12）：全部 `.py` 以 3.11 文法解析通過。
- 相依套件：正式模組所有第三方 import 都可載入；PyPDF2／argostranslate／tiktoken 皆為可選且有 try 保護。
- HTTP 煙霧測試：92 個 GET 路由（未登入與超級管理員各一輪）＋ 97 個 POST 路由空 JSON。
  **真 bug 1 個已修**：`/api/admin/tbx/validate` 對任何格式錯誤的 TBX 回 500（set 無法 JSON 序列化），改為正確回傳錯誤清單。
  其餘 503 為測試環境沒有 Redis／GitHub／LINE 網路，屬預期。
- 後台／LIFF 內嵌 JavaScript 9 段與 static 5 支以 node --check 全通過；全部 JSON 資產可解析。
- 過時模型預設統一（規則 1「同類一起改」）：`auto_post_edit`、`quality_estimation`、`batch_translation`、`glossary_enforcement`
  仍寫死 gpt-5.4／Haiku 4.5／Sonnet 4.6，雖經遷移層可用，現改為現行模型；APE 後台存的 Claude 模型也走遷移。
- README 補 2026-10-21 更新段，修正過時的模型說明。
- 圖片費用（規則 2 先查官方）：OpenAI 在 detail=high 時伺服器端自行縮到 ≤2,500 patches／2048 px，不需客戶端縮圖；
  Gemini 同理。只有 Claude 5.5 改了解析度層級，前一段已加 1,568 px 上限。無需再改。
- 舊審核 bundle 13 筆錯誤類型由 other 改為逐筆判定並補上原因（term 4、station_equipment 4、omission 2、tone 2、number_unit 1、other 1）。
- `learning_loop_eval.py` 評審解析加強：支援 OpenAI／Anthropic 兩種回應形狀與 ```json 圍欄，解析失敗不中斷；新增測試。

未解決、如實列出：
- 作業確認提醒的兩個測試早前在整包執行時各偶發失敗 1 次；之後連續 3 次整包執行（4,307 項）全部通過，單獨執行、CPU 壓力下從未失敗。
  正式邏輯未見缺陷（`exc` 閉包綁定已順手修正，但它不是偶發原因）；若再出現，需在 CI 固定 seed 重跑定位。
- `/api/admin/richmenu/setup` 遇 LINE 網路錯誤回 500（帶 JSON 錯誤訊息），語意上應為 502；行為正確，只是狀態碼。

## 2026-10-21（續）速度／費用／品質再檢
- 本機管線開銷實測：短句 17–30 ms、長公告 ≤240 ms；同一句重送命中驗證快取不呼叫 API。延遲主要在供應商回應，程式端無可壓縮空間。
- 工單 OCR 費用根治：原本每張照片固定送 3 張圖（原圖 + 90°/270° 放大到 2,400 px，皆 detail=high）給 gpt-6.1-sol。
  改為先只送原圖；只有讀不到工單（NO_WORK_ORDER）或少於 3 個欄位時，才加旋轉圖再讀一次。正向照片影像費用降為 1/3，
  側拍照片多一次呼叫但結果不變。既有的套環／長度／客戶重讀流程未動。
- 預設設定：全新安裝的 active_provider 改為 openai（與正式環境一致）；`/api/admin/richmenu/setup` 上游錯誤改回 502；
  `.gitignore` 加入 translation_log.json、last_translate_debug.json、*.db。
- 看到但未做：一般圖片翻譯與語音轉文字（gpt-4o-transcribe）未重新查價；一般圖片翻譯仍可能因列切割重讀多次，待有實際費用數據再決定。
