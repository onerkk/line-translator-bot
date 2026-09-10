# 「了解」一次性私訊確認更新

版本：`2026-09-10.5-private-ack-once`

本包接續 `line-translator-owner-controls-update-20260910.zip`，僅包含這一次變更的檔案。將本包所有檔案依相對路徑覆蓋專案根目錄，再重新部署並重啟所有程序。`line_ack_receipts.py` 是必要的新模組，請一併上傳；`line_ack_reminders.py` 本次只同步版本識別，以維持既有版本一致性檢查。

## 實際行為

- 每則通知、每位名單內成員第一次成功按「了解」：先保存後台回覆，再只私訊本人一則「✅ 已記錄 / Sudah tercatat」，附上通知短編號以便辨識。
- 同一則通知重複點擊、webhook 重送或程序重啟，不再新增私人確認；重複「了解」保留原回覆時間和姓名。不同通知各自計算一次。
- 名單外的人，包括未被列入名單的發起人或管理員，按「了解」仍不回應、不加入回覆或私人確認紀錄。
- 群組不因「了解」而新增文字、卡片或 @。原有後台提醒時間、僅標記未回覆者、零未回覆自動停止，及發起人／管理員管理權限不變。
- 停止提醒後的首次有效回覆仍可記錄並私訊一次，不會重新啟動群組提醒。舊卡片的「需說明」相容操作不發私人確認，也不能重新啟用已送過的確認。
- 更新前已記錄為「了解」的回覆，不會因安裝更新或再次點擊而補發歷史確認。

## 防止重複通知

回覆與私人確認待辦以同一次 SQLite／Redis 原子更新保存。LINE 私訊只在回覆保存成功後送出，使用真正點擊者的 LINE 使用者 ID，不使用群組 reply token，也不走翻譯或群組補送流程。

私人確認從第一次送出就附上固定 `X-Line-Retry-Key`；失敗重試保持相同對象、文字及識別碼。LINE 回覆 HTTP 200，或帶有已接受請求識別碼的 409 後，即保存 `accepted` 狀態。逾時、暫時錯誤或傳送後的存檔失敗，交由既有簽章 webhook 復原佇列處理，不要求使用者再按一次。重試採退避等待，超過 23 小時安全期限後停止，以避免超出 LINE 的 24 小時防重複期限。

無法重試的 API 拒絕保留為 `failed`；超過安全期限的未確認傳送保留為 `uncertain`。這些狀態不會撤銷已保存的回覆，也不會改向群組發送錯誤訊息。未另外新增輪詢排程、資料庫或 AI 呼叫。

## LINE 的實際限制

私人確認使用 LINE Push API，會依官方規則計入訊息額度，但不消耗翻譯 AI 額度。請成員先加機器人好友且不要封鎖；官方也允許對 7 天內曾在一對一聊天室傳訊的使用者推播。單純同在群組，不代表能收到機器人私訊。

`accepted` 代表 LINE API 接受請求，不能證明手機已收到、已讀或振動。未加好友、封鎖及手機通知設定等情況可能影響接收或提示；本次未加入不存在的振動 API。

復原沿用專案既有 webhook／儲存機制，其可用性仍取決於實際部署及 LINE 重送設定。測試不代表已操作正式伺服器或實測所有手機。

## 驗證與查閱

新增測試涵蓋 SQLite、模擬 Redis、一次性私訊、名單與管理權限、多人並行、重複點擊、來源更新、停止提醒、舊回覆、LINE HTTP 狀態、傳送逾時與儲存失敗，以及實際簽章驗證的本機 webhook 復原。

實際驗證結果：新增 **84 個測試通過**；完整離線回歸 **2,486 passed、443 subtests passed**，耗時 67.30 秒。後台、群組選單、使用者頁面、表單與提醒介面操作測試全部通過；翻譯資產驗證無錯誤或警告。

回歸測試使用既有封鎖外部 HTTP、DNS、socket 的離線執行器；LINE 與 AI 使用模擬傳輸，未傳送真實私訊或群組訊息，未部署正式服務。

後台既有回覆 API 的 `build_id` 應為 `2026-09-10.5-private-ack-once`。每則通知新增的 `receipt_confirmations` 保存私人確認狀態，`responses` 仍是作業回覆的依據。

## 官方依據（2026-09-10 查閱）

- [LINE Push API 與可傳送對象](https://developers.line.biz/en/reference/messaging-api/#send-push-message)
- [LINE 失敗重試、防重複識別碼及期限](https://developers.line.biz/en/docs/messaging-api/retrying-api-request/)
- [LINE 訊息額度計算](https://developers.line.biz/en/docs/messaging-api/pricing/)
- [LINE Postback action 規格](https://developers.line.biz/en/reference/messaging-api/#postback-action)
