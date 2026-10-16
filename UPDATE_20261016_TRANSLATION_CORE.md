# 2026-10-16 翻譯核心與修正記憶重整

本 ZIP 只含相對於本次上傳專案的變動／新增檔案。將內容依原路徑覆蓋到專案根目錄，或使用既有手機 ZIP 更新頁上傳，再重新部署／啟動。不要放進 `000/` 或另一層日期資料夾。未修改線上服務。

## 截圖判讀與直接原因

第二版基本可理解；第一版的 `bagian ini saja` 容易把提醒縮成「只注意這個部分」。兩版都應清楚連結：巨昌的備註是針對爐號標籤，不要貼在端面；以後包到該客戶的料時要留意。

依第二則已更正的中文，較清楚的參考譯文是：

> 巨昌 memberi catatan agar label Huaxin tidak ditempel pada permukaan ujung. Yang dimaksud adalah label nomor heat. Saat nanti mengemas material untuk 巨昌, mohon perhatikan ketentuan ini.

實際擷取舊程式送往模型的內容，確認同時存在：

- 硬性詞庫要求 `標籤 => label produk`，使泛稱被固定成產品標籤。
- 另一份歷史詞表又提供 `label heat number`、`nomor furnace`，與目前知識庫的 `nomor heat` 並存。
- 原文意思在舊提示詞優先順序中排在機械規則、硬性詞庫之後。

不能從截圖斷言模型內部究竟採用了哪條指示，但這些矛盾可從真正送出的請求重現。

## 本次改動

1. **直接組裝正式翻譯請求。** 移除 `app.py` 中超過 500 行舊提示詞組裝內容，改用一份共通原則、當句術語、當句語意資料和相關修正記憶。舊提示詞解析函式只保留給相容呼叫端，正式翻譯入口不再使用它。
2. **原文明確意思優先。** AI 先讀完整訊息，連結後句對前句的說明，保留否定、完成狀態、條件、受詞與「只／除了／全部」的範圍。可依充分語境理解錯字，不能猜改姓名、代碼、數字，或把提醒擴寫成額外限制。
3. **修正詞庫來源。** 「標籤」改為中性的 `label`；產品、爐號、警告、客戶等修飾由原句決定。共享詞庫、舊資料正規化及內嵌備援同步，文字與 OCR 後續翻譯一致。不新增巨昌專用替換句。
4. **完整的修正記憶。** 原文與核准譯文作為完整一對檢索；不再分別截短，避免漏掉後段的否定或限定。重複範例只送一次，超出預算的完整範例當次略過，原始記憶仍保留。相似句只作參考，不直接套用歷史姓名、數值或範圍。
5. **依內容配置推理。** 簡單、無上下文負擔的訊息走快速設定；否定、條件、多句關係、站別或其他已偵測風險保留低量推理。保留每次翻譯單次生成、現有連線重用及已驗證重複訊息快取，不新增付費 AI 複審。
6. **部署與快取一致。** 更新核心版本檢查及快取指紋；保留雙向站別識別、數值檢查、來源逐句對齊及既有人工修正審核。

「持續學習」是使用經核准的修正記憶，不是自動訓練模型權重。測試包含一個沒有新增專用錯誤規則的修正：待審時不使用，核准後可檢索到相似句，其他群組看不到，撤回後移除。錯譯回報仍需要提供可信的正確譯文；不能只靠 AI 自己聲稱正確。

## 品質、速度與費用的實測界線

同一份既有 15 例資料，擷取實際送出訊息的總字數，由 **63,370 降為 53,909，減少 14.93%**。這是字數，不是 token、費用或網路延遲。截圖原句的請求由 **5,344 降為 4,396 字，減少 17.74%**。簡單訊息的測試請求由 `low` 改為 `none`；複雜句仍維持 `low`。

這裡沒有可用的翻譯 API 金鑰，未呼叫付費模型、未發 LINE 訊息，也未測量線上生成速度或真實帳單。不能據此承諾全球最低價、最快或所有未來句子都正確。模型供應商與主備順序保留現有設定。

本次沒有擴充以句子為單位的強制改寫。既有本地檢查仍不是完整語意證明；現有系統對非空譯文採「品質警示控制快取／學習」的交付政策也維持原樣，不能將送達當成譯文已完全正確。

完整測試結果、資產驗證與檔案 SHA-256 見 `TRANSLATION_VALIDATION_20261016.json`。重跑：

```bash
python run_translation_offline_checks.py
python validate_factory_translation_assets.py --json
python benchmark_translation_cp.py --runs 3 --output cp-result.json
```

離線測試使用固定模型回覆，驗證的是流程、術語與回歸行為，不是外部模型實際生成品質。

## 官方技術查核

- [OpenAI：Latency optimization](https://developers.openai.com/api/docs/guides/latency-optimization) — 減少請求、輸入與生成 token 是延遲優化方向；本次未以字數減幅代替實際延遲。
- [OpenAI：Cost optimization](https://developers.openai.com/api/docs/guides/cost-optimization) — 控制請求量、token 與適用模型。
- [Claude 官方價格](https://platform.claude.com/docs/en/about-claude/pricing)、[Gemini 官方價格](https://ai.google.dev/gemini-api/docs/pricing)、[OpenAI 官方價格](https://developers.openai.com/api/docs/pricing) — 已於本次查核；不同模型與快取計費不同，不能單憑單價斷言最適合本廠翻譯。
