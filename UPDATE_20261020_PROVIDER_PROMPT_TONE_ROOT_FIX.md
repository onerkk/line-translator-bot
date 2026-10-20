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
