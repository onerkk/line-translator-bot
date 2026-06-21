# 異型包裝站站名根治（2026-06-21）

## 根因

目前系統的 ERP 站別表只有異型矯直機、異型拋光機與產品詞「異型棒」，缺少使用者先前指定的正式站名「異型包裝站」。現場句「異型那站」因此沒有進入站別解析，模型只能用一般字典義猜成 `stasiun barang khusus`。另外，外部 `glossary_data.json` 會取代內嵌詞庫，當中也沒有木箱／裝箱／前站等既有現場詞，造成靜態 prompt 與真正執行的強制詞庫不一致。

## 修正

1. 新增正式站名：`異型包裝站 → Stasiun packing barang bentuk khusus`。
2. 新增集中式 `FACTORY_STATION_ALIAS_RULES`，只在明確站別語法下把「異型那站／異型那邊／異型支援」正規化，不會誤傷「異型棒／異型矯直機／異型拋光機」。
3. 正規化提升到翻譯邊界層，TM、向量 TM、NMT、快取與 LLM 全部使用 canonical source，舊錯譯不再命中。
4. 新增語義契約，舊 TM 或模型輸出若缺少正式印尼站名就會被攔截。
5. 同步補齊 `glossary_data.json`：前站、料源不足、木箱、裝箱、支援裝箱、木箱包。

## 正確譯法

原文：`前站料源不足有木箱就包沒關係，異型那站可以支援裝箱。這個月木箱包預估有700箱，目前應該還差很多`

建議譯文：`Kalau pasokan material dari stasiun sebelumnya tidak cukup, kalau ada kotak kayu langsung packing saja, tidak apa-apa. Stasiun packing barang bentuk khusus bisa bantu proses memasukkan barang ke dalam kotak kayu. Bulan ini estimasi packing kotak kayu sekitar 700 kotak, sekarang sepertinya masih kurang banyak.`
