# 翻譯問題根治：統一案例庫、來源語意框架與結構化獨立核對

Build：2026-07-24.6

## 問題根因

舊系統把人工修正、Translation Memory／Vector TM、工廠知識與提示範例分散處理。即使範例很多，模型與本地品質檢查仍主要確認「關鍵字是否出現」，沒有先鎖定原文中的角色關係，例如：

- 到料的期限、數量與集中程度；
- 材料種類與大／小尺寸範圍；
- 指定機台與生產優先順序；
- 禁止行為、動作順序與產出效率；
- 原文沒有寫出的天車種類、RPM 或機速不得自行補充。

因此，譯文只要表面通順、相關詞彙大致齊全，仍可能把修飾範圍、禁止關係或現場簡語翻錯。

## 架構修正

1. `translation_casebook.py` 統一內建範例、自訂範例、工廠知識與 Active Learning 修正，保存錯譯、正譯及原因。
2. 同一來源存在多版修正時只採最新人工修正；精確 TM、Vector TM、案例庫與 `/wrong` 共用同一更新流程。
3. 工廠案例必須通過來源契約；公告套語不能單獨觸發不相關案例。
4. 新增 `factory_semantic_audit.py`，先從中文原文建立來源語意框架，再進行翻譯與驗證。
5. 語意框架拆解期限、月份範圍、材料、尺寸、製程、機台代碼、到料、數量、集中程度、優先順序、禁止、吊／上機與慢跑語意。
6. 品質閘門不只檢查詞彙存在，還檢查關係：大量與集中必須修飾到料；機台代碼必須和優先指令在同一子句；禁止、小尺寸、上機及慢跑必須形成同一個作業限制。
7. 高風險工廠訊息使用結構化獨立核對。審查模型必須回傳來源 claims、歧義處理、逐項 target evidence、無根據新增與最終譯文；缺少 claim、證據不在譯文中或自行承認有新增內容時一律拒收。
8. OpenAI／Gemini 使用 JSON Schema；Anthropic 使用原生 Messages API 的 `output_config.format`。嚴格格式不可用時，降級為提示詞強制 JSON，再由本地程式驗證內容。
9. 即使獨立審查 API 無法使用，完整命中的「大尺寸拋光棒材集中到料與機台優先排程」仍可由當前來源欄位保守重建。機台代碼從當前原文取得，不會固定貼上 I5、I15。
10. 已知錯譯、審查失敗譯文及語意驗證失敗譯文不得寫入快取或 TM。
11. 只有完全相同來源才能直接使用已驗證人工譯文；改寫句必須重新依來源框架翻譯，禁止盲貼舊句。

## 本次案例

來源：

> 月底前拋光機大尺寸棒材會集中大量到料，I5、I15要先從本月份的大尺寸優先生產，不可以吊小尺寸慢慢跑。

已驗證譯文：

> Sebelum akhir bulan, batang berukuran besar untuk mesin polishing akan tiba dalam jumlah besar dalam waktu yang berdekatan. I5 dan I15 harus mendahulukan produksi batang berukuran besar yang dijadwalkan untuk bulan ini. Jangan mengangkat dan memasukkan batang berukuran kecil ke mesin lalu menjalankan produksinya secara lambat.

系統會拒絕：

- `produksi ukuran besar dari bulan ini`：讓「本月份」錯誤修飾抽象的 ukuran besar；
- 將「慢慢跑」只接到吊掛動作，沒有保留生產／占機語意；
- 原文沒有天車時自行加入 `crane`、`derek` 或 `overhead crane`；
- 原文沒有轉速時自行加入 RPM、低轉速或降低機速；
- 把到料量大、到料時間集中、指定機台優先與禁止小尺寸慢跑拆散到無關句子。

## 驗證結果

- 132 項可執行 pytest 測試全部通過，另有 27 項參數化子測試通過。
- 本次來源語意框架與結構化輸出新增 18 項專項測試，涵蓋已知錯譯、改寫句、關係錯置、虛構天車、虛構 RPM、審查模型自我誤判、證據不存在及 API 不可用時的安全重建。
- OpenAI、Gemini、Anthropic 三條結構化輸出傳輸均以假客戶端驗證實際參數形狀。
- 所有變動 Python 檔案通過 `py_compile`；`factory_knowledge.json` 通過 JSON 解析及知識案例測試。
- 目前執行環境缺少 `flask`、`line-bot-sdk` 與 `anthropic`，因此兩個必須完整匯入 `app.py` 的測試模組無法載入，也未執行真實 LINE webhook 或供應商 API smoke test。

## 保證範圍

這一版能對已建模的工廠語意、歷史修正與反例提供可重複的程式驗證；獨立審查失敗時，完整命中的本案例類型仍有來源欄位式安全重建，不再只靠模型自行判斷。

任何生成式翻譯系統都不能誠實保證所有未見領域、所有新簡語與所有未來句型永遠零錯誤。新型問題仍需新增來源語意規則或人工修正，但新增後會自動進入同一套案例檢索、來源守門、結構化核對、TM 污染防護與回歸測試流程。
