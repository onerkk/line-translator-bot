# PMI 鋼種檢驗語義根治（2026-07-20）

## 根因

本廠現場用語「打鋼種／打材質」不是打印、標示或貼鋼種標籤，而是使用 PMI 分光儀檢驗並確認材料鋼種。一般翻譯模型只看「打＋鋼種」的字面時，容易在以下兩種錯誤之間漂移：

1. 誤譯成 `memberi tanda / menandai grade baja`，把檢驗動作改成標示動作。
2. 只譯成泛稱 `menguji jenis baja`，漏掉 PMI 方法、逐捆範圍、強制性與包裝前檢驗順序。

這不是單一句型問題，而是「廠內動作術語＋量詞範圍＋品質流程」的語義缺口。若只在翻譯後替換某一句，舊 TM、NMT、其他模型、OCR 路徑與改寫路徑仍可能再次產生同類錯誤。

## 根治架構

1. **廠內知識卡**：新增 `pmi_grade_verification_bundle_packaging`，以「打鋼種、驗鋼種、打材質、做 PMI」等概念觸發，不綁定單一完整句子。
2. **全管線語義契約**：知識卡命中後禁止 TM／向量 TM／NMT 直接繞過，LLM 必須接收相同語義，最終譯文也必須通過同一份驗證。
3. **術語索引**：在共用 Trie 詞庫加入「打鋼種」的 soft semantic hint，以及「每一把 → setiap bundel」的 hard classifier mapping；文字與 OCR 共用。
4. **品質閘門**：明確拒絕打印、標示、貼標籤等錯誤動作，並驗證 PMI、grade baja、每一捆、wajib/harus、班別、站別、未檢驗即包裝及嫌麻煩原因均未遺失。
5. **啟動自我檢查**：`app.py` 啟動時必須確認新版知識庫 build ID 與 PMI 語義卡可被實際檢索，避免只上傳部分檔案造成靜默退化。

## 正確語義

- 打鋼種／打材質：`memeriksa grade baja dengan PMI`／`pemeriksaan grade baja dengan PMI`
- 每一把：`setiap bundel`
- 沒檢驗 PMI 就包了：`dikemas tanpa pemeriksaan PMI`
- 出貨這把：`bundel yang sudah dikirim`
- A 班：`shift A`
- 異型站：沿用本廠正式站名 `Stasiun packing barang bentuk khusus`

## 防誤判邊界

「打印鋼種標籤」「打上鋼種標示」「噴印鋼種」屬於標示／列印語境，不應觸發 PMI 檢驗知識卡。測試已覆蓋此反例，避免把所有含「打」與「鋼種」的句子粗暴套成 PMI。
