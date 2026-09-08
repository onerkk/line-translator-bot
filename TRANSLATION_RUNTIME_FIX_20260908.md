# 本次翻譯速度與品質修正

以你這次上傳的 `line-translator-bot-main.zip` 為基準，SHA-256：
`b0c720a6d0f72381eadba1cf99bfa465cd0d99b2738366f11365fe8233d8b6b5`。

**本包是可部署的程式修正，不是正式環境測速報告。沒有取得你主機的當時日誌或付費 AI 金鑰，因此不能斷言截圖那一分鐘究竟耗在哪裡，也不能宣稱已消除所有延遲或保證所有翻譯正確。**

## 截圖核對

| 原文 | 核對結果 |
|---|---|
| 昨天拋光手寫報表放在哪裡 | `Laporan tulis tangan proses polishing kemarin diletakkan di mana?` 大意正確，保留昨天、拋光、手寫及詢問位置。 |
| 先不要生產，等研發單位量過圓度在生產 | 截圖的 `Jangan produksi dulu. Tunggu bagian R&D mengukur kebulatan terlebih dahulu, baru produksi.` 大意正確；把「在生產」按上下文理解成「再生產」。三個工件代碼與 `@All` 均應原樣保留。 |

`kebulatan` 是可用的圓度用語；不是表面粗糙度，也不等於只量直徑。原文要求量完再生產，沒有說檢驗合格或取得放行許可，程式不能自行加上這些要求。印尼工程論文也在 CMM 加工件量測中使用 kebulatan，並與直徑等項目分開描述。[BRIN 工程研究](https://ejournal.brin.go.id/MIPI/article/download/1553/933)

16:24／16:25 是分鐘精度的畫面，無法單憑畫面計算精確延遲；你回報等待超過一分鐘仍需要追查。

## 修正內容

1. **接收與翻譯分離。** 有可持續保留工作資料的執行環境時，驗證 LINE 原始簽章、提交 SQLite 工作後立即回應 webhook，由最多 8 個工作執行緒處理。不同 webhook 可同時處理；同一包事件保留 SDK 原始順序，以維持指令、編輯及前文的順序。工作按需要啟動，不為每則訊息固定建立 8 條執行緒。
2. **Render 暫存磁碟有例外處理。** 偵測 `RENDER=true` 且 outbox 位於 `/tmp` 等暫存位置、或無法確認持久掛載時，維持處理後才回應成功，避免新增「已向 LINE 確認接收、尚未處理的工作卻隨容器重建消失」的風險。Docker 的 HTTP threads 從 4 改為 8，仍只有 1 個 process，以增加網路等待期間的處理容量。這個模式下不能套用非同步 ACK 的毫秒數據。
3. **文字與媒體補送隔離。** 文字／譯文版本補送有 2 個工作名額，圖片、音訊、影片與文件補送有獨立的 1 個名額。慢媒體不再佔用唯一的文字補送執行緒。每個空閒名額只領取一筆工作，保留租約、發送檢查點、穩定的 LINE 重試鍵及完成回執。正常模型失敗仍走既有有上限的接力策略，沒有同時向三家模型競速付費。
4. **補上來源語義約束。** 對明示「等指定單位量完指定項目再生產」的句型，檢查量測項目、單位與生產先後。對單一報表的放置位置問句，檢查日期、工序、手寫媒介及問句。提示和檢查共用同一組語義資料，不靠整句硬寫答案，也不逐句增加付費模型覆核。模糊、多個無法對應的報表不強套單一報表規則。
5. **自然說法仍可通過。** 例如「生產只能在量完之後開始」可表達先不要生產，不強求譯文一定有字面上的 `jangan`；報表的 `polishing` 也不強求前面一定有 `proses`。其他獨立禁令繼續檢查。
6. **可追查完整等待時間。** 啟動日誌新增 `[TranslationRuntime]` 的版本與接收模式；`[DeliveryPerf]` 增加匿名 trace、事件到達前時間、排隊時間、AI 嘗試次數／累計耗時、處理至送出時間與 process 運行時間。LINE 使用者畫面不增加工程狀態訊息。

LINE 官方建議非同步處理 webhook，避免後續請求等待；回覆 token 應在收到 webhook 後一分鐘內使用，超過不保證可用，所以仍保留既有 push 補送。[LINE 接收事件](https://developers.line.biz/en/docs/messaging-api/receiving-messages/)、[LINE Messaging API](https://developers.line.biz/en/reference/messaging-api/)

本次採用獨立工作並行、減少重複工作及本機語義檢查，符合 OpenAI 延遲最佳化的方向。翻譯完整內容仍需保留，沒有靠截斷譯文減少輸出。[OpenAI 延遲最佳化](https://developers.openai.com/api/docs/guides/latency-optimization)

## 驗證與費用界線

- 完整離線回歸：**1,781 項測試、438 項子測試通過**。工廠素材檢查 `ok=true`。
- 新增測試包含真正的 Flask／LINE SDK 簽章路由、SQLite、非同步完成、重送去重、重啟後讀取已保存工作、佇列競爭、文字與媒體隔離、Render 暫存磁碟模式及語義反例。既有簽章整合測試改為等待「實際處理完成」，沒有把 HTTP 200 當成譯文已送出。
- 兩則截圖以固定 AI 回應走完整翻譯入口，均為 **1 次模擬生成**，保留人名與工件代碼。這不是兩次真實 AI 翻譯評分。
- 基準程式會接受的四個錯譯反例——圓度換成粗糙度、先後倒置、昨天換成今天、位置問句變成自行回答——修正後會被最後發送檢查攔下。正確截圖及自然同義說法可通過。
- 好譯文仍可一次生成，快取與既有節費修正持續使用。新的語義提示會增加少量輸入內容；沒有實際 token 帳單，**不宣稱這次已證明每筆費用降低某個百分比**。偵測到真實錯譯時仍可能使用原有的修正生成預算。

### 同一程式的受控排隊對照

AI、LINE 外網均封鎖；人工設定 webhook 工作 120 ms、每筆媒體工作 100 ms、文字工作 5 ms。

| 本機受控情境 | 修改前 | 修改後 | 範圍 |
|---|---:|---:|---|
| 回應 webhook 的中位數，5 次 | 121.352 ms | 3.268 ms | 適用非同步模式；只是接收回應，並非譯文送達。 |
| 兩筆媒體之後的文字補送完成，中位數 5 次 | 211.509 ms | 8.289 ms | 驗證文字不必等待媒體工作。 |
| 暫存磁碟模式下 16 筆工作全部完成，中位數 3 次 | 490.427 ms | 271.167 ms | 真正 Flask callback，依 Docker 4／8 threads 配置建立本機 thread executor；不是正式 Gunicorn 或 Render 壓測。 |

完整樣本、語義反例與限制見 `tests/data/webhook_latency_quality_20260908.json`。這些毫秒數不能推算線上翻譯會從一分鐘變成幾秒，也不是實際準確率。採用真實例句、自然同義說法和反例來檢查行為，參照 [OpenAI 評估最佳實務](https://developers.openai.com/api/docs/guides/evaluation-best-practices)。

## 安裝與正式環境判讀

這個 ZIP 只放相對於本次上傳的修改／新增檔案，路徑相對於專案根目錄。將它們一併覆蓋到原專案，保留其他原有檔案；尤其要包含 `durable_workers.py`、`webhook_runtime.py`、`factory_sequence_semantics.py`。既有 CI 通過並部署成功後才會生效。

Docker 啟動配置已更新。若 Render 使用手填的 Python Start Command，請對照本次 README 的命令：

```sh
gunicorn --bind 0.0.0.0:8080 --workers 1 --threads 8 --timeout 180 app:app
```

`LINE_WEBHOOK_WORKERS` 可調整非同步工作容量，預設 8、上限 12；`LINE_WEBHOOK_ASYNC=0` 可停用非同步入口。Render 只有確認 outbox 位於持久掛載時才會啟用非同步入口。沒有新模型訂閱或必要的付費 API。

在 Render Logs 查看 `[TranslationRuntime] build=2026-09-08.1-durable-webhook-lanes` 及 `async_ingress`；再用 `[DeliveryPerf]` 判讀：

| 欄位 | 意義 |
|---|---|
| `ingress_ms` | LINE 事件時間到應用程式收到 webhook；可能包含平台、網路、服務喚醒等時間，不能單獨當成冷啟動證據。 |
| `queue_ms` | 接收後到這筆事件開始處理；包含同一 webhook 裡先前事件的等待。 |
| `ai_ms` / `ai_attempts` | AI 客戶端呼叫的累計時間與嘗試數；多目標並行時累計時間可大於實際經過時間。 |
| `sent` / `event_sent_ms` | 開始處理到 LINE 接受發送／原始事件時間到 LINE 接受發送；不是手機顯示或同事已讀時間。 |
| `uptime_ms` | process 載入本次 runtime 模組後的運行時間，可與啟動日誌交叉判斷。 |

Render 官方明載：免費 web service 閒置 15 分鐘會休眠，下次啟動約一分鐘；免費服務檔案系統也會因重啟／重新部署等情況被清除。本次 SQLite 工作保護可以跨 process 重啟，但不能把暫存磁碟變成持久磁碟。舊版 outbox 及圖片背景工作也有這個儲存限制，本次沒有把定時快照冒充為即時可靠的工作佇列。[Render 免費方案](https://render.com/docs/free)、[Render 內建環境變數](https://render.com/docs/environment-variables)

若那筆延遲主要發生在服務啟動之前，程式內加速無法消除平台喚醒時間。持續不休眠的運算環境才可處理那部分延遲，但可能增加主機費用；本次沒有擅自更改你的方案。HTTP 工作採單 process、多 threads，依據 [Gunicorn 官方設計說明](https://gunicorn.org/design/)，實際容量仍需從你的主機日誌觀察。

### 重現

```sh
python run_translation_offline_checks.py
python validate_factory_translation_assets.py --json
python benchmark_webhook_lanes.py --repo /path/to/original --output before.json
python benchmark_webhook_lanes.py --repo /path/to/patched --output after.json
```
