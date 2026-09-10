# LINE 翻譯：共通語意修正與速度驗證

這個更新包可直接覆蓋本次上傳的專案，並包含前一版「全員確認完成卡」的累積變更。程式已完成離線驗證；尚未部署至線上 LINE 服務。

## 截圖核對

| 截圖內容 | 判定 | 修正方向 |
| --- | --- | --- |
| 系統異常、存檔前檢查、0 支標籤 | 大意可理解，但 `1 buah data` 憑空增加資料筆數；`sistem baru terbaru` 也不自然 | 「另一個問題」是問題的區別，不能變成儲存 1 筆資料的要求。0 支仍需完整保留。 |
| 包裝重量嚴禁手打、已提報七筆 | 主要意思可理解，資料取值主詞與句法不夠精準 | 明確表達重量資料必須直接由磅秤取得、欄位尚未鎖定、手動輸入會造成異常重量、已提報 7 筆。 |
| 急單看一下，幫忙處理 | `segera periksa order` 只說立即檢查，未完整保留急單性質；✅ 非原文內容 | 保留 `work order mendesak` 與請求協助的狀態。此項在附件程式已有共通修正，本次保留並回歸驗證。 |

截圖不能證明線上目前使用哪個程式版本，因此沒有把截圖與附件版本視為同一份部署狀態。

## 可重現的根因

舊 `factory_quantity_semantics.py` 先抓「數字＋量詞」，再把「個」固定要求成 `buah`。第一張原文中的「另一個問題」因此被解析成必須出現 `1 buah` 的實物數量。原文又有 `0支`，使整段數量檢查被啟動。

舊檢查器只在譯文全段尋找數字與量詞，沒有確認它屬於哪個名詞。因此：

| 同一原文的候選譯文 | 修正前品質檢查 | 修正後品質檢查 |
| --- | --- | --- |
| `masalah lain`，存檔處不增加數量 | 退回：缺少 `1 buah` | 通過 |
| `masalah lain`，但存檔處新增 `1 buah data` | 通過 | 拒絕：資料數量無來源 |

現在先連結量詞與所指名詞，再區分明確數量、序數、每一個、另一個、同一個與不定指稱。抽象名詞可以使用自然的印尼文；實際物料量詞、0 支、半包、每人分配、加總與更正仍受檢查。新增的「筆」會依資料或提報語境連結到紀錄／案件，避免把筆誤解為書寫工具。

資料輸入流程則使用共用的操作關係：手動輸入的允許／禁止／必須、資料來源、強制磅秤取值、欄位未鎖定，以及手動輸入造成異常重量的條件與結果。只在其他句子出現「禁止」或「磅秤」不能代替對應操作。未知或未完整解析的長文仍走模型翻譯。

相同來源框架供首輪提示詞、模型回覆驗收與送出前檢查使用；快取版本同步更新，舊版本快取不能繞過新的檢查。這些修正不依靠截圖整句查表或生成後替換整句。

## 正確譯文參考

第一張：

> Memang benar, sistem baru ini memiliki banyak masalah. Setelah data diperoleh dari timbangan, belakangan ini kalian perlu saling mengingatkan untuk memeriksa apakah berat, ID, dan jumlah batang tidak normal sebelum menyimpan data.
>
> Masalah konversi jumlah batang adalah masalah lain. Namun, kalian harus dapat secara aktif menemukan label yang jelas tidak normal, seperti jumlah 0 batang ini. Informasi pada TAG memang harus dicocokkan saat memasangnya. Kondisi seperti ini sangat berbahaya.

第二張：

> @All Data berat hasil pengemasan dilarang keras diinput secara manual. Meskipun kolom saat ini belum dikunci, data berat wajib diperoleh langsung dari timbangan. Jika diinput secara manual, sistem akan mencatat berat yang tidak normal. Saat ini sudah dilaporkan tujuh kasus.

第三張：

> Tolong periksa work order mendesak ini dan bantu menanganinya.

## 速度與品質證據

重播使用同一批已核對的固定候選譯文，跑實際 `translate_openai` 和供應商驗收流程；只有外部模型回覆被模擬，LINE 發送與網路連線皆停用。原始結果在 `validation/replay-before.json` 與 `validation/replay-after.json`。

| 場景 | 修正前模型生成次數 | 修正後模型生成次數 | 修正後結果 |
| --- | ---: | ---: | --- |
| 第一張：另一個問題＋0 支 | 2 | 1 | 正確譯文通過；增加資料數量的譯文被拒絕 |
| 第二張：磅秤取值＋七筆回報 | 1 | 1 | 新增操作語意防護，正確譯文仍一次通過 |

第一個案例的提示詞總字元從兩次合計 12,160 降為一次 6,410，約減少 47%；這是字元數，並非帳單 token 數。第二個案例因新增必要的操作條件，單次提示詞增長，生成次數維持一次。局部檢查增加的成本也保留在原始重播報告中。

速度改善主要來自消除誤判造成的額外生成。數量檢查重用同一請求內、相同完整輸入的結果；固定語彙模式預先編譯，額外數量說明只在需要時加入提示詞。這與減少串行模型呼叫的 [OpenAI 延遲最佳化原則](https://developers.openai.com/api/docs/guides/latency-optimization) 一致。既有穩定提示詞前綴快取沿用現有實作；[Anthropic 快取文件](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) 說明了前綴一致的重要性。

這些結果不是線上秒數、真人盲測評分或所有訊息的效能保證。正式速度仍受模型、網路、訊息長度與供應商負載影響。

## 驗證與套用

完整 Python 回歸結果與測試總數見 `validation/full-regression.txt`。測試涵蓋本次截圖、不同抽象名詞、明確數量、序數、數字與名詞錯置、量詞、輸入權限反轉、磅秤來源錯置、原因遺漏、錯誤快取、模型重試，以及既有急單／全員確認功能。

1. 將 ZIP 內的檔案依相對路徑覆蓋專案，所有執行程式檔一併更新。
2. 重啟服務，使新版本檢查與快取指紋生效；本次無新增套件或資料庫遷移。
3. 在測試群組驗證三則原文，確認 0 支、7 筆、禁止手打、急單與無多餘 ✅；確認最後一位名單人員回報後才出現全員完成卡。

可重跑的指令：

```bash
python -m pytest -q test_contextual_factory_translation.py test_factory_quantity_semantics_root_fix.py test_factory_record_contract.py test_urgent_order_translation.py
python benchmark_contextual_translation.py --output replay.json
```

ZIP 僅包含累積變更檔與驗證資料，未包含執行期設定、金鑰、資料庫或翻譯日誌。
