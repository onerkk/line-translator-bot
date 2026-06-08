"""
DB 快照 sidecar — 把 SQLite .db 檔備份/還原到 Upstash Redis(REST)。
目的:讓 LINE 翻譯 bot 可以跑在 Render Free(無持久磁碟)上而不丟累積資料。

設計重點
- 啟用條件:env UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN(沿用既有設定)。
  沒設 → 整支停用,模組照舊用本地檔(Starter 上行為完全不變,可逆)。
- 不改動 translation_memory / vector_tm / active_learning 任何一行。
- restore_all() 必須在 app.py import 那三個模組「之前」呼叫:
  它會把雲端副本還原到 /tmp,並用 env 變數把模組的 DB 路徑釘到 /tmp。
- start_autosnapshot() 在模組起來後呼叫:背景每隔 N 秒,若 .db 有變動才壓縮上傳。
- 安全鐵則:雲端有副本但「還原失敗 / 完整性不過」→ 凍結該庫,
  本回合絕不上傳,避免用空檔或壞檔覆蓋雲端的好副本。
"""

import os
import json
import gzip
import time
import base64
import hashlib
import sqlite3
import threading
import atexit
import urllib.request
import logging

logger = logging.getLogger("db_snapshot")

_URL = os.environ.get("UPSTASH_REDIS_REST_URL", "").strip().rstrip("/")
_TOK = os.environ.get("UPSTASH_REDIS_REST_TOKEN", "").strip()
# 快照間隔(秒)。DB 越大就調越長以省 Upstash 流量;預設 5 分鐘。
try:
    _INTERVAL = max(60, int(os.environ.get("DBSNAP_INTERVAL_SEC", "300") or "300"))
except ValueError:
    _INTERVAL = 300
_CHUNK = 1_000_000  # 每塊 base64 字元數(~1MB/塊),避免單一 value 過大

# 要備份的庫:統一工作路徑放 /tmp;legacy 是舊磁碟路徑(首次遷移用);table 供完整性檢查
_DBS = [
    {"name": "tm",  "path": "/tmp/translation_memory.db", "env": "TM_DB_PATH",
     "legacy": ["/var/data/translation_memory.db", "/data/translation_memory.db"],
     "table": "tm_entries"},
    {"name": "vec", "path": "/tmp/vector_tm.db", "env": "VECTOR_TM_DB_PATH",
     "legacy": ["/var/data/vector_tm.db", "/data/vector_tm.db"],
     "table": "vector_entries"},
    {"name": "al",  "path": "/tmp/active_learning.db", "env": "ACTIVE_LEARNING_DB_PATH",
     "legacy": ["/var/data/active_learning.db", "/data/active_learning.db"],
     "table": "corrections"},
]

_frozen = set()    # 還原失敗、本回合不可上傳的庫名
_last_hash = {}    # name -> 上次已同步內容的 sha256(沒變就不重複上傳)
_lock = threading.Lock()


def enabled():
    return bool(_URL and _TOK)


# ---- Upstash REST(與 app.py 已驗證可用的格式相同) ----
def _cmd(args, timeout=25):
    body = json.dumps(args).encode("utf-8")
    req = urllib.request.Request(
        _URL, data=body,
        headers={"Authorization": "Bearer " + _TOK, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8")).get("result")


def _kv_get(k):
    return _cmd(["GET", k])


def _kv_set(k, v):
    return _cmd(["SET", k, v])


# ---- 打包 / 還原 ----
def _consistent_copy(src):
    """用 SQLite 線上備份 API 取得一致快照的 bytes(避免讀到寫入中途的半套狀態)。"""
    tmp = src + ".snap.tmp"
    s = sqlite3.connect(src, timeout=15)
    d = sqlite3.connect(tmp)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()
    try:
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _pack(raw):
    return base64.b64encode(gzip.compress(raw, 6)).decode("ascii")


def _unpack(b64):
    return gzip.decompress(base64.b64decode(b64))


def _store(name, b64):
    parts = [b64[i:i + _CHUNK] for i in range(0, len(b64), _CHUNK)] or [""]
    # 記下舊塊數,寫完新塊後把用不到的尾段刪掉(避免 DB 縮小後殘留垃圾佔 Upstash 空間)
    try:
        old_n = int(_kv_get("dbsnap:%s:n" % name) or "0")
    except Exception:
        old_n = 0
    for i, p in enumerate(parts):
        _kv_set("dbsnap:%s:p%d" % (name, i), p)
    _kv_set("dbsnap:%s:n" % name, str(len(parts)))
    for i in range(len(parts), old_n):
        try:
            _cmd(["DEL", "dbsnap:%s:p%d" % (name, i)])
        except Exception:
            pass


def _load(name):
    n = _kv_get("dbsnap:%s:n" % name)
    if not n:
        return None
    n = int(n)
    buf = []
    for i in range(n):
        buf.append(_kv_get("dbsnap:%s:p%d" % (name, i)) or "")
    return "".join(buf)


def _valid_db(path, table):
    """確認還原出來的是合法 SQLite,且該庫的主表存在。"""
    try:
        c = sqlite3.connect(path, timeout=10)
        try:
            row = c.execute("PRAGMA quick_check").fetchone()
            if not row or str(row[0]).lower() != "ok":
                return False
            c.execute("SELECT 1 FROM %s LIMIT 1" % table).fetchone()
        finally:
            c.close()
        return True
    except Exception:
        return False


def restore_all():
    """app.py 最早期呼叫(在 import TM/Vector/AL 之前)。"""
    if not enabled():
        logger.info("[snap] disabled (no Upstash env); modules use their own paths")
        return
    for d in _DBS:
        os.environ[d["env"]] = d["path"]          # 釘住工作路徑 → 模組一律用 /tmp
        name, path = d["name"], d["path"]
        try:
            b64 = _load(name)
        except Exception as e:
            _frozen.add(name)
            logger.warning("[snap] %s cloud load FAILED -> FROZEN this run (cloud copy protected): %s", name, e)
            continue
        if b64:
            try:
                raw = _unpack(b64)
                tmp = path + ".rst"
                with open(tmp, "wb") as f:
                    f.write(raw)
                if _valid_db(tmp, d["table"]):
                    os.replace(tmp, path)
                    _last_hash[name] = hashlib.sha256(raw).hexdigest()
                    logger.info("[snap] restored %s from cloud (%d bytes)", name, len(raw))
                else:
                    _frozen.add(name)
                    logger.error("[snap] %s restore FAILED integrity -> FROZEN (cloud copy protected)", name)
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass
            except Exception as e:
                _frozen.add(name)
                logger.error("[snap] %s unpack FAILED -> FROZEN: %s", name, e)
        else:
            # 雲端沒副本 → 首次遷移:把舊磁碟的檔複製進 /tmp(原檔不動,留作備份)
            seeded = False
            for lp in d["legacy"]:
                if os.path.exists(lp):
                    try:
                        with open(lp, "rb") as fi, open(path, "wb") as fo:
                            fo.write(fi.read())
                        logger.info("[snap] %s seeded from legacy disk %s", name, lp)
                        seeded = True
                    except Exception as e:
                        logger.warning("[snap] %s seed from %s FAILED: %s", name, lp, e)
                    break
            if not seeded:
                logger.info("[snap] %s: no cloud + no legacy -> will start empty", name)
            # 不設 _last_hash → 首輪 autosnapshot 會把它上傳,完成雲端 seed


def _snapshot_one(d):
    name, path = d["name"], d["path"]
    if name in _frozen:
        return
    if not os.path.exists(path):
        return
    try:
        raw = _consistent_copy(path)
    except Exception as e:
        logger.warning("[snap] %s consistent copy FAILED: %s", name, e)
        return
    h = hashlib.sha256(raw).hexdigest()
    if _last_hash.get(name) == h:
        return  # 內容沒變 → 不上傳(省流量)
    try:
        _store(name, _pack(raw))
        _last_hash[name] = h
        logger.info("[snap] uploaded %s (%d bytes raw)", name, len(raw))
    except Exception as e:
        logger.warning("[snap] %s upload FAILED: %s", name, e)


def snapshot_now():
    if not enabled():
        return
    with _lock:
        for d in _DBS:
            _snapshot_one(d)


def _loop():
    while True:
        time.sleep(_INTERVAL)
        try:
            snapshot_now()
        except Exception as e:
            logger.warning("[snap] loop error: %s", e)


def start_autosnapshot():
    """模組起來後呼叫:先 seed/同步一次,再開背景定時。"""
    if not enabled():
        return
    snapshot_now()
    threading.Thread(target=_loop, daemon=True).start()
    atexit.register(snapshot_now)  # 優雅關閉(部署/重啟)前補存一次;硬殺則靠定時間隔
    logger.info("[snap] autosnapshot ON, every %ds, dbs=%s",
                _INTERVAL, [d["name"] for d in _DBS])
