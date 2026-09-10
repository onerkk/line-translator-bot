"""Signed image postbacks enforce ownership before consume, feedback or AI."""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
import hashlib
import hmac
import json
import threading
import time
from types import SimpleNamespace

import pytest
from linebot.v3.webhooks import PostbackEvent

import app
from conftest import wait_for_webhooks
from test_line_factory_features import GROUP, OTHER, USER, COLLEAGUE

ADMIN = "U" + "9" * 32
MID = "owner-image-001"


def postback(uid, *, group=GROUP, action="img_translate", eid="image-click"):
    return {"type": "postback", "mode": "active", "timestamp": int(time.time() * 1000),
            "webhookEventId": eid, "replyToken": "offline-" + eid,
            "deliveryContext": {"isRedelivery": False},
            "source": {"type": "group", "groupId": group, "userId": uid},
            "postback": {"data": action + "=" + MID}}


@pytest.fixture
def image_case(monkeypatch, tmp_path):
    state = SimpleNamespace(path=tmp_path / "pending-images.json", sends=[], jobs=[], background=[])
    monkeypatch.setattr(app, "_PENDING_IMG_FILE", str(state.path))
    monkeypatch.setattr(app, "admin_users", {ADMIN: {"is_admin": True, "allowed_tabs": []}})
    monkeypatch.setattr(app, "_BOOTSTRAP_ADMIN_IDS", set())
    monkeypatch.setattr(app, "_processed_msg_ids", app._collections_dedup.OrderedDict())
    monkeypatch.setattr(app, "ApiClient", lambda *a, **kw: nullcontext(None))
    monkeypatch.setattr(app, "MessagingApi", lambda *a: SimpleNamespace(reply_message=lambda req: state.sends.append(req)))
    def enqueue(ctx, **kw):
        state.jobs.append((dict(ctx), kw))
        return ctx["group_id"] + ":" + ctx["message_id"] + ":image"
    monkeypatch.setattr(app, "_schedule_image_translation_retry", enqueue)
    monkeypatch.setattr(app, "_handle_image_background", lambda ctx: state.background.append(dict(ctx)))
    monkeypatch.setattr(app, "_has_ai_capability", lambda kind: True)
    app._pending_img_set(MID, {"group_id": GROUP, "user_id": USER, "ts": int(time.time())})
    return state


def signed_click(raw):
    body = json.dumps({"destination": "U" + "f" * 32, "events": [raw]}).encode()
    secret = app.handler.parser.signature_validator.channel_secret
    if isinstance(secret, str):
        secret = secret.encode()
    signature = base64.b64encode(hmac.new(secret, body, hashlib.sha256).digest()).decode()
    response = app.app.test_client().post("/callback", data=body,
        headers={"X-Line-Signature": signature, "Content-Type": "application/json"})
    wait_for_webhooks()
    return response


@pytest.mark.parametrize("action", ["img_translate", "img_skip"])
@pytest.mark.parametrize("role", ["sender", "admin", "bootstrap", "other", "foreign_sender", "foreign_admin", "unknown"])
def test_signed_image_buttons_only_allow_uploader_or_admin_in_original_chat(image_case, monkeypatch, role, action):
    state = image_case
    uid = USER if role in {"sender", "foreign_sender"} else COLLEAGUE if role == "other" else "" if role == "unknown" else ADMIN
    if role == "bootstrap":
        monkeypatch.setattr(app, "admin_users", {})
        monkeypatch.setattr(app, "_BOOTSTRAP_ADMIN_IDS", {ADMIN})
    group = OTHER if role.startswith("foreign_") else GROUP
    before = state.path.read_bytes()
    raw = postback(uid, group=group, action=action)
    assert signed_click(raw).status_code == 200
    allowed = role in {"sender", "admin", "bootstrap"}
    if not allowed:
        assert state.path.read_bytes() == before
        assert state.sends == state.jobs == state.background == []
        return
    assert MID not in app._load_pending_imgs()
    if action == "img_skip":
        assert state.sends == state.jobs == state.background == []
    else:
        assert len(state.sends) == len(state.jobs) == len(state.background) == 1
        ctx = state.jobs[0][0]
        assert (ctx["group_id"], ctx["user_id"], ctx["message_id"]) == (GROUP, USER, MID)
        # Redelivery and a second physical click must not start another job.
        assert signed_click(raw).status_code == 200
        assert signed_click(postback(uid, action=action, eid="second-image-click")).status_code == 200
        assert len(state.sends) == len(state.jobs) == len(state.background) == 1


@pytest.mark.parametrize("entry", ["_process_pending_image_translate", "_process_pending_image_translate_inner"])
def test_direct_image_entry_cannot_bypass_owner_check(image_case, entry):
    before = image_case.path.read_bytes()
    getattr(app, entry)(PostbackEvent.from_dict(postback(COLLEAGUE)), MID)
    assert image_case.path.read_bytes() == before
    assert image_case.sends == image_case.jobs == image_case.background == []


def test_expired_image_card_is_silent_even_for_owner(image_case):
    app._pending_img_set(MID, {"group_id": GROUP, "user_id": USER, "ts": int(time.time()) - 61})
    assert signed_click(postback(USER)).status_code == 200
    assert image_case.sends == image_case.jobs == image_case.background == []


def test_image_owner_is_rechecked_under_lock_before_consuming(image_case, monkeypatch):
    original = app._file_lock
    @contextmanager
    def changed(path, *args, **kwargs):
        with original(path, *args, **kwargs):
            data = app._load_pending_imgs()
            data[MID]["user_id"] = COLLEAGUE
            app._save_pending_imgs(data)
            yield
    monkeypatch.setattr(app, "_file_lock", changed)
    app._process_pending_image_translate(PostbackEvent.from_dict(postback(USER)), MID)
    assert app._load_pending_imgs()[MID]["user_id"] == COLLEAGUE
    assert image_case.sends == image_case.jobs == image_case.background == []


def test_owner_and_admin_race_only_starts_one_translation(image_case):
    barrier = threading.Barrier(2)
    def click(uid):
        barrier.wait(timeout=5)
        app._process_pending_image_translate(PostbackEvent.from_dict(postback(uid, eid=uid)), MID)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(click, uid) for uid in (USER, ADMIN)]
        for job in jobs:
            job.result(timeout=10)
    assert len(image_case.jobs) == len(image_case.sends) == len(image_case.background) == 1


def test_admin_failure_retry_retains_photo_owner_and_chat(image_case, monkeypatch):
    def broken(*args):
        raise RuntimeError("offline simulated OCR failure")
    monkeypatch.setattr(app, "_process_pending_image_translate_inner", broken)
    app._process_pending_image_translate(PostbackEvent.from_dict(postback(ADMIN)), MID)
    ctx, options = image_case.jobs[0]
    assert ctx["user_id"] == USER and ctx["group_id"] == GROUP and options["delay_seconds"] == 2
    assert image_case.sends == []
