# 漏譯與 CI #98 修正版

## 已重現的漏譯缺陷

截圖原文：

> 這些研發單位已確認完，
> 三把都可以生產

測試使用的正確印尼文：

> Bagian R&D sudah selesai memeriksa material-material ini. Ketiga bundel tersebut sudah boleh diproduksi.

原本的數量檢查不認得名詞前的集合數詞 `ketiga`。即使 AI 已回傳上述譯文，程式仍報 `quantity_semantics:atom_missing:q1:3:bundel`，攔住首次傳送，背景補送也再次被同一檢查攔住。

這是本機以截圖原文與正確候選譯文重現的程式缺陷。未取得正式環境當次 webhook／翻譯日誌，因此無法證明截圖當次的唯一原因就是此缺陷。

修正內容：

- 數量檢查接受名詞前的集合數詞，例如 `kedua bundel`、`ketiga bundel`、`keempat lot`，同時保留數字與一般數詞的既有支援。
- 區分「三把」與「第三把」；`ketiga bundel` 和 `bundel ketiga` 分別處理。
- 排除大數與小數的局部字串誤認，例如 `tiga puluh bundel`、`dua puluh tiga bundel`、`1.3 bundel` 都不能滿足原文的三把。
- 誤譯數量、誤換單位及混淆序數仍會被擋下。本次沒有加入這整句的固定譯文替換。
- 更新數量引擎與主程式的配套版本，以及既有部署測試。

文法查證：[Narabahasa：Numeralia Pokok dan Tingkat](https://narabahasa.id/artikel/linguistik-umum/kata/numeralia-pokok-dan-tingkat/)，其中說明集合數詞置於名詞前，序數置於名詞後。

## CI #98 的原因與更新方式

#98 的測試已讀到 `tests/factory_ui_lifecycle.cjs`，但執行中的 JS 堆疊仍對應舊版 `static/admin_factory.js` 第 5、184 行。該測試直接讀取 repository 中的 JS 檔。

本包納入配套後台 JS 與 UI 測試，並新增提前檢查：CI 會在安裝套件、執行完整回歸前列出後台 JS 的實際路徑、版本與 SHA-256；讀到舊檔或缺檔時，直接指出需更新的路徑。

解壓縮後，依以下路徑覆蓋至既有 repository，所有檔案在同一個 commit 提交：

| repository 路徑 | 用途 |
| --- | --- |
| `app.py` | 配套數量引擎版本與後台 JS 快取版本 |
| `factory_quantity_semantics.py` | 集合數詞、序數與數量完整性修正 |
| `static/admin_factory.js` | 後台頁面生命週期修正，取消離頁請求，忽略舊回應 |
| `test_factory_quantity_semantics_root_fix.py` | 更新既有配套版本測試 |
| `test_quantity_collectives.py` | 數量／序數正反例測試 |
| `test_quoted_release_delivery.py` | 截圖原文的傳送及補送測試 |
| `tests/factory_ui_lifecycle.cjs` | 頁面離開、返回與延遲回應測試 |
| `tests/factory_ui_smoke.cjs` | 等待整頁載入的配套 UI 流程 |
| `tests/run_factory_ui.py` | 前端配套檢查及 UI 測試入口 |
| `.github/workflows/factory-translation-release-gate.yml` | 在發布檢查前段驗證 JS 版本 |

其中 `static/admin_factory.js` 檔案頂端應為：

```javascript
// FACTORY_ADMIN_BUILD: 2026-09-09.ci98-lifecycle
// FACTORY_ADMIN_LIFECYCLE_API: 1
```

數量引擎及 `app.py` 的配套版本為 `2026-09-09.1-collective-and-ordinal-quantities`。

## 驗證結果

- 完整離線 Python 回歸：**1904 passed，443 subtests passed，56.78 秒**。
- 數量與漏譯針對性檢查：**52 passed，2 subtests passed**。
- 截圖這句在有引用快取、無引用快取、AI 暫時失敗後補送的情境，均成功送出完整候選譯文。
- 全部 UI 流程通過，含後台、確認紀錄、成員頁、表單、重複提醒、單筆停止、群組關閉及選單分頁。
- 前端配套檢查：舊 JS 與缺檔會被正確拒絕；新版通過，且均列出檔案路徑與 SHA-256。
- 前端與測試 JS 共 6 檔語法檢查通過。
- 資產驗證：`ok: true`、`errors: []`、`warnings: []`。

測試環境為 Python 3.12.14、Node.js 24.19.0、jsdom 30.0.1。LINE 傳送與 AI 回應使用隔離測試替身，沒有向正式群組發訊息。GitHub 結果以上傳後的 Actions 為準。
