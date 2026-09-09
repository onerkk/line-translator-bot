# CI #115／#116：作業確認檔案同步修正

已在隔離副本重現兩種失敗：

- 新版測試配舊版 `static/admin_quick_reply.js`，會出現 #115 的「確認卡固定保留」斷言失敗。
- 新版前端配舊版 `line_quick_reply.py`，會出現 #116 的 `hidden acknowledgement remains answerable` 逾時。舊後端在關閉所有快捷鍵後回傳 0 顆按鈕，正確結果應保留 1 顆「了解」。

這符合部分檔案未同步更新的情況。目前未直接讀取正式 repository 的檔案，不能把本機重現當成正式分支版本的直接證據。本版 CI 會列出兩個實際載入檔案的完整路徑、版本及 SHA-256，便於精確定位。

## 套用方式

請解壓縮後依原路徑覆蓋專案；`line_quick_reply.py` 在專案根目錄，`admin_quick_reply.js` 在 `static/`。本包包含本次修正及其必要配套檔案。

如果先前多次漏放檔案，可只把 `apply_ack116_update.py` 放進有 `app.py` 的專案根目錄，執行：

```bash
python apply_ack116_update.py
python apply_ack116_update.py --check
```

這個單檔工具內含同一批可讀程式與測試，會按正確路徑安裝並逐檔校驗。套用前備份舊檔，寫入失败會回復已替換的檔案；`--check` 只檢查，不寫入。完成後，將變更提交到 GitHub，通過 CI 再部署並重啟機器人。

本次工具不覆蓋 `app.py`、翻譯引擎、資料庫或群組設定，因此保留先前翻譯修正。請使用本次工具處理作業確認更新；`apply_ci106_update.py` 是舊版完整更新，重新執行會寫入舊版檔案。

## 保留的功能與新增檢查

指令模式下，關閉、刪除或限定圖片用途的「了解」快捷鍵，不會停用 `/ack`／`/確認`；確認卡固定保留「了解」，底部選單仍依管理員設定。一般翻譯不自動附確認卡，也未恢復「不了解」按鈕。群組明確關閉、個別通知停止提醒、重複提醒及群組隔離均保留。

CI 原有確認卡介面與回覆測試保留。新增檢查會在安裝依賴前辨認不完整的前後端更新；實際 HTTP 測試另驗證關閉／刪除／圖片限定快捷鍵、底部選單關閉及明確停用，失敗時會顯示 API 回應，不再只看到逾時。

```bash
python tests/run_factory_ui.py --check-assets
python run_translation_offline_checks.py
python tests/run_factory_ui.py
python validate_factory_translation_assets.py --json
```

測試使用隔離資料與模擬 LINE／AI；尚未部署或向正式 LINE 群組發送訊息。
