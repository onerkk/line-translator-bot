# 翻譯品質與等待時間修正

基準版本：`b10eb100ba8620c0a01e071766b5c37c88941c80`。開始修改前逐一比對 GitHub 的 325 個檔案；交付前再次確認 GitHub HEAD 相同。本包只包含相對此版本的變更，保留上次點名及速度優化。

## 截圖中的翻譯判定

| 原文重點 | 判定 | 應保留的意思 |
| --- | --- | --- |
| `tolong bukakan komputer tengah` | 「中間計算機」不符合此處臺灣用語 | 請幫忙開一下中間那台電腦。不能加上重新啟動、解鎖或登入成功。 |
| `小心別移到帳，非本月這兩捆也不要移出` | `area akun` 把帳務操作寫成實體地點 | 在這段帳務語境中，操作對象是材料的紀錄；保留兩捆、非本月及兩個禁止動作。若原文明確說搬出倉庫，就仍是搬運材料。 |
| `木箱前面有 * 代表有裝箱，共13把10.7噸可以移出` | 「木箱的實體前面已放進木箱」不清楚 | 星號與裝箱狀態的對應；保留 13 把、10.7 噸、可以移出，不擅自補搬到哪裡。 |
| `這把噴漆錯誤的記得重洗` | 截圖主要意思正確，印尼文可以更自然 | 這把材料噴漆有誤，記得重新清洗；不能只剩重新噴漆，也不推定未寫明的清洗方法。 |
| `一股說今天可能會有人進來巡廠，我不知道消息可信度，自己留意一點` | 部門與不確定性有保留；自動加的 ✅ 容易誤導 | 消息來自一股；只是可能來巡廠，說話者不知道消息是否可靠。不能寫成已確認。 |

帳務句可表達為：

> Sebagian material belum dimasukkan ke dalam peti kayu. Hati-hati, jangan memindahkan catatannya ke pencatatan stok. Catatan untuk dua bundel yang bukan untuk bulan ini juga jangan dipindahkan keluar.

星號句可表達為：

> Tanda * di depan keterangan peti kayu menunjukkan bahwa material sudah dikemas dalam peti kayu. Total 13 bundel dengan berat 10.7 ton boleh dipindahkan keluar.

「移到帳」有明確帳務線索；同句「非本月這兩捆」依前後文解讀為該分類的紀錄。程式不把所有「移出」一概改成移動資料。原文沒有交代的實際位置與清洗流程，不由程式補寫。

## 重現與修改

1. **原本的檢查放行了錯誤操作對象。** 在基準版本注入截圖的 `area akun` 候選，品質檢查沒有列出問題，模擬 LINE 處理流程會送出。新增從當次原文建立的帳務關係，核對操作對象、方向、禁止與非本月分類，並按編號隔離不同指令。
2. **特定詞義可以先在本機修正。** `komputer` 明確指電腦時統一為「電腦」；來源同時出現計算器／計算機程式名稱時不做這項替換。帳務與標記的修正只處理能和來源對上的局部詞組；不修改數量、否定、月份或動作狀態。
3. **符號對應的是狀態。** `X 前面有符號，代表已／未裝箱` 會進入標記語義核對。真正的木箱位置描述及「代表要裝箱」不被套成已完成。這不是儲存整句原文與固定答案的捷徑。
4. **首次提示包含對應原則。** 固定提示補上紀錄移動／實體搬運與欄位標記的區分，臺灣中文明確區分電腦與計算機；當次辨識出的關係放進原有語義提示。
5. **避免為單純術語再呼叫 AI。** 新增電腦術語時，原本的案例複核政策會因帶有整句錯例而多呼叫一次 AI。改為一般正確術語範例，仍保留專用錯詞檢查與完整品質閘門。真實人工修正、事故、高風險或驗證失敗需要的複核政策不變。
6. **中英混合點名完整保護。** 原先中文名字後只接小寫 Latin 名字，`@法比恩 Fabian` 會只保護中文部分；現在大小寫皆支援，仍在 `tolong/jangan/please` 等句子開頭停止，不吞掉待翻譯文字。
7. **裝飾不能改變可信度。** 已重現巡廠句被本機表情模組追加 ✅；現在不確定敘述與一般檢查敘述不會自行加成功勾號。正式工廠模式涵蓋巡廠、裝箱與帳務；來源自己寫的表情仍保留。
8. **舊快取不直接繼承新檢查結論。** 更新品質閘門、語義與提示版本；原有快取範圍包含這些識別。沒有刪除正式資料庫或更動群組設定。

完整測試也找出兩個新檢查的誤攔截，已在交付前修正：接受原本有效的 `non-bulan berjalan`，以及把「入帳時間／過帳日期」當作欄位名稱，不錯當新增入帳命令。

## 官方技術查核與採用方式

| 官方資料 | 與本次修改的關係 |
| --- | --- |
| [OpenAI 延遲優化](https://developers.openai.com/api/docs/guides/latency-optimization) | 減少連續 API 往返、固定提示前綴及縮小不必要內容。本次把可確定的用語修正放在本機，避免為它重新產生整句。 |
| [OpenAI Prompt Caching](https://developers.openai.com/api/docs/guides/prompt-caching)／[Anthropic Prompt Caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) | 快取重用相同提示前綴，不能假定每次一定命中。保留專案原有固定／動態提示分離與供應商快取處理，沒有宣稱本次取得線上命中率或節費比例。 |
| [Google 翻譯術語表](https://docs.cloud.google.com/translate/docs/advanced/glossary) | 專業名詞須有一致對應。本次同時加入語境條件；單靠詞表不能判斷帳務動作、否定或狀態。未新接入 Google 付費服務。 |
| [OpenAI 評估實務](https://developers.openai.com/api/docs/guides/evaluation-best-practices) | 將實際問題、正常變體與反例納入測試。沒有用「API 回傳成功」代替語義驗證。 |
| [LINE 接收 Webhook](https://developers.line.biz/en/docs/messaging-api/receiving-messages/)／[回覆訊息 API](https://developers.line.biz/en/reference/messaging-api/#send-reply-message) | 官方建議非同步處理 Webhook；reply token 的使用期限也限制了長鏈式等待。本次保留既有傳送、重試與總呼叫期限，不新增逐句反譯或額外群組推送。 |
| [Render 免費服務](https://render.com/docs/free) | 免費服務閒置 15 分鐘會休眠，重新喚醒約需一分鐘。若線上仍用免費方案，這段啟動延遲不會因翻譯程式加速而消失。付費常駐執行個體需另外由管理員決定；本次沒有變更方案。 |

## 驗證結果與範圍

- 完整發布測試：**1,549 項通過，438 項子測試通過，0 失敗**。
- 本次新增 **60 項回歸測試**，包括五張截圖的相關行為，以及大小寫點名、實體搬運、帳號／銀行帳戶、計算器程式、編號隔離、數量變更、否定反轉、已／未裝箱及不確定語氣。
- 電腦、帳務及星號三個回報候選走完整 `translate()` 管線，只替代最外層供應商傳輸；各使用 **1 次 AI 生成呼叫**，沒有 NMT 備援。LINE handler 另驗證點名保留與成功送出後不留重試項目。
- 工廠素材發布檢查：30 則標準目標通過，29 則禁止候選被拒絕，沒有錯誤或警告。
- 本次沒有調高預設模型、增加生成預算或移除品質檢查。

這些是離線程式與模擬傳輸的結果：沒有呼叫付費 AI、沒有對正式 LINE 群組發送訊息，也沒有量測本次正式站的秒數、帳單或模型首答正確率。截圖只顯示到分鐘，不能據此精確計算一次翻譯耗時。新詞、新歧義及模型自然語言輸出仍需持續用實際案例驗收，不能以測試通過宣稱所有未見過的句子永遠零錯誤。

## 本包檔案與上傳

ZIP 只有以下 **13 個檔案**，全部放在專案根目錄：

1. `app.py`
2. `translation_quality_gate.py`
3. `factory_instruction_semantics.py`
4. `factory_record_semantics.py`（新增，必須一起上傳）
5. `factory_terminology.py`
6. `factory_knowledge.json`
7. `prompt_optimizer.py`
8. `translation_mentions.py`
9. `translation_extras.py`
10. `expressive_engine.py`
11. `test_record_and_computer_translation.py`（新增）
12. `README.md`
13. `TRANSLATION_QUALITY_SPEED_FIX_20260907.md`

1. 解壓 ZIP；在 GitHub `line-translator-bot` 專案根目錄選 **Add file → Upload files**。
2. 一次上傳上述 13 個檔案並提交，保留專案其他檔案；不要把 ZIP 本身上傳當作更新，也不要只上傳 `app.py`。
3. 等待 **Factory translation release gate** 成功，Render 新部署顯示 **Live**。
4. 在測試群組重新發送本文件中的電腦、帳務、星號、重洗及巡廠句。檢查名字、星號、13、10.7、否定和可能性是否保留。
5. 測試另一句「這兩捆不要移出倉庫」，確認仍描述實體材料；測試「可以移出」及「不可以移出」，確認方向和允許狀態相反。

更新只影響之後的新翻譯，不會改寫 LINE 已送出的歷史訊息。若更新後有新錯例或仍明顯慢，保留原文、譯文及同一時間的 `[Perf]`、`[DeliveryPerf]`、供應商／重試紀錄，才能區分模型耗時、休眠、儲存、傳送及品質複核；不要附上金鑰。
