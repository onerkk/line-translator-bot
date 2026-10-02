# 工廠翻譯根因修正（2026-10-02）

本包包含 2026-09-29 的 source-alignment 根因更新，以及本次四張截圖的增量修正。將 ZIP 內同名檔案覆蓋到專案根目錄。

## 截圖核對

- 400 系鋼種、磁性、疑似混入 303、PMI 與工單報表：整體意思正確。`供查核` 指報表供後續資料核對／稽核；已加入文件查核語境，避免被理解成再做一次實體 PMI 檢驗。
- 地面 `掉落物` 原譯成 `material yang jatuh`，把未指定種類的物品縮窄成材料。規則改為保留一般物件層級，如 `benda/barang yang jatuh`。
- 總經理「開始在釘環境」依使用者確認，是開始嚴格重視環境整潔。舊譯 `mulai menaruh perhatian serius pada kebersihan lingkungan` 大意正確；新語境指引會保留開始、嚴格重視和整潔對象，不自行加出稽核、懲處或新命令。
- `Barang BF3 micro/mikro besar` 依使用者確認是 BF3 料件量測尺寸偏大。錯誤根源是 parser 沒有把 `barang` 識別為量測對象，因此完整語義框架未啟動。現在會輸出 `BF3料件的尺寸量測值偏大`，並阻擋把尺寸狀態誤掛到分厘卡本身。
- 噴漆不要過多噴到棒身、該捆應由本班包裝及已有客訴：原譯大意正確，無須改成特定事件或額外責任。

## 根因修正

- 將設備代碼、量測工具線索、明示量測對象及尺寸結果拆成不同語義角色。`barang/produk/material/bahan/batang` 保留原本對象類別；相互矛盾的類別不會被硬合併。
- `micro/mikro` 只有和已知設備代碼及明確尺寸狀態一起出現時才作量測讀法，不會在一般印尼文中被全域改義。
- 提示詞要求依完整語句和現場關係解析口語縮寫，保留原文對象的具體程度，並區分環境整理、生態環境及棒材清洗製程。
- 文件「供查核／稽核／備查」與對材料或機台執行實體檢查分開處理。
- 保留上一包 source-alignment 修正與 CI 測試更新；本包可作為累積更新覆蓋使用。

## 驗證

- 31 個量測語義及統一政策 `unittest` 通過。
- 4 個 housekeeping 語義測試通過，包含一般物件範圍、主管重視整潔，以及不誤觸棒材清洗製程。
- `validate_factory_translation_assets.py` 通過；修改檔案 `py_compile` 通過。
- 目前本機沒有 pytest 和 Flask，因此無法在此執行完整離線 release gate 或載入 Flask app 的 package delivery tests。CI 環境仍應執行完整 release gate。
