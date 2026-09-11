# 翻譯連線重用更新

這次處理翻譯等待時間。後台頁面、模型選擇、提示詞、術語／學習系統與品質檢查均沿用現有版本。

## 已確認並修正的問題

LINE SDK 與 Upstash HTTP Session 原本分別保存在 `threading.local()`。下一則訊息換到不同執行緒，或背景 webhook 執行緒處理完畢後結束，便無法沿用前一條連線。原本的「連線重用」只對同一條仍存活的執行緒有效。

改成程序內的連線借還池。每次請求獨占一個 client／Session，完整讀完並關閉回應後才歸還，下一個工作執行緒便能重用。同步請求各自取得不同實例，借用時不等待其他請求的網路操作。

- 每個池最多保留 8 個閒置 client；超過 120 秒閒置的 client 在下次借用時清理。併發多出的 client 用完即關閉。
- 更換金鑰、端點、SDK factory 或程序時，舊連線不再出借；已在進行的請求完成後才回收。
- 不新增預熱請求、重試、AI 呼叫或背景雲端寫入。
- 撤回／修改檢查、冪等送出與持久化 outbox 不變。操作選單資料仍須先完成保存，才送出 LINE 翻譯。

## 實際驗證結果

完整離線回歸：**2,879 項測試、443 項子測試通過**。外部網路被測試工具封鎖，僅允許測試所需的本機回送連線；沒有付費 AI 呼叫或正式 LINE 訊息。

另以真實 `handle_message`、SQLite、Redis Lua、LINE SDK 與本機 HTTP 服務，讓 6 則訊息各自使用新的短命工作執行緒：

| 同一組 6 則測試訊息 | 修改前 | 修改後 |
|---|---:|---:|
| 翻譯路徑建立的 TCP 連線 | 12 | 2 |
| 模擬 AI 生成 | 6 | 6 |
| 翻譯路徑 HTTP 請求 | 18 | 18 |
| 譯文完整／按鈕保存後才送出 | 通過 | 通過 |

舊版確實未通過「跨工作執行緒只需 2 條連線」的測試，新版通過。同一套測試刻意讓每條新 TCP 連線等待 150 ms；排除第一則後，中位耗時為修改前 **365.879 ms**、修改後 **63.798 ms**。這段差異用於確認重複建連線的成本已被移除。

**以上是含人工連線延遲的本機 HTTP 測試，不是正式 LINE／AI 延遲，也沒有量測真實 TLS 握手。** 第一次連線仍需建立；伺服器關閉閒置連線後亦需重新連線。已使用同一條存活執行緒的熱連線情境，改善可能很小。這份測試不能據此把使用者回報的 5 秒換算成某個正式秒數。

正式環境應比較相同類型訊息的既有 `[DeliveryPerf]` 記錄：`ai_ms`、`ai_attempts`、`storage`、`queue_ms`、`ingress_ms`、`sent`。AI、LINE 網路和服務冷啟動仍會影響總時間；程式沒有為了追求秒數而縮短逾時或放寬品質檢查。

## 更新方式

將 ZIP 裡的檔案放到專案根目錄，一起提交／部署。執行時必須同時包含：

1. `reusable_transport_pool.py`（新增）
2. `line_api_transport.py`
3. `line_factory_store.py`

其餘檔案是回歸測試與驗證紀錄。無須更改環境變數、模型或資料庫結構。本次修改前的兩個執行檔已核對，與 GitHub `f7ceefc979c9a8d8796b7ed1edbd8045ec2512c5` 版本完全一致。本工作環境未部署至正式服務。

可使用專案既有的隔離測試入口驗證，所需測試依賴沿用原專案：

```bash
python run_translation_offline_checks.py
```

若需復原，還原更新前的 `line_api_transport.py` 與 `line_factory_store.py` 即可；沒有資料遷移。

實作依據：[Requests Session 與 Keep-Alive 文件](https://requests.readthedocs.io/en/latest/user/advanced/#keep-alive)、[urllib3 連線池文件](https://urllib3.readthedocs.io/en/stable/user-guide.html#connection-pools)，並檢查專案實際安裝的 LINE SDK client／回應關閉流程。
