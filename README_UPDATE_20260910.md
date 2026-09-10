2026-09-10 LINE 工廠翻譯更新

這是本次變更檔，不是完整專案。以本次提供的 line-translator-bot-main.zip 為基底。

已確認的問題與修正

1. 原版語言偵測能把截圖訊息判為印尼文，也沒有把它當成純設備代碼略過。但現有報表捷徑沒有涵蓋這種長度／重量格式，外部 AI 沒有可用結果時就只剩重試。
2. 原檢查器會把正確譯文中「R：」的 R 誤判為未翻譯文字；部分保留的公尺、噸單位也有相同問題。現在只在完整解析的數值報表中允許原有代號與單位，仍逐欄核對。
3. 原版可能放過長度與重量數值對調、擅自增加 mm／kg，以及把 R 擅自解讀為半徑或直徑。現在核對欄位、數值、明示單位、設備代碼與欄位完整性；原文未寫的單位不添加。
4. Render 暫存磁碟模式下，部分處理函式回傳時其實還有未完成翻譯，舊 Webhook 卻回傳 HTTP 200。現在當這則訊息仍有待處理工作時回傳 HTTP 503，保留 LINE 重送的可能性；既有拋出例外的傳送失敗仍維持 HTTP 500。只檢查當則訊息，不受其他群組待辦影響。

截圖案例的輸出

原文：
R: 14.47mm
Panjang 6040
Berat 1377

更新後：
R：14.47mm
長度：6040
重量：1377

完整可解析的材料報表直接在本機翻譯，這條路徑不呼叫 AI 翻譯 API。支援中印雙向、不同數值、空白、大小寫、換行、冒號與等號，以及長度、重量、直徑、尺寸、寬度、厚度、支數與數量。R 保留原標記。

含問題、補充指令、未知內容或重複欄位時，仍交由原有完整翻譯流程，避免只翻出報表而漏掉指令。既有人工核定譯文的優先順序、群組開關、跳過使用者設定、送出失敗後保存譯文與防重複補送均保留。更新品質規則版本會使舊快取重新驗證。

實際驗證

- 原始版本：2187 項測試及 443 個子測試通過。
- 新增的三個問題重現測試，在修正前全部失敗。
- 最終版本：2239 項測試及 443 個子測試通過（本機離線測試耗時 56.51 秒，這不是 LINE 翻譯速度）。新增 52 項回歸案例。
- 官方 SDK／Flask／SQLite 的實際處理流程搭配模擬 API，驗證 AI 全部不可用、LINE 傳送失敗、完成後補送不重翻、Webhook 重送與快取防污染。
- 工廠資產與部署版本檢查通過。
- 原有後台、會員頁、作業確認、指定收件人、提醒、LIFF 表單與統一選單的 DOM 操作檢查通過。
- 測試不對正式 AI 或 LINE 發出請求。本次沒有部署至你的 Render，也沒有讀到正式環境的 Logs；無法僅憑截圖確定當時是否另外發生休眠、額度、網路或設定問題。

更新方式

將 ZIP 解壓後的檔案放入原專案根目錄，同名程式檔一起覆蓋後部署。五個執行檔應一起更新，避免版本不一致：

| 檔案 | 作用 |
| --- | --- |
| app.py | 報表翻譯入口、快取版本、未完成 Webhook 回應 |
| factory_structured_report.py | 雙向材料報表解析與欄位核對 |
| translation_quality_gate.py | R／明示單位誤判修正與欄位完整性檢查 |
| translation_retry_queue.py | 當則訊息待處理工作查詢 |
| webhook_runtime.py | 暫存磁碟模式的未完成工作重送處理 |
| test_material_report_delivery.py | 本次新增回歸測試 |
| README_UPDATE_20260910.md | 本說明 |

LINE 設定與實際限制

請確認 LINE Developers Console → 你的 Messaging API 頻道 → Messaging API → Use webhook 與 Webhook redelivery 已開啟。LINE 預設不開啟重送；伺服器回傳非 2xx 也必須搭配此設定，才能使用 LINE 的重送機制。重送次數／間隔由 LINE 決定，官方並不保證一定送達。[LINE Webhook 官方文件](https://developers.line.biz/en/docs/messaging-api/receiving-messages/)

Render 免費服務閒置 15 分鐘會休眠，喚醒約一分鐘；休眠、重啟或重新部署會失去本機暫存檔與 SQLite。程式更新不能消除這些主機限制。若要持續運作並保住尚未送出的工作，需要不休眠的服務與持久化儲存；目前程式已有持久掛載檢查。這份更新改善漏譯原因，但不宣稱任何網路／主機條件下都能零延遲或永不漏譯。[Render 官方文件](https://render.com/docs/free)

本次核對的其他官方依據

- [LINE API 重試規則](https://developers.line.biz/en/docs/messaging-api/retrying-api-request/)：既有 push 使用穩定重試鍵，保留已完成譯文，補送不重複呼叫 AI。Reply API 不支援該重試鍵；回覆逾時後改 push 的跨 API 重複風險仍不能完全消除。
- [OpenAI 延遲最佳化](https://developers.openai.com/api/docs/guides/latency-optimization)：减少外部請求，對可確定的結構化工作使用一般程式處理；本次據此增加完整報表的本機路徑。
- [Claude 錯誤處理](https://platform.claude.com/docs/en/api/errors)、[Gemini 故障排除](https://ai.google.dev/gemini-api/docs/troubleshooting)：核對暫時性錯誤、SDK 自動重試與退避行為。本次沿用既有供應商切換與請求預算，相關原有測試已通過，沒有聲稱重新實作全部供應商流程。

基底 ZIP SHA-256：60aedbb5d54f442e0ffd2a55a3cc492a77a9589b6be7652f7b153fc8be886343
