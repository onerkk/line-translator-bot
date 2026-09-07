"""LINE factory tools: revisions, native mentions, QR, sharing and receipts.

All LINE sends use the application's durable delivery boundary. Web pages only
preview/share on an explicit user click; no background broadcast is performed.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from flask import jsonify, request, render_template
from itsdangerous import URLSafeTimedSerializer, BadSignature
from linebot.v3.messaging import Message, TextMessage, QuickReply, QuickReplyItem, PostbackAction

import line_translation_delivery as delivery
from line_factory_store import FeatureStore, configured_store, StoreError, encode, measure_storage, mark_delivery
import translation_retry_queue as queue

BUILD_ID = "2026-09-07.factory-speed.2"
_EVENT = ContextVar("factory_line_event", default=None)
_STATION = ContextVar("factory_selected_station", default=None)
DEFAULTS = {"translation_mode": "all", "edit_translation": True, "native_mentions": True,
            "sharing": True, "station_tools": True, "acknowledgements": "work"}
_USER = re.compile(r"U[0-9a-f]{32}\Z")
_CODE = re.compile(r"[A-Za-z0-9\u3400-\u9fff][A-Za-z0-9\u3400-\u9fff_.-]{0,39}\Z")
_WORK = re.compile(r"PMI|檢[驗測查]|检[验测查]|生產|生产|產量|设备|設備|班[別次]|交[接班]|"
                   r"入[庫帐帳]|出[貨库庫]|包裝|包装|秤[重料]|標[籤签]|工[單单]|"
                   r"\b(?:produksi|periksa|pemeriksaan|shift|mesin|timbang|gudang|label)\b", re.I)


class SupersededMessage(queue.LeaseLostError):
    """A newer original/cancellation owns the operation now."""


def field(obj, name, default=None):
    if isinstance(obj, dict):
        if name in obj:
            return obj[name]
        pieces = name.split("_")
        return obj.get(pieces[0] + "".join(p.title() for p in pieces[1:]), default)
    return getattr(obj, name, default)


def source_ids(event):
    src = field(event, "source", {})
    return (field(src, "group_id") or field(src, "room_id") or field(src, "user_id") or "",
            field(src, "user_id", "") or "")


def event_identity(event):
    message = field(event, "message", {})
    mid = str(field(message, "id", "") or "")
    if field(event, "type") == "messageEdited":
        raw = str(field(event, "timestamp", 0)) + ":" + str(field(message, "text", ""))
        return mid + ":edit:" + hashlib.sha256(raw.encode()).hexdigest()[:20]
    return mid


def postback_identity(event):
    return "pbk:" + str(field(event, "webhook_event_id") or field(event, "reply_token") or "")


def mentions_bot(message):
    return any(field(item, "is_self", False) is True
               for item in field(field(message, "mention", {}), "mentionees", []) or [])


def native_mentions(message):
    """Only signed webhook mention metadata creates a real mention, never names."""
    raw = str(field(message, "text", "") or "")
    encoded = raw.encode("utf-16-le")
    result = []
    for item in field(field(message, "mention", {}), "mentionees", []) or []:
        if field(item, "is_self", False):
            continue  # Messaging API cannot mention a bot.
        kind, uid = field(item, "type"), field(item, "user_id", "")
        if kind != "all" and (kind != "user" or not _USER.fullmatch(str(uid))):
            continue
        try:
            start, length = int(field(item, "index")), int(field(item, "length"))
            if start < 0 or length <= 0 or (start + length) * 2 > len(encoded):
                continue
            label = encoded[start * 2:(start + length) * 2].decode("utf-16-le")
        except (TypeError, ValueError, UnicodeError):
            continue
        if not label.strip():
            continue
        result.append({"label": label, "type": kind, "userId": uid if kind == "user" else ""})
        if len(result) == 20:
            break
    return result


def text_with_mentions(message, mentions):
    """Escape literal braces; preserve all text while substituting known spans."""
    obj = message.to_dict() if hasattr(message, "to_dict") else copy.deepcopy(message)
    if obj.get("type") != "text" or not mentions:
        return obj
    value, substitution = obj.get("text", ""), {}
    spans = []
    for item in sorted(mentions, key=lambda x: len(x["label"]), reverse=True):
        start = value.find(item["label"])
        while start >= 0 and any(start < end and start + len(item["label"]) > begin for begin, end, _ in spans):
            start = value.find(item["label"], start + len(item["label"]))
        if start < 0:
            continue
        spans.append((start, start + len(item["label"]), item))
    if not spans:
        return obj
    parts, end = [], 0
    for index, (begin, finish, item) in enumerate(sorted(spans)):
        key = "person" + str(index)
        parts.append(value[end:begin].replace("{", "{{").replace("}", "}}"))
        parts.append("{" + key + "}")
        mentionee = {"type": item["type"]}
        if item["type"] == "user":
            mentionee["userId"] = item["userId"]
        substitution[key] = {"type": "mention", "mentionee": mentionee}
        end = finish
    parts.append(value[end:].replace("{", "{{").replace("}", "}}"))
    text = "".join(parts)
    if delivery.utf16_units(text) > 5000:
        return obj  # Braces can expand at the framing limit; keep readable text.
    obj.update(type="textV2", text=text, substitution=substitution)
    return obj


def station_prompt():
    station = _STATION.get()
    if not station:
        return ""
    # Context identifies the referent only; it must never append an SOP action.
    return ("\n<selected_factory_station>使用者已選定以下設備／站別背景。"
            "僅用於消除原文簡稱歧義；不得把背景或作業說明添加到譯文，"
            "不得覆寫原文明確指定的其他設備、數字、動作或否定。\n" +
            encode({k: station.get(k, "") for k in ("code", "name_zh", "name_id", "context")}) +
            "\n</selected_factory_station>")


def station_scope():
    return hashlib.sha256(station_prompt().encode()).hexdigest()[:16] if _STATION.get() else ""


def measure_delivery(kind):
    """Include pre-translation storage and LINE delivery in request timing."""
    def decorate(fn):
        @wraps(fn)
        def run(*args, **kwargs):
            with measure_storage() as stats:
                try:
                    return fn(*args, **kwargs)
                finally:
                    from flask import current_app, has_app_context
                    if has_app_context():
                        logger = current_app.logger
                    else:
                        import logging
                        logger = logging.getLogger("app")
                    logger.info("[DeliveryPerf] kind=%s total=%dms sent=%s storage=%dms storage_calls=%d storage_skipped=%d",
                                kind, (time.monotonic() - stats["started"]) * 1000,
                                (str(round(stats["delivered_ms"])) + "ms") if stats["delivered_ms"] is not None else "none",
                                stats["milliseconds"], stats["requests"], stats["skipped"])
        return run
    return decorate


class FactoryHub:
    def __init__(self, app, host, store=None):
        self.app, self.h = app, host
        self._store = store
        self._lock = threading.RLock()
        self._insight_cache = {}
        self._revision_db = None

    @property
    def store(self):
        with self._lock:
            if self._store is None:
                self._store = configured_store()
            return self._store

    def settings(self):
        return self.h.get("factory_line_settings") or {}

    def options(self, group):
        return {**DEFAULTS, **self.settings().get("groups", {}).get(group, {})}

    def _rev_key(self, group, message):
        return "revision:" + hashlib.sha256((group + ":" + message).encode()).hexdigest()

    @property
    def revisions(self):
        # Same local persistence boundary as the durable LINE outbox. Optional
        # cloud interaction storage must not stop core translation during outage.
        path = str(queue.DB_PATH) + ".revisions"
        with self._lock:
            if self._revision_db is None or self._revision_db.path != path:
                self._revision_db = FeatureStore(path=path)
            return self._revision_db

    def _update_revision(self, key, advance):
        latest = self.revisions.update(key, advance, 30 * 86400)
        try:
            remote = self.store.merge_revision(key, latest, 30 * 86400)
            latest = self.revisions.merge_revision(key, remote, 30 * 86400)
        except StoreError:
            self.app.logger.warning("[FactoryTools] cloud interactions unavailable; local revision journal retained")
        return latest

    @contextmanager
    def message_scope(self, event, kind):
        group, uid = source_ids(event)
        message = field(event, "message", {})
        mid = str(field(message, "id", "") or "")
        options = self.options(group)
        edited = field(event, "type") == "messageEdited"
        text = str(field(message, "text", "") or "")
        command = text.startswith("/") or bool(self.h.get("_detect_control_shortcut", lambda _: None)(text))
        filtered = (edited and (not options["edit_translation"] or command)) or (
            kind == "text" and group.startswith(("C", "R")) and not command and
            options["translation_mode"] == "mentioned" and not mentions_bot(message)
        )
        if filtered and not edited:
            yield False
            return
        identity = event_identity(event)
        timestamp = int(field(event, "timestamp", 0) or 0)
        revision = {"group_id": group, "message_id": mid, "identity": identity,
                    "timestamp": timestamp, "edited": edited,
                    "order": str(field(event, "webhook_event_id", "") or ""),
                    "mentions": native_mentions(message), "user_id": uid}
        if group and mid:
            key = self._rev_key(group, mid)
            def advance(old):
                if old:
                    if old.get("cancelled"):
                        return old
                    old_order = (old.get("timestamp", 0), old.get("order", ""))
                    new_order = (timestamp, revision["order"])
                    if old.get("identity") != identity and (not edited or new_order <= old_order):
                        return old
                return {**revision, "cancelled": False}
            latest = self._update_revision(key, advance)
            if latest.get("cancelled") or latest.get("identity") != identity:
                yield False
                return
            if edited:
                queue.cancel_source(group, mid, except_identity=identity)
        if filtered:
            yield False
            return
        marker = _EVENT.set(revision)
        try:
            yield True
        finally:
            _EVENT.reset(marker)

    def job_key(self, group, message_id, default):
        current = _EVENT.get()
        if current and current["group_id"] == group and current["message_id"] == str(message_id):
            return group + ":" + current["identity"]
        return default

    def payload_metadata(self):
        return copy.deepcopy(_EVENT.get())

    def current(self, metadata):
        if not metadata or not metadata.get("message_id"):
            return True
        key = self._rev_key(metadata["group_id"], metadata["message_id"])
        latest = self.revisions.get(key)
        if latest is None:
            latest = self.store.get(key)
        return bool(latest and not latest.get("cancelled") and latest.get("identity") == metadata.get("identity"))

    def assert_current(self, payload):
        metadata = (payload or {}).get("factory_event")
        if metadata and not self.current(metadata):
            raise SupersededMessage("source was edited or unsent")

    def unsend(self, event):
        group, _ = source_ids(event)
        mid = str(field(field(event, "unsend", {}), "message_id", "") or "")
        if not group or not mid:
            return
        self._update_revision(self._rev_key(group, mid), lambda old: {
            **(old or {}), "group_id": group, "message_id": mid, "cancelled": True,
        })
        queue.cancel_source(group, mid)
        # Do not keep accessible copies in receipts or translation buttons.
        try:
            indexed = self.store.get("source-contexts:" + self._rev_key(group, mid)) or {}
            for token in indexed.get("tokens", []):
                self.store.delete("context:" + token)
                self.store.delete("notice:" + group + ":" + token)
            self.store.delete("source-contexts:" + self._rev_key(group, mid))
            previous_notices = self.store.recent("notice:" + group, 200)
        except StoreError:
            previous_notices = []  # Tombstone already blocks all further use.
            self.app.logger.warning("[FactoryTools] unsent source blocked; cloud cleanup deferred to TTL")
        for context in previous_notices:
            if context.get("msg_id") == mid:
                self.store.delete("context:" + context["token"])
                self.store.delete("notice:" + group + ":" + context["token"])
        cache = self.h.get("_translation_action_cache", {})
        with self.h.get("_translation_action_lock", self._lock):
            for token, context in list(cache.items()):
                if context.get("group_id") == group and context.get("msg_id") == mid:
                    cache.pop(token, None)

    def _wants_notice(self, group, record):
        ack = self.options(group)["acknowledgements"]
        return group.startswith(("C", "R")) and (
            ack == "all" or (ack == "work" and bool(_WORK.search(record.get("original", "")))))

    def save_context(self, token, record):
        record = copy.deepcopy(record)
        record["token"] = token
        record["factory_event"] = record.get("factory_event") or self.payload_metadata()
        remaining = max(1, int(record.get("expires_at", time.time() + 86400) - time.time()))
        group, mid = record.get("group_id", ""), record.get("msg_id")
        source_key = "source-contexts:" + self._rev_key(group, mid) if group and mid else None
        notice = None
        if self._wants_notice(group, record):
            record["_notice_prepared"] = True
            members = self.h.get("group_user_names", {}).get(group, {})
            notice = {**record, "created_at": time.time(), "responses": {},
                      "delivery_state": "prepared",
                      "expected": {uid: str(name) for uid, name in members.items() if _USER.fullmatch(uid)},
                      "roster_basis": "known_chat_members"}
        self.store.save_interaction(record, remaining, source_key=source_key, notice=notice)
        return record

    def get_context(self, token, group=None):
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", str(token or "")):
            return None
        record = self.store.get("context:" + token)
        if (not record or record.get("expires_at", 0) <= time.time() or
                (group and record.get("group_id") != group) or not self.current(record.get("factory_event"))):
            return None
        return record

    def _context_for_delivery(self, messages, payload, rendered):
        group = payload.get("group_id") or payload.get("user_id") or ""
        def tokens(value):
            if isinstance(value, dict):
                if value.get("type") == "postback":
                    token = dict(urllib.parse.parse_qsl(value.get("data") or "")).get("token")
                    if token:
                        yield token
                for child in value.values():
                    yield from tokens(child)
            elif isinstance(value, list):
                for child in value:
                    yield from tokens(child)
        seen = set()
        for message in messages:
            # Flex buttons and Quick Reply can both already own this context.
            for token in tokens(message.to_dict()):
                if token in seen:
                    continue
                seen.add(token)
                with self.h.get("_translation_action_lock", self._lock):
                    record = copy.deepcopy(self.h.get("_translation_action_cache", {}).get(token))
                if not record:
                    record = self.get_context(token, group)
                elif (record.get("expires_at", 0) <= time.time() or record.get("group_id") != group or
                      not self.current(record.get("factory_event"))):
                    record = None
                if record:
                    metadata = payload.get("factory_event")
                    if metadata and not record.get("factory_event") and str(record.get("msg_id")) == str(metadata.get("message_id")):
                        record["factory_event"] = metadata
                        record = self.save_context(token, record)
                        # Media extraction may register controls before the
                        # delivery job supplies its revision. Bind that receipt
                        # too, preserving any concurrently recorded responses.
                        self.store.update("notice:" + group + ":" + token,
                                          lambda row: {**row, "factory_event": metadata}
                                          if row and not row.get("factory_event") else row,
                                          7 * 86400)
                        with self.h.get("_translation_action_lock", self._lock):
                            cache = self.h.get("_translation_action_cache", {})
                            if token in cache:
                                cache[token] = record
                    return token, record
        original = (payload.get("source_text") or payload.get("ocr_text") or
                    payload.get("transcribed_text") or payload.get("document_text") or "")
        if not original:
            return None, None
        token = secrets.token_urlsafe(12)
        record = {"group_id": group, "original": str(original), "translated": rendered,
                  "src": payload.get("src_lang", "auto"), "tgt": (payload.get("target_langs") or [""])[0],
                  "msg_id": payload.get("message_id", ""), "factory_event": payload.get("factory_event"),
                  "expires_at": time.time() + 7 * 86400}
        return token, self.save_context(token, record)

    def decorate_delivery(self, messages, payload, text):
        """Create complete, serializable deliveries before their first send."""
        self.assert_current(payload)
        group = payload.get("group_id") or payload.get("user_id") or ""
        options = self.options(group)
        metadata = payload.get("factory_event") or self.payload_metadata() or {}
        if metadata.get("edited"):
            messages.insert(0, TextMessage(text="✏️ 原文已修改，以下為更正翻譯。\nTeks asli diedit; berikut terjemahan terbaru."))
        try:
            token, record = self._context_for_delivery(messages, payload, text)
        except StoreError:
            self.app.logger.warning("[FactoryTools] interaction buttons unavailable; translation delivery continues")
            token, record = None, None
        if token:
            buttons = []
            if options["sharing"]:
                buttons.append(("📤 分享/Bagikan", "factory_share"))
            if options["station_tools"]:
                buttons.append(("🏭 工具/Alat", "factory_open"))
            if self._wants_notice(group, record):
                try:
                    if not record.get("_notice_prepared"):
                        record = self.save_context(token, record)
                    payload["factory_notice_token"] = token
                    buttons.extend([("✅ 了解/Paham", "factory_ack"), ("❓ 說明/Jelaskan", "factory_help"),
                                    ("📋 確認/Status", "factory_receipts")])
                except StoreError:
                    self.app.logger.warning("[FactoryTools] receipt storage unavailable; no confirmation buttons added")
            if buttons:
                # The last message owns Quick Reply, so long replies retain it.
                current = list(getattr(getattr(messages[-1], "quick_reply", None), "items", []) or [])
                needed = [QuickReplyItem(action=PostbackAction(label=label, data="action=" + action + "&token=" + token))
                          for label, action in buttons]
                if len(current) + len(needed) <= 13:
                    messages[-1].quick_reply = QuickReply(items=current + needed)
                else:
                    messages.append(TextMessage(text="🏭 工廠工具 / Alat pabrik",
                                                quick_reply=QuickReply(items=needed)))
        if options["native_mentions"] and group.startswith(("C", "R")) and metadata.get("mentions"):
            mentions = metadata["mentions"]
            # A companion native mention preserves the existing Flex card.
            converted = False
            for index, message in enumerate(messages):
                obj = text_with_mentions(message, mentions)
                if obj.get("type") == "textV2":
                    messages[index] = Message.from_dict(obj)
                    converted = True
                    break
            if not converted:
                header = TextMessage(text="📣 " + " ".join(item["label"] for item in mentions))
                messages.insert(0, Message.from_dict(text_with_mentions(header, mentions)))
        return messages

    def delivery_accepted(self, payload, plan):
        mark_delivery()
        token = plan.get("factory_notice_token")
        if not token:
            return
        group = payload.get("group_id") or payload.get("user_id")
        try:
            self.store.update("notice:" + group + ":" + token,
                              lambda row: {**row, "delivery_state": "delivered"} if row else None,
                              7 * 86400)
        except StoreError:
            # LINE has already accepted it. Never resend just to update stats.
            self.app.logger.warning("[FactoryTools] delivery accepted; receipt status sync unavailable")

    def _signer(self):
        secret = self.h.get("LINE_CHANNEL_SECRET") or os.environ.get("LINE_CHANNEL_SECRET")
        if not secret:
            raise StoreError("尚未設定 LINE Channel Secret。")
        return URLSafeTimedSerializer(secret, salt="line-factory-member-session-v1")

    def session_url(self, group, uid, context=""):
        ticket = self._signer().dumps({"group_id": group, "user_id": uid, "context": context})
        params = urllib.parse.urlencode({"view": "factory", "session": ticket})
        liff_id = self.h.get("LIFF_ID", "").strip()
        if liff_id:
            return "https://liff.line.me/" + liff_id + "?" + params
        base = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
        if not base.startswith("https://"):
            raise StoreError("請先設定 LIFF_ID，或 HTTPS 的 PUBLIC_URL。")
        return base + "/liff/settings?" + params

    def member_session(self):
        token = request.headers.get("X-Factory-Session", "")
        try:
            data = self._signer().loads(token, max_age=600)
            if not isinstance(data, dict) or not data.get("group_id") or not data.get("user_id"):
                raise BadSignature("missing actor")
            return data
        except BadSignature as exc:
            raise PermissionError("連結已過期，請回 LINE 按「工廠工具」。 / Tautan kedaluwarsa; buka kembali dari LINE.") from exc

    def _reply(self, event, text, *, url=None):
        from linebot.v3.messaging import URIAction
        group, _ = source_ids(event)
        msg = TextMessage(text=text)
        if url:
            msg.quick_reply = QuickReply(items=[QuickReplyItem(action=URIAction(label="開啟/Buka", uri=url))])
        self.h["_send_reply_with_push_fallback"](
            reply_token=field(event, "reply_token"), target_id=group, message_obj=msg,
            fallback_text=text, retry_key="factory-control:" + str(field(event, "webhook_event_id") or field(event, "reply_token")))

    def command(self, event):
        text = str(field(field(event, "message", {}), "text", "") or "").strip()
        if text.casefold() not in {"/factory", "/工廠", "/qr", "/掃碼"}:
            return False
        group, uid = source_ids(event)
        if not uid:
            self._reply(event, "無法辨識 LINE 使用者。 / Pengguna LINE tidak teridentifikasi.")
            return True
        url = self.session_url(group, uid)
        self._reply(event, "🏭 工廠工具 / Alat pabrik\n掃描站別、查看作業說明與雙語翻譯。\n"
                          "Pindai stasiun, lihat petunjuk, dan terjemahkan.\n" + url, url=url)
        return True

    def postback(self, event, params):
        action = params.get("action", "")
        if not action.startswith("factory_"):
            return False
        group, uid = source_ids(event)
        token = params.get("token", "")
        context = self.get_context(token, group) if token else None
        if action != "factory_open" and not context:
            self._reply(event, "原文已更新、收回或操作已過期，請使用最新翻譯。\nGunakan terjemahan terbaru; teks telah berubah atau tautan kedaluwarsa.")
            return True
        if action in {"factory_open", "factory_share"}:
            if action == "factory_share" and not self.options(group)["sharing"]:
                self._reply(event, "此群組已關閉分享。 / Berbagi dinonaktifkan.")
                return True
            url = self.session_url(group, uid, token if context else "")
            self._reply(event, ("📤 預覽並分享 / Pratinjau dan bagikan\n" if action == "factory_share"
                               else "🏭 工廠工具 / Alat pabrik\n") + url, url=url)
            return True
        notice_key = "notice:" + group + ":" + token
        notice = self.store.get(notice_key)
        if not notice:
            self._reply(event, "找不到這筆作業確認。 / Catatan konfirmasi tidak ditemukan.")
            return True
        if action in {"factory_ack", "factory_help"}:
            if self.options(group)["acknowledgements"] == "off":
                self._reply(event, "此群組已關閉作業確認。 / Konfirmasi dinonaktifkan.")
                return True
            if not uid:
                raise PermissionError("無法辨識確認者。")
            state = "understood" if action == "factory_ack" else "needs_help"
            timestamp = int(field(event, "timestamp", 0) or 0)
            name = self.h.get("group_user_names", {}).get(group, {}).get(uid, "成員 / Anggota")
            def record_reply(row):
                if not row:
                    raise ValueError("作業確認已過期。")
                responses = row.setdefault("responses", {})
                row["delivery_state"] = "delivered"  # The signed button proves receipt.
                previous = responses.get(uid)
                if not previous or timestamp >= previous.get("event_timestamp", 0):
                    responses[uid] = {"status": state, "name": str(name), "at": time.time(),
                                      "event_timestamp": timestamp}
                return row
            notice = self.store.update(notice_key, record_reply, 7 * 86400)
            saved_state = notice["responses"][uid]["status"]
            self._reply(event, ("✅ 已記錄：已了解。 / Dicatat: sudah paham." if saved_state == "understood"
                               else "❓ 已記錄：需要說明。 / Dicatat: perlu penjelasan."))
        elif action == "factory_receipts":
            responses = notice.get("responses", {})
            understood = [x["name"] for x in responses.values() if x["status"] == "understood"]
            help_names = [x["name"] for x in responses.values() if x["status"] == "needs_help"]
            pending = [name for uid, name in notice.get("expected", {}).items() if uid not in responses]
            self._reply(event, "📋 作業確認 / Konfirmasi\n✅ 了解/Paham: " + ("、".join(understood) or "—") +
                        "\n❓ 需說明/Perlu penjelasan: " + ("、".join(help_names) or "—") +
                        "\n⏳ 已知成員未回覆/Belum menjawab: " + ("、".join(pending) or "—") +
                        "\n僅記錄主動按鈕回覆；成員清單依已知發言者。\nHanya konfirmasi tombol; daftar berdasarkan anggota yang dikenal bot.")
        else:
            raise ValueError("未知的工廠工具操作。")
        return True

    def station_catalog(self, group):
        result = {}

        def add(code, zh, idn, context=""):
            code = str(code)
            if _CODE.fullmatch(code):
                result[code.casefold()] = {"code": code, "name_zh": zh, "name_id": idn,
                                          "context": context[:1200], "sop_zh": "", "sop_id": "", "form_id": ""}

        # Legacy custom glossaries may contain codes; the production equipment
        # and station assets are separate from the phrase glossary.
        try:
            glossary = json.loads(self.h.get("_GLOSSARY_JSON", "{}"))
            for code, item in glossary.items():
                if re.fullmatch(r"[A-Z]{1,4}\d{1,3}", code) and isinstance(item, dict):
                    add(code, code, str(item.get("idn") or code), str(item.get("note_zh") or ""))
        except (TypeError, ValueError):
            pass

        stations = {}
        for name, item in self.h.get("STATION_NAMES", {}).items():
            number = item.get("no")
            context = (f"站別 {number}。" if number is not None else "") + f"所屬部門：{item.get('dept', '')}。"
            add(name, name, str(item.get("id") or name), context)
            if number is not None:
                stations.setdefault(str(number), []).append(f"{name}／{item.get('id', '')}（{item.get('dept', '')}）")

        for code, item in self.h.get("STATION_CODES", {}).items():
            number = item.get("station")
            context = f"設備代碼 {code}。" + (f"站別 {number}。" if number is not None else "")
            add(code, str(item.get("zh") or code), str(item.get("id") or code), context)
            if number is not None and str(number) not in stations:
                stations[str(number)] = []
            # The original asset explicitly documents these reused identifiers.
            # A bare code cannot determine the process; a group override can.
            if code in {"I5", "I15"}:
                add(code, f"{code} 機台（需確認製程）", f"Mesin {code} (konfirmasi proses)",
                    f"{code} 在既有資料同時用於拋光（452）與研磨（453）。單看代碼不能判定製程，"
                    "需依原文或群組確認；不得自行把研磨換成拋光，也不得把拋光換成研磨。")

        for number, names in stations.items():
            # Some numbers cover several operations (e.g. 490). Preserve the
            # alternatives rather than silently choosing the first dictionary row.
            if not names:
                names = [f"{code}／{item.get('zh', '')}／{item.get('id', '')}"
                         for code, item in self.h.get("STATION_CODES", {}).items()
                         if str(item.get("station")) == number]
            add(number, f"{number} 站", f"Stasiun {number}",
                f"站別 {number}。既有對照：" + "；".join(names) + "。同號若有多個製程，依原文或群組確認。")

        add("PMI", "PMI 鋼種檢驗", "Pemeriksaan PMI",
            "PMI 是檢驗鋼種的行為流程，不是列印標籤或檢查儀器本身。")
        for item in sorted(self.settings().get("stations", []), key=lambda row: bool(row.get("group_id"))):
            if item.get("group_id", "") in {"", group}:
                result[item["code"].casefold()] = {k: v for k, v in item.items() if k != "group_id"}
        return sorted(result.values(), key=lambda x: x["code"])

    def resolve_station(self, value, group):
        raw = str(value or "").strip()
        if raw.startswith("line-factory:"):
            raw = raw[len("line-factory:"):]
        # QR values are identifiers, never URLs to fetch or executable commands.
        if not _CODE.fullmatch(raw):
            raise ValueError("請掃描此機器人產生的站別 QR Code，或輸入設備代碼。")
        for station in self.station_catalog(group):
            if station["code"].casefold() == raw.casefold():
                return station
        raise ValueError("找不到設備／站別，請由管理員建立對照。")

    def readiness(self):
        from linebot.v3 import webhooks
        from linebot.v3 import messaging
        try:
            self.store.get("health")
            storage = self.store.kind
            path = self.store.path or ""
            persistent = storage == "upstash" or path.startswith(("/var/data/", "/data/"))
            store_error = "" if persistent else "目前使用本機儲存；Render 免費主機重新部署可能清除互動紀錄。"
        except Exception:
            storage, persistent, store_error = "unavailable", False, "工廠工具儲存無法連線。"
        return {"build": BUILD_ID, "liff_configured": bool(self.h.get("LIFF_ID")), "storage": storage,
                "persistent": persistent, "storage_message": store_error,
                "edit_event_supported": hasattr(webhooks, "MessageEditedEvent"),
                "native_mentions_supported": hasattr(messaging, "TextMessageV2"),
                "form_identity_configured": bool(os.environ.get("LINE_LOGIN_CHANNEL_ID", "").strip()),
                "scan_share_console_status": "needs_client_check",
                "provider": self.h["ai_provider"].get_provider_diagnostics()}

    def update_settings(self, data):
        if not isinstance(data, dict):
            raise ValueError("設定格式不正確。")
        previous = copy.deepcopy(self.settings())
        if data.get("expected_version") != self.settings_version():
            raise ValueError("設定已變更，請重新整理後再修改，避免覆蓋其他管理員的更新。")
        result = copy.deepcopy(previous)
        group = data.get("group_id", "")
        if "options" in data:
            groups = self.h["_reminder_catalog"]()
            if group not in groups:
                raise ValueError("請選擇已加入的群組。")
            supplied = data["options"]
            if not isinstance(supplied, dict) or set(supplied) - set(DEFAULTS):
                raise ValueError("功能設定包含未知欄位。")
            options = {**self.options(group), **supplied}
            if options["translation_mode"] not in {"all", "mentioned"} or options["acknowledgements"] not in {"off", "work", "all"}:
                raise ValueError("翻譯模式或確認模式不正確。")
            for key in ("edit_translation", "native_mentions", "sharing", "station_tools"):
                if type(options[key]) is not bool:
                    raise ValueError("開關值必須是布林值。")
            result.setdefault("groups", {})[group] = options
        if "stations" in data:
            items = data["stations"]
            if not isinstance(items, list) or len(items) > 500:
                raise ValueError("設備清單最多 500 筆。")
            clean, seen = [], set()
            known_groups = self.h["_reminder_catalog"]()
            for item in items:
                if not isinstance(item, dict):
                    raise ValueError("設備資料格式不正確。")
                row = {key: str(item.get(key) or "").strip() for key in
                       ("code", "group_id", "name_zh", "name_id", "context", "sop_zh", "sop_id", "form_id")}
                if not _CODE.fullmatch(row["code"]) or not row["name_zh"] or not row["name_id"]:
                    raise ValueError("設備需填代碼與中文、印尼文名稱。")
                if row["group_id"] and row["group_id"] not in known_groups:
                    raise ValueError("設備指定的群組不存在。")
                if row["form_id"]:
                    form = self.h.get("forms_data", {}).get(row["form_id"])
                    if not form or form.get("status") != "active":
                        raise ValueError("設備連結的表單不存在或已關閉。")
                    targets = form.get("target_groups") or []
                    if targets and (not row["group_id"] or row["group_id"] not in targets):
                        raise ValueError("表單的開放群組與設備所屬群組不符。")
                identity = (row["group_id"], row["code"].casefold())
                if identity in seen:
                    raise ValueError("同一群組的設備代碼不能重複。")
                if any(len(row[k]) > limit for k, limit in (("name_zh", 100), ("name_id", 200), ("context", 1200),
                                                            ("sop_zh", 5000), ("sop_id", 5000), ("form_id", 80))):
                    raise ValueError("設備名稱或作業說明過長。")
                seen.add(identity)
                clean.append(row)
            result["stations"] = clean
        self.h["factory_line_settings"] = result
        if not self.h["save_settings"](force=True):
            self.h["factory_line_settings"] = previous
            raise StoreError("設定未確認持久儲存，已保留原設定。")
        return result

    def settings_version(self):
        return hashlib.sha256(encode(self.settings()).encode()).hexdigest()[:24]

    def insight(self, menu, start, end, mode="summary"):
        if not re.fullmatch(r"richmenu-[0-9a-f]{32}", menu):
            raise ValueError("圖文選單 ID 不正確。")
        if mode not in {"summary", "daily"}:
            raise ValueError("統計模式不正確。")
        try:
            first, last = (datetime.strptime(value, "%Y%m%d").date() for value in (start, end))
        except (TypeError, ValueError) as exc:
            raise ValueError("請輸入統計起訖日期。") from exc
        today = datetime.now(timezone(timedelta(hours=9))).date()
        if first < today - timedelta(days=1096) or last < first or last > today or (last - first).days > (99 if mode == "daily" else 396):
            raise ValueError("日期超出 LINE 統計範圍：每日最多 100 天、彙總最多 397 天。")
        key = (menu, start, end, mode)
        with self._lock:
            cached = self._insight_cache.get(key)
            if cached and cached[0] > time.time():
                return {**cached[1], "cached": True}
            # Official endpoints are limited to 60 calls/hour, per endpoint.
            count_key = "insight-rate:" + mode
            def consume(previous):
                now = time.time()
                calls = [stamp for stamp in (previous or {}).get("calls", []) if stamp > now - 3600]
                if len(calls) >= 55:
                    raise ValueError("本小時查詢次數已接近 LINE 上限，請稍後再查。")
                return {"calls": calls + [now]}
            self.store.update(count_key, consume, 7200)
            query = urllib.parse.urlencode({"from": start, "to": end})
            url = "https://api.line.me/v2/bot/insight/richmenu/" + menu + "/" + mode + "?" + query
            req = urllib.request.Request(url, headers={"Authorization": "Bearer " + os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")})
            try:
                with urllib.request.urlopen(req, timeout=12) as response:
                    data = json.loads(response.read(2_000_000))
            except urllib.error.HTTPError as exc:
                messages = {401: "LINE 權杖無效。", 403: "此帳號無權查詢選單統計。", 404: "找不到這個圖文選單。", 429: "LINE 查詢次數已達上限。"}
                raise ValueError(messages.get(exc.code, "LINE 暫時無法提供統計資料。")) from exc
            result = {"data": data, "timezone": "Asia/Tokyo", "cached": False,
                      "privacy_limited": not any(key in data for key in ("impression", "clicks")),
                      "note": "LINE 以 UTC+9 統計，通常次日完成；不足 20 位點擊使用者時可能不提供數據。"}
            if len(self._insight_cache) > 100:
                self._insight_cache.clear()
            self._insight_cache[key] = (time.time() + 600, result)
            return result

    def register_routes(self):
        def protected(member=False):
            from functools import wraps
            def decorate(fn):
                @wraps(fn)
                def run(*args, **kwargs):
                    try:
                        if request.content_length and request.content_length > 2_000_000:
                            return jsonify(ok=False, message="資料過大。"), 413
                        if member:
                            actor = self.member_session()
                        else:
                            if not self.h["check_manager_access"]("factory"):
                                raise PermissionError("請先以有工廠工具權限的管理員登入。")
                            actor = None
                        return fn(actor, *args, **kwargs)
                    except PermissionError as exc:
                        return jsonify(ok=False, message=str(exc)), 403
                    except ValueError as exc:
                        return jsonify(ok=False, message=str(exc)), 400
                    except SupersededMessage:
                        return jsonify(ok=False, message="原文已更新或收回，請使用最新翻譯。"), 409
                    except Exception:
                        self.app.logger.exception("[FactoryTools] request failed")
                        return jsonify(ok=False, message="操作未確認成功，請重新整理後再試。 / Muat ulang lalu coba lagi."), 503
                return run
            return decorate

        @self.app.route("/api/admin/factory", methods=["GET", "PUT"])
        @protected()
        def factory_admin(_):
            if request.method == "PUT":
                with self.h["_state_lock"]:
                    self.update_settings(request.get_json(silent=True))
            groups = self.h["_reminder_catalog"]()
            return jsonify(ok=True, settings=self.settings(), defaults=DEFAULTS,
                           settings_version=self.settings_version(),
                           forms=[{"id": fid, "title": f.get("title_zh", fid)} for fid, f in self.h.get("forms_data", {}).items() if f.get("status") == "active"],
                           groups=[{"id": gid, "name": data["name"]} for gid, data in groups.items()],
                           readiness=self.readiness())

        @self.app.route("/api/admin/factory/stations")
        @protected()
        def factory_stations(_):
            group = request.args.get("group_id", "")
            return jsonify(ok=True, stations=self.station_catalog(group))

        @self.app.route("/api/admin/factory/qr")
        @protected()
        def factory_qr(_):
            from io import BytesIO
            import qrcode
            station = self.resolve_station(request.args.get("code"), request.args.get("group_id", ""))
            output = BytesIO()
            qrcode.make("line-factory:" + station["code"]).save(output, format="PNG")
            response = self.app.response_class(output.getvalue(), mimetype="image/png")
            response.headers["Cache-Control"] = "no-store"
            return response

        @self.app.route("/api/admin/factory/receipts")
        @protected()
        def factory_receipts(_):
            group = request.args.get("group_id", "")
            if group not in self.h["_reminder_catalog"]():
                raise ValueError("請選擇群組。")
            rows = self.store.recent("notice:" + group, 100)
            for row in rows:
                row["current"] = self.current(row.get("factory_event"))
            return jsonify(ok=True, notices=rows)

        @self.app.route("/api/admin/factory/insight")
        @protected()
        def factory_insight(_):
            return jsonify(ok=True, **self.insight(request.args.get("menu_id", ""), request.args.get("from", ""),
                                                   request.args.get("to", ""), request.args.get("mode", "summary")))

        @self.app.route("/api/factory/session")
        @protected(member=True)
        def factory_session(actor):
            group = actor["group_id"]
            context = self.get_context(actor.get("context", ""), group) if actor.get("context") else None
            if actor.get("context") and not context:
                raise ValueError("原文已更新、收回或操作已過期，請回 LINE 開啟最新翻譯。")
            return jsonify(ok=True, liff_id=self.h.get("LIFF_ID", ""), options=self.options(group),
                           group_name=self.h.get("group_tracking", {}).get(group, {}).get("name", "工廠 / Pabrik"),
                           stations=self.station_catalog(group), context=context)

        @self.app.route("/api/factory/station", methods=["POST"])
        @protected(member=True)
        def factory_station(actor):
            if not self.options(actor["group_id"])["station_tools"]:
                raise PermissionError("此群組已關閉站別工具。")
            data = request.get_json(silent=True) or {}
            return jsonify(ok=True, station=self.resolve_station(data.get("value"), actor["group_id"]))

        @self.app.route("/api/factory/translate", methods=["POST"])
        @protected(member=True)
        def factory_translate(actor):
            group, uid = actor["group_id"], actor["user_id"]
            if not self.options(group)["station_tools"]:
                raise PermissionError("此群組已關閉站別工具。")
            data = request.get_json(silent=True) or {}
            text = str(data.get("text") or "").strip()
            if not text or len(text) > 5000:
                raise ValueError("請輸入 1～5000 字的翻譯內容。")
            station = self.resolve_station(data.get("station"), group)
            src, tgt = data.get("src", "zh"), data.get("tgt", "id")
            if src not in self.h["VALID_TARGETS"] or tgt not in self.h["VALID_TARGETS"] or src == tgt:
                raise ValueError("請選擇不同的有效來源與目標語言。")
            fingerprint = self.h["_translation_cache_asset_fingerprint"]()
            digest = hashlib.sha256(encode([group, uid, station, text, src, tgt, fingerprint]).encode()).hexdigest()
            key = "station-result:" + digest
            # Serialize identical work locally, then cache it across restarts.
            with self.h["serialize_request"](("factory-station", digest)):
                cached = self.store.get(key)
                if cached:
                    return jsonify(ok=True, **cached, cached=True)
                def consume(previous):
                    now = time.time()
                    calls = [stamp for stamp in (previous or {}).get("calls", []) if stamp > now - 60]
                    if len(calls) >= 20:
                        raise ValueError("操作頻繁，請稍後再試。 / Terlalu banyak permintaan; coba sebentar lagi.")
                    return {"calls": calls + [now]}
                self.store.update("station-rate:" + uid, consume, 120)
                marker = _STATION.set(station)
                try:
                    with self.h["_translation_job_scope"]():
                        self.h["_tl"].group_id, self.h["_tl"].user_id = group, uid
                        result = self.h["translate"](text, src, tgt)
                finally:
                    _STATION.reset(marker)
                if not result or self.h["_is_translation_failure_sentinel"](result):
                    return jsonify(ok=False, message="未取得完整翻譯，請稍後重試。 / Terjemahan belum tersedia."), 503
                response = {"original": text, "translated": result, "src": src, "tgt": tgt}
                self.store.put(key, response, 600)
                return jsonify(ok=True, **response, cached=False)

        @self.app.route("/api/factory/share")
        @protected(member=True)
        def factory_share(actor):
            if not self.options(actor["group_id"])["sharing"]:
                raise PermissionError("此群組已關閉分享。")
            context = self.get_context(actor.get("context", ""), actor["group_id"])
            if not context:
                raise ValueError("原文已更新或收回，無法分享舊版。")
            text = str(context["original"]) + "\n\n" + str(context["translated"])
            chunks = delivery.split_text(text)
            if len(chunks) > 5:
                return jsonify(ok=True, messages=[], copy_text=text, too_long=True)
            return jsonify(ok=True, messages=[{"type": "text", "text": chunk} for chunk in chunks],
                           copy_text=text, too_long=False)


def install(app, host, store=None):
    hub = FactoryHub(app, host, store)
    hub.register_routes()
    app.extensions["line_factory"] = hub
    # Explicit registration: edited text has its own event class and identity.
    try:
        from linebot.v3.webhooks import MessageEditedEvent
        host["handler"].add(MessageEditedEvent)(host["handle_message"])
    except ImportError:
        app.logger.error("[FactoryTools] SDK lacks MessageEditedEvent; install line-bot-sdk>=3.25.0")
    return hub


def factory_page(app, liff_id):
    response = app.make_response(render_template("line_factory.html", liff_id=liff_id))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def entry_page(app, liff_id):
    response = app.make_response(render_template("liff_entry.html", liff_id=liff_id))
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
