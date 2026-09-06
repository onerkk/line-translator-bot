"""Admin/cron integration for scheduled reminders; no live sends on save."""
from __future__ import annotations

from functools import wraps
import hmac
import os
import sqlite3
import threading
import time

from flask import jsonify, request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from scheduled_reminders import ReminderError, ReminderService, ReminderWorker, StoreUnavailable, configured_store


def issue_manager_token(user_id, secret):
    return URLSafeTimedSerializer(secret, salt="reminder-manager-v1").dumps({"user_id": user_id})


def verified_manager(token, secret):
    if not token or not secret:
        return None
    try:
        data = URLSafeTimedSerializer(secret, salt="reminder-manager-v1").loads(token, max_age=12 * 3600)
        return data.get("user_id") if isinstance(data, dict) else None
    except (BadSignature, ValueError, TypeError):
        return None


def _public(record):
    return {key: value for key, value in record.items()
            if key not in {"retry_key", "lease_token", "lease_until", "created_by"}}


def register_reminders(flask_app, *, authorize, catalog):
    cached_service = []
    service_lock = threading.Lock()
    service_pid = os.getpid()

    def service():
        nonlocal service_lock, service_pid
        if service_pid != os.getpid():
            # A preloaded Gunicorn parent may fork while its poller owns this
            # lock. Never inherit either a locked mutex or a service instance.
            service_pid = os.getpid()
            service_lock = threading.Lock()
            cached_service.clear()
        with service_lock:
            if not cached_service:
                cached_service.append(ReminderService(configured_store(), catalog))
            return cached_service[0]

    worker = ReminderWorker(service, flask_app.logger)
    # Exposed for deterministic tests and operational diagnostics, not an API.
    flask_app.extensions["scheduled_reminders"] = {"service": service, "worker": worker}

    def protected(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            actor = authorize()
            if not actor:
                return jsonify(ok=False, message="權限不足或登入已過期，請重新登入。"), 403
            try:
                if request.content_length is not None and request.content_length > 20000:
                    raise ReminderError("提醒資料過大。", 413)
                return func(actor, *args, **kwargs)
            except ReminderError as exc:
                return jsonify(ok=False, message=str(exc)), exc.status
            except (sqlite3.Error, OSError, ValueError, TypeError):
                flask_app.logger.exception("[Reminders] storage operation failed")
                return jsonify(ok=False, message="提醒資料無法讀寫，請重新整理確認；本次操作尚未確認成功。"), 503
        return wrapped

    @flask_app.route("/api/admin/reminders", methods=["GET", "POST"])
    @protected
    def admin_reminders(actor):
        if request.method == "POST":
            if not os.environ.get("LINE_CHANNEL_ACCESS_TOKEN"):
                raise StoreUnavailable("尚未設定 LINE 存取權杖。")
            row = service().create(request.get_json(silent=True), actor)
            worker.start()
            return jsonify(ok=True, reminder=_public(row)), 201
        try:
            offset = max(0, int(request.args.get("offset", "0")))
        except ValueError as exc:
            raise ReminderError("清單頁碼無效。") from exc
        groups = catalog()
        status = {"timezone": "Asia/Taipei", "server_time": time.time(),
                  "worker_enabled": os.environ.get("REMINDERS_WORKER_ENABLED", "1") != "0",
                  "cron_configured": len(os.environ.get("REMINDERS_CRON_SECRET", "")) >= 32,
                  "last_check_at": worker.last_check_at, "last_error": worker.last_error}
        try:
            instance = service()
            rows = instance.store.list(offset, 101)
            status.update(ready=bool(os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")), storage=instance.store.kind,
                          message="" if os.environ.get("LINE_CHANNEL_ACCESS_TOKEN") else "尚未設定 LINE 存取權杖。")
        except StoreUnavailable as exc:
            rows = []
            status.update(ready=False, storage="unavailable", message=str(exc))
        return jsonify(ok=True, reminders=[_public(row) for row in rows[:100]],
                       next_offset=offset + 100 if len(rows) > 100 else None, status=status,
                       groups=[dict(id=gid, name=data["name"], members=[
                           {"user_id": uid, "name": name} for uid, name in data["members"].items()])
                               for gid, data in groups.items()])

    @flask_app.route("/api/admin/reminders/<key>", methods=["PUT"])
    @protected
    def edit_reminder(actor, key):
        row = service().change(key, request.get_json(silent=True))
        return jsonify(ok=True, reminder=_public(row))

    @flask_app.route("/api/admin/reminders/<key>/cancel", methods=["POST"])
    @protected
    def cancel_reminder(actor, key):
        row = service().change(key, request.get_json(silent=True), cancel=True)
        return jsonify(ok=True, reminder=_public(row))

    @flask_app.route("/api/reminders/tick", methods=["POST"])
    def reminder_tick():
        expected = os.environ.get("REMINDERS_CRON_SECRET", "")
        if len(expected) < 32:
            return jsonify(ok=False, message="排程喚醒尚未設定。"), 503
        supplied = request.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode("utf-8"), ("Bearer " + expected).encode("utf-8")):
            return jsonify(ok=False, message="forbidden"), 403
        try:
            service()
        except (ReminderError, OSError, sqlite3.Error):
            return jsonify(ok=False, message="提醒儲存尚未就緒。"), 503
        worker.start(force=True)
        return jsonify(ok=True, message="已喚醒提醒排程。"), 202

    def maybe_start():
        if (os.environ.get("REMINDERS_DB_PATH") or
                (os.environ.get("UPSTASH_REDIS_REST_URL") and os.environ.get("UPSTASH_REDIS_REST_TOKEN"))):
            worker.start()

    # Gunicorn --preload forks after import. Start again in each serving process.
    flask_app.before_request(maybe_start)
    return maybe_start
