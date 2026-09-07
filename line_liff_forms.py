"""Server-verified LINE form identity and validation shared by both LIFF entries."""
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import os
import re
import urllib.parse
import urllib.request
import urllib.error

from flask import request


def _get_json(url, token=None):
    headers = {"Authorization": "Bearer " + token} if token else {}
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            return json.loads(response.read(100_000))
    except (OSError, ValueError) as exc:
        raise PermissionError("LINE 登入已失效，請關閉表單後重新開啟。 / Masuk kembali melalui LINE.") from exc


def actor(host):
    """Never accept client-supplied user IDs, names or decoded token claims."""
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else ""
    channel = os.environ.get("LINE_LOGIN_CHANNEL_ID", "").strip()
    if not channel:
        raise PermissionError("管理員需設定 LIFF 所屬的 LINE_LOGIN_CHANNEL_ID，才能驗證填表身分。")
    if not token or len(token) > 4096:
        raise PermissionError("請從 LINE 開啟表單並登入。 / Buka formulir melalui LINE.")
    info = _get_json("https://api.line.me/oauth2/v2.1/verify?" + urllib.parse.urlencode({"access_token": token}))
    if str(info.get("client_id", "")) != channel or float(info.get("expires_in", 0)) <= 0:
        raise PermissionError("LINE 登入驗證不符，請重新開啟此機器人的表單。")
    profile = _get_json("https://api.line.me/v2/profile", token)
    uid = profile.get("userId", "")
    if not re.fullmatch(r"U[0-9a-f]{32}", uid):
        raise PermissionError("LINE 未提供有效填表身分。")
    groups = {gid for gid, users in host.get("group_user_names", {}).items() if uid in users}
    if request.headers.get("X-Factory-Session"):
        session = host["factory_hub"].member_session()
        if session["user_id"] != uid:
            raise PermissionError("請使用自己在 LINE 開啟的工廠工具連結。")
        groups.add(session["group_id"])
    return {"user_id": uid, "user_name": str(profile.get("displayName", ""))[:200],
            "picture_url": str(profile.get("pictureUrl", ""))[:2048], "groups": groups}


def allowed(form, user):
    targets = set(form.get("target_groups") or [])
    # Group-scoped forms are available to members already observed by the bot,
    # or to a member who requested a signed factory link via a LINE webhook.
    return not targets or bool(targets & user["groups"])


def answers(form, body):
    if not isinstance(body, dict) or not isinstance(body.get("answers"), dict):
        raise ValueError("回答格式不正確。 / Format jawaban tidak valid.")
    supplied = body["answers"]
    fields = form.get("fields", [])
    if len(fields) > 100 or set(supplied) - {f["id"] for f in fields}:
        raise ValueError("表單欄位已更新，請重新開啟表單。")
    clean = {}
    for field in fields:
        key, kind = field["id"], field.get("type", "text")
        value = supplied.get(key, "")
        if not isinstance(value, str) or len(value) > 5000:
            raise ValueError("回答過長或格式不正確。")
        value = value.strip()
        label = str(field.get("label_zh") or key)
        if field.get("required") and (not value or (kind == "checkbox" and value != "yes")):
            raise ValueError("請填寫／勾選：" + label + " / Wajib diisi.")
        if value:
            if kind == "number":
                try:
                    if not Decimal(value).is_finite():
                        raise InvalidOperation
                except InvalidOperation as exc:
                    raise ValueError(label + "：請填有效數字。") from exc
            elif kind == "date":
                try:
                    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                        raise ValueError
                    date.fromisoformat(value)
                except ValueError as exc:
                    raise ValueError(label + "：日期不正確。") from exc
            elif kind == "select" and value not in {str(o.get("zh", "")) for o in field.get("options", [])}:
                raise ValueError(label + "：請選擇表單提供的選項。")
            elif kind == "checkbox" and value not in {"yes", "no"}:
                raise ValueError(label + "：勾選格式不正確。")
        clean[key] = value
    return clean
