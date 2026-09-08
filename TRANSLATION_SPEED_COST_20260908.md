# 翻譯時間與費用修正 — 2026-09-08

本次修正減少錯誤觸發的 AI 覆核、重複請求、重複指令與無用輸出。ZIP 僅含本次修改及新增檔案，適用於本次提供的 `line-translator-bot-main.zip`。

**更新方式：**將 ZIP 內容按相同相對路徑覆蓋至原專案，全部一起更新後重新部署／重啟。無須新增套件或環境變數。`app.py`、`prompt_optimizer.py`、`active_learning.py` 有版本相容檢查，請勿只更新其中一個。原資料庫與設定不需重建。

## 實際修正

| 問題 | 本次處理 |
|---|---|
| 依賴上下文而不能存入一般快取，被當成錯譯風險 | 移除「不可快取＝風險 0.65」的判斷。無錯誤的翻譯不再因此觸發下一次 AI 覆核。讀取舊資料時忽略沒有內容錯誤證據的風險；若舊紀錄覆蓋了錯誤代碼，從保留的事件恢復真實錯誤證據。保留負評、真實錯譯與原有歷史。 |
| 相同上下文仍重新翻譯 | 新增程序內 120 秒、最多 256 筆的已驗證結果快取，並合併同時抵達的相同請求。原文、方向、原始對話及相對時間、群組、說話者、接收者、引文、工作站、名稱、語氣、術語資產、模型設定與已知錯譯風險均納入識別。命中後仍執行公開翻譯出口的檢查。 |
| Claude 重複包裝已編譯的提示 | 正確識別 `<translation_principles>`，移除第二套角色、任務及規則外框；OpenAI 的 XML 輸出契約也縮短。保留語意契約、原文事實、名字、數字、狀態、術語與排版要求。 |
| 結構化輸出要求多個未使用欄位 | 啟用此功能時只要求 `translation`，經共用 schema 路徑送入供應商；檢查譯文字段，而非把 JSON 外框當成譯文。先做既有的、由原文支持的職稱校正，再判斷是否需要重新生成。 |
| Gemini 2.5 的 `minimal` 仍開啟思考預算 | 快速翻譯模式下，2.5 Flash／Flash-Lite 使用官方支援的 `none`；Pro 與其他版本使用各自可接受的最低檔位。維持原模型選擇及覆核機制。 |
| 動態內容被當作可重用提示寫入快取 | 固定原則置於前綴，語氣與變體置後。GPT-5.6 使用 explicit 模式，僅在固定前綴的本機長度估算達門檻時加入斷點；不足時不補字、不額外呼叫計數 API。實際能否快取仍由供應商 token 計數決定。對不支援的新參數做既有預算內的相容重試，並暫停重送該參數 5 分鐘。 |

上下文結果只放在短期記憶體，不寫入原文單獨索引的翻譯記憶、向量庫或一般快取。圖片／OCR、可取得的近期影像場景及上下文儲存失敗時不使用此快取。原始訊息編輯／收回後，先按現存原始對話重新檢查，再計算識別。不同程序各自維護快取。

## 可重現的離線對照

使用原專案既有的 15 筆 `tests/data/cp_holdout_20260907.json`，同一支測試程式分別載入原始版與修正版，執行實際提示組裝及供應商 adapter，僅把最後的 SDK 回應替換成固定測試譯文。以下是所有樣本送出文字的**字元總數**，不是計費 token，也不是帳單減幅。

| 供應商 | 原始版字元 | 修正版字元 | 字元減少 |
|---|---:|---:|---:|
| Anthropic | 84,173 | 66,029 | 21.56% |
| OpenAI | 79,328 | 66,029 | 16.76% |
| Gemini | 67,208 | 63,494 | 5.53% |

另用公開 `translate()` 路徑、固定原文及有效上下文連續呼叫六次。下表計算模擬 AI 協調層收到的生成／覆核請求，包含第一次；這六次之間不加入新的上下文，也不改變對話的有效相對時間。

| 案例 | 原始版逐次呼叫數 | 修正版逐次呼叫數 | 總呼叫數 |
|---|---|---|---|
| 關閉新快取，單獨驗證錯誤風險修正 | 1, 2, 2, 2, 2, 2 | 1, 1, 1, 1, 1, 1 | 11 → 6 |
| 啟用新快取，相同有效上下文 | 1, 2, 2, 2, 2, 2 | 1, 0, 0, 0, 0, 0 | 11 → 1 |

第二項是可重用情境的測試，不代表六則不同新訊息只需呼叫一次 AI。一般群組不斷新增訊息、對話相對時間或設定變動時，會重新翻譯。真正需要修復的錯譯仍可產生額外覆核。

本機計時、每筆請求與摘要保存在 `tests/data/translation_speed_economy_20260908.json`。計時採固定 AI 回應，沒有外網延遲；加入上下文識別會增加未命中時的本機工作。這份測試不能推算 LINE 完整回覆秒數，也不能證明線上翻譯品質或實際金額降低多少。

## 驗證與重現方式

完整離線回歸：**1,681 項測試及 438 項子測試通過**。包含本次新增的 35 項案例，涵蓋上下文撤回／修改、群組與名稱隔離、同時重複請求、過期與容量限制、真實錯誤與負評保留、JSON 譯文檢查、反向狀態／錯誤設備代碼拒絕、Gemini 參數，以及真實 OpenAI SDK 序列化後的 HTTP body。

工廠翻譯資產驗證亦通過：30 個核准目標、29 個禁止譯法探針；守門器自測另通過 43 個核准範例與 18 個已知錯譯。這些驗證使用固定候選譯文，不是雲端模型的翻譯品質 A/B 測試。

在已安裝原專案 `requirements.txt` 與 `requirements-test.txt` 的開發環境執行：

```bash
python run_translation_offline_checks.py
python benchmark_translation_speed_economy.py --output economy-after.json
python benchmark_translation_speed_economy.py --repo /path/to/original/line-translator-bot-main --output economy-before.json
```

兩個測試工具使用臨時資料目錄並阻擋外部連線；回歸工具只允許既有連線重用測試所需的本機 loopback。對照時 `--repo` 指向尚未套用本 ZIP 的原始專案。所有雲端 AI 呼叫與 LINE 發送均為 0。

預設已啟用此次優化。需要局部停用時，可設 `TRANSLATION_CONTEXT_CACHE_ENABLED=0` 或 `OPENAI_TRANSLATION_EXPLICIT_CACHE=0` 後重啟；錯誤風險修正與提示縮短仍有效。

## 查證的官方與業界技術

查證日期：2026-09-08。以下為本次採用或評估後排除的技術依據。

| 官方來源 | 與本次程式的對應 |
|---|---|
| [OpenAI Latency optimization](https://developers.openai.com/api/docs/guides/latency-optimization) | 減少序列呼叫、縮短必要輸出、重用已有結果。移除錯誤覆核及未使用的替代譯文；LINE 仍須交付完整譯文，不能把首字串流速度當成完整回覆速度。 |
| [OpenAI Cost optimization](https://developers.openai.com/api/docs/guides/cost-optimization) | 降低不必要的請求與 token。此次先處理可確認的程式浪費，維持原模型選擇；Batch／Flex 的時間取捨不適合直接套入此互動回覆流程。 |
| [OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching) | GPT-5.6 起寫入與讀取費率不同，重用價值需分別考量。固定前綴、explicit 斷點與動態內容分離，避免把每筆不同的內容都當成值得寫入的快取。未將官方最大優惠或快取命中視為實測。 |
| [OpenAI Chat Completions reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create) | 查證 `prompt_cache_options` 與文字區塊 `prompt_cache_breakpoint` 的 API 格式；以 SDK MockTransport 檢查實際送出的 JSON。 |
| [Anthropic Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) | 快取有模型門檻與首次寫入成本。保留現有支援，但不為了達門檻填入額外文字；先清除程式重複包裝。 |
| [Google OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)／[Thinking](https://ai.google.dev/gemini-api/docs/thinking) | Gemini 2.5 的 `minimal` 與 `low` 對應 1,024 的思考預算；可停用的 Flash 模型使用 `none`，Pro／3.x 使用支援的最低檔位。這是預算設定，不代表每次原本都產生 1,024 個思考 token。 |
| [Go singleflight](https://pkg.go.dev/golang.org/x/sync/singleflight) | 同一工作鍵的重複呼叫等待同一份結果。本次以 Python 現有鍵鎖與已驗證結果快取實作，不新增 Go 相依。 |
| [Render Free instances](https://render.com/docs/free) | 若使用免費服務，閒置後的啟動可能約需一分鐘。這屬主機啟動時間，本次翻譯流程優化不能消除；未變更付費主機方案。 |

## 本次檔案

| 路徑 | 用途 |
|---|---|
| `app.py` | 翻譯路徑、上下文識別、驗證及 schema |
| `active_learning.py` | 排除無錯誤證據的覆核風險，保留真實歷史 |
| `ai_provider.py` | 提示包裝、快取參數及 Gemini 思考設定 |
| `prompt_optimizer.py` | 精簡原則及穩定前綴 |
| `translation_request_guard.py` | 限量、限時上下文結果及同鍵請求同步 |
| `conftest.py` | 測試間隔離新增的程序內快取 |
| `test_translation_speed_economy.py` | 本次回歸案例 |
| `benchmark_translation_speed_economy.py` | 原始版／修正版離線對照工具 |
| `run_translation_offline_checks.py` | 臨時狀態、外網封鎖的回歸入口 |
| `tests/data/translation_speed_economy_20260908.json` | 逐筆對照、環境與驗證證據 |
| `TRANSLATION_SPEED_COST_20260908.md` | 此說明 |
