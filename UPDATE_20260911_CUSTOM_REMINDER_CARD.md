# 自訂提醒卡片更新

本包接續前兩次「翻譯自動學習」及「提前停止追蹤卡片」更新，僅包含這次變更的檔案。請先保留前兩包的更新，再依 ZIP 內的原路徑覆蓋檔案並重新啟動服務；`static/`、`tests/` 內檔案不要移到專案根目錄。

## 完成內容

- 自訂提醒改為深藍、霧金及白底的 LINE Flex 卡片，日期、星期、時間、內容及通知對象分層呈現。
- 固定標題與欄位採中文／印尼文，時間明示台灣 UTC+8。自訂內容依輸入原樣顯示，包含雙語文字、換行及表情符號；沒有新增自動翻譯或模型呼叫。
- 支援完整 1,500 UTF-16 字元內容；提醒本文不省略、不截斷。長文字會展開卡片高度。
- 「不標註」僅送一張卡；「@所有人／指定成員」送出真正的 LINE TextV2 標註及卡片，合併在同一次 Push 請求內，內容不重複貼兩遍。
- 後台輸入時立即顯示相同風格的預覽，切換日期、內容與標註對象都不發預覽 API 請求。群組名稱與輸入內容以文字節點呈現，避免被解析為網頁程式。
- 保留原本排程、修改、取消、權限及七天提醒歷史規則。

## 更新與重試

第一次發送前，實際訊息內容與派送租約一起保存。遇到逾時、重新啟動或更新設計，重試仍使用原始內容及同一個 Retry Key。

更新前已嘗試發送的舊提醒，會以原本純文字格式完成重試，以遵守 LINE 同一 Retry Key 必須搭配同一內容的規則。尚未嘗試發送的提醒會使用新卡片。這不會補發已完成或已取消的提醒。

正常每筆提醒仍只有一次 Push API 請求，沒有新增 AI 或圖片下載成本。LINE 訊息額度的計算不以同一請求中包含幾個訊息物件累加：[LINE 發送訊息與計數說明](https://developers.line.biz/en/docs/messaging-api/sending-messages/)。[TextV2 原生標註說明](https://developers.line.biz/en/docs/messaging-api/message-types/)。

## 更新檔案

| 檔案 | 用途 |
|---|---|
| `line_message_ui.py` | 新增自訂提醒卡片產生器，保留前次卡片 |
| `scheduled_reminders.py` | 新卡與原生標註同次派送、保存重試內容、相容已派送的舊格式 |
| `reminders_web.py` | 後台回應不夾帶內部派送用卡片資料 |
| `static/admin_reminders.js` | 本地即時卡片預覽 |
| `static/admin_reminders.css` | 卡片配色、字級及手機版排版 |
| `app.py` | 更新前端資產版本，讓瀏覽器載入新預覽；保留先前翻譯更新 |
| `test_custom_reminder_card.py` | 卡片、內容完整性、原生標註及跨版本重試測試 |
| `tests/custom_reminder_card_ui.cjs` | 實際後台表單的 DOM 操作測試 |
| `tests/run_factory_ui.py` | 將新預覽測試納入現有 CI 的 UI 檢查階段 |
| `UPDATE_20260911_CUSTOM_REMINDER_CARD.md` | 本次更新說明 |

## 完成圖片

另附 `custom-reminder-card-preview.png`，由實際卡片 JSON 與示例內容繪製。示例為「2026/09/20 08:00、研磨 C 班、開班股會議」，不會建立任何真實提醒。圖片是本地預覽，實際 LINE 字型與顯示可能因裝置不同而略有差異：[LINE Flex 顯示說明](https://developers.line.biz/en/docs/messaging-api/using-flex-messages/)。

## 驗證

交付前完整回歸：**2,822 項測試、443 個子測試全部通過**。新卡片相關 Python 測試共 19 項。既有前後台 DOM 操作測試及新增自訂提醒預覽測試均通過，前端語法及翻譯資產驗證也通過。

測試包含 SQLite／Redis 實際儲存與原子更新、最多 20 位成員、1,500 字元、括號及表情符號、不重複送出、舊提醒重試相容、後台預覽及文字注入防護。只有外部 LINE／AI 傳輸以離線替身驗證。未部署或向正式群組發送測試訊息。
