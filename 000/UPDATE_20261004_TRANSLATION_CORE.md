# 2026-10-04 翻譯核心流程更新

本更新以本次上傳的 line-translator-bot-main.zip 為基準，只包含修改或新增檔案。

## 截圖核對

- 「一股」在既有現場詞庫中是「冷抽一股」，固定為 Bagian Cold Drawing 1；本次保留此對照。
- 「副總」在既有現場詞庫中固定為 Wakil Direktur，不能單憑一般職稱翻譯改掉現場對照。問題是原文「副總**級**的主管」的職級限定被省略；建議使用 pimpinan setingkat Wakil Direktur。
- 「異型棒價錢好、維持穩定入庫就不會被那邊牽扯」的截圖譯文大意可理解，不需要捏造新的管理事件或處分。不要把關注、環境整潔、產出與入庫等不同議題混在一起。
- 「如果開兩站分流時盡量把異型分流包裝」應明確表達包裝站的人力／工作分配，保留條件、兩站和盡量。截圖的 membagi aliran / melalui aliran terpisah 太抽象。

第二則公告的建議譯法（人工核對範例，不是正式 AI 服務的實測回應）：

> @All Saat ini ada seorang pimpinan setingkat Wakil Direktur yang secara khusus memantau hambatan produksi batang profil khusus. Hasil produksi dan status masuk gudang batang profil khusus akan dilaporkan setiap hari.
>
> Selama beberapa bulan ini, selain pesanan mendesak, prioritaskan juga pengemasan batang profil khusus. Jika dua stasiun packing dioperasikan, usahakan agar batang profil khusus dikemas secara terpisah.

## 核心流程

1. 在生成前建立原文擁有的句子單位及語意契約，讀取完整原文、既有原始對話和核准術語。
2. 對帶職級限定、站別分流安排及較複雜的工廠公告，在原本一次 AI 呼叫中要求逐句 JSON 對應。簡短一般訊息沿用既有路徑。
3. 本機核對句子 ID、順序、完整度、重複、未知句子、空句、職級與正確角色的連結，以及分流包裝的站數、條件與努力程度。直接保留自然措辭，不用整句模板取代模型的翻譯。
4. 生成結果、舊快取、翻譯記憶與學習准入共用檢查。版本指紋會使舊版已驗證快取失效；不必手動刪除核准術語或歷史資料。
5. 有結構問題的回應不得寫入快取，也不得進入背景學習。無可讀文字的 JSON 不會送成 JSON 或只剩 @All 的訊息。
6. 環境整潔知識卡必須先找到環境／地面整潔對象，不能因為「盯」或「主管」就套用到上、下料過磅等其他議題。

保留原有單次翻譯呼叫和可用性政策，不新增第二次 AI 審稿、反向翻譯或品質重試。結構格式與語意檢查是必要防線，不能保證任意未知口語都百分之百正確；未通過檢查的可讀候選仍依既有可用性政策處理，但不作核准資料學習。

## 部署

1. 若下載的是 `.down`，將副檔名改為 `.zip` 後解壓縮。
2. 以包內檔案覆蓋專案根目錄的同名檔案，並加入新增的 translation_alignment.py 與測試檔案。不要只換 app.py。
3. 重新安裝 requirements.txt 並重新部署。Anthropic SDK 最低版本改為 1.0，確保支援原生 output_config.format。既有 AI 供應商、金鑰、管理員及其他設定沿用。
4. 可在部署前執行 `python run_translation_offline_checks.py` 與 `python validate_factory_translation_assets.py`。

包內檔案：

- app.py
- factory_knowledge.json
- factory_terminology.py
- factory_translation_guard.py
- requirements.txt
- translation_quality_gate.py
- translation_alignment.py（新增）
- test_translation_alignment.py（新增）
- UPDATE_20261004_TRANSLATION_CORE.md（本說明）

## 驗證範圍

完整離線回歸：3,687 項測試與 450 個子測試通過；工廠資料與版本檢查、Python 編譯檢查通過。包含 38 項本次新增測試，檢查原生供應商參數、實際程式的翻譯路徑、逐句完整度及錯誤快取防線。

外部網路在回歸測試期間封鎖，沒有傳送 LINE 訊息或呼叫付費 AI。尚未部署到正式 Render 服務，也未以正式 API 金鑰評量模型的實際生成品質。

## 本次查證的官方技術文件

查證日期：2026-10-04。

- OpenAI Structured Outputs：https://developers.openai.com/api/docs/guides/structured-outputs
- Anthropic Structured Outputs：https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- Gemini Structured Outputs：https://ai.google.dev/gemini-api/docs/structured-output

官方結構化輸出約束格式；本更新另在應用程式中驗證原文對應與欄位語意。沒有以模型自報信心值當作正確性證明。
