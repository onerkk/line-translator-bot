# 截圖翻譯核對與學習更新

十張截圖有重疊，共核對十則不同訊息。本次納入使用者最終確認：資料進入801即算入庫、時間平均是分散系統入庫時間、TAG首印沒儲區而補印才出現、成品待裝入木箱，以及開三站是三個包裝站。
本包保留上一版的不攔截譯文、單次生成、訊息去重與自動學習機制。

| 訊息 | 判定 | 修正／保留重點 |
| --- | --- | --- |
| 洪副總突擊檢查 | 大意正確 | 副總維持 Wakil Direktur；洪是姓氏，原樣保留。巡視、晚班反應快、沒人被抓到違規均有表達。 |
| 要補印才有 | 大意正確，但省略對象不清楚 | 有明確前文時可指出缺少的儲區資訊；獨立出現時不擅自補上儲區。 |
| 存檔入庫都沒儲區 | 語意需修正 | 存檔／入庫時，TAG第一次列印沒有儲區。不得誤說成系統欄位空白、保管檔案或倉庫沒有空間。 |
| 中秋禮盒領取 | 大意正確 | 保留台灣／印尼同仁的不同領取地點；「應該會」仍是不確定，不改成確定發放。 |
| 大成今晚包的……EC51 | 重要語意修正 | 大成的成品本次一律實體吊到 EC51，不論 TAG 印哪個儲區；不是幫忙某機台或某人。 |
| 包裝存檔驗證錯誤、手動維護 | 語意需修正 | 維護的是已知客戶的儲區資料；用填寫／更新資料，不用機械維修的 pemeliharaan。 |
| 待料設備、線外人員、待裝木箱 | 語意需修正 | 待裝木箱是成品等待裝入木箱，不是木箱等待安裝；保留「有待料設備／沒有待料設備」兩個分支。 |
| 沒人取樣、牌子掛著 | 大意正確 | 保留沒人取樣與掛牌後不再負責的抱怨語氣，不額外斷言他人惡意。 |
| 請營業明天早點來取 | 大意正確 | 保留營業部、明天、早點來取。有明確引用前文才補出取樣對象。 |
| 19 噸／130 噸／3600／143 噸／開三站 | 數字與期間正確，用語改善 | 入幾噸是將該重量的成品登錄入庫；開三站是運轉圓型、異型、削皮三個包裝站；最後一句是分散系統登錄時間。 |

## 重要修正參考譯文

**存檔入庫都沒儲區**

Baik saat menyimpan data maupun mencatat masuk gudang, lokasi penyimpanan tidak tercantum pada TAG yang pertama kali dicetak.

**要補印才有**

有截圖中的儲區資訊前文：Informasi lokasi penyimpanan pada TAG baru muncul setelah TAG dicetak ulang.

單獨一句：Harus dicetak ulang dulu, baru muncul.

**@All 大成今晚包的不論哪一站都幫忙EC51**

@All Untuk material 大成 yang dikemas malam ini, setelah selesai, angkat dan pindahkan semua produk jadi ke area penyimpanan EC51, terlepas dari lokasi penyimpanan yang tercetak pada TAG.

使用者確認的背景：包裝完成，資料進入 801 即算入庫；成品再實際吊入指定儲區。
801 是資料所到站別；EC51 是實際儲放位置。原句沒有 801，因此此譯文不額外增加 801 過帳指令。
此為當次指示，不永久修改大成的預設儲區，也不將其他客戶的指令套成 EC51。

**包裝存檔如果跳驗證有誤，先檢查是不是儲區預設不見了，如果你明確知道這間客戶的儲區，先手動維護，我明天跟儲運反應一下。**

Jika muncul kesalahan validasi saat menyimpan data pengemasan, periksa dahulu apakah nilai default lokasi penyimpanannya hilang. Jika kalian tahu pasti lokasi penyimpanan untuk pelanggan ini, isi atau perbarui datanya secara manual terlebih dahulu. Besok saya akan melaporkan masalah ini kepada bagian pergudangan dan transportasi.

**@All 有設備待料我會拉人安排分擔線外工作，如果沒有再麻煩當天線外人員要注意一下待裝木箱。**

@All Jika ada mesin yang sedang menunggu material, saya akan menugaskan sebagian personel untuk membantu pekerjaan di luar lini produksi. Jika tidak ada, mohon personel yang bertugas di luar lini hari itu memperhatikan produk jadi yang masih menunggu dikemas ke dalam peti kayu.

**中班幫忙入19噸，今日計劃量破130噸。**

**本月入庫目標提高到3600，明天開始到月底前平均一天143噸，月底前人力會優先開三站，入庫時間注意平均一點。**

Shift tengah, tolong bantu catat pemasukan gudang sebanyak 19 ton dalam sistem agar rencana hari ini tembus 130 ton.

Target pencatatan pemasukan gudang bulan ini dinaikkan menjadi 3.600 ton. Mulai besok sampai akhir bulan, rata-rata 143 ton per hari. Sampai akhir bulan, tenaga kerja akan diprioritaskan untuk mengoperasikan tiga stasiun packing. Harap atur waktu pencatatan masuk gudang agar lebih merata.

## 已納入程式的學習

- 將系統登錄入庫、TAG首印／補印結果、手動更新儲區資料、成品裝木箱、三個包裝站及指定儲區的正確語意加入首次翻譯提示與可編輯知識資料。
- 三個包裝站名稱也寫入術語表與站別名稱表：圓型包裝站（Stasiun packing batang bulat）、異型包裝站（Stasiun packing barang bentuk khusus）、削皮包裝站（Stasiun packing peeling）。未指定站號時不自動補站號。
- 入庫噸數是登錄入庫的成品重量，不是資料筆數；系統入庫時間與實體吊放儲區分開理解。修正舊例句擅自加入490／801的情況。
- 首印缺儲區不等於系統欄位空白；原文明說欄位空白時才翻欄位空白。單獨「要補印才有」仍保留省略，只有實際前文可確認時才帶出TAG儲區資訊。
- EC51 的修正同時記住「實體吊放」「TAG 不優先」「只限當次」「客戶／時間／代碼由當前原文決定」。
- 只對來源可確認的錯誤片語做本機修正；遇到真正的紙本檔案、機台維護、木箱組裝、新建站別或混合語意，不套用相反的修正。
- 包裝儲區簡語只有完整解析客戶、日期與目的儲區後才能本機重建；包含其他例外、否定或後續指令的長句不採用這個簡式。
- 修復名稱與外側印尼文黏在一起的情況；姓名、客戶、混合字元識別碼內部維持原樣。
- 自動學習繼續保存經本機檢核證實改善的錯誤類別，用於後續相似新句子的第一份提示。未解決的問題只作診斷，不把錯譯升格成核准答案。
- 接話使用當群組的原始訊息與引用關係；不能把某次「補印才有」的省略對象變成所有群組的固定事實。
- 品質診斷不攔截非空譯文，不觸發第二次生成，不排入反覆翻譯佇列；已核對的完整例句可直接回覆，不需 API。

## 更新與驗證

ZIP 保持專案相對路徑，只收錄相對於上傳原始 ZIP 有變更／新增的檔案，包含前次單次生成修正與本次截圖修正。覆蓋至專案根目錄時請整包一起更新，避免版本配對不完整。

未改寫 `storage_data.json` 的客戶預設；未覆蓋正式環境的學習資料庫或金鑰。
測試採用假的 AI 與 LINE 傳輸，檢查本機修正、學習、首份提示、API 次數與重複事件去重。
本次未直接部署 Render，離線驗證不代表已驗證正式環境或所有未來譯文。

驗證結果：完整離線回歸 3,107 項測試通過，443 項子測試通過。涵蓋十張截圖、使用者確認的工廠語意、既有翻譯與反向翻譯、自動學習、原始前文範圍、單次生成、重複事件去重及既有功能。測試未呼叫付費翻譯 API。
