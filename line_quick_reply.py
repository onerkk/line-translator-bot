"""One persisted menu for LINE translations and the admin Quick Reply editor.

Legacy settings are read only until the first unified document is saved. Menu
composition is local: no model request, profile lookup or storage read is added.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import threading
from urllib.parse import urlencode, urlsplit

from flask import jsonify, request
from linebot.v3.messaging import QuickReply

KEY = "quick_reply_menu_settings"
SCHEMA = 1
NOTICE_ACTIONS = {"factory_ack", "factory_help", "factory_receipts"}
WORK = re.compile(r"PMI|檢[驗測查]|检[验测查]|生產|生产|產量|设备|設備|班[別次]|交[接班]|入[庫帐帳]|出[貨库庫]|包裝|包装|秤[重料]|標[籤签]|工[單单]|\b(?:produksi|periksa|pemeriksaan|shift|mesin|timbang|gudang|label)\b", re.I)
BUILTINS = {
    "natural": "✨ 自然/Alami", "literal": "🔎 直譯/Harfiah",
    "formal": "📢 正式/Formal", "backcheck": "↩ 回譯/Cek balik",
    "personal": "👤 我的語言/Bahasa", "handover": "📋 交班摘要/Serah",
    "interpreter": "🎙 即時口譯/Interpret", "factory_share": "📤 分享/Bagikan",
    "factory_open": "🏭 工具/Alat", "factory_ack": "✅ 了解/Paham",
    "factory_help": "❓ 說明/Jelaskan", "factory_receipts": "查看回覆/Status",
    "overlay": "🖼 圖文對照/Gambar", "context_qry": "📋 查此工單/Gudang",
    "tts_replay": "🔊 重播/Ulang",
}
TYPES = {"message", "camera", "camera_roll", "location", "clipboard", "uri", "builtin", "external_link"}
REMOVED_FEATURES = {"quick_reply_enabled", "camera_qr_enabled", "camera_roll_qr_enabled",
                    "clipboard_qr_enabled", "location_qr_enabled", "image_translation_actions_enabled",
                    "image_translation_action_modes"}


def short(text, units=20):
    return str(text or "").encode("utf-16-le")[:units * 2].decode("utf-16-le", errors="ignore")


def clean_profile(raw):
    if not isinstance(raw, dict) or type(raw.get("enabled")) is not bool:
        raise ValueError("請設定快捷選單總開關。")
    mode = raw.get("acknowledgements", "command")
    if mode in {"work", "all"}:
        mode = "command"  # Migrate automatic cards without resetting button choices.
    if mode not in {"off", "command"}:
        raise ValueError("作業確認模式不正確。")
    if not isinstance(raw.get("items"), list) or len(raw["items"]) > 60:
        raise ValueError("快捷鍵清單最多 60 筆。")
    items, seen, actions = [], set(), set()
    for item in raw["items"]:
        if not isinstance(item, dict):
            raise ValueError("按鈕資料格式不正確。")
        row = {key: str(item.get(key) or "").strip() for key in
               ("id", "type", "label", "text", "clipboard_text", "uri", "action", "link_key", "cmd_check")}
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", row["id"]) or row["id"] in seen:
            raise ValueError("按鈕 ID 不正確或重複。")
        if row["type"] not in TYPES or not row["label"] or len(row["label"].encode("utf-16-le")) > 40:
            raise ValueError("按鈕需填名稱，最長 20 個 LINE 字元（表情符號可能占 2 個）。")
        if type(item.get("enabled")) is not bool:
            raise ValueError("按鈕開關值不正確。")
        kinds = item.get("contexts", ["text", "image"])
        if not isinstance(kinds, list) or any(k not in {"text", "image"} for k in kinds):
            raise ValueError("請選擇按鈕適用的文字或圖片翻譯。")
        if item["enabled"] and not kinds:
            raise ValueError("啟用的按鈕至少需勾選一種翻譯情境。")
        if row["type"] == "builtin":
            if row["action"] not in BUILTINS or row["action"] in actions:
                raise ValueError("內建功能不正確或重複。")
            actions.add(row["action"])
        if row["type"] == "message" and (not row["text"] or len(row["text"]) > 300):
            raise ValueError("訊息按鈕需填 1～300 字的送出文字。")
        if row["type"] == "clipboard" and (not row["clipboard_text"] or len(row["clipboard_text"]) > 1000):
            raise ValueError("複製按鈕需填 1～1000 字的內容。")
        if row["type"] == "uri" and (urlsplit(row["uri"]).scheme != "https" or not urlsplit(row["uri"]).netloc or len(row["uri"]) > 1000):
            raise ValueError("網址按鈕需填完整的 HTTPS 網址。")
        if row["type"] == "external_link" and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", row["link_key"]):
            raise ValueError("請選擇外連項目。")
        row.update(enabled=item["enabled"], contexts=list(dict.fromkeys(kinds)))
        seen.add(row["id"])
        items.append(row)
    return {"enabled": raw["enabled"], "acknowledgements": mode, "items": items}


class Menu:
    def __init__(self, host):
        self.h = host
        self.lock = host.get("_state_lock") or threading.RLock()

    def _legacy_profile(self, group=""):
        h = self.h
        def setting(name, overrides, fallback):
            return h.get(overrides, {}).get(group, h.get(name, fallback)) if group else h.get(name, fallback)
        factory = (h.get("factory_line_settings") or {}).get("groups", {}).get(group, {})
        image_on = setting("image_translation_actions_enabled", "group_image_translation_actions_settings", True)
        modes = {**h.get("image_translation_action_modes", {}), **h.get("group_image_translation_action_modes", {}).get(group, {})}
        old = copy.deepcopy(h.get("quick_reply_items_settings", []))
        old_by_id = {x.get("id"): x for x in old if isinstance(x, dict)}
        items = []
        for action, label in BUILTINS.items():
            contexts = ["image"] if action == "overlay" else ["text", "image"]
            enabled = action != "factory_help"
            if action in {"natural", "literal", "formal", "backcheck", "personal", "overlay"}:
                if not image_on or not modes.get(action, True):
                    contexts = [k for k in contexts if k != "image"]
            if action in {"handover", "interpreter"} and action in old_by_id:
                enabled = bool(old_by_id[action].get("enabled", True))
                previous_label = old_by_id[action].get("label")
                original_labels = {h.get("_CORE_BILINGUAL_QR_LABELS", {}).get(action),
                                   *[d.get("label") for d in h.get("QUICK_REPLY_DEFAULTS", []) if d.get("id") == action]}
                if previous_label and previous_label not in original_labels:
                    label = previous_label
            if action == "factory_share":
                enabled = factory.get("sharing", True)
            if action == "factory_open":
                enabled = factory.get("station_tools", True)
            if action in {"context_qry", "tts_replay"}:
                enabled = bool(h.get("group_flex_v2", {}).get(group, {}).get("buttons", h.get("_V2_DEFAULTS", {}).get("buttons", True)))
            items.append({"id": action if action in {"handover", "interpreter"} else "builtin_" + action,
                          "type": "builtin", "action": action, "label": short(label),
                          "enabled": bool(enabled and contexts), "contexts": contexts})
        seen = {x["id"] for x in items}
        for row in old:
            if not isinstance(row, dict) or row.get("id") in seen:
                continue
            row["label"] = short(row.get("label") or row.get("id"))
            row["contexts"] = ["text", "image"]
            row["enabled"] = bool(row.get("enabled", True))
            # Old per-group camera overrides used to have no effect on menus.
            overrides = h.get("group_" + str(row.get("id")) + "_qr_settings", {})
            if group in overrides:
                row["enabled"] = bool(overrides[group])
            seen.add(row.get("id"))
            items.append(row)
        for key, link in h.get("external_links_settings", {}).items():
            label = "/".join(filter(None, [link.get("label_zh"), link.get("label_id")])) or key
            items.append({"id": "link_" + key, "type": "external_link", "link_key": key,
                          "label": short(label), "enabled": bool(link.get("enabled", True) and link.get("url")),
                          "contexts": ["text", "image"]})
        return clean_profile({"enabled": bool(setting("quick_reply_enabled", "group_qr_settings", True)),
                              "acknowledgements": factory.get("acknowledgements", "work"), "items": items[:60]})

    def document(self):
        saved = self.h.get(KEY)
        if isinstance(saved, dict) and saved.get("schema") == SCHEMA:
            saved = copy.deepcopy(saved)
            for profile in [saved["default"], *saved.get("groups", {}).values()]:
                if profile.get("acknowledgements", "work") in {"work", "all"}:
                    profile["acknowledgements"] = "command"
            return saved
        groups = set((self.h.get("factory_line_settings") or {}).get("groups", {}))
        for name in ("group_qr_settings", "group_image_translation_actions_settings", "group_image_translation_action_modes",
                     "group_camera_qr_settings", "group_clipboard_qr_settings", "group_camera_roll_qr_settings", "group_location_qr_settings", "group_flex_v2"):
            groups.update(self.h.get(name, {}))
        default = self._legacy_profile()
        overrides = {gid: self._legacy_profile(gid) for gid in groups}
        return {"schema": SCHEMA, "default": default,
                "groups": {gid: value for gid, value in overrides.items() if value != default}}

    def profile(self, group=""):
        saved = self.h.get(KEY)
        if not isinstance(saved, dict) or saved.get("schema") != SCHEMA:
            return self._legacy_profile(group or "")
        saved = self.document()
        return saved.get("groups", {}).get(group) or saved["default"]

    def version(self):
        return hashlib.sha256(json.dumps(self.document(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]

    def enabled_action(self, group, action, kind=None):
        profile = self.profile(group)
        return profile["enabled"] and any(r["enabled"] and r["type"] == "builtin" and r["action"] == action
                                          and (kind is None or kind in r["contexts"]) for r in profile["items"])

    def factory_options(self, group):
        profile = self.profile(group)
        return {"sharing": self.enabled_action(group, "factory_share"),
                "station_tools": self.enabled_action(group, "factory_open"),
                "acknowledgements": profile["acknowledgements"] if any(
                    row["enabled"] and row["type"] == "builtin" and row["action"] in NOTICE_ACTIONS
                    for row in profile["items"]) else "off"}

    def notice_rows(self, group, original, kind="text", profile=None, *, requested=False):
        profile = profile or self.profile(group)
        if not str(group).startswith(("C", "R")) or profile["acknowledgements"] == "off":
            return []
        if not requested:
            return []
        return [r for r in profile["items"] if r["enabled"] and r["type"] == "builtin" and r["action"] in NOTICE_ACTIONS and kind in r["contexts"]]

    def needs_context(self, group, kind="text"):
        return any(self.enabled_action(group, a, kind) for a in BUILTINS
                   if a not in {"handover", "interpreter", "context_qry", "tts_replay"} | NOTICE_ACTIONS)

    def actions(self, group, record=None, token=None, kind="text", profile=None, preview=False):
        profile = profile or self.profile(group)
        if not profile["enabled"]:
            return []
        record = record or {}
        result, seen = [], set()
        notice_allowed = {r["action"] for r in self.notice_rows(group, record.get("original"), kind, profile,
                                                              requested=bool(record.get("notice_requested")))}
        for row in profile["items"]:
            if not row["enabled"] or kind not in row["contexts"]:
                continue
            command = row.get("cmd_check")
            if command and not self.h.get("is_cmd_enabled", lambda *_: True)(group, command):
                continue
            label, typ = row["label"], row["type"]
            action = None
            if typ == "builtin":
                name = row["action"]
                if preview:
                    if name in NOTICE_ACTIONS and profile["acknowledgements"] == "off":
                        continue
                    action = {"type": "postback", "label": label, "data": "preview=" + name}
                elif name in {"handover", "interpreter"}:
                    data = {"action": "handover_summary" if name == "handover" else "open_interpreter"}
                    if token:
                        data["token"] = token
                    action = {"type": "postback", "label": label, "data": urlencode(data), "displayText": label}
                elif token or name in {"context_qry", "tts_replay"}:
                    data = {"token": token}
                    if name in {"natural", "literal", "formal", "backcheck"}:
                        data.update(action="translation_variant", mode=name)
                    elif name == "personal":
                        data["action"] = "show_language_menu"
                    elif name == "overlay":
                        if not record.get("menu_overlay_token"):
                            continue
                        data.update(action="image_overlay", token=record["menu_overlay_token"], context_token=token)
                    elif name in NOTICE_ACTIONS:
                        if name not in notice_allowed:
                            continue
                        data["action"] = name
                    elif name == "context_qry":
                        match = re.search(r"[A-Z]{1,5}[-\s]?\d{3,8}|\b\d{5,10}\b", record.get("original", ""))
                        if not match or not self.h.get("is_cmd_enabled", lambda *_: True)(group, "qry"):
                            continue
                        data = {"action": "qry", "q": match.group(0)[:50]}
                    elif name == "tts_replay":
                        if not self.h.get("get_tts_enabled", lambda _: False)(group):
                            continue
                        text = record.get("translated", "")[:80]
                        data = {"action": "tts_replay", "lang": record.get("tgt", ""), "t": text}
                        while len(urlencode(data)) > 280 and data["t"]:
                            data["t"] = data["t"][:-1]
                    else:
                        data["action"] = name
                    action = {"type": "postback", "label": label, "data": urlencode(data)}
            elif typ == "message":
                action = {"type": "message", "label": label, "text": row["text"]}
            elif typ == "external_link":
                link = self.h.get("external_links_settings", {}).get(row["link_key"], {})
                if link.get("enabled", True) and link.get("url"):
                    action = {"type": "message", "label": label, "text": "/" + row["link_key"]}
            elif typ == "clipboard":
                action = {"type": "clipboard", "label": label, "clipboardText": row["clipboard_text"]}
            elif typ == "uri":
                action = {"type": "uri", "label": label, "uri": row["uri"]}
            elif typ in {"camera", "camera_roll", "location"}:
                action = {"type": "cameraRoll" if typ == "camera_roll" else typ, "label": label}
            if action:
                identity = json.dumps({k: v for k, v in action.items() if k not in {"label", "displayText"}}, sort_keys=True)
                if identity not in seen:
                    seen.add(identity)
                    result.append(action)
        return result

    def build(self, group, record=None, token=None, kind="text", page=0):
        actions = self.actions(group, record, token, kind)
        if not actions:
            return None
        if len(actions) > 13:
            pages = math.ceil(len(actions) / 12)
            page = max(0, int(page)) % pages
            actions = actions[page * 12:(page + 1) * 12]
            data = {"action": "quick_reply_page", "page": (page + 1) % pages, "kind": kind}
            if token:
                data["token"] = token
            actions.append({"type": "postback", "label": short(f"更多/Lain {page + 1}/{pages}"), "data": urlencode(data)})
        return QuickReply.from_dict({"items": [{"type": "action", "action": a} for a in actions]})

    def groups(self):
        catalog = self.h.get("_reminder_catalog", lambda: {})()
        return [{"id": gid, "name": data.get("name") or gid} for gid, data in catalog.items()]

    def snapshot(self, group=""):
        return {"ok": True, "group_id": group, "profile": copy.deepcopy(self.profile(group)),
                "customized": bool(group and group in self.document()["groups"]), "version": self.version(),
                "groups": self.groups(), "builtins": BUILTINS,
                "links": [{"key": key, "label": short("/".join(filter(None, [row.get("label_zh"), row.get("label_id")])) or key)}
                          for key, row in self.h.get("external_links_settings", {}).items()]}

    def save(self, group, raw, expected, reset=False):
        with self.lock:
            if expected != self.version():
                raise ValueError("設定已被更新，請重新載入後再儲存，避免覆蓋其他管理員。")
            if group and group not in {r["id"] for r in self.groups()}:
                raise ValueError("請選擇已加入的群組。")
            doc = copy.deepcopy(self.document())
            if reset and group:
                doc["groups"].pop(group, None)
            else:
                profile = clean_profile(raw)
                if group:
                    doc["groups"][group] = profile
                else:
                    doc["default"] = profile
            previous = self.h.get(KEY)
            self.h[KEY] = doc
            try:
                if not self.h["save_settings"](force=True):
                    raise RuntimeError("設定未儲存成功，請稍後重試。")
            except Exception:
                self.h[KEY] = previous
                raise
            return self.snapshot(group)

    def register(self, app):
        def access():
            return self.h["check_manager_access"]("quickreply")

        @app.route("/api/admin/quick-reply/list")
        def unified_menu_list():
            if not access():
                return jsonify(error="需要快捷鍵管理權限。"), 403
            return jsonify(self.snapshot(request.args.get("group_id", "")))

        @app.route("/api/admin/quick-reply/save", methods=["POST"])
        @app.route("/api/admin/quick-reply/reset", methods=["POST"])
        def unified_menu_save():
            if not access():
                return jsonify(error="需要快捷鍵管理權限。"), 403
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return jsonify(error="設定格式不正確。"), 400
            try:
                return jsonify(self.save(str(data.get("group_id") or ""), data.get("profile"), data.get("version"),
                                         reset=request.path.endswith("/reset")))
            except ValueError as exc:
                return jsonify(error=str(exc)), 400
            except Exception:
                app.logger.exception("[QuickReply] menu persistence failed")
                return jsonify(error="設定未確認儲存成功，請重新載入後再試。"), 503

        @app.route("/api/admin/quick-reply/preview", methods=["POST"])
        def unified_menu_preview():
            if not access():
                return jsonify(error="需要快捷鍵管理權限。"), 403
            data = request.get_json(silent=True) or {}
            try:
                profile = clean_profile(data.get("profile"))
                group = str(data.get("group_id") or "C" + "0" * 32)
                kind = "image" if data.get("kind") == "image" else "text"
                record = {"original": "PMI ABC123 入庫，請確認", "translated": "Periksa PMI ABC123 sebelum masuk gudang.",
                          "tgt": "id", "menu_overlay_token": "preview_image" if kind == "image" else None}
                if data.get("kind") == "ack":
                    rows = self.notice_rows(group, record["original"], "text", profile, requested=True)
                else:
                    rows = self.actions(group, record, "preview_context", kind, profile)
                size = 12 if len(rows) > 13 else 13
                return jsonify(ok=True, pages=[[r["label"] for r in rows[i:i + size]] for i in range(0, len(rows), size)],
                               count=len(rows))
            except (ValueError, TypeError, AttributeError) as exc:
                return jsonify(error=str(exc)), 400
