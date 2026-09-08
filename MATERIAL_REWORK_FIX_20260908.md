# 材料噴漆與重洗翻譯修正 — 2026-09-08

這次已重現截圖的程式缺陷。原版會放行 `Yang salah pengecatan semprot, ingat untuk dicuci ulang.`，卻因術語字串不符而拒絕自然的被動式。先前大量離線測試通過，並不能證明實際譯文正確；其中一個舊測試甚至要求保留這個生硬片語，本次已修正該驗收條件。

## 截圖應保留的意思

原文：`這把噴漆錯誤的記得重洗`

依專案既有棒材定義，「把」是材料捆數；這句必須保留「這一捆」「噴漆有誤」「記得再次清洗」。一個較清楚的表達是：

> Jangan lupa cuci ulang bundel yang salah dicat semprot ini.

這是本次用來檢查程式的參考譯文，並未經現場母語使用者確認。原文未交代錯誤顏色、藥劑或重新噴漆，不能自行補上。專案只定義材料清洗製程，沒有證據把這裡的「重洗」直接改成某種退漆藥劑處理。

## 找到並修正的原因

| 原因 | 處理 |
|---|---|
| 「這把」未形成可檢查的材料指涉 | 針對有明確噴漆錯誤及重工動作的材料口語，解析指示詞、量詞、錯誤及處理動作。區分捆、支、批、件與這／那。明示的刀、傘、噴漆槍等工具，以及難以確定的多重指涉，不套用材料捆數推論。 |
| 術語標成 soft，後續仍要求固定字串 | 工廠知識與語意契約共用同一個噴漆概念辨識，接受 `dicat semprot`、`disemprot cat`、`mengecat dengan semprotan` 等語法形式。停止把自然的 `cat semprot`、`penyemprotan cat` 再強制換成名詞。 |
| 只檢查詞是否出現，沒有確認動作歸屬 | 檢查同一材料的清洗／再噴漆、禁止／尚未／已完成是否一致；另一項材料的正確清洗文字不能補足本項缺漏。噴漆已完成不代表清洗已完成。 |
| 已知生硬片語直接送出 | 原文明確、只有一個可判定指涉時，局部修正錯誤名詞片語並補回其材料指涉；其餘文字保留，且須再次通過語意與交付檢查。不能靠局部修正掩蓋錯誤動作、狀態、數字、名稱或新增藥劑。 |
| 舊測試把錯誤片語當成正確期待 | 改為拒絕原錯誤、接受多種自然表達，加入相反動作、相反狀態、錯誤量詞、跨項目借詞、引用標題及一般工具等反例。 |

以上是來源關係解析與局部片語校正，沒有新增「看見整句原文就回傳固定整句譯文」的查表捷徑。此解析有明確範圍；不適用或語意不明時仍交由既有翻譯流程處理。AI 額外覆核仍受原有總請求預算限制。

## 實際對照與驗證

以相同測試程式分別載入前次修正版與本次修正版，只在 AI 傳輸邊界提供固定候選譯文，執行原有協調、翻譯及交付流程：

| 檢查 | 修正前 | 修正後 |
|---|---|---|
| 截圖候選的原始品質檢查 | 放行 | 偵測材料／片語關係缺失 |
| 自然譯文的工廠術語檢查 | 拒絕 `dicat semprot` | 接受 |
| 相同自然候選經實際協調層 | 2 次模擬生成，仍被拒收後降級 | 1 次模擬生成即通過 |
| 截圖候選經完整 `translate()` | 原錯誤文字送出 | 局部修正後送出，仍為 1 次模擬生成 |

本次新增 **54 項回歸案例**。完整回歸的測試總數、逐筆對照、檔案雜湊與執行環境記錄於 `tests/data/material_rework_verification_20260908.json`。舊有工廠資產檢查亦通過，包括 30 個核准目標與 29 個禁止譯法探針。

**這些是程式行為測試，不是線上模型的品質 A/B。** 本次環境沒有可用的模型 API 憑證，未呼叫真實模型或發送 LINE 訊息。因此不能把測試數量當成所有翻譯都正確，也不能宣稱任意句子已經根治或線上速度／帳單改善了特定百分比。

## 更新與重現

ZIP 是相對最初上傳專案的累積更新，包含本次修正及前次效能修正所需檔案，不含整個未修改專案。可套用至最初上傳版本或前次修正版：將 ZIP 內檔案依相同相對路徑覆蓋，再重新部署／重啟。`app.py` 與品質檢查模組有相容版本檢查，請整包一起更新。原資料庫與設定不需重建。

在已安裝原專案 `requirements.txt` 與 `requirements-test.txt` 的開發環境執行：

```bash
python run_translation_offline_checks.py
python run_translation_offline_checks.py test_material_rework_translation.py
python benchmark_material_rework.py --output rework-after.json
python benchmark_material_rework.py --repo /path/to/previous/version --output rework-before.json
```

工具會使用暫存狀態並封鎖外網；完整回歸只允許既有連線重用測試需要的本機 loopback。`--repo` 對照基準是先前的 `line-translator-speed-cost-fix-20260908.zip` 套用後、尚未加入本次修正的專案。

## 查證依據

查證日期：2026-09-08。

- [OpenAI Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices)：生成結果具變動性，傳統程式測試不足以代表 AI 品質。此次將真實錯誤、自然替代表達與相反事實納入驗證，並明確區分固定候選測試與真實模型評測。
- [Anthropic Prompting best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)：以清楚指令與具代表性的例子表達要求。本次讓提示保留原文的材料、動作及狀態，而非要求貼入固定術語名詞。
- [tesa 印尼文噴漆作業文章](https://www.tesa.com/id-id/tentang-tesa/pers-wawasan/cerita/masking-tape-untuk-cat-semprot-dan-pengaplikasian-lainnya.html)：廠商實際使用 `dicat semprot`，支持此被動形式不應被術語檢查排除。該文章不能證明本工廠「重洗」的特定作業定義。
