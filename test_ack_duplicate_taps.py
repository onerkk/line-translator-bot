"""Silent durable acknowledgements, including first taps and concurrent workers."""
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
    assert replies == []
    hub.h["group_user_names"][GROUP][COLLEAGUE] = "改名之後"
    original_name = hub._member_name
    def name(group, uid, *, lookup=False):
        assert not lookup, "Duplicate tap must not perform another profile lookup"
        return original_name(group, uid, lookup=lookup)
    monkeypatch.setattr(hub, "_member_name", name)
    for stamp in [100, 200, 300, 50, 400]:
        assert hub.postback(event(uid=COLLEAGUE, stamp=stamp),
                            {"action": "factory_ack", "token": row["token"]})
    assert replies == [] and len(sent) == 1
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
def test_concurrent_taps_across_workers_record_each_person_without_reply(hub, monkeypatch, same_person):
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
    assert replies == [] and len(stored(hub, row)["responses"]) == expected
    if not same_person:
        assert_finished(hub, row)


def test_different_notices_accept_the_same_person_independently(hub):
    first, _, replies = start(hub)
    answer(hub, first)
    second = command(hub, "/ack 第二項工作", mid="second-notice")
    answer(hub, second)
    answer(hub, first)
    answer(hub, second)
    assert first["token"] != second["token"] and replies == []
    assert stored(hub, first)["responses"][COLLEAGUE]["status"] == "understood"
    assert stored(hub, second)["responses"][COLLEAGUE]["status"] == "understood"


def test_duplicate_after_completion_keeps_reminders_stopped(hub):
    row, sent, replies = start(hub)
    answer(hub, row)
    completed = assert_finished(hub, row)
    for stamp in [200, 300, 400]:
        hub.postback(event(uid=COLLEAGUE, stamp=stamp), {"action": "factory_ack", "token": row["token"]})
    tick(hub, row["reminder_due_at"] + 3600)
    assert stored(hub, row) == completed and replies == [] and len(sent) == 1


def test_duplicate_is_silent_with_group_disabled_but_new_answers_are_still_blocked(hub):
    row, _, replies = start(hub, people=(COLLEAGUE, THIRD))
    answer(hub, row)
    doc = copy.deepcopy(hub.menu.document())
    doc["default"]["acknowledgements"] = "off"
    hub.h[line_quick_reply.KEY] = doc
    assert hub.options(GROUP)["acknowledgements"] == "off"
    answer(hub, row)
    assert replies == []
    answer(hub, row, THIRD)
    assert replies == []
    assert THIRD not in stored(hub, row)["responses"]


def test_non_manager_status_query_is_ignored_after_understood_tap(hub):
    row, _, replies = start(hub)
    answer(hub, row)
    hub.postback(event(uid=COLLEAGUE), {"action": "factory_receipts", "token": row["token"]})
    assert replies == [] and COLLEAGUE not in stored(hub, row).get("status_views", {})
    answer(hub, row)
    assert replies == []


def test_removed_legacy_action_is_silent_and_cannot_veto_an_ack(hub):
    row, _, replies = start(hub)
    params = {"action": "factory_help", "token": row["token"]}
    hub.postback(event(uid=COLLEAGUE, stamp=400), params)
    hub.postback(event(uid=COLLEAGUE, stamp=500), params)
    assert COLLEAGUE not in stored(hub, row)["responses"]
    params["action"] = "factory_ack"
    hub.postback(event(uid=COLLEAGUE, stamp=200), params)
    first = copy.deepcopy(stored(hub, row)["responses"][COLLEAGUE])
    assert replies == [] and first["status"] == "understood" and first["event_timestamp"] == 200
    hub.postback(event(uid=COLLEAGUE, stamp=600), params)
    hub.postback(event(uid=COLLEAGUE, stamp=700), params)
    assert replies == [] and stored(hub, row)["responses"][COLLEAGUE] == first


def test_first_and_repeated_ack_never_call_the_reply_transport(hub):
    row, _, _ = start(hub)
    calls = []
    def timeout(**kwargs):
        calls.append(kwargs)
        raise TimeoutError("feedback acceptance unknown")
    hub.h["_send_reply_with_push_fallback"] = timeout
    answer(hub, row)
    answer(hub, row)
    assert calls == [] and assert_finished(hub, row)["responses"][COLLEAGUE]["status"] == "understood"


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
    assert replies == []
    answer(hub, row)
    answer(hub, row)
    assert replies == []  # Storage failure remains retryable without chat feedback.
    assert_finished(hub, row)
