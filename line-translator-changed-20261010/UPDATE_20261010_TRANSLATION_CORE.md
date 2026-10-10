# 2026-10-10 翻譯核心修正

本 ZIP 僅含與原始 `line-translator-bot-main.zip` 相比有變更的檔案，以及本次新增的回歸測試、測試資料與驗證報告。解壓縮至原專案根目錄，覆蓋同名檔案後重新啟動服務；核心模組與 JSON 詞庫必須一起更新。

## 截圖檢查與根因

- 第一張的 HOLD、三個多星期、吊至品保架等主要意思可接受。
- 第二張把「蔣總工」譯為 `Kepala Teknisi Jiang`，混淆總工程師與技師主管，也自行羅馬拼音化姓名。現已加入總工程師／副總工程師／技師主管的雙向語義，區分 `Insinyur Kepala`、`Wakil Insinyur Kepala` 與 `kepala teknisi`，並保留原文姓名。
- 「檢驗完成就可以上車過來包裝」表示完成檢驗後具備條件，不應自行加強為「立即裝車」。已加入逐句條件／語氣約束與本地檢查。
- 歷史範例檢索曾因「地上」「回報」「處長」等一般詞而帶入清潔、木箱、紀律公告等無關案例。已收窄情境觸發，並依最相關案例分數篩除弱相關項目。
- 身分證標籤 `NIK` 曾誤中 `menikmati` 等普通印尼文的內部字串。現在使用標籤邊界，實際識別碼仍受保護。

## 品質、速度與 API 消耗

1. 共用術語與語義約束貫穿文字、圖片 OCR、逐句輸出、歷史重用與學習驗證。標題修正以來源句子的角色為依據，不以整篇全域替換猜測人物關係。
2. 保留既有單次生成、原生結構化輸出與供應商提示快取。移除結構化請求中重複的原文，以及重複的職稱提示；不新增付費重試或額外模型評分。
3. 品質驗證先還原隱私遮罩，再與原始語義比對，避免正確姓名被誤判而無法快取。保留修正前的實際候選，提供學習所需的錯誤證據。
4. 學習政策現在辨識既有 `semantic:` 錯誤分類，僅接受已知機器錯誤碼。仍需通過「原候選有錯、修正後通過、可安全重用」的驗證；不把模型自行產生的內容提升為人工核准。
5. 這種學習改善的是可驗證的記憶與提示規則，沒有訓練或修改模型權重。仍有語義缺陷的結果不能進入已驗證記憶；品質檢查本身不增加付費生成。
6. 更新品質資產指紋，讓舊快取無法繞過新的職稱、姓名與語義檢查；同一版本下，已通過檢查的重複訊息可直接重用。

## 驗證方法與限制

驗證報告見 `TRANSLATION_VALIDATION_20261010.json`。新增測試涵蓋兩張截圖、職稱雙向區分、人物與職稱互換、識別碼遮罩、無關案例檢索、文字／OCR 入口及重複訊息的 API 次數。測試阻擋外部網路並使用固定模型回覆，沒有發送 LINE 訊息，也沒有呼叫付費模型。

第二張截圖的模型請求文字由 9,468 字元降為 8,816 字元，減少約 6.89%，同時新增語義保障。第一張維持 4,746 字元。短職稱訊息因原本缺少職稱與姓名保障，提示可能增加；不能把字元數直接換算為帳單 token、費用或真人模型延遲，也不能宣稱所有句子都更省。

執行：

```bash
python -m pip install -r requirements-test.txt
python -m pip install -r requirements.txt
python run_translation_offline_checks.py
python validate_factory_translation_assets.py
```

本次未變更模型選擇、API 金鑰或依賴版本。未進行正式環境部署；真人模型品質、帳單與網路延遲需於部署後觀察。

## 技術依據

維持穩定提示前綴與原生結構化輸出，並以應用端語義檢查補足 JSON 格式驗證的限制：

- [OpenAI Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)
- [OpenAI Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Anthropic Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
- [Gemini Structured output](https://ai.google.dev/gemini-api/docs/structured-output)
