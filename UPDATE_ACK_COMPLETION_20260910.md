# 作業確認：全員完成通知卡

本包以此次上傳的 `line-translator-bot-main.zip` 為基準，只包含新增或變更檔案。

## 更新後的行為

- 名單內最後一位成員按下「了解」，且回覆成功儲存後，在原群組發出一張「目標人員已全員確認完畢」卡片。
- 保留每位成員原有的一次私人「已記錄」回覆。未全員確認時，不發完成卡。
- 新卡採深藍、墨綠與白色版面；中印雙語、確認人數、100% 進度、通知摘要、發起人及 UTC+8 完成時間。
- 只有 `understood` 算完成。舊「需說明」、空名單、名單外點擊，以及未回覆就離群的人，都不能湊成全員確認。
- 依該通知已列入的目標名單核對。若只能取得部分群組身分，卡片會註明範圍，不宣稱未辨識成員也已確認。
- 完成後停止原通知的自動提醒。原本手動停止提醒的通知，仍可記錄後續回覆並發出一次全員完成卡，不重新啟動提醒。
- 歷史上早已完成的通知不會因部署、查詢、重複點擊而補發卡片；尚未完成的舊通知在最後一人有效確認時適用新流程。
- 完成卡確認的是「成員已按了解」，不代表現場作業已實際執行完成。

## 防止重複與漏送

最後一筆回覆與完成通知待辦使用同一次資料庫原子更新。通知內容、完成時間與 LINE retry key 儲存後固定；多人同時點擊、網路逾時或發送成功但儲存狀態中斷時，沿用相同識別碼恢復。既有簽章 webhook 復原佇列負責重試，不要求使用者再按一次。

完成卡與私人確認各自保留發送狀態，任一通道失敗不會擋住另一個。發送前重新檢查通知版本、有效期限、目標名單及群組開關。接近 LINE 24 小時去重期限時停止不確定重試；LINE 永久拒絕則記錄失敗。這些狀態保留在通知資料的 `completion_notification`。

固定的雙語卡片由程式直接產生，不新增 AI 翻譯請求，也不需要額外圖片主機。

## 一併修正的附件啟動問題

原 `app.py` 要求數量語意模組版本 `2026-09-10.6-contextual-abstract-classifiers`，但附件實際提供 `2026-09-09.1-collective-and-ordinal-quantities`，會在啟動時直接丟出 deployment mismatch。

本包將啟動檢查明確配對到附件實際提供的模組版本，保留嚴格的 API／build 檢查；沒有更改該模組的翻譯規則，也沒有將舊實作重新標記成不存在的新版本。

## 套用方式

1. 解壓縮本更新包，將所有檔案上傳到原專案的同名位置；新增的 `line_ack_completion.py` 必須一起上傳。
2. 等待 GitHub 檢查及 Render 部署完成。
3. 在測試群組發出一則指定兩位成員的 `/確認` 通知；第一人按了解時維持未完成，第二人按了解後應出現一次完成卡。兩人再重按時不應重複出現。

若你的線上專案已經更新成不同版本，請先核對本包基準，避免覆蓋之後新增的修改。

## 已完成驗證

- 全套 Python 回歸：**2,669 passed，443 subtests passed**。
- 真實 SQLite／Redis CAS、同時最後回覆、重複點擊、私人通知失敗隔離、HTTP 200／409／400／429／500、接受後儲存中斷、期限／權限變更及簽章 webhook 復原。
- LINE SDK Flex 訊息往返驗證、Unicode 與字數界線、長文、500 人計數及 UTC+8 時間。
- 版本化工廠資產檢查與前端配對檢查通過；`tests/run_factory_ui.py` 完整介面驗收通過（後台、成員頁、確認作業、提醒、表單、統一選單及頁面生命週期）。
- 依實際 Flex JSON 產生 320px／280px 排版示意並檢查；示意使用樣本資料，LINE App 字型與間距可能略有差異。
- 尚未在你的 LINE 正式群組發送或部署；LINE API 接受訊息也不等於所有手機實際收到推播。

## 官方規格依據

- [LINE：Send Flex Messages](https://developers.line.biz/en/docs/messaging-api/using-flex-messages/)
- [LINE：Flex Message layout](https://developers.line.biz/en/docs/messaging-api/flex-message-layout/)
- [LINE：Retry failed API requests](https://developers.line.biz/en/docs/messaging-api/retrying-api-request/)
