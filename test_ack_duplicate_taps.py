"""One visible acknowledgement per person/status/notice, durable across workers."""
import copy
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

import line_factory_features as factory
import line_quick_reply
from line_factory_store import StoreError
from test_line_factory_features import hub, storage, event, GROUP, USER, COLLEAGUE
from test_line_ack_commands import command
from test_ack_known_zero_stop import start, answer, assert_finished
from test_ack_repeat_controls import THIRD, stored, tick


def test_repeated_taps_and_redelivery_are_silent_and_keep_first_response(hub, monkeypatch):
    row, sent, replies = start(hub, people=(COLLEAGUE, THIRD))
    answer(hub, row)
    first = stored(hub, row)
    assert len(replies) == 1
    hub.h["group_user_names"][GROUP][COLLEAGUE] = "改名之後"
    original_name = hub._member_name
    def name(group, uid, *, lookup=False):
        assert not lookup, "Duplicate tap must not perform another profile lookup"
        return original_name(group, uid, lookup=lookup)
    monkeypatch.setattr(hub, "_member_name", name)
    for stamp in [100, 200, 300, 50, 400]:
        assert hub.postback(event(uid=COLLEAGUE, stamp=stamp),
                            {"action": "factory_ack", "token": row["token"]})
    assert len(replies) == 1 and len(sent) == 1
    assert stored(hub, row) == first


def test_saved_responses_from_previous_release_are_also_silent_after_restart(hub):
    row, _, replies = start(hub, people=(COLLEAGUE, THIRD))
    key = "notice:" + GROUP + ":" + row["token"]
    old = {"status": "understood", "name": "原姓名", "at": row["created_at"], "event_timestamp": 80}
    hub.store.update(key, lambda r: dict(r, responses={COLLEAGUE: old}))
    fresh = factory.FactoryHub(hub.app, hub.h, hub.store)
    assert fresh.postback(event(uid=COLLEAGUE, stamp=500), {"action": "factory_ack", "token": row["token"]})
    assert replies == [] and stored(hub, row)["responses"][COLLEAGUE] == old


@pytest.mark.parametrize("same_person", [True, False])
def test_concurrent_taps_across_workers_reply_once_per_person(hub, monkeypatch, same_person):
    row, _, replies = start(hub, people=(COLLEAGUE, THIRD))
    other = factory.FactoryHub(hub.app, hub.h, hub.store)
    key = "notice:" + GROUP + ":" + row["token"]
    barrier, local = threading.Barrier(2), threading.local()
    original_cas, results = hub.store.compare_swap, []
    def compare(key_arg, previous, current, ttl):
        if key_arg == key and not getattr(local, "checked", False):
            local.checked = True
            barrier.wait(timeout=5)  # Both writers have read the pre-ack row.
        result = original_cas(key_arg, previous, current, ttl)
        if key_arg == key:
            results.append(result)
        return result
    monkeypatch.setattr(hub.store, "compare_swap", compare)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(worker.postback, event(uid=uid, stamp=stamp),
                            {"action": "factory_ack", "token": row["token"]})
                for worker, uid, stamp in [(hub, COLLEAGUE, 200),
                                           (other, COLLEAGUE if same_person else THIRD, 300)]]
        assert all(job.result(timeout=10) for job in jobs)
    assert False in results  # Exercise an actual failed CAS, not serial taps.
    expected = 1 if same_person else 2
    assert len(replies) == len(stored(hub, row)["responses"]) == expected
    if not same_person:
        assert_finished(hub, row)


def test_different_notices_accept_the_same_person_independently(hub):
    first, _, replies = start(hub)
    answer(hub, first)
    second = command(hub, "/ack 第二項工作", mid="second-notice")
    answer(hub, second)
    answer(hub, first)
    answer(hub, second)
    assert first["token"] != second["token"] and len(replies) == 2
    assert stored(hub, first)["responses"][COLLEAGUE]["status"] == "understood"
    assert stored(hub, second)["responses"][COLLEAGUE]["status"] == "understood"


def test_duplicate_after_completion_keeps_reminders_stopped(hub):
    row, sent, replies = start(hub)
    answer(hub, row)
    completed = assert_finished(hub, row)
    for stamp in [200, 300, 400]:
        hub.postback(event(uid=COLLEAGUE, stamp=stamp), {"action": "factory_ack", "token": row["token"]})
    tick(hub, row["reminder_due_at"] + 3600)
    assert stored(hub, row) == completed and len(replies) == len(sent) == 1


def test_duplicate_is_silent_with_group_disabled_but_new_answers_are_still_blocked(hub):
    row, _, replies = start(hub, people=(COLLEAGUE, THIRD))
    answer(hub, row)
    doc = copy.deepcopy(hub.menu.document())
    doc["default"]["acknowledgements"] = "off"
    hub.h[line_quick_reply.KEY] = doc
    assert hub.options(GROUP)["acknowledgements"] == "off"
    answer(hub, row)
    assert len(replies) == 1
    answer(hub, row, THIRD)
    assert len(replies) == 2 and "已關閉作業確認" in replies[-1]["fallback_text"]
    assert THIRD not in stored(hub, row)["responses"]


def test_explicit_status_query_still_replies_when_understood_tap_is_silent(hub):
    row, _, replies = start(hub)
    answer(hub, row)
    hub.postback(event(uid=COLLEAGUE), {"action": "factory_receipts", "token": row["token"]})
    assert len(replies) == 2 and "此通知已自動停止提醒" in replies[-1]["fallback_text"]
    answer(hub, row)
    assert len(replies) == 2


def test_stale_legacy_action_is_silent_but_a_real_status_change_is_recorded(hub):
    row, _, replies = start(hub)
    params = {"action": "factory_help", "token": row["token"]}
    hub.postback(event(uid=COLLEAGUE, stamp=400), params)
    hub.postback(event(uid=COLLEAGUE, stamp=500), params)
    params["action"] = "factory_ack"
    hub.postback(event(uid=COLLEAGUE, stamp=200), params)
    assert len(replies) == 1 and stored(hub, row)["responses"][COLLEAGUE]["status"] == "needs_help"
    hub.postback(event(uid=COLLEAGUE, stamp=600), params)
    hub.postback(event(uid=COLLEAGUE, stamp=700), params)
    assert len(replies) == 2 and stored(hub, row)["responses"][COLLEAGUE]["status"] == "understood"


def test_failed_feedback_does_not_repeat_a_durable_ack(hub):
    row, _, _ = start(hub)
    calls = []
    def timeout(**kwargs):
        calls.append(kwargs)
        raise TimeoutError("feedback acceptance unknown")
    hub.h["_send_reply_with_push_fallback"] = timeout
    with pytest.raises(TimeoutError):
        answer(hub, row)
    answer(hub, row)
    assert len(calls) == 1 and assert_finished(hub, row)["responses"][COLLEAGUE]["status"] == "understood"


def test_failed_storage_does_not_suppress_a_later_successful_first_ack(hub, monkeypatch):
    row, _, replies = start(hub)
    key = "notice:" + GROUP + ":" + row["token"]
    original = hub.store.compare_swap
    def unavailable(key_arg, *args):
        if key_arg == key:
            raise StoreError("write unavailable")
        return original(key_arg, *args)
    with monkeypatch.context() as patch:
        patch.setattr(hub.store, "compare_swap", unavailable)
        with pytest.raises(StoreError):
            answer(hub, row)
    assert COLLEAGUE not in stored(hub, row)["responses"]
    assert len(replies) == 1 and "尚未確認儲存成功" in replies[-1]["fallback_text"]
    answer(hub, row)
    answer(hub, row)
    assert len(replies) == 2 and "已了解" in replies[-1]["fallback_text"]
    assert_finished(hub, row)
