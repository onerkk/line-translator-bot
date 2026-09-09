"""Shared command aliases and paged help. Never rewrite ordinary message text."""
import re
from urllib.parse import urlencode

BUILD_ID = "2026-09-09.ui104"


def entry(key, chinese, category, description, indonesian, example="", *,
          aliases=(), english=None, admin=False, scope="both", usage=""):
    return dict(key=key, chinese=chinese, category=category, description=description,
                indonesian=indonesian, example=example, aliases=aliases,
                english=english or key, admin=admin, scope=scope, usage=usage)


CATEGORIES = {
    "start": ("日常翻譯", "Terjemahan sehari-hari"),
    "ack": ("作業確認與交班", "Konfirmasi & serah terima"),
    "tools": ("工廠查詢工具", "Alat pabrik"),
    "media": ("圖片、語音與文件", "Gambar, suara & dokumen"),
    "buttons": ("譯文工具與表單", "Tombol & formulir"),
    "quality": ("翻譯修正與品質", "Koreksi & kualitas"),
    "settings": ("群組與翻譯開關", "Setelan grup"),
    "names": ("人名與保留詞", "Nama & kata khusus"),
    "admin": ("管理與診斷", "Admin & diagnosis"),
}

GUIDES = {
    "media": [
        ("圖片翻譯", "Terjemahan gambar", "直接傳送照片。圖片模式為「詢問」時，按「翻譯這張」才開始；工單查儲區依群組工單開關處理。", "Kirim foto. Dalam mode ask, tekan tombol terjemahkan. Pencarian gudang dari WO mengikuti pengaturan grup."),
        ("語音與朗讀", "Suara & pembacaan", "傳送 LINE 語音即可轉文字並翻譯，須開啟語音翻譯。朗讀譯文是獨立開關，可在群組設定啟用。", "Kirim pesan suara untuk transkripsi dan terjemahan jika aktif. Pembacaan hasil memiliki pengaturan terpisah."),
        ("文件與影片", "Dokumen & video", "直接傳送支援的文件或影片，依群組設定擷取內容翻譯。文字過長會分段；工廠工具可複製完整原文與譯文。", "Kirim dokumen atau video yang didukung; konten diterjemahkan sesuai pengaturan grup. Teks panjang dibagi, dan teks lengkap dapat disalin melalui alat pabrik."),
    ],
    "buttons": [
        ("自然、直譯、正式與回譯", "Alami, harfiah, formal & cek balik", "按譯文下方按鈕切換表達方式；回譯會把譯文翻回原語言供核對。按鈕由管理員依群組設定，超過一頁時按下一頁。", "Gunakan tombol di bawah terjemahan untuk mengganti gaya atau menerjemahkan balik. Admin mengatur tombol per grup; pilih halaman berikut jika tersedia."),
        ("分享與圖文對照", "Bagikan & gambar", "「分享」先開啟內容預覽，再由你選擇收件對象；也可複製全文。圖片下方「圖文對照」依該群組的按鈕設定顯示。", "Bagikan membuka pratinjau, lalu Anda memilih penerima. Teks lengkap juga dapat disalin. Tombol gambar mengikuti pengaturan grup."),
        ("作業說明與表單", "SOP & formulir", "用 /factory／/工廠 選設備或掃碼，可看中印作業說明；有連結表單時可直接填寫。必填欄位完成後提交，系統會顯示是否已填過。", "Gunakan /factory untuk memilih mesin atau memindai QR, lalu baca SOP bilingual. Buka formulir terkait, isi kolom wajib, dan kirim; status duplikat ditampilkan."),
    ],
}

COMMANDS = [
    entry("help", "說明", "start", "分類查看所有指令；可接分類或指令名稱。", "Lihat perintah menurut kategori; tambahkan nama perintah untuk detail.", "/說明 確認", aliases=("幫助",), usage="[分類／指令]"),
    entry("mylang", "我的語言", "start", "選擇自己的閱讀語言，不會更改整個群組的語言。", "Pilih bahasa bacaan pribadi tanpa mengubah bahasa grup.", "/我的語言 印尼文", aliases=("mylanguage", "my-language", "melange", "language", "bahasa", "bahasa-saya", "bahasaku", "母語", "母语", "我的语言"), usage="[語言]"),
    entry("interpreter", "口譯", "start", "開啟雙向語音口譯，輪流說話；連結 10 分鐘有效。", "Buka interpretasi suara dua arah; tautan berlaku 10 menit.", "/口譯", aliases=("interpret", "voiceinterpreter", "penerjemah", "即時口譯", "即时口译", "口译")),
    entry("whoami", "我是誰", "start", "查看自己的 LINE ID 與管理員身分。", "Lihat ID LINE dan status admin Anda.", "/我是誰"),
    entry("status", "狀態", "start", "查看此群組的語言、文字模式及圖片、語音、工單開關。", "Lihat bahasa, mode teks, gambar, suara dan foto WO di grup.", "/狀態", scope="group"),
    entry("to", "私訊語言", "start", "查看私訊互譯說明；設定閱讀語言請用 /mylang／/我的語言。", "Info terjemahan pribadi; gunakan /mylang untuk memilih bahasa.", "/私訊語言", scope="dm"),
    entry("ack", "確認", "ack", "建立雙語確認。@All 或未標記追蹤全群；只 @ 指定成員則僅追蹤那些人。按了解只記錄；查看回覆查名單。", "Konfirmasi bilingual. @All atau tanpa mention: seluruh grup; mention tertentu: hanya anggota terpilih. Paham dicatat tanpa balasan; Status menampilkan daftar.", "/確認 @同事 請完成設備檢查。（請用 LINE 的 @ 選人）", aliases=("确认",), scope="group", usage="[@All／@成員] 通知內容"),
    entry("handover", "交班摘要", "ack", "整理本群組最近 12 小時的翻譯紀錄；摘要需由交接人核對。", "Ringkas terjemahan 12 jam terakhir di grup; periksa saat serah terima.", "/交班摘要", aliases=("summary", "ringkasan", "serahterima", "今天重點", "今天重点", "未完成事項", "未完成事项"), scope="group"),
    entry("notice", "公告", "ack", "產生雙語公告，不建立了解回覆紀錄；需要追蹤請用 /ack／/確認。", "Buat pengumuman bilingual tanpa catatan Paham; gunakan /ack untuk melacak jawaban.", "/公告 明天上午八點開會。", admin=True, scope="group", usage="公告內容"),
    entry("factory", "工廠", "tools", "開啟設備查詢、雙語作業說明、站別翻譯、複製與分享。", "Buka pencarian mesin, SOP bilingual, terjemahan, salin dan bagikan.", "/工廠"),
    entry("qr", "掃碼", "tools", "開啟工具後按掃描 QR；相機不可用時可手動輸入設備代碼。", "Buka alat lalu pindai QR; kode mesin juga dapat diketik.", "/掃碼"),
    entry("qry", "儲區", "tools", "查詢儲區資料；未帶參數會顯示查詢用法。", "Cari data lokasi gudang; tanpa parameter untuk petunjuk.", "/儲區 大成", aliases=("查儲區",), usage="查詢內容"),
    entry("pkg", "包裝", "tools", "查詢包裝代碼對應的包裝方式。", "Cari cara pengemasan berdasarkan kode.", "/包裝 1A", aliases=("包裝碼",), usage="代碼"),
    entry("pw1", "班長密碼", "tools", "顯示後台設定的班長密碼資料。", "Tampilkan informasi sandi mandor yang diatur admin.", "/班長密碼"),
    entry("pw2", "儲運密碼", "tools", "顯示後台設定的儲運密碼資料。", "Tampilkan informasi sandi gudang yang diatur admin.", "/儲運密碼"),
    entry("scrap", "廢料", "tools", "查看廢料鋼種的顏色分類。", "Lihat klasifikasi warna jenis scrap.", "/廢料", aliases=("廢料顏色",)),
    entry("saran", "建議", "tools", "取得後台設定的建議表單連結。", "Buka tautan formulir saran yang diatur admin.", "/建議", english="suggest", scope="group"),
    entry("absen", "請假", "tools", "取得後台設定的請假／出勤入口。", "Buka tautan izin atau kehadiran yang diatur admin.", "/請假", english="leave", scope="group"),
    entry("wrong", "標錯", "quality", "無參數：標記最近一筆；接正確譯文：送出修正；接 N：倒數第 N 筆；list：最近 10 筆。成員修正須管理員核准。", "Tanpa isi: tandai terakhir; tambah koreksi atau nomor N. list: 10 terakhir. Koreksi anggota menunggu persetujuan admin.", "/標錯 2 正確譯文", aliases=("錯", "markwrong", "修正"), scope="group", usage="[N／ID] [正確譯文]｜list"),
    entry("wrongstats", "標記統計", "quality", "查看錯誤標記與已核准修正數量，不等同實測翻譯準確率。", "Lihat jumlah penandaan dan koreksi disetujui; ini bukan akurasi terukur.", "/標記統計", scope="group"),
    entry("export", "匯出", "quality", "無參數：已核准訓練資料統計；jsonl：產生有效 1 小時的下載連結。", "Statistik data disetujui; jsonl membuat tautan unduhan berlaku 1 jam.", "/匯出 jsonl", admin=True, scope="group", usage="[jsonl]"),
    entry("on", "開啟翻譯", "settings", "開啟此群組翻譯；仍遵循個人略過與群組文字模式。", "Aktifkan terjemahan grup; tetap mengikuti mode teks dan daftar skip.", "/開啟翻譯", admin=True, scope="group"),
    entry("off", "關閉翻譯", "settings", "關閉此群組自動翻譯；管理與查詢指令仍可使用。", "Nonaktifkan terjemahan otomatis grup; perintah tetap tersedia.", "/關閉翻譯", admin=True, scope="group"),
    entry("img", "圖片", "settings", "on／開啟：自動翻譯；off／關閉：停用；ask／詢問：按按鈕才翻譯。", "on: otomatis; off: nonaktif; ask: terjemahkan setelah tombol ditekan.", "/圖片 詢問", admin=True, scope="group", usage="on｜off｜ask"),
    entry("voice", "語音", "settings", "on／開啟、off／關閉：控制收到語音訊息時的翻譯。", "on atau off: atur terjemahan pesan suara masuk.", "/語音 開啟", admin=True, scope="group", usage="on｜off"),
    entry("wo", "工單", "settings", "on／開啟、off／關閉：控制拍工單自動查儲區。", "on atau off: atur pencarian gudang dari foto WO.", "/工單 開啟", admin=True, scope="group", usage="on｜off"),
    entry("panel", "設定", "settings", "開啟群組語言面板；修改語言須管理員。", "Buka panel bahasa grup; perubahan memerlukan admin.", "/設定", aliases=("setting", "settings"), scope="group"),
    entry("lang", "語言", "settings", "無參數：語言面板；接語言代碼：更改群組目標語言，以逗號分隔。", "Tanpa parameter: panel; tambahkan kode bahasa dipisah koma untuk mengubah bahasa grup.", "/語言 id,vi", admin=True, scope="group", usage="[id,vi,th,en,ja,ko,hi,tl]"),
    entry("liff", "群組設定", "settings", "開啟手機設定頁，調整語言、翻譯、朗讀與保留詞。", "Buka setelan seluler untuk bahasa, terjemahan, audio dan kata khusus.", "/群組設定", admin=True, scope="group"),
    entry("skip", "略過我", "names", "此群組略過你的訊息，不再自動翻譯。", "Lewati terjemahan pesan Anda di grup ini.", "/略過我", aliases=("不翻譯我",), scope="group"),
    entry("unskip", "恢復我", "names", "恢復自動翻譯你在此群組的訊息。", "Terjemahkan kembali pesan Anda di grup ini.", "/恢復我", aliases=("恢復翻譯",), scope="group"),
    entry("skiplist", "略過名單", "names", "查看此群組不翻譯的成員名單。", "Lihat anggota yang dilewati terjemahannya.", "/略過名單", aliases=("白名單",), scope="group"),
    entry("skipadd", "略過成員", "names", "依已辨識姓名加入不翻譯名單；重名時需更完整姓名。", "Tambahkan anggota dikenal ke daftar skip; gunakan nama lengkap jika ambigu.", "/略過成員 姓名", admin=True, scope="group", usage="姓名"),
    entry("skipdel", "恢復成員", "names", "從此群組不翻譯名單移除指定成員。", "Hapus anggota dari daftar skip grup ini.", "/恢復成員 姓名", admin=True, scope="group", usage="姓名"),
    entry("skipterm", "保留詞", "names", "無參數：清單；接詞：新增；-詞 或 del 詞：刪除；clear／清空：清除此群組自訂詞。", "Tanpa isi: daftar; kata: tambah; -kata atau del kata: hapus; clear: hapus semua kata khusus grup.", "/保留詞 -產品名稱", admin=True, scope="group", usage="[詞｜-詞｜del 詞｜clear]"),
    entry("clearcache", "清除快取", "admin", "清除記憶體翻譯快取；不刪除詞庫、修正資料或紀錄。", "Hapus cache memori; glosarium dan catatan tetap tersimpan.", "/清除快取", admin=True, scope="group"),
    entry("audit", "抽檢", "admin", "私訊抽取最近 N 筆翻譯供人工核對，預設 10、最多 20。", "Di chat pribadi, ambil N terjemahan; default 10, maksimal 20.", "/抽檢 10", admin=True, scope="dm", usage="[1–20]"),
    entry("mark", "評分", "admin", "先用 /audit／/抽檢，再標記樣本編號：o／對 或 x／錯。", "Setelah /audit, tandai nomor sampel: o benar atau x salah.", "/評分 1 對", admin=True, scope="dm", usage="編號 o｜x"),
    entry("auditstats", "抽檢統計", "admin", "查看人工抽檢累計結果與正確比例。", "Lihat hasil dan persentase pemeriksaan manusia.", "/抽檢統計", admin=True, scope="dm"),
    entry("ttscheck", "朗讀檢查", "admin", "檢查朗讀設定；條件齊全時會合成測試語音，可能產生 API 費用。", "Periksa konfigurasi suara; tes sintesis dapat memakai API berbayar.", "/朗讀檢查", admin=True, scope="dm"),
    entry("setprice", "設定收費", "admin", "私訊查看或更改服務收費說明，可輸入多行文字。", "Lihat atau ubah teks harga layanan di chat pribadi.", "/設定收費 收費說明", admin=True, scope="dm", usage="[收費文字]"),
]

BY_KEY = {row["key"]: row for row in COMMANDS}
ALIASES = {}
for _row in COMMANDS:
    for _alias in (_row["key"], _row["english"], _row["chinese"], *_row["aliases"]):
        if _alias.casefold() in ALIASES and ALIASES[_alias.casefold()] != _row["key"]:
            raise ValueError("Duplicate command alias: " + _alias)
        ALIASES[_alias.casefold()] = _row["key"]


def normalize_command(text):
    original = str(text or "")
    match = re.fullmatch(r"\s*/([^\s/]+)(?:\s+([\s\S]*))?\s*", original)
    if not match:
        return original
    head, tail = match.group(1).casefold(), (match.group(2) or "").strip()
    if head == "stats" and tail.casefold() == "wrong":
        return "/wrongstats"
    key = ALIASES.get(head)
    if not key:
        return original
    if key in {"img", "voice", "wo"}:
        tail = {"開啟": "on", "開": "on", "關閉": "off", "關": "off", "詢問": "ask"}.get(tail, tail.casefold())
    elif key == "wrong" and tail in {"列表", "清單", "最近"}:
        tail = "list"
    elif key == "skipterm":
        tail = "clear" if tail == "清空" else re.sub(r"^刪除\s+", "del ", tail)
    elif key == "mark":
        tail = re.sub(r"\s+(對|正確)$", " o", tail)
        tail = re.sub(r"\s+(錯|錯誤)$", " x", tail)
    return "/" + key + (" " + tail if tail else "")


def command_key(text):
    value = normalize_command(text).split(None, 1)
    return value[0][1:] if value and value[0].startswith("/") else ""


def usage_text(key):
    row = BY_KEY[key]
    suffix = " " + row["usage"] if row["usage"] else ""
    return ("/" + row["english"] + suffix + "\n/" + row["chinese"] + suffix +
            "\n\n" + row["description"] + "\n" + row["indonesian"] +
            ("\n\n範例 / Contoh:\n" + row["example"] if row["example"] else ""))


def txt(text, size="sm", color="#203447", **options):
    return dict(type="text", text=text or "—", size=size, color=color, wrap=True, **options)


def nav(label, topic, lang):
    return dict(type="button", height="sm", style="link", color="#087F78",
                action=dict(type="postback", label=label[:20], data="action=help&" + urlencode(dict(topic=topic, lang=lang))))


def bubble(title, subtitle, contents, footer):
    return {"type": "bubble", "size": "mega", "header": {
        "type": "box", "layout": "vertical", "paddingAll": "20px", "backgroundColor": "#102F42",
        "contents": [txt("翻譯小助手 · PENERJEMAH", "xxs", "#8DDDD1"),
                     txt(title, "xl", "#FFFFFF", weight="bold", margin="sm"),
                     txt(subtitle, "xs", "#BED3DD", margin="sm")]},
        "body": {"type": "box", "layout": "vertical", "paddingAll": "20px", "spacing": "lg", "backgroundColor": "#FFFFFF", "contents": contents},
        "footer": {"type": "box", "layout": "vertical", "paddingAll": "12px", "backgroundColor": "#F1F7F8", "contents": footer}}


def build_help(primary_lang="zh", is_admin=False, topic=""):
    lang = "id" if primary_lang == "id" else "zh"
    topic = str(topic or "").strip().lstrip("/").casefold()
    topic = topic if topic in CATEGORIES else ALIASES.get(topic, topic)
    topic = next((k for k, v in CATEGORIES.items() if topic in v), topic)
    visible = [r for r in COMMANDS if is_admin or not r["admin"]]
    category = topic if topic in CATEGORIES else None
    if topic in BY_KEY and topic not in CATEGORIES:
        rows = [r for r in visible if r["key"] == topic]
    elif category in GUIDES:
        rows = [dict(guide=True, title=zh if lang == "zh" else idn,
                     description=desc, indonesian=desc_id, category=category)
                for zh, idn, desc, desc_id in GUIDES[category]]
    elif category:
        rows = [r for r in visible if r["category"] == category]
    else:
        rows = []
    pages = []
    if not rows:
        groups = [key for key in CATEGORIES if key in GUIDES or any(r["category"] == key for r in visible)]
        for offset in range(0, len(groups), 4):
            body = [txt("直接傳訊息即可翻譯；群組須已開啟自動翻譯。\nKirim pesan untuk diterjemahkan jika mode otomatis aktif.", color="#526577")]
            for key in groups[offset:offset + 4]:
                zh, idn = CATEGORIES[key]
                body.append({"type": "box", "layout": "vertical", "backgroundColor": "#F1F7F8", "cornerRadius": "12px", "paddingAll": "12px",
                             "action": {"type": "postback", "label": zh, "data": "action=help&" + urlencode(dict(topic=key, lang=lang))},
                             "contents": [txt(zh if lang == "zh" else idn, "md", weight="bold"), txt(idn if lang == "zh" else zh, "xs", "#526577", margin="xs")]})
            body.append(txt("所有指令皆可使用英文或中文，例如 /ack = /確認。\nPerintah menerima nama Inggris atau Mandarin.", "xs", "#526577"))
            pages.append(bubble("使用說明" if lang == "zh" else "Panduan", "選擇分類查看用法與範例 / Pilih kategori", body,
                                [nav("Bahasa Indonesia" if lang == "zh" else "中文說明", "", "id" if lang == "zh" else "zh")]))
    else:
        title = CATEGORIES.get(category or rows[0]["category"])[0 if lang == "zh" else 1]
        for offset in range(0, len(rows), 3):
            body = []
            if category == "ack" and offset == 0:
                body.append(txt("通知內容 → 按了解 → 查看回覆\n後台可設定提醒間隔、持續提醒與群組關閉；發起人或管理員可停止單筆提醒。了解不代表作業完成。名單不足時 @All 也會通知已回覆者。" if lang == "zh" else "Isi pesan → Paham → Status\nAdmin mengatur interval, pengulangan dan penonaktifan grup. Pengirim/admin dapat menghentikan satu pesan. Paham bukan berarti pekerjaan selesai. Jika daftar belum lengkap, @All juga menyebut yang sudah menjawab.", color="#087F78"))
            for row in rows[offset:offset + 3]:
                if row.get("guide"):
                    body.append({"type": "box", "layout": "vertical", "spacing": "sm", "contents": [
                        txt(row["title"], "md", weight="bold"),
                        txt(row["description"] if lang == "zh" else row["indonesian"])]})
                    continue
                scope = {"both": "群組／私訊 · Grup/pribadi", "group": "群組 · Grup", "dm": "私訊 · Pribadi"}[row["scope"]]
                cells = [txt("/" + row["english"] + "  /" + row["chinese"], "md", weight="bold"),
                         txt(("管理員 · Admin · " if row["admin"] else "") + scope, "xxs", "#647689"),
                         txt(row["description"] if lang == "zh" else row["indonesian"], "sm", margin="sm")]
                if row["usage"]:
                    cells.append(txt("/" + row["english"] + " " + row["usage"], "xs", "#526577", margin="sm"))
                cells.append(txt(row["example"], "sm", "#087F78", margin="sm"))
                body.append({"type": "box", "layout": "vertical", "spacing": "xs", "contents": cells})
            pages.append(bubble(title, "用法與範例 · " + str(offset // 3 + 1) + "/" + str((len(rows) + 2) // 3), body,
                                [nav("全部分類 / Menu", "", lang), nav("Bahasa Indonesia" if lang == "zh" else "中文說明", topic, "id" if lang == "zh" else "zh")]))
    return "翻譯小助手使用說明 / Panduan perintah", {"type": "carousel", "contents": pages}
