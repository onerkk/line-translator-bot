# CI #133 修正：後台「指定成員」介面檔未更新

此包適用於 CI 已顯示 `2026-09-09.ack-recipient-scope.7` 的版本，只包含本次需補上的檔案。

## 已確認的原因

CI #133 載入的 `static/admin_factory.js` 仍為 ACK119，SHA-256 為 `eb2cb6de12747381821e6305cc467b5b9e177af2dae8f29e7fdb006c3a712095`。後端與測試已支援指定成員，但這份舊介面沒有顯示對應範圍，造成 `selected audience in admin` 逾時。

本次已用相同檔案組合重現相同錯誤。保留原測試，只替換正確的 `static/admin_factory.js`，整套 UI 測試即通過。

## 套用方式

請解壓縮後依下列路徑覆蓋；ZIP 內没有額外的專案外層資料夾。

| 更新檔 | GitHub 中的位置 |
| --- | --- |
| `static/admin_factory.js` | 先進入 `static` 資料夾，再上傳覆蓋 `admin_factory.js` |
| `tests/run_factory_ui.py` | 先進入 `tests` 資料夾，再上傳覆蓋 `run_factory_ui.py` |
| `test_ci133_admin_asset.py` | 專案根目錄 |
| `CI133_UPDATE_README.md` | 專案根目錄 |
| `CI133_SHA256SUMS.txt` | 專案根目錄 |

**放在專案根目錄的 `admin_factory.js` 不會被頁面載入；必須更新 `static/admin_factory.js`。**

更新後開啟 GitHub 上的 `static/admin_factory.js`，第一行應為：

```javascript
// FACTORY_ADMIN_BUILD: 2026-09-09.ci133-recipient-scope
```

其 SHA-256 應為 `1ddfd8375fc149a6ed993d0a825c5e920080a080aebf2aa849c8f37991d84cd3`（LF 換行）。

完成以上覆蓋後重新執行 Actions；通過後再部署。可先執行：

```sh
python tests/run_factory_ui.py --check-assets
```

日誌應出現 `CHECK admin recipient scope`，並顯示 `build=2026-09-09.ci133-recipient-scope api=1`。這次新增的檢查會在載入舊介面時立即指出正確檔案路徑，避免等到 UI 測試才逾時。

## 行為與驗證

後台會依通知顯示「全部成員」或「指定成員」。原本的範圍功能測試完整保留，沒有移除斷言或延長逾時。此次不修改翻譯引擎、提醒排程及回覆紀錄邏輯。

本機已完成：

- 完整離線測試：2,187 passed，443 subtests passed。
- 完整 UI 測試：通過，包括指定成員範圍、非指定成員回覆忽略、回覆完成停止提醒、靜默記錄及未回覆者標記。
- 原始 CI #133 測試搭配單一介面檔替換：通過。
- 資產檢查：`ok: true`，沒有警告。
- 新增舊檔、缺檔、錯誤目錄、版本及換行情境的回歸檢查。

以上為本機驗證結果；尚未替你更新 GitHub 或部署正式伺服器。
