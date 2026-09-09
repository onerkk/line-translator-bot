# CI106：工廠介面版本混用修復

這包保留 UI104 的新版介面與 41 組中英文指令，並補齊 CI106 需要的前端檔案、檢查與更新工具。

## 原因與修復

你的 CI106 紀錄載入 `static/admin_factory.js` 的版本是 `2026-09-09.ci98-lifecycle`，SHA-256 以 `c831f044` 開頭，與保存的 CI98 舊版完全相同。`static/line_factory.js` 的錯誤行仍直接使用 `document.getElementById`，也是舊版內容。另一方面，新版 `tests/member_ui_lifecycle.cjs` 已經存在，所以出現新測試搭配舊前端。

已在本機用這組新舊檔案重現相同的 `getElementById` 錯誤。這個錯誤發生於頁面關閉後，未完成的請求繼續存取失效的 document。

本次提供：

- 正確的 `static/line_factory.js`：保存目前頁面 document、離開時取消請求、返回後拒收上一輪回應；修改原文、語言或站別後，不顯示或分享舊譯文。
- 相互匹配的 `static/admin_factory.js` 與 `static/liff_forms.js`。三份檔案均標記 `2026-09-09.ci106`。
- 前置檢查同時核對後台、工廠工具、表單三份 JS，不再只檢查後台；一次列出所有缺少或不相容的檔案，並指出誤放在根目錄的檔案。
- 自動更新程式 `apply_ci106_update.py`：內含完整更新，會將原始碼放回正確資料夾。替換前會備份，途中失敗會還原已替換的檔案。

原有 DOM 行為測試完整保留。前置檢查不會自動更改程式、不會隱藏異常，也沒有把未處理錯誤改成測試通過。

## 建議套用方式：單一更新程式

把 `apply_ci106_update.py` 放進 Repository 根目錄，也就是 `app.py` 所在的位置。這一份程式已內含其餘更新檔案，可從 Codespaces、你的電腦或伺服器上的專案工作目錄執行：

```bash
python apply_ci106_update.py
python tests/run_factory_ui.py --check-assets
```

更新時會顯示原始碼備份位置。所有目標檔案都已正確時，重複執行不會再改檔。想先查看待更新清單，可單獨執行 `python apply_ci106_update.py --check`；這個唯讀檢查在有檔案待更新時回傳 1，套用完成後回傳 0。

完成後，把這次原始碼變更提交到 GitHub，再走原本的測試、部署流程。此程式只操作指定原始碼，沒有外部網路或部署動作，亦不會改動資料庫、金鑰或群組設定檔。

## 使用 GitHub 網頁手動上傳

ZIP 裡也提供所有可直接閱讀的檔案。先解壓，再依目錄逐一上傳：

| GitHub 開啟的目錄 | 要放入的檔案 |
|---|---|
| Repository 根目錄 | `app.py`、新增模組、`apply_ci106_update.py`、`test_*.py` 與說明檔 |
| `static/` | `admin_factory.js`、`line_factory.js`、`liff_forms.js`、`line_factory.css`、`interface_theme.css` |
| `templates/` | `line_factory.html` |
| `tests/` | `run_factory_ui.py`、`member_ui_lifecycle.cjs` |
| `.github/workflows/` | `factory-translation-release-gate.yml` |

CI106 最直接需要修正的檔案是 `static/line_factory.js`，請在 GitHub 的 `static` 資料夾內覆蓋。根目錄中同名的 `line_factory.js` 不會取代 `static/line_factory.js`。

更新後，Actions 的前置檢查應列出三行：

```text
CHECK admin asset: .../static/admin_factory.js build=2026-09-09.ci106 ...
CHECK member asset: .../static/line_factory.js build=2026-09-09.ci106 ...
CHECK form asset: .../static/liff_forms.js build=2026-09-09.ci106 ...
```

接著仍會執行四組介面測試，不會因檔案有版本註記就略過實際功能檢查。

## 驗證範圍

已從重現 CI106 失敗的混用版本，實際執行更新程式後驗證：

- Python：**1,934 passed，443 subtests passed**，完整離線回歸 56.98 秒。
- 四組 DOM 介面測試全部通過，包含原本失敗的 member lifecycle。
- 新增 14 項檢查，涵蓋前端缺檔／舊檔、錯放路徑、換行格式、完整還原、重複執行、唯讀模式與失敗回復。
- 翻譯資料資產檢查通過；三份修正 JS 語法檢查通過。
- 本機已核對套用後的檔案內容與更新包完全一致。

本次以你貼出的紀錄與保存的原始碼重現問題。目前沒有可直接讀取這個 Repository 的 GitHub 連線，因此本機通過不代表你的 GitHub 或正式伺服器已更新。

翻譯模型、提示詞、品質檢查、持久化傳送及提醒設定沿用 UI104；本次重點是讓完整介面程式真正套用，並提早發現缺漏。沒有重新量測正式 LINE 回覆速度或執行付費 AI 呼叫。
