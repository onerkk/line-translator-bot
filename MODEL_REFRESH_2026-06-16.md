# OpenAI 模型更新（2026-06-16）

## 新的後台選單

- 短訊息預設：`gpt-5.4-mini`
- 長訊息預設：`gpt-5.4`
- 低成本：`gpt-5.4-nano`
- 穩定非推理：`gpt-4.1-mini` / `gpt-4.1`
- 最高品質：`gpt-5.5`
- 圖片 OCR：`gpt-5.4-mini`，備援 `gpt-4.1-mini`

## 自動遷移

舊的 GPT-5 dated snapshots、`gpt-4.1-nano`、`o3-mini`、`o4-mini`，以及過去程式可能產生的不存在型號，會在設定載入與 API 呼叫前自動轉換。

## 部署修正

原始 `Dockerfile` 是 NUL bytes，已重建；Gunicorn 改為 `1 worker + 4 threads + 180 秒 timeout`，與程式內的共享狀態設計一致。

## 其他 OpenAI 模型

- TTS 預設改為仍在服務的 `tts-1`；舊 `gpt-4o-mini-tts` IDs 會自動遷移。
- STT 維持 `gpt-4o-transcribe`；目前未列入停用公告。

## 驗證

- 全專案 Python 語法編譯
- 完整 `app.py` 匯入與 Flask 路由建立
- 舊模型設定遷移與管理 API 儲存測試
- OpenAI Chat Completions 參數整形測試
- 後台模型選單掃描

未包含真實 OpenAI、LINE、Anthropic、Gemini 帳號的線上呼叫；部署後仍需用實際金鑰做一次冒煙測試。
