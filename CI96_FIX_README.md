# CI #96 修正版

適用於本次日誌顯示的核心版本 `2026-09-09.ack-repeat-stop.3`。

## 原因與修正

後台啟動時同時讀取設備、確認紀錄及成員名單。舊 UI 測試在確認紀錄出現後就關閉重新開啟的頁面；當成員名單較慢回來，程式仍更新已銷毀的 DOM，產生 `Cannot read properties of undefined (reading 'getElementById')`。本機以延遲名單回應重現了相同錯誤堆疊。

本次修正會在離開頁面時取消未完成請求並停止畫面更新；返回頁面時重新載入，舊回應不會覆蓋新資料。仍在使用中的頁面會正常顯示讀取失敗與逾時訊息。UI 測試會等待整頁載入完成，並加入延遲回應、關閉頁面及返回頁面的回歸檢查。

## 覆蓋路徑

ZIP 包含下列 5 個程式／測試檔，以及本說明。解壓縮後，依原路徑覆蓋至既有 repository，同一個 commit 提交這些檔案。

| 檔案 | repository 中的位置與用途 |
| --- | --- |
| `app.py` | 根目錄；更新後台 JS 的快取版本 |
| `static/admin_factory.js` | `static/`；修正非同步請求與頁面離開／返回流程 |
| `tests/factory_ui_smoke.cjs` | `tests/`；等待整頁載入，關閉時發出頁面離開事件 |
| `tests/factory_ui_lifecycle.cjs` | `tests/`；新增頁面關閉與延遲回應回歸檢查 |
| `tests/run_factory_ui.py` | `tests/`；納入新的回歸檢查 |

提交後執行 `Factory translation release gate`。UI 步驟應顯示 `PASS lifecycle` 與 `CHECK factory pages: ci96-page-lifecycle`，接著完成其餘 `PASS` 檢查。

## 本機驗證

- Python 3.12.14；完整離線回歸：**1865 passed，443 subtests passed，54.88 秒**。
- Node.js 24.19.0、jsdom 30.0.1；`python tests/run_factory_ui.py` 全部通過。
- UI 測試涵蓋後台、確認紀錄、群組設定、成員頁、表單、原生點名訊息格式、重複提醒排程、單筆停止、群組關閉及選單分頁。
- 延遲回應與錯誤在關閉頁面後抵達、離開時取消請求、返回後拒絕舊回應，以及使用中頁面的錯誤／逾時顯示，均通過檢查。
- 6 個前端與測試 JS 檔的語法檢查通過。
- 資產驗證：`ok: true`，`errors: []`，`warnings: []`。

上述為本機隔離測試，LINE 與 AI 傳輸使用測試替身。GitHub 的執行結果需以上傳後的 Actions 為準。
