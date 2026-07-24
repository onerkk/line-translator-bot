# 翻譯問題根治：統一修正案例庫與來源核對

Build：2026-07-24

## 問題根因

舊系統把相同的人工知識分散在四條互不一致的路徑：`/wrong` 自訂範例、Active Learning 修正資料庫、Translation Memory／Vector TM，以及 `factory_knowledge.json`。其中自訂範例只以字面重疊選取，注入數量又過少；人工修正主要只能保護完全相同的原句。句子換個說法後，模型可能再次犯同一類語意錯誤。

## 本次架構修正

1. 新增 `translation_casebook.py`，把內建範例、自訂範例、工廠知識案例、Active Learning 人工修正編譯成同一個「對比案例庫」。
2. 人工修正同時保存錯譯、正譯與修正理由；提示詞會明確標示已知錯譯不可仿照，而非只顯示正確答案。
3. 使用中文標點容錯、加權字詞／片語檢索及 IDF，讓「上下料」與「上、下料」等改寫仍能找到同類案例；同時加入負例，避免把機台上下料誤判為貨車裝卸。
4. 同一來源若存在多版修正，採「最新人工修正 > 人工案例 > 工廠知識 > 內建範例」的單一真相優先序，舊版不再與新版同時進入提示詞。
5. `/wrong` 現在會一次寫入 Active Learning、精確 TM、Vector TM 與對比案例；後台修正後也會立即使案例快取失效。
6. 強匹配人工修正或高風險公告時，最多追加一次獨立、以原文為準的來源核對；若有其他已設定供應商，優先使用不同供應商，避免同一模型自我批准。
7. 新增案例語意驗證：若譯文仍接近已知錯譯，或缺少高信心修正案例中的關鍵語意，禁止寫入快取與 TM。原文完全相同時才允許直接使用已驗證正譯；改寫句不得盲目套句。
8. 將所有工廠知識範例納入一致性回歸測試，驗證每個範例都能找回自己的知識卡且通過該卡規則。

## 本次案例的保護結果

已知錯譯：

> Mulai hari ini akan dilakukan pemeriksaan acak terhadap pelaksanaan penimbangan saat memasukkan dan mengeluarkan material. Mohon setiap shift menegaskan hal ini.

會被判定為：

- 未明確表達材料「進入機台／從機台取出」；
- 將「請各班要求」弱化為「請各班強調此事」。

已驗證譯文：

> Mulai hari ini, akan dilakukan pemeriksaan acak untuk memastikan pelaksanaan penimbangan pada saat material dimasukkan ke mesin maupun dikeluarkan dari mesin. Mohon setiap shift memastikan operator menjalankan prosedur ini dengan benar.

## 驗證結果

- 111 項既有純模組測試通過，另有 12 項子測試通過。
- 6 項新案例庫／獨立核對／完整工廠案例回歸測試通過。
- `app.py`、`translation_casebook.py`、`translation_quality_gate.py` 均通過 Python 編譯檢查。
- 完整 Flask／LINE 啟動測試未在本環境執行，因執行環境未安裝 `flask`、`linebot` 與 `openai` 套件；部署時的版本契約與啟動行為自檢會阻止漏檔或舊檔混用。

## 參考的官方工程原則

- OpenAI Evaluation best practices：以實際生產案例建立可重複的 eval，避免只靠主觀判斷，並納入人工回饋。
- OpenAI Structured Outputs：結構化輸出可保證格式，但語意仍須由領域驗證器檢查。
- Anthropic Prompting best practices：使用多個貼近實際情境的範例，並以 XML 分隔指令、情境與案例。
- Anthropic consistency guidance：以 retrieval 固定上下文，並用具體案例提高一致性。
