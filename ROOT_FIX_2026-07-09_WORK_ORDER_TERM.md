# ROOT FIX 2026-07-09 — 工單誤譯為 Tempat Buku bahan

## 問題

中文公告句：

> 公司宣導過的規定不要自己省略，現在很多噴漆也不噴，工單也不寫……麻煩一切按照工單資訊執行……

曾被翻成：

> ... tidak menulis Tempat Buku bahan ... mengikuti informasi pesanan ... perawatan ukuran pendek ...

主要錯誤：

- `工單` 被 glossary 誤導為 `Tempat Buku bahan`。
- `工單資訊` 在公告語境應是 `informasi pada work order`，不是一般訂單資訊。
- `短尺維護` 應是 `penanganan material pendek`，不是 `perawatan ukuran pendek`。

## 根因

`glossary_data.json` 內存在錯誤對照：

```json
"工單": { "idn": "Tempat Buku bahan" }
```

這會污染 prompt glossary、glossary enforcement、TM seed 與模型輸出。

## 修正內容

1. 將 `glossary_data.json` 與 `app.py` embedded fallback glossary 的 `工單` 改成：

```json
"工單": { "idn": "work order" }
```

2. 新增公告常用術語：

- `工單資訊` → `informasi pada work order`
- `噴漆` → `spray cat`
- `來料尺寸` → `ukuran material masuk`
- `表面品質` → `kualitas permukaan`
- `短尺維護` → `penanganan material pendek`
- `重量確認` → `konfirmasi berat`

3. 在 `app.py` 新增 `factory_work_order_document` semantic contract：

- 自動偵測自然語句中的 `工單` 公告語境。
- 禁止 `Tempat Buku bahan`、`Buku bahan`、`lembar kerja`、`book order`、`order book`。
- 對 NMT / TM bypass 關閉，避免舊翻譯記憶覆蓋正確術語。
- 對 NMT、LLM、TM/cache 路徑都追加 deterministic post-fix。

4. 新增測試檔：

```text
test_work_order_term_root_fix.py
```

## 驗證

```bash
python -m py_compile app.py glossary_enforcement.py translation_quality_gate.py
python -m compileall -q .
python -m unittest discover -v
```

結果：

```text
Ran 28 tests in 1.692s
OK
```

## 修正後期待翻譯方向

```text
@All Peraturan yang sudah disosialisasikan oleh perusahaan jangan diabaikan atau dihilangkan sendiri. Sekarang banyak yang tidak melakukan spray cat dan work order juga tidak ditulis. Kondisi seperti ini semakin sering terjadi.

Mohon semuanya bekerja sesuai informasi pada work order. Pekerjaan dasar yang harus dilakukan di setiap stasiun meliputi: ukuran material masuk, kualitas permukaan, penanganan material pendek, dan konfirmasi berat. Semua ini merupakan bagian dari alur kerja.
```
