"""Release status, not a request: real guards and handler with offline I/O."""
from types import SimpleNamespace

import pytest

import app
import factory_knowledge as knowledge
import factory_message_semantics as semantics
import translation_retry_queue as queue
from test_translation_notice_availability import (
    clean_translation_context, runtime, event, delivered_text, retry_pending, response,
)

SOURCE = "@鄭黑輪 @小麥（研磨股班長） 未放行的剛二股通知 資料異常無法放行到下站別（480都有檢驗過了）要吊出來貼資料異常 還是要拆包？"
TARGET = (
    "@鄭黑輪 @小麥（研磨股班長） Mengenai yang belum di-release, Bagian Cold Drawing 2 "
    "baru saja memberi tahu bahwa datanya bermasalah sehingga tidak bisa di-release "
    "ke stasiun berikutnya (semuanya sudah diperiksa di stasiun 480). "
    "Apakah perlu diangkat keluar dan diberi label data bermasalah, "
    "atau harus dibuka kemasannya?"
)


@pytest.mark.parametrize("source,target", [
    (SOURCE, TARGET),
    ("資料異常無法放行到下站別。", "Datanya bermasalah sehingga tidak bisa di-release ke stasiun berikutnya."),
    ("這把還沒放行。", "Data untuk bundel ini belum di-release ke stasiun berikutnya."),
    ("這批無法放行。", "Data untuk batch ini tidak dapat dirilis ke proses berikutnya."),
    ("這筆資料不要放行。", "Jangan release data ini ke stasiun berikutnya."),
    ("資料放行了嗎？", "Apakah data sudah di-release ke stasiun berikutnya?"),
    ("這筆資料不能放行。", "Data ini tidak bisa dirilis ke stasiun berikutnya."),
    ("這筆資料可以放行嗎？", "Apakah data ini bisa dirilis ke stasiun berikutnya?"),
    ("這筆資料放行失敗。", "Data ini gagal di-release ke stasiun berikutnya."),
    ("這筆資料尚未放行。", "Data ini belum dirilis ke tahap berikutnya."),
])
def test_correct_release_status_passes_every_delivery_boundary(source, target):
    app._tl.semantic_contract = app.build_translation_semantic_contract(source, "zh", "id")
    assert app._build_translation_response_validator(source, "zh", "id")(response(target), "test") == (True, "ok")
    cards = knowledge.retrieve(source, "zh", "id", limit=50)
    assert knowledge.validate_translation(cards, source, target) == (True, [])
    assert app._final_delivery_guard(source, target, "zh", "id") == target


@pytest.mark.parametrize("source,bad", [
    ("這把還沒放行。", "Tolong release data untuk bundel ini ke stasiun berikutnya."),
    ("這批無法放行。", "Data untuk batch ini sudah dirilis ke stasiun berikutnya."),
    ("這筆資料不要放行。", "Tolong release data ini ke stasiun berikutnya."),
    ("資料放行了嗎？", "Data sudah di-release ke stasiun berikutnya."),
    ("這筆資料已經放行。", "Data ini belum dirilis ke stasiun berikutnya."),
    ("這筆資料放行失敗。", "Data ini sudah di-release ke stasiun berikutnya."),
])
def test_changed_status_or_question_is_rejected_by_the_shared_relation(source, bad):
    frame = semantics.build_frame(source, "zh", "id")
    ok, issues = semantics.validate_translation(frame, bad)
    assert not ok, frame
    assert any("erp_release" in issue for issue in issues)
    assert not app.is_translation_acceptable(source, bad, "zh", "id")


def test_inspection_and_unpacking_do_not_change_the_release_sense():
    frame = semantics.build_frame(SOURCE, "zh", "id")
    assert frame["kind"] == "zh_id_erp_data_release"
    assert not frame["complete"]
    assert not frame["slots"]["request"]
    assert semantics.translate_source_directly(SOURCE, "zh", "id") == ""
    assert semantics.validate_translation(frame, TARGET) == (True, [])


def test_mention_role_is_an_identity_but_section_in_prose_is_translated():
    contract = app.build_translation_semantic_contract(SOURCE, "zh", "id")
    pairs = [pair for risk in contract["risks"] if risk.get("sense") == "factory_organization_terms"
             for pair in risk["pairs"]]
    assert any(pair[0] == "二股" for pair in pairs)
    assert not any(pair[0] == "研磨股" for pair in pairs)


def test_other_clause_can_place_a_physical_bundle_after_releasing_its_data():
    source = "這把麻煩他們放一下，再把材料放在架上。"
    target = "Tolong minta mereka release data untuk bundel ini ke stasiun berikutnya, lalu letakkan material di rak."
    frame = semantics.build_frame(source, "zh", "id")
    assert frame["kind"] == "zh_id_erp_data_release"
    assert not frame["complete"]
    assert semantics.validate_translation(frame, target) == (True, [])


def test_other_actions_cannot_lend_their_completed_status_to_release():
    source = "資料還沒放行，檢驗已經完成。"
    bad = "Data sudah dirilis ke stasiun berikutnya, pemeriksaan belum selesai."
    assert not semantics.validate_translation(semantics.build_frame(source, "zh", "id"), bad)[0]


def test_release_status_follows_station_identity_even_when_clauses_are_reordered():
    source = "I6資料尚未放行，I7資料已經放行。"
    good = "Data I7 sudah dirilis ke stasiun berikutnya, sedangkan data I6 belum dirilis ke stasiun berikutnya."
    bad = "Data I6 sudah dirilis ke stasiun berikutnya, sedangkan data I7 belum dirilis ke stasiun berikutnya."
    frame = semantics.build_frame(source, "zh", "id")
    assert semantics.validate_translation(frame, good) == (True, [])
    assert not semantics.validate_translation(frame, bad)[0]


def test_actual_handler_with_two_line_mentions_delivers_once(runtime):
    runtime.provider_result = TARGET
    e = event(SOURCE)
    names = ("@鄭黑輪", "@小麥（研磨股班長）")
    e.message.mention = SimpleNamespace(mentionees=[
        SimpleNamespace(index=SOURCE.index(name), length=len(name), user_id="U" + str(i + 1) * 32, type="user")
        for i, name in enumerate(names)
    ])
    app.handle_message(e)
    text = delivered_text(runtime)
    for value in (*names, "480", "tidak bisa di-release", "dibuka kemasannya"):
        assert value in text
    assert len(runtime.generations) == 1
    assert queue.pending_count() == 0
    app.handle_message(e)
    assert len(runtime.sends) == 1


def test_line_recovery_reuses_the_finished_translation(runtime):
    runtime.provider_result = TARGET
    runtime.reply_down = runtime.push_down = True
    with pytest.raises(TimeoutError):
        app.handle_message(event(SOURCE))
    # A previously verified exact TM hit may make even the first attempt free.
    generations_before_retry = len(runtime.generations)
    assert generations_before_retry <= 1
    assert queue.get("notice-group:notice-message") is not None
    runtime.push_down = False
    assert retry_pending()
    assert len(runtime.generations) == generations_before_retry
    assert "480" in delivered_text(runtime)
