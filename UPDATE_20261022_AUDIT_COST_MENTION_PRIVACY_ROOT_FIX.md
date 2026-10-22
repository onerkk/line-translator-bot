# 2026-10-22：全面體檢 — 成本計價、點名／隱私記號外漏、職稱大小寫、後台 PWA 圖示

本包以 GitHub 主幹（README 最新為 2026-10-21 更新）為基準，只含 **變更或新增的檔案**，解壓後覆蓋專案根目錄即可；沒有新的必填環境變數。

## 這次怎麼查的（都是實際操作，不是讀碼推論）

| 項目 | 方法 | 結果 |
| --- | --- | --- |
| 全套測試基線 | `pytest`（排除歷史快照資料夾） | 4,308 項 + 450 子測試通過 |
| 外部介面規格 | 先查 OpenAI／Anthropic／Google 當日價格頁與型號 ID 再動手 | 程式內 `gpt-6-luna`／`gpt-6.1-sol`／`claude-haiku-5-5`／`claude-sonnet-5-5`／`gemini-3.1-flash-lite` 全部正確、未過期 |
| 提示詞成本 | 攔截實際送出的請求，用語料 49 句量測 | 系統提示 3.2k–5.7k tokens；同方向穩定前綴 ≈3.15k 且 `prompt_cache_key` 綁前綴 hash，可命中快取；`reasoning_effort=none`、單次生成；估每則 US$0.0002–0.0004 |
| 本機處理延遲 | 同上，計時 | 中位數 62 ms、最長 144 ms；瓶頸不在程式端 |
| 後台 179 條路由 | Flask test client 全打一遍 | 0 個 500 |
| 後台手機版面 | 真實 Chrome 390px，19 個分頁逐一切換 | 0 個 JS 錯誤、0 個 4xx/5xx、無橫向溢出 |
| 工廠語料 | `factory_translation_regression.json` 30 案 + 截圖審核 13 案跑完整 `translate()` | 全部命中已核准案例、0 次 API 呼叫；修正後 43/43 與核准譯文完全一致（修正前 3 案有缺陷，見下） |
| 真實 DOM 測試 | `tests/run_factory_ui.py`（jsdom） | 全部 PASS |
| `/help` 排版 | 把 Flex JSON 轉成近似 HTML 以 Chrome 渲染 | 版面正常；印尼文列說明截斷過短已改善；所有訊息 < 45 KB |

## 修正的 Bug（根因，非單句補丁）

1. **成本儀表板高估**（`app.py calc_cost_usd`、`ai_provider.py count_tokens`）
   - OpenAI 快取折扣只認 `gpt-5` 前綴，GPT-6 全被當 50%；實際 `gpt-6-luna` 10%、`gpt-6.1-sol` 5%。
   - Sonnet 5.5／Opus 5.5 cache read 10/7 起為 5%，程式一律 10%；改為按型號查表。
   - Sonnet 5 的 $2/$10 已定為永久價，移除 9/1 後自動跳 $3/$15 的邏輯。
   - `count_tokens` 估價表沒有 5.5 系列，Haiku 5.5 落到預設 $1（高估 10 倍）；改最長前綴查表。
2. **點名後的保護名單記號外漏**（`translation_mentions.py`、`app.py translate_openai`）
   - 語料實測：`@budi santoso 山多 I5已發料成功` 送到 LINE 的是 `@budi santoso __QG_KEEP_900_DBB44B60__ I5 sudah…`。
   - 根因：保護名「山多」先被隱私層遮罩，純文字點名解析把遮罩記號吞進 `@mention`，模型看不到、還原時原樣漏出。
   - 修法：點名解析永遠不吞任何 `__XXX__` 管線記號；點名還原後再補一次隱私還原。新增 `test_mention_privacy_placeholder_root_fix.py`。
3. **職稱正規化把句首改小寫**（`factory_terminology.py`）
   - 「處長下來巡視」→ `kepala divisi turun…`。根因 `_replace_id_role` 一律寫回小寫 canonical。
   - 新增 `_match_case` 保留原大小寫形狀（句首、Title Case、全大寫），所有職稱一併生效；`test_translation_review_20261010.py` 一條把舊缺陷當預期的斷言已更正。
4. **id→zh 核准譯文結尾句號被吃掉**（`app.py post_fix_factory_id_to_zh`）
   - 舊邏輯無條件去掉結尾「。」。改為：多句公告且原文有句末標點時保留，單句聊天維持無句號（與既有 38 項 runtime 測試一致）。
5. **後台 PWA 圖示壞掉、favicon 404**（`app.py`）
   - `/icon-192.png`／`/icon-512.png` 的 PNG 簽名寫成雙反斜線字面值，Chrome 實測報「不是有效圖片」。改用 Pillow 產生真正的圖示（快取於記憶體，無 Pillow 時退回合法 1×1 PNG）；新增 `/favicon.ico`。
6. **登入頁版號固定顯示 v3.0** → 顯示實際 `VERSION`。
7. **測試會污染工作目錄與 /tmp**（`conftest.py`）
   - 測試寫入 repo 內 `translation_log.json`／`last_translate_debug.json`，且 TM／向量／主動學習 SQLite 落在 `/tmp` 跨次執行殘留，導致 `test_material_rework_translation` 第二次執行時因殘留的 verified 列而失敗。改為每次執行用獨立暫存目錄。
8. **`/help` 印尼文列說明截斷過短**（`line_command_catalog.py`）：34 字改 72 字並在單字邊界截斷；訊息大小仍 < 45 KB。
9. **Docker 映像瘦身**：新增 `.dockerignore`，排除 4 份歷史 `app.py` 快照、1 MB 更新腳本、文件與測試（約 15 MB）。

## 已存在、本次沒有改的（供你判斷）

- Gemini 備援：`gemini-3.1-flash-lite` $0.25/$1.50 比 Luna／Haiku 5.5 貴 2.5 倍，`gemini-2.5-flash` 對新帳號已受限；現行 failover 順序已是 Anthropic → OpenAI → Gemini，建議之後把升級對應改成 `gemini-3.5-flash-lite`（$0.30/$2.50，GA）。
- `tests/fixtures/sample_messages.json` 是空檔且無人引用，可刪。
- 倉庫內 `000/`、`line-translator-changed-2026101x/` 四份舊版快照建議從 Git 移除（本包只用 `.dockerignore` 擋在映像外）。
- 後台 AI 分頁仍保留 Opus 4.7／4.6 的舊說明文字（功能正常，只是文案過時）。
- `/help` 中文版頁尾「Bahasa Indonesia」按鈕在 300px bubble 上接近寬度上限，真機若截字可改短標籤。
- `@就是這個光 ✨༄`：純文字點名只抓到 `@就是這個光`，emoji 名尾落到可譯文字；LINE 原生事件有 mention metadata，正式環境通常不受影響。

## 限制

容器內沒有真實 API 金鑰與對外網路：翻譯品質與線上秒數無法實打實跑模型，上表所有「翻譯正確」指程式層對核准譯文的處理不再出錯；模型譯文品質仍靠你的截圖審核閉環驗證。
