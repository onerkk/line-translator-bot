# 個人常用句快捷鍵（不同群、不同人分開）— 進度檔

## 需求
每個群組裡，每個人底部看到「自己」最常講的 4 句，按了就以本人身分送出（再由機器人照常翻譯）。

## LINE 規格（已查官方文件）
- Quick reply 掛在訊息上，群組內所有人看到同一組，有人發言就消失 → 無法每人不同。
- LIFF `liff.sendMessages()`：在 1對1／群組／多人聊天開啟的 LIFF（LIFF 瀏覽器內、chat_message.write 範圍）
  可「以使用者本人身分」把訊息送進該聊天室；文字訊息會觸發 webhook（機器人照常翻譯）。
- `liff.getContext()` 自 2023-02 起不再提供 groupId → 用連結參數帶「群組代碼」（HMAC，不外露群組 ID）。
- LIFF 必須建在 LINE Login channel（Messaging API channel 不能建）→ 需老闆在 Console 建立一次。

## 分段
- [x] 第 1 段：統計模組 `phrase_stats.py`＋群組訊息背景記錄（每群每人、≤30 字、排除指令／點名／資料碼／多行；
      Upstash 每人保留 40 句；用過 2 次以上才算習慣）。
- [x] 第 2 段：LIFF 面板頁 `/liff/phrases`＋API `/api/liff/phrases`（向 LINE 驗證 access token：client_id 必須是 LINE Login 頻道 2009810040，再用 profile 取 userId；只回傳本人在該群前 4 句）。
- [x] 第 3 段：底部快捷鈕「⚡ 常用句/Kalimat」放在快捷列第一顆（URI → https://liff.line.me/2009810040-hthJ46K7?g=群組代碼）；LIFF ID 預設內建，可用環境變數 LIFF_PHRASES_ID 覆蓋。
- [x] 第 4 段（部分）：「⚡ 常用句」做成快捷選單的內建項目 `phrases`，後台快捷選單設定可排序／關閉；舊的已儲存選單會自動把它放第一顆，管理員重新儲存後照管理員設定。
- [ ] 第 5 段（之後）：後台清除某人某句。

## 已完成設定
LINE Login 頻道「翻譯表單系統」(2009810040) 新增 LIFF「常用句」：2009810040-hthJ46K7（Tall、profile＋chat_message.write、Endpoint /liff/phrases）。
