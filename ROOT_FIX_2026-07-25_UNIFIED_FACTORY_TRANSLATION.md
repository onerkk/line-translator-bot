# 統一工廠翻譯驗收架構（2026-07-25）

## 問題根因

先前系統雖然累積詞庫、工廠知識、翻譯記憶與人工修正，但不同入口可在不同階段提前回傳。一般 TM、向量 TM、模型輸出、表情裝飾、OCR 或通用 NMT 只要其中一層繞過語義檢查，就可能把舊錯譯重新送出或寫回學習資料。

已確認的典型錯誤包括：

- `抓帳` 被翻成檢查或彙整資料，而不是會計結帳 `tutup buku`。
- 客戶名 `大成` 被字面翻成 `Besar`。
- 工業出貨木箱被翻成一般小木盒 `kotak kayu`，而不是 `peti kayu`。
- `自然拉動` 在來源未提人工時，被擅自補成 `manual`。
- 上下料、PMI、包裝、工單、ERP、設備異常與組織層級的主體、範圍、責任或時間被弱化。
- 模型輸出看似通順，但遺漏數字、設備代碼、否定、條件、@mention 或原文動作。

## 現行架構

### 1. 中印互譯統一進入工廠路由

`factory_translation_policy.py` 的正式預設為：

```bash
FACTORY_TRANSLATION_MODE=always
FACTORY_TRANSLATION_REVIEW_MODE=always
FACTORY_TRANSLATION_REQUIRE_REVIEW_SUCCESS=1
FACTORY_TRANSLATION_FAIL_CLOSED=1
FACTORY_ALLOW_GENERIC_NMT_FALLBACK=0
```

文字、圖片 OCR 與翻譯變體共用相同政策。系統不再依單一關鍵字決定是否採用工廠規則，因此客戶名、製程簡稱、設備、會計或現場指令不會因句型改寫而掉回一般生活翻譯。

### 2. 精確已驗證案例與安全正規化

`translation_casebook.py` 與 `factory_translation_guard.py` 只忽略標點、空白與呈現差異。來源語義不同的近似句不得冒充精確命中。

精確命中的人工確認案例仍須通過目前版本的工廠驗收，才可直接回傳。這條路徑不需要再次呼叫模型；任何衝突的已驗證目標會使啟動自檢失敗。

### 3. 新生成譯文必須從原文獨立複核

`translation_quality_gate.py` 不只潤飾第一版譯文。複核者收到原文、第一版譯文、術語限制與已知風險，必須從原文重新建構意思，檢查：

- 主體、動作、受詞、時間與條件
- 否定、強制程度、原因與後果
- 數字、單位、設備代碼、客戶名與 @mention
- 工廠術語、作業範圍、責任角色與流程順序
- 是否加入原文沒有的人工、設備、會計或流程推論

正式預設要求複核成功。若複核不可用、複核結果被本地規則拒絕，且沒有可由原文欄位確定重建的安全結果，系統不交付第一版譯文。

### 4. 單一確定性驗收邊界

`factory_translation_guard.py` 編譯並統一使用：

- `factory_knowledge.json` 的 21 組核准範例與已知壞譯文
- `factory_translation_regression.json` 的 16 組正式歷史案例
- 精確案例索引與資產指紋
- 客戶名、設備代碼、數字、單位、否定及禁用片語規則
- 每一案例的必要語義群組

同一驗收邊界已接到：

1. 精確案例回傳
2. 一般快取與翻譯記憶讀取
3. 模型候選結果
4. 最終交付
5. 表情／語氣裝飾後結果
6. OCR 與文字翻譯結果
7. 快取、TM 與主動學習寫入

因此，「模型通過但裝飾後壞掉」、「舊 TM 先命中」或「OCR 走另一條規則」不再是合法捷徑。

### 5. 詞彙與整句語義分層

`glossary_data.json`／`glossary_policy.py` 管理可局部判定的正式詞與禁用詞；`factory_knowledge.json` 管理必須讀完整句子才能判斷的流程語義。

| 中文語義 | 正式印尼文 | 禁用／限制 |
|---|---|---|
| 抓帳、會計結帳 | `tutup buku` | 禁止 `cek data`、`rekap data` |
| 木箱 | `peti kayu` | 工業出貨木箱禁止 `kotak kayu` |
| 裝箱 | `memasukkan material ke dalam peti kayu`／依句意 `pengemasan` | 必須保留裝入木箱或安排包裝的實際動作 |
| 陸續到料 | `material akan tiba secara bertahap` | 保留未來與分批抵達 |
| 電子系統 | `sistem elektronik` | 不得泛化成不明設備 |
| 自然／被動拉動 | `tarikan alami`／`tarikan pasif` | 原文未寫人工時禁止補 `manual` |

### 6. 通用 NMT 不再靜默降級

中印工廠翻譯預設禁止 Google 或一般 NMT 作為未驗證備援。供應商故障時，系統寧可明確失敗，也不回傳看似自然但工廠語義未經驗收的內容。只有管理者主動設定 `FACTORY_ALLOW_GENERIC_NMT_FALLBACK=1` 才可改變此行為。

## 正式回歸範圍

`factory_translation_regression.json` 現有 16 組案例，涵蓋：

- 大成結帳、160 噸分批到料、優先包裝
- 本月木箱交期與非本月材料暫不裝箱
- 電子系統拆除後改為自然／被動拉動
- 上下料秤重、CCTV／現場抽查與各班責任
- PMI 每把／每捆材料鋼種驗證
- 大棒研磨、工單優先級與生產排程
- 工單公告、ERP 490→801 紀錄時點與庫存責任
- 包裝站別、洗滌支援、S/H 異型材
- 機台失火、設備受損與操作前／後端問題
- 工廠組織層級、紀律與責任通知

每個案例包含核准目標、必要語義群組及禁用錯譯探針。核准目標必須被接受；禁用探針必須被拒絕。

## 發布驗證

不需要 Flask、LINE SDK 或任何翻譯 API 即可先執行：

```bash
python validate_factory_translation_assets.py --json
```

驗證器會檢查 JSON、Python 編譯、政策正式預設、資產衝突、精確案例可尋址性、16 組核准目標與禁用錯譯探針。GitHub Actions 已加入同一發布閘門及依賴無關的回歸測試。

本次封裝前驗證結果：

- 離線資產驗證：16/16 核准目標通過，16/16 禁用探針被拒絕，零錯誤、零警告。
- 完整測試（使用 Flask／LINE／OpenAI 測試替身，不呼叫外部 API）：197 項測試與 133 個子測試通過。
- 原始環境未安裝 Flask 時，純 `unittest discover` 會有 2 個匯入錯誤；這是依賴缺失，不是工廠驗收邏輯失敗。使用測試替身後 123 項 `unittest` 全部通過。

## 限制

此架構能阻止已知錯譯回歸、未經複核的模型結果、舊快取污染與跨入口繞過，但不能以數學方式保證所有未來未知句子永遠零錯誤。正式策略因此採「來源複核成功才交付、驗收成功才學習、通用備援預設關閉」。新增人工修正時，應同步加入核准例、禁用例與回歸測試，而不是只改提示詞。
