# ACK121：了解回覆靜默記錄，排程才廣播名單

本包以 ACK120 為基礎，只包含本次變更檔案。

## 更新方式

將 ZIP 內檔案依原路徑覆蓋至儲存庫根目錄，保留 `tests/` 目錄，再部署並重新啟動所有服務程序。

`line_factory_features.py` 與 `line_ack_reminders.py` 必須一起更新，兩者版本皆為 `2026-09-09.ack-silent-receipts.6`。不要再執行以前更新包的安裝程式，以免覆蓋成舊版本。既有通知與回覆資料可繼續使用，不需要重建通知。

## 本次行為

| 操作／狀態 | 後台記錄 | 群組訊息 |
| --- | --- | --- |
| 第一次按「了解」 | 儲存姓名、回覆狀態及時間 | 不回覆、不重發卡片 |
| 已了解者重複按下 | 保留首次有效回覆，不重複寫入 | 不回覆 |
| 停止提醒後，其他人第一次按「了解」 | 仍可儲存，維持停止狀態 | 不回覆、不恢復廣播 |
| 廣播時間到，通知仍啟用且有人未回覆 | 讀取當時回覆資料 | 顯示更新名單及通知內容，個別 @ 未回覆者 |
| 已知成員未回覆為 0 | 立即取消後續提醒 | 不額外發送完成卡片 |
| 發起人／管理員按「停止提醒」 | 取消該通知後續提醒 | 只回覆簡短停止通知，不重發整張卡片 |
| 明確按「查看回覆」 | 查詢最新狀態 | 顯示查詢結果 |

後台可以即時查看回覆；群組的自動名單更新依設定的廣播時間進行。新發送卡片會註明「按了解只記錄，不另發訊息」。正常回覆不呼叫 LINE 回覆或推播；真正儲存失敗時仍保留錯誤提示及重試機制。

## 驗證

驗證涵蓋 SQLite／Redis 儲存、同時點擊、LINE 簽章 webhook、停止與回覆同時發生、停止後補答、廣播前的最新名單、原有傳送重試，以及後台介面。

- 完整後端回歸：2,105 passed、443 subtests passed。
- 介面檢查：全部通過，包含頁面生命週期、後台回覆更新、停止後補答、排程名單、群組設定與快捷鍵。
- 翻譯資產檢查：通過，0 errors、0 warnings。

測試使用本機模擬 LINE 傳送，未向正式群組發訊息，也尚未部署至你的伺服器。

## 檔案

- `line_factory_features.py`
- `line_ack_reminders.py`
- `line_message_ui.py`
- `test_ack_configuration.py`
- `test_ack_duplicate_taps.py`
- `test_ack_known_zero_stop.py`
- `test_ack_reminders_offline.py`
- `test_ack_silent_receipts.py`
- `test_factory_receipt_flow.py`
- `tests/factory_ui_smoke.cjs`
- `ACK121_UPDATE_README.md`
- `ACK121_SHA256SUMS.txt`

`ACK121_SHA256SUMS.txt` 提供本包內容的 SHA-256 校驗碼。
