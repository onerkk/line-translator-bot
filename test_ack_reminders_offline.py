"""Run with `python -m unittest -v test_ack_reminders_offline`.

Exercises the real SQLite outbox and scheduler without Flask, LINE, Redis or
network access. The unused HTTP module is stubbed only if requests is absent.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
import logging
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

try:
    import requests
except ImportError:
    with patch.dict(sys.modules, {"requests": ModuleType("requests")}):
        from line_factory_store import FeatureStore
else:
    from line_factory_store import FeatureStore
from line_ack_reminders import NoticeService, SendError, RETRY_WINDOW

GROUP = "C" + "1" * 32
AUTHOR = "U" + "a" * 32
PEOPLE = ["U" + format(i, "032x") for i in range(1, 45)]


class NoticeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = FeatureStore(path=Path(self.temp.name) / "notices.db")
        self.now = time.time()
        self.options = {"acknowledgements": "command", "ack_reminder_enabled": True}
        self.sent = []
        self.hub = SimpleNamespace(store=self.store, options=lambda group: self.options,
            current=lambda metadata: True, app=SimpleNamespace(logger=logging.getLogger("ack-test")),
            _receipt_text=lambda row, heading, **kwargs: heading,
            _short=lambda text, count: text[:count], _notice_card=lambda token, text, row:
                SimpleNamespace(to_dict=lambda: {"type": "flex", "altText": text, "contents": {"type": "bubble"}}))
        self.service = NoticeService(self.hub, self.send, clock=lambda: self.now)

    def send(self, group, messages, key):
        self.sent.append((group, copy.deepcopy(messages), key))

    def create(self, token="notice-one", count=3, delivered=True, responses=None):
        row = {"token": token, "group_id": GROUP, "created_at": self.now, "expires_at": self.now + 604800,
               "original": "檢查設備", "sender_id": AUTHOR, "expected": dict.fromkeys([AUTHOR, *PEOPLE[:count]], "成員"),
               "responses": responses or {}, "wake_at": self.now, "reminder_minutes": 30,
               "delivery_state": "delivered" if delivered else "prepared", "factory_event": {},
               "initial_messages": [{"type": "text", "text": "原通知"}]}
        self.store.save_interaction({"token": token, "group_id": GROUP}, 604800, notice=row)
        return "notice:" + GROUP + ":" + token

    def test_waits_from_delivery_and_excludes_only_understood_and_author(self):
        key = self.create(delivered=False, responses={PEOPLE[0]: {"status": "understood"}, PEOPLE[1]: {"status": "needs_help"}})
        self.service.run_due()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.store.get(key)["reminder_due_at"], self.now + 1800)
        self.now += 1799
        self.service.run_due()
        self.assertEqual(len(self.sent), 1)
        self.now += 1
        self.service.run_due()
        mentions = self.sent[1][1][0]["substitution"].values()
        self.assertEqual([m["mentionee"]["userId"] for m in mentions], [PEOPLE[1], PEOPLE[2]])
        self.service.run_due()
        self.service.run_due()
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.store.get(key)["reminder_state"], "sent")

    def test_restart_recovers_due_queue_and_keeps_original_deadline(self):
        key = self.create(delivered=False)
        self.service.run_due()
        self.hub.store = FeatureStore(path=self.store.path)
        self.now += 1800
        fresh = NoticeService(self.hub, self.send, clock=lambda: self.now)
        fresh.run_due()
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.store.get(key)["delivered_at"], self.now - 1800)

    def test_timeout_retries_identical_payload_and_key_when_recipients_unchanged(self):
        key = self.create()
        def timeout(*args):
            self.send(*args)
            raise TimeoutError("ambiguous response")
        self.service.sender = timeout
        self.service.run_due()
        self.now += 16
        self.service.sender = self.send
        self.service.run_due()
        self.assertEqual(self.sent[0], self.sent[1])
        self.assertEqual(self.store.get(key)["reminder_state"], "sent")

    def test_multiple_workers_claim_once_while_receipt_arrives(self):
        key = self.create()
        started, release = threading.Event(), threading.Event()
        def blocked(*args):
            started.set()
            release.wait(5)
            self.send(*args)
        self.service.sender = blocked
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.service.run_due)
            self.assertTrue(started.wait(3))
            second = pool.submit(self.service.run_due)
            self.store.update(key, lambda row: dict(row, responses={PEOPLE[0]: {"status": "understood"}}))
            release.set()
            first.result()
            second.result()
        self.assertEqual(len(self.sent), 1)
        self.assertIn(PEOPLE[0], self.store.get(key)["responses"])

    def test_departed_and_all_answered_do_not_receive_reminders(self):
        key = self.create(count=2, responses={PEOPLE[0]: {"status": "understood"}})
        self.store.put("members-left:" + GROUP, {PEOPLE[1]: self.now})
        self.service.run_due()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.store.get(key)["reminder_state"], "no_pending")

    def test_reply_during_roster_lookup_is_excluded_before_payload_is_frozen(self):
        key = self.create(count=1)
        original = self.store.get
        def get(name):
            if name == "members-left:" + GROUP:
                self.store.update(key, lambda row: dict(row, responses={PEOPLE[0]: {"status": "understood"}}))
            return original(name)
        with patch.object(self.store, "get", get):
            self.service.run_due()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.store.get(key)["reminder_state"], "no_pending")

    def test_disabled_expired_unsent_and_bot_left_cancel(self):
        for reason in ("disabled", "expired", "unsent", "left", "mode_off"):
            with self.subTest(reason=reason):
                key = self.create(token="notice-" + reason)
                self.options["ack_reminder_enabled"] = reason != "disabled"
                self.options["acknowledgements"] = "off" if reason == "mode_off" else "command"
                self.hub.current = lambda metadata: reason != "unsent"
                if reason == "expired":
                    self.store.update(key, lambda row: dict(row, expires_at=self.now - 1))
                if reason == "left":
                    self.store.put("bot-left:" + GROUP, {"at": self.now + 1})
                self.service.process(key)
                self.assertEqual(self.store.get(key)["reminder_state"], "cancelled")
                self.store.delete("bot-left:" + GROUP)
        self.assertEqual(self.sent, [])

    def test_more_than_twenty_split_and_new_replies_excluded_between_batches(self):
        key = self.create(count=44)
        self.service.run_due()
        self.store.update(key, lambda row: dict(row, responses={PEOPLE[21]: {"status": "understood"}}))
        for _ in range(4):
            self.service.run_due()
        recipients = [m["mentionee"]["userId"] for _, messages, _ in self.sent
                      for m in messages[0]["substitution"].values()]
        self.assertEqual(len(recipients), 43)
        self.assertEqual(len(set(recipients)), 43)
        self.assertNotIn(PEOPLE[21], recipients)
        self.assertEqual(len({key for _, _, key in self.sent}), 3)
        self.assertTrue(all(len(messages[0]["substitution"]) <= 20 for _, messages, _ in self.sent))

    def test_permanent_error_is_visible_and_not_retried(self):
        key = self.create()
        def invalid(*args):
            raise SendError("LINE 400", False)
        self.service.sender = invalid
        self.service.run_due()
        self.assertEqual(self.store.get(key)["reminder_state"], "failed")
        self.assertIsNone(self.store.get(key)["wake_at"])

    def test_retry_window_stops_before_line_key_expires(self):
        key = self.create()
        def timeout(*args):
            self.send(*args)
            raise TimeoutError()
        self.service.sender = timeout
        self.service.run_due()
        self.now += RETRY_WINDOW
        self.service.run_due()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.store.get(key)["reminder_state"], "uncertain")

    def test_atomic_creation_and_global_index_not_limited_by_history(self):
        due = self.create(token="old-due")
        for i in range(205):
            key = self.create(token="future-" + str(i))
            self.store.update(key, lambda row: dict(row, wake_at=self.now + 3600))
        self.assertEqual(len(self.store.recent("notice:" + GROUP, 200)), 200)
        self.assertEqual([row["token"] for row in self.store.due_notices(self.now)], ["old-due"])
        self.store.delete(due)
        self.assertEqual(self.store.due_notices(self.now), [])

    def test_crash_lease_expires_and_same_frozen_request_recovers(self):
        key = self.create()
        def crash(*args):
            self.send(*args)
            raise KeyboardInterrupt()
        self.service.sender = crash
        with self.assertRaises(KeyboardInterrupt):
            self.service.run_due()
        self.service.sender = self.send
        self.service.run_due()
        self.assertEqual(len(self.sent), 1)
        self.now += 121
        self.service.run_due()
        self.assertEqual(self.sent[0], self.sent[1])


if __name__ == "__main__":
    unittest.main()
