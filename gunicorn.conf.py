# gunicorn.conf.py — LINE 翻譯 Bot 部署配置
# v3.9.57 (2026-05-31): 從 code 層面根治 multi-worker 不同步問題。
#
# 根因：gunicorn workers 各自獨立記憶體,不共享 group_tracking/group_settings/bot_stats。
# 官方文檔 (github.com/benoitc/gunicorn/discussions/3017):
#   "Gunicorn workers are designed to share nothing"
#
# 治本：強制 1 worker + 4 threads。
#   - 1 worker = 所有 request 在同一個 process,共享記憶體 ✓
#   - 4 threads = 並發處理能力（LINE webhook 可同時處理 4 個訊息）
#   - timeout 120 = 長訊息翻譯（Sonnet 4.6 + extended thinking）有足夠處理時間
#
# 優先順序：gunicorn 命令列參數 > config file。
# 所以 Render Start Command 應改成：gunicorn app:app（不帶 --workers）
# 或直接：gunicorn app:app -c gunicorn.conf.py

import multiprocessing
import os

# ═══ 核心：強制單 worker ═══
workers = 1
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))

# ═══ 綁定 ═══
bind = "0.0.0.0:" + os.environ.get("PORT", "10000")

# ═══ 日誌 ═══
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")

# ═══ 效能優化 ═══
# preload_app: 啟動時載入 app（省記憶體、加速啟動）
# 單 worker 不存在 fork 後不同步問題,可安全開啟
preload_app = True

# graceful_timeout: 收到 SIGTERM 後等多久才強制殺 worker
# Render 重啟時給正在翻譯的請求完成的機會
graceful_timeout = 30

# keep_alive: HTTP keep-alive 連接保持時間
# LINE webhook 不需要長連接,5 秒足夠
keepalive = 5

# max_requests: worker 處理 N 個 request 後自動重啟（防記憶體洩漏）
# 單 worker 重啟時會短暫無法服務,但 LINE webhook 有重發機制
max_requests = 10000
max_requests_jitter = 1000  # 加隨機偏移避免同時重啟

print(f"[gunicorn.conf.py] workers={workers} threads={threads} "
      f"timeout={timeout} bind={bind} preload={preload_app}")
