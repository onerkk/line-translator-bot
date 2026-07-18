# 工廠術語翻譯根治：共用字首樹引擎（2026-07-18）

版本：`v3.40.0-factory-terminology-engine-2026-07-18`

## 結論

文字訊息與圖片 OCR 現在共用同一套工廠翻譯邏輯：

1. OCR 只辨識原文，不直接翻譯。
2. OCR 原文與一般文字都進入標準 `translate()` 管線。
3. 標準管線用同一個工廠術語字首樹、組織單位解析、工廠知識卡、翻譯記憶、術語合規檢查與品質閘門。
4. 詞庫只擷取「本句實際出現」的詞，不把整本詞庫塞入模型提示詞。
5. 詞庫熱更新後會同步清除字首樹與反向索引快取，避免舊詞繼續生效。

## 原始問題

原專案雖已有 glossary、翻譯記憶與工廠情境規則，但存在三個結構性風險：

- 文字、圖片直譯、OCR、術語提示、反向詞庫與品質檢查的入口不完全一致。
- 部分路徑每次翻譯都排序或掃描整本詞庫；詞條變多後會浪費時間與 token。
- 「一課」「一股股長」這類台灣工廠組織簡稱，可能被一般模型誤判為一般部門、人名或班組，產生 `Departemen 1`、`Yigu`、`Regu 1`、`Subseksi 1` 等錯譯。

## 新架構

```text
LINE 文字 ───────────────────────────────┐
                                         ├─> 標準 translate()
LINE 圖片 -> Vision OCR 原文 -> 安全正規化 ┘
                                               │
                 ┌─────────────────────────────┼─────────────────────────────┐
                 │                             │                             │
          工廠術語 Trie                 工廠知識 JSON                 TM / 品質閘門
       最長詞、別名、組織單位       情境、禁用詞、必要概念         合規、修正、阻擋舊錯譯
```

### 1. `factory_terminology.py`

新增不可變字首樹（Trie）索引：

- 建索引一次，後續重複使用。
- 最長詞優先，避免「第一股股長」同時錯套「第一股」。
- 支援大量中文別名與安全的印尼文反向別名。
- 只回傳原句命中的詞條，降低提示詞長度。
- OCR 提示只包含中文原詞，不包含翻譯，避免 Vision 模型自行補字。
- OCR 安全正規化可把 `一 股 股 長` 合併為 `一股股長`，但不猜測看不清楚的字。

### 2. 組織單位語法

新增確定性解析：

| 中文 | 印尼文 | 規則 |
|---|---|---|
| 一課／第一課 | `Seksi 1` | 不泛化成 `Departemen 1` |
| 一股／第一股／冷抽一股 | `Bagian Cold Drawing 1` | 依 ERP 股別表；不可譯成 `Regu 1`、`Subseksi 1` 或 `Yigu` |
| 一股股長／第一股股長／冷抽一股股長 | `kepala bagian Cold Drawing 1` | 冷抽一股主管，不是班長或姓名 |
| 課長 | `kepala seksi` | 工廠課級主管 |
| 股長 | `kepala bagian` | 股級生產部門主管 |
| 班長 | `kepala regu` | 班／工作小組主管；不可與股長混用 |
| 處長 | `kepala divisi` | 處級主管 |

數字型單位由語法動態處理，不必為每一個數字逐句加補丁。

### 3. `glossary_data.json` 詞條格式

大量詞庫可使用以下欄位：

```json
{
  "一股股長": {
    "canonical_idn": "kepala bagian Cold Drawing 1",
    "translation_mode": "hard",
    "reverse_safe": true,
    "aliases_zh": ["第一股股長"],
    "aliases_id": ["kepala bagian cold drawing satu"],
    "category": "organization",
    "priority": 140,
    "ocr_hint": true,
    "note_zh": "第一股的股長；一股不是姓名。",
    "note_id": "Kepala Bagian Cold Drawing 1; bukan kepala regu dan bukan nama orang."
  }
}
```

欄位用途：

- `canonical_idn`：標準印尼文。
- `translation_mode: hard`：輸出必須保留該術語語義／標準詞形。
- `translation_mode: soft`：提供語義與語氣，不強迫生硬逐字輸出。
- `aliases_zh`：中文簡稱、舊稱、OCR 常見寫法。
- `aliases_id`：印尼文簡稱或工人慣用寫法。
- `reverse_safe`：只有明確無歧義時才允許印尼文反查中文。
- `priority`：衝突時優先順序。
- `ocr_hint`：提高該中文詞形進入 OCR 提示的優先度。
- `category`：設備、站別、組織、ERP、製程等分類。

## 此次新增的情境修正

原文：

> 一課最近被釘很緊，上週還被處長抓到一堆人在控制室休息吹冷氣。樓上是一股股長，基本紀律注意一下，他蠻公司派的，什麼都會跟處長講。

建議譯文：

> Akhir-akhir ini Seksi 1 sedang diawasi dengan ketat. Minggu lalu, kepala divisi memergoki banyak orang sedang beristirahat sambil menikmati AC di ruang kontrol.
>
> Di lantai atas ada kepala bagian Cold Drawing 1. Tolong perhatikan disiplin dasar. Dia cukup berpihak kepada perusahaan dan akan menyampaikan semuanya kepada kepala divisi.

品質規則會拒絕：

- `Departemen 1`
- `Yigu`
- `Kepala Bagian Yigu`
- 原文只有「蠻公司派」卻自行加重成 `sangat berpihak kepada perusahaan`

## 效能策略

- 字首樹建好後，不再每次排序整本詞庫。
- 查找成本主要取決於輸入文字長度與實際前綴深度，而不是詞庫總筆數。
- 模型提示最多只帶入本句命中的詞；OCR 提示限制高優先詞數量，避免 token 膨脹。
- 20,001 筆同首字模擬詞庫，本機測得：建索引約 `0.32 秒`；快取後 10,000 次查找平均約 `0.013 毫秒／次`。此數字僅為該測試環境基準，不代表正式伺服器保證值。

## 驗證結果

- 新增工廠術語整合測試：`8/8` 通過。
- Python 編譯：`app.py`、`factory_terminology.py`、`glossary_enforcement.py` 通過。
- 正式模組啟動與部署自檢通過：258 筆詞條、261 個索引詞形、1,483 個 Trie 節點。
- 完整 Pytest：新版 `106 passed, 7 failed, 12 subtests passed`。
- 原始壓縮檔在相同環境：`98 passed, 7 failed, 12 subtests passed`。
- 7 個失敗均為原專案既有問題（品質閘門舊測試與雙語按鈕／emoji 預期不一致），本次修改沒有新增既有測試失敗。

## 維護原則

- 新設備、站別、單位簡稱：優先新增至 `glossary_data.json`，不要在 `app.py` 寫句子替換。
- 跨句才判斷得出的工廠情境：新增至 `factory_knowledge.json`。
- `hard` 只用於真正固定的標準名稱；動詞、口語與程度詞應使用 `soft`。
- 印尼文反向翻譯只有無歧義術語才設定 `reverse_safe: true`。
- 圖片翻譯不可恢復獨立 OCR+直譯提示，否則會再次繞過詞庫與品質閘門。
