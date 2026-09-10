# 急單翻譯與完成符號修正

版本：2026-09-10.9

## 截圖案例

原文：`急單看一下，幫忙處理`

修正後：`Tolong periksa work order mendesak ini dan bantu menanganinya.`

`急單` 表示工單本身是急件。原譯把急迫性放在「立即檢查」上，沒有明確保留急單屬性；額外的 `✅` 也不是原文內容，容易讓待處理要求看起來已完成。

## 本次修正

- 補上中印雙向的急單語意檢查，保留急迫性、否定、工單號與條目歸屬，接受自然的同義表達。
- 能完整辨識的短句直接依原文組成譯文，不需要 AI 呼叫。遇到未辨識的人名、工單號、數量、期限、條件、問句或額外內容，仍走完整翻譯流程，不省略內容；原文的 LINE 點名完整保留。
- 「幫忙處理」等待處理要求不再自動添加完成勾號；保留原文已有的符號，以及正常的感謝、恭喜表情。
- 新規則套用於品質檢查、最終送出、快取與翻譯記憶寫入。快取版本隨語意規則更新，舊錯譯不能靠舊驗證標記繼續被採用。
- 表情裝飾若破壞已驗證譯文，改為保留裝飾前的合格譯文，避免因可選的表情功能而漏發或重試。

照片翻譯仍受後台開關控制。本包沿用前次「只計算了解、舊需說明列為未回覆」的修正基礎，沒有改動通知權限、提醒排程或照片執行條件。

## 更新方式

本 ZIP 僅包含這次新增或修改的檔案，檔案路徑相對於原專案根目錄。覆蓋同名檔案，務必一併放入新增的 `factory_order_semantics.py`，再重新啟動服務。請整包一起更新，讓主程式與語意、表情模組版本一致。

程式變更共 6 個檔案：`app.py`、`factory_order_semantics.py`、`factory_message_semantics.py`、`factory_translation_guard.py`、`expressive_engine.py`、`translation_extras.py`。另含 8 個新增或更新的測試檔與本說明。

## 驗證

- 完整離線回歸：**2,685 項測試、443 個子案例全部通過**（70.34 秒）。
- 新增急單與表情回歸：**72 項通過**。
- 翻譯資產驗證：**通過，0 錯誤、0 警告**；訊息語意模組的 **105 項啟動自檢通過**。
- 本案例直接翻譯路徑已驗證不呼叫 AI 供應商；以上測試總耗時不是單則訊息的線上翻譯時間。

可重跑的指令：

```sh
python run_translation_offline_checks.py
python validate_factory_translation_assets.py --json
```

測試涵蓋截圖錯譯攔截、正確譯文接受、急單短句變體、否定與問句、工單號及條目錯置、額外條件不得省略、完成符號、舊快取、錯譯不得寫入翻譯記憶，以及表情失敗仍保留合格譯文。測試在隔離環境執行，封鎖正式 LINE 與翻譯供應商的網路呼叫；尚未部署到您的正式服務。

## 已查閱的官方技術資料

- [OpenAI：Evaluation best practices](https://developers.openai.com/api/docs/guides/evaluation-best-practices) — 依實際任務建立評估資料，加入失敗案例與邊界情境，持續檢查變更後的品質；本次據此加入錯譯、合理譯法與相反情境的回歸案例。
- [OpenAI：Latency optimization](https://developers.openai.com/api/docs/guides/latency-optimization) — 對可明確處理的任務使用一般程式方法、減少不必要的模型呼叫；本次只對完整辨識的短句採用本機翻譯，不以省略內容換取速度。
- [Anthropic：Define success criteria and build evaluations](https://platform.claude.com/docs/en/test-and-evaluate/develop-tests) — 以任務的正確性與實際使用情境訂定評估標準；離線測試結果不等於正式 LINE 的端到端延遲保證。
