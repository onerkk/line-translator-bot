# 翻譯問題根治：統一修正案例庫、來源守門與來源核對

Build：2026-07-24.5

## 問題根因

舊系統把相同的人工知識分散在 `/wrong` 自訂範例、Active Learning、Translation Memory／Vector TM 與 `factory_knowledge.json`。人工修正多半只能保護完全相同原句；模糊案例檢索又容易被「今日起、抽查、各班要求、監視器」等公告套語誤導。結果是：句子換個說法可能重犯舊錯，不相關公告也可能被套入錯誤領域規則。

## 本次架構修正

1. 新增 `translation_casebook.py`，把內建範例、自訂範例、工廠知識案例與 Active Learning 修正編譯成同一個對比案例庫。
2. 人工修正同時保存錯譯、正譯與原因；提示詞明確標示錯譯不可仿照。
3. 同一來源存在多版修正時，採最新人工修正，避免新舊正解同時進入提示詞。
4. `/wrong` 與後台人工修正會同步更新 Active Learning、精確 TM、Vector TM 與案例庫快取。
5. 工廠知識案例新增 `source_match` 來源契約；未滿足領域條件的句子不能參與模糊檢索。
6. 未配置來源契約的人工修正，若不是完全相同原句，必須至少共享兩個排除公告套語後的非通用語意錨點，才可泛化到其他句子。
7. 「上下料秤重」規則現在必須同時命中機台上下料與秤重；不能因出現「今日起、抽查、各班要求、監視器」而誤觸發。
8. 「監視器監看＋現場觀察」拆成獨立雙重查核規則，來源必須同時包含監視器、現場觀察及查核語意。
9. 高信心人工修正或高風險公告可追加一次以原文為準的獨立來源核對；若有其他供應商，優先使用不同供應商。
10. 已知錯譯再次出現時會被品質閘門攔截；但不再要求正確譯文逐字包含某個固定印尼文片語，避免誤殺同義且正確的翻譯。
11. 只有完全相同來源才能直接回退至已驗證譯文；改寫句不得盲目貼上舊句。

## 本次案例的保護結果

已知錯譯：

> Mulai hari ini akan dilakukan pemeriksaan acak terhadap pelaksanaan penimbangan saat memasukkan dan mengeluarkan material. Mohon setiap shift menegaskan hal ini.

會被判定為：

- 未明確表達材料進入機台與從機台取出；
- 將「請各班要求」弱化成只強調此事。

已驗證譯文：

> Mulai hari ini, akan dilakukan pemeriksaan acak untuk memastikan pelaksanaan penimbangan pada saat material dimasukkan ke mesin maupun dikeluarkan dari mesin. Mohon setiap shift memastikan operator menjalankan prosedur ini dengan benar.

另保護以下反例，不得誤套用上下料秤重規則：

- 抽查員工出勤、各班要求準時打卡；
- 監視器發現設備漏油並要求維修；
- 現場觀察後要求清掃機台；
- 抽查秤重設備校正；
- 貨車／司機／月台的物流裝卸。

## 驗證結果

- 114 項可在目前環境執行的 pytest 測試通過，另有 23 項子測試通過。
- 正向改寫可找回「上下料＋秤重」案例；5 組不相關公告反例均不會命中該工廠知識卡或案例庫。
- 兩種非逐字但語意正確的印尼文譯法均通過案例驗證與工廠知識驗證。
- 所有 Python 檔案通過 `compileall`；`factory_knowledge.json` 通過載入、結構驗證與全部知識案例回歸。
- 目前執行環境缺少 `flask`、`line-bot-sdk` 與 `anthropic`，因此兩個必須完整匯入 `app.py` 的測試模組未在此環境執行。部署環境仍需執行完整服務啟動、LINE webhook 與實際供應商 API smoke test。

## 能保證與不能保證的範圍

本修正能保證：已知錯譯、已建模的領域語意與已加入的反例會由可重複測試保護；人工修正不再只是一句精確快取，也不會僅靠公告套語過度泛化。

任何生成式翻譯系統都不能誠實保證所有未見句子永遠零錯誤。新領域詞義或新型錯誤仍需新增工廠知識／人工修正與回歸案例，但新增後會進入同一套來源守門、提示、驗證及測試流程。
