# CI #88 前端測試更新

本包僅包含要覆蓋的兩個前端測試檔，及本說明。

請覆蓋至儲存庫內以下路徑：
- tests/factory_ui_smoke.cjs
- tests/unified_menu_ui_smoke.cjs

如果使用 GitHub 網頁上傳：
1. 開啟 line-translator-bot 儲存庫，選擇發生 #88 的分支。
2. 進入 tests 資料夾。
3. 按 Add file → Upload files，上傳本 ZIP 的 tests 資料夾內兩個 .cjs 檔。
4. 確認修改的是上列兩個既有路徑，按 Commit changes。
5. 到 Actions 查看這次新提交的 Factory translation release gate 結果。

更新生效後，前端測試紀錄會出現：
CHECK factory pages: ci88-reminder-settings
CHECK unified menu: ci88-fixed-pagination

原因與修正：
- #88 提供的第 31 行錯誤與管理頁 PASS 文字，均符合舊版前端測試。
- 舊版要求預設圖片選單一定分兩頁；確認卡改成指令觸發後，預設按鈕變少，這項假設已不成立。
- 新版分頁測試停用既有測試按鈕，建立 14 個內容不同的測試按鈕，驗證共兩頁、第一頁 12 顆與第二頁 2 顆，並實際點擊換頁。
- 同時驗證指令確認卡預覽，以及群組各自的提醒開關與分鐘數儲存。
- 兩份測試加入版本提示，方便直接從 CI 紀錄確認執行中的檔案。

本次驗證：
- 在本機副本換回舊版兩個測試檔，重現相同的 image pagination 第 31 行失敗。
- 覆蓋本包兩個修正版檔案後，以 jsdom 30.0.1 執行 python tests/run_factory_ui.py，退出碼為 0。
- 確認紀錄、管理頁、成員頁、表單、選單五項流程全部 PASS。
- 本次僅修改前端測試；Python 回歸測試未重跑。
- 尚未直接讀取或執行使用者 GitHub #88 分支；上述結果為本機驗證，無法據此判定舊檔是因上傳位置或分支差異而被執行。

本包適用於目前已完成 Python 測試、但卡在 #88 前端操作測試的版本。
