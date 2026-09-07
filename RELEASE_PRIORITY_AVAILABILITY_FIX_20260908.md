# 點名後「優先放行」未回覆修正

基準：`onerkk/line-translator-bot` commit `723b96bca57b9e6e98682a2bcf068aad48365bc0`。
語義模組版本：`2026-09-08.1-release-priority-availability`。

## 查到什麼

已比對最新 GitHub 的 328 個檔案，確認上一包修改已存在。本次使用回報文字 `@小麥（研磨股班長） 麻煩優先放行`，重現「正確候選被程式攔截，進入重試而沒有 LINE 回覆」的路徑。

截圖沒有顯示伺服器收到的 webhook、AI 原始輸出或 LINE API 結果，因此不能據此斷言當次線上事件一定由這個缺陷造成。以下是程式中已重現並修正的缺陷。

| 缺陷 | 修正 |
| --- | --- |
| `Tolong prioritaskan release data ke stasiun berikutnya.` 的 `prioritaskan` 已表達「優先」，舊規則仍回報 `erp_release_priority_missing` | 接受優先詞的正常動詞、名詞及被動詞形，包含 prioritaskan、utamakan、dahulukan 等 |
| `rilis data`／`pelepasan data` 等正常表達被固定詞組要求擋下 | 檢查生產資料放行的語意；原文明確指定下一站時仍要求保留目的地 |
| 中文「不要優先放行」「已經優先放行」的「優先」覆蓋否定／完成狀態 | 優先是修飾語，不可把禁止、未完成或已完成改成請求 |
| 英文句號沒有切句，可能借用另一個動作或另一筆資料的優先要求 | 切開句子、保留小數；依相符資料代碼和放行子句核對 |

點名原樣保留。放行仍採本廠 ERP 生產資料語意，不能改成把實體材料放在架上。未關閉品質檢查、未新增整句固定翻譯，也未提高 AI 呼叫預算。

## 驗證結果

- 全部 Python 發佈測試：**1,580 passed、438 subtests passed**，48.10 秒。
- 新增 **31 項回歸測試**；上述總數包含新增測試。
- 工廠素材驗證：通過，無錯誤、無警告。
- 3 種正確候選 × 原生 LINE 點名／貼上文字點名，共 6 種情境均只產生 **1 次替代 AI 呼叫、1 次 LINE 回覆**，沒有剩餘文字重試。
- 關閉圖片翻譯、或圖片等待可用視覺服務時，接續文字可獨立送出；這不代表已完成截圖內表格的真實 OCR 品質驗證。
- LINE reply 與 push 同時失敗後，已完成的譯文保留在 outbox；恢復 push 後直接補送，不重新生成。
- 不正確的實體搬運、遺漏優先、錯誤狀態、否定及不同動作借用優先字詞，仍會被拒絕。

新增整合測試只替代外部 AI 傳輸及 LINE 傳輸，保留實際回應解析、點名保護、品質檢查、翻譯流程與重試資料庫。測試各自使用獨立學習資料庫，避免上一個刻意輸入錯譯的測試影響下一個測試。既有學習風險觸發的必要複核仍保留。

**這些是離線程式測試，不是 1,580 句真實 AI 翻譯的正確率，也不是線上翻譯秒數。未向正式群組發送訊息，未呼叫付費 AI，未變更 Render 設定或部署。**

## 上傳

1. 解壓縮 `line_translation_release_priority_fix_20260908.zip`。
2. 把裡面的 **12 個檔案**一次上傳到既有 GitHub 專案根目錄，覆蓋同名檔案；其他檔案保留。
3. 等待 GitHub 的 Factory translation release gate 通過，再確認 Render 已部署此次 commit。
4. 在測試群組重新傳送 `@小麥（研磨股班長） 麻煩優先放行`。可接受的結果例如：`@小麥（研磨股班長） Tolong prioritaskan release data ke stasiun berikutnya.`

只上傳 `app.py` 會造成版本檢查不一致，請務必連同 `factory_message_semantics.py` 和其餘檔案一起上傳。沒有新的必填環境變數。現有未完成重試依原有持久佇列繼續；已遺失或未收到的 webhook 無法由這個 ZIP 重建。

若上線後仍沒有回覆，請取對應訊息時間附近的 Render logs，查找 `mention-guard`、`text_skipped`、`FinalDeliveryGuard`、`TranslationRetry` 和 `DeliveryPerf`。需要區分未收到事件、設定略過、翻譯拒收和 LINE 傳送失敗，不能再由聊天截圖猜測原因。

## 官方技術依據

- [OpenAI 延遲最佳化](https://developers.openai.com/api/docs/guides/latency-optimization)：減少不必要的模型請求。本次讓已正確的候選通過檢查，避免誤判引起的第二次生成及重試。
- [OpenAI 評估原則](https://developers.openai.com/api/docs/guides/evaluation-best-practices)：評估應納入一般、邊界與對抗情境；本次同時測試正確改寫和語意相反的候選。
- [LINE 接收訊息](https://developers.line.biz/en/docs/messaging-api/receiving-messages/)及 [Messaging API](https://developers.line.biz/en/reference/messaging-api/#send-reply-message)：保留原有非同步圖片處理、事件去重及補送流程；reply token 有使用限制，不可將重試等同於重複回覆同一 token。

官方文件支持處理方法，不是本廠術語的翻譯依據；術語語意依既有專案設定及使用者提供的情境。
