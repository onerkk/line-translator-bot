# 翻譯中斷恢復與健康檢查修正

這次以「幫忙今日點檢」追查從原文、AI 接力、背景恢復到 LINE 送出的流程。一般情況下，這句的三種合理印尼文譯法都能通過既有品管。實際找到並修正的是下列三項程式問題。

## 1. 有備援 AI，部分逾時卻直接中止

舊版靠錯誤訊息是否包含 `timeout`、`connection` 等文字決定接力。空白的 `ReadTimeout`、連線重設，以及本程式自己產生的 `TimeoutError("Provider transport deadline exhausted")` 都可能未被識別。

改以例外類型、明確的網路錯誤碼辨識傳輸故障，再沿用原有供應商接力政策。一般資料格式、磁碟或程式錯誤不會被當成可重試的網路故障。單一供應商仍只有一次有預算限制的重試。

舊版的 8 項傳輸恢復案例全部失敗；修正後通過。正常翻譯維持一次生成；整個要求仍共用原有期限、最多 3 次 API 嘗試與 2 次完成生成的預設上限。保留目前主力及既有接力順序，不因本次短暫斷線永久切換主力。

## 2. 翻譯恢復後，原本可用的 LINE 回覆方式被丟掉

舊版保存原文等待恢復，卻沒有保存該事件尚未使用的回覆機會。背景工作只走 Push；因此即使翻譯已恢復、Reply 還能使用，只要 Push 不可用，使用者仍收不到譯文。這個情境已用完整處理流程重現。

現在將事件的原始接收時間與 Reply token 一起保存在文字翻譯工作中，恢復時先使用尚未嘗試過、仍在保守有效時間內的 Reply。工作啟動、重試或重啟不會把接收時間改成現在。

- 已嘗試送出、已有部分批次成功、後續語言批次、舊工作缺少時間資料，均沿用既有 Push 恢復流程。
- 超過本地 55 秒保守窗口，或事件發生超過 20 分鐘，改走既有 Push；實際 token 是否接受仍由 LINE 判定。
- Reply 被 LINE 拒絕時仍可接續 Push；Push 重試保留原先完成的譯文及穩定重試識別碼。
- 使用相同租約與修改／撤回檢查；重複 webhook 不建立第二份翻譯工作。
- 收到 LINE 接受回應後的本地寫入與通知保存，與網路呼叫的例外處理分開。

測試模擬三個 AI 傳輸先失敗、隨後恢復，同時令 Push 不可用：修正後能透過一次 Reply 送出。這是模擬服務中斷的程式驗證，並非已查明正式帳號的推播額度用完。

[LINE 官方回覆規則](https://developers.line.biz/en/reference/messaging-api/#send-reply-message)說明 token 的單次使用與接收後時限；[計費文件](https://developers.line.biz/en/docs/messaging-api/pricing/#sending-methods-that-are-counted-towards-the-number-of-messages-for-the-subscription-plan)說明 Reply 不計入方案訊息數，Push 則會計入。

## 3. 健康檢查固定回傳 500

`/health` 及 `/admin/health-check` 引用了不存在的 `glossary_policy.BUILD_ID`，觸發 `AttributeError`。改讀模組實際提供的 `POLICY_VERSION`，保留既有回傳欄位名稱。

以原版本獨立副本驗證，兩個 HTTP 路徑都回傳 500；修正後公開健康檢查及授權的後台健康檢查回傳 200，未授權後台檢查仍回傳 403。

如果正式 Render 的 HTTP Health Check Path 設為 `/health`，原本的 500 可能影響流量分配、啟動判定或觸發重啟。這是依[Render 官方健康檢查規則](https://render.com/docs/health-checks)的條件推論；目前尚未取得正式服務的此項設定及 16:06 日誌，不能判定這就是截圖當次原因。

## 驗證與套用

完整離線回歸：**2,914 項測試、443 項子測試通過，81.22 秒**。結果與檔案雜湊見 `DELIVERY_RECOVERY_VALIDATION_20260911.json`。所有測試由 `run_translation_offline_checks.py` 執行，封鎖外部 AI、LINE、HTTP、DNS 與 socket；本機測試回送連線除外。沒有正式 AI 呼叫或 LINE 訊息發送。

將 ZIP 內檔案依原路徑覆蓋至現有專案，再一起提交／部署。執行檔必須同時包含：

1. `app.py`
2. `ai_provider.py`
3. `webhook_runtime.py`
4. `line_reply_window.py`（新增）

其餘檔案為測試與說明。無新增套件或資料庫遷移；未更新的先前檔案繼續保留。這份是本次差異包，不能單獨作為完整專案啟動。

部署後 `/health` 應回傳 200，並包含：

```json
{
  "provider_error_policy_build": "2026-09-11.5-typed-transport-recovery",
  "reply_recovery_build": "2026-09-11.1-preserve-unused-reply",
  "glossary_policy_build": "policy-v1"
}
```

這只能確認程式版本與 HTTP 路徑可運作，不能當成正式 AI／LINE 測試成功。本工作環境沒有部署這份修改，也未取得正式端到端秒數。若需核對截圖那次事件，需提供台灣時間 2026-09-11 16:05–16:08 的 Render 日誌，包含 `[callback]`、`[ai_provider]`、`[DeliveryPerf]` 及 traceback；請遮掉金鑰與 token。
