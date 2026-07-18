# 2026-07-19 工廠組織單位根治

## 根因

舊版新增的通用規則把「數字＋股」一律推導成 `Regu N`。但本專案原有 ERP 股別表已明確定義：

- 一股／冷抽一股：`Bagian Cold Drawing 1`
- 二股／冷抽二股：`Bagian Cold Drawing 2`
- 削皮股：`Bagian Peeling`
- 研磨股：`Bagian Grinding`

因此 `Regu 1` 與 `Subseksi 1` 都不是本廠「一股」的正確譯法。`Regu` 應保留給「班／工作小組」。

## 修正原則

1. ERP／工廠詞庫中的具名股別優先於通用組織推導。
2. 不再用正規表示式把任意「數字＋股」推導成 `Regu`、`Subseksi` 或 `Bagian N`。
3. 未知股別不猜測，必須新增明確詞庫或 ERP 對照後才鎖定。
4. 「股長」與「班長」分層：
   - 股長：`kepala bagian`
   - 班長：`kepala regu`
5. 文字與 OCR 共用相同詞庫、提示詞、品質驗證與反向安全索引。

## 本次固定譯法

| 中文 | 印尼文 |
|---|---|
| 一課／第一課 | `Seksi 1` |
| 一股／第一股／冷抽一股 | `Bagian Cold Drawing 1` |
| 一股股長／第一股股長／冷抽一股股長 | `kepala bagian Cold Drawing 1` |
| 二股／第二股／冷抽二股 | `Bagian Cold Drawing 2` |
| 二股股長／第二股股長／冷抽二股股長 | `kepala bagian Cold Drawing 2` |
| 削皮股 | `Bagian Peeling` |
| 研磨股 | `Bagian Grinding` |
| 股長 | `kepala bagian` |
| 班長 | `kepala regu` |
| 課長 | `kepala seksi` |
| 處長 | `kepala divisi` |

## 目標案例

中文：

> 一課最近被釘很緊。樓上是一股股長。

印尼文：

> Akhir-akhir ini Seksi 1 diawasi dengan ketat. Di lantai atas ada kepala bagian Cold Drawing 1.

以下譯法會被品質閘門拒絕：

- `Yigu`
- `Regu 1`
- `kepala regu 1`
- `Subseksi 1`
- `kepala subseksi 1`
