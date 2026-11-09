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
- [ ] 第 2 段：LIFF 面板頁 `/liff/phrases`＋API（驗證 LINE access token 取 userId，回傳本人在該群前 4 句）。
- [ ] 第 3 段：底部快捷鈕「⚡ 常用句／Kalimat」（URI → https://liff.line.me/{LIFF_ID}?g=群組代碼），設定 LIFF_PHRASES_ID 後才出現。
- [ ] 第 4 段：後台可關閉、可清除某人某句。

## 待老闆提供
LINE Developers Console 建 LINE Login channel → 新增 LIFF app（Size: Tall；Endpoint: https://<Render網域>/liff/phrases；
Scope: profile、chat_message.write），把 LIFF ID 給我。
