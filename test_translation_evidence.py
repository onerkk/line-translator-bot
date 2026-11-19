import json
from collections import deque

import pytest

import app
import conversation_context as context
import factory_quantity_semantics as quantity
import factory_station_catalog as stations
import learning_eval_live as exam
import model_compare_live as compare
import translation_request_cache


@pytest.mark.parametrize("number,modifier", [("480", "昨天退回的"), ("490", "夜班重新量過的"),
                                              ("625", "剛從倉庫送回來的")])
def test_independent_object_count_survives_modifiers_and_station_changes(number, modifier):
    source = f"{number}把{modifier}兩捆先標成HOLD"
    good = f"Pihak {number} memberi status HOLD pada dua bundel itu."
    bad = f"{number} bundel dan dua bundel itu diberi status HOLD."
    frame = quantity.build_frame(source)
    assert frame["disposal_actors"] == [number]
    assert [(a["value"], a["classifier"]) for a in frame["atoms"]] == [("2", "捆")]
    assert quantity.validate_translation(frame, good)[0]
    assert not quantity.validate_translation(frame, bad)[0]
    assert not stations.translation_issues(source, good, "zh", "id")
    assert not app._delivery_validation_issues(source, good, "zh", "id")


def test_same_digits_can_name_both_the_actor_and_a_real_count():
    source = "480把480捆都標成HOLD"
    good = "Petugas 480 memberi status HOLD pada 480 bundel."
    frame = quantity.build_frame(source)
    assert quantity.validate_translation(frame, good)[0]
    assert not quantity.validate_translation(frame, "480 bundel diberi status HOLD.")[0]
    assert "Never write '480 bundel'" not in quantity.build_prompt(frame)
    assert not app._delivery_validation_issues(source, good, "zh", "id")
    assert not quantity.build_frame("總共480把，另有兩捆要退")["disposal_actors"]
    assert not quantity.build_frame("A480把兩捆送回")["disposal_actors"]


def test_uncertain_numeral_is_neither_a_demanded_unit_nor_an_assumed_station():
    source = "480把料先包起來"
    def frame(previous):
        with context.scope(exam.exam_snapshot({"src_lang": "zh", "exam_previous": previous}, source)):
            return quantity.build_frame(source)
    with translation_request_cache.scope():
        assert frame("")["unresolved_numbers"] == ["480"]
        assert frame("昨晚480的人交班了")["disposal_actors"] == ["480"]
        assert [a["value"] for a in frame("確認總共480把")["atoms"]] == ["480"]
        assert frame("480站還是480捆？")["unresolved_numbers"] == ["480"]
        assert frame("隔壁機台還在保養")["unresolved_numbers"] == ["480"]
        explicit = "共有480把料要包"
        with context.scope(exam.exam_snapshot({"src_lang": "zh", "exam_previous": "480站交班"}, explicit)):
            assert quantity.build_frame(explicit)["atoms"][0]["value"] == "480"


def test_confirmed_reversal_cannot_be_silently_delivered_or_admitted_to_cache(monkeypatch):
    monkeypatch.setattr(app._tl, "cacheable", True, raising=False)
    src = "490把剛量好的兩捆都標成HOLD"
    bad = "490 bundel dan dua bundel sudah diberi status HOLD."
    sent = app._final_delivery_guard(src, bad, "zh", "id")
    assert bad not in sent and src in sent and "490" in sent
    assert app._tl.cacheable is False
    assert app._final_delivery_guard(src, sent, "zh", "id") == sent


def test_reverse_direction_explicit_control_label_and_a_genuine_count():
    source = "Stasiun 480 sudah memberi status HOLD pada dua bundel."
    assert not app._delivery_validation_issues(source, "480把兩捆標成HOLD了。", "id", "zh")
    assert app._delivery_validation_issues(source, "480捆跟兩捆都已經標成HOLD了。", "id", "zh")
    assert stations.translation_issues("Ada 480 bundel.", "480站有料。", "id", "zh")
    report = app.tqg_module.validate_translation("Jangan tahan material.", "不要HOLD材料。", "id", "zh")
    assert not report.ok


def test_only_a_single_unambiguous_status_value_can_repair_an_opposite_label():
    src = "這兩捆的狀態是HOLD"
    wrong = "Status kedua bundel adalah RELEASE."
    # An intervening subject is legitimate Indonesian. The field below is
    # explicit; an ordinary word occurring elsewhere stays untouched.
    explicit = "Kedua bundel diberi status RELEASE."
    assert app.tqg_module._opposing_state_field(src, wrong) is None
    assert "status HOLD" in app._final_delivery_guard(src, explicit, "zh", "id")
    assert "factory_status:opposite_explicit_value" in app._delivery_validation_issues(src, explicit, "zh", "id")
    assert app.tqg_module._opposing_state_field("如果狀態是HOLD，先等", explicit) is None
    assert app.tqg_module._opposing_state_field("把狀態從HOLD改成RELEASE", explicit) is None


def test_exact_review_recovery_uses_current_approval_and_honors_context(monkeypatch):
    src, good, bad = "今天先收料", "Terima material dahulu hari ini.", "Kirim material dahulu hari ini."
    monkeypatch.setattr(app, "_REVIEWED_FAILURE_PAIRS", {("zh", "id", src, bad)})
    monkeypatch.setattr(app, "_translation_cache_context_bound", lambda _: False)
    monkeypatch.setattr(app._tl, "eval_no_cache", False, raising=False)
    row = {"source": src, "target": good, "bad_target": bad, "verified_correction": True}
    monkeypatch.setattr(app, "_retrieve_verified_translation_cases", lambda *args: [row])
    assert app._final_delivery_guard(src, bad, "zh", "id") == good
    assert app._approved_review_replacement("明天先收料", bad, "zh", "id") is None
    monkeypatch.setattr(app, "_translation_cache_context_bound", lambda _: True)
    assert app._approved_review_replacement(src, bad, "zh", "id") is None
    monkeypatch.setattr(app, "_translation_cache_context_bound", lambda _: False)
    row["target"] = bad
    assert app._approved_review_replacement(src, bad, "zh", "id") is None


def test_pending_reply_does_not_become_a_completed_release():
    src = "belum"
    snap = exam.exam_snapshot({"src_lang": "id", "exam_previous": "Tolong release data."}, src)
    with context.scope(snap):
        assert app._final_delivery_guard(src, "已經放行了。", "id", "zh") == "還沒放行。"
    assert not context.repair_resolved_reply(src, "已經放行了。", "id", "zh")


def test_live_comparison_gives_translation_and_judge_the_same_isolated_prior(monkeypatch):
    case = dict(id="test", item_id="test", src_lang="id", tgt_lang="zh", source="belum",
                corrected="還沒", original="已經完成", reason="完成狀態")
    seen = []
    def ask(_app, _model, prompt, _max):
        if "previous_original" in prompt:
            seen.append(("judge", "Tolong release data." in prompt))
            return '{"correct":true,"error":""}'
        return json.dumps({"previous": "Tolong release data.", "message": "Belum selesai."})
    def translate(source, src, tgt):
        snap = context.current_for(source)
        seen.append(("translation", snap["entries"][0]["text"]))
        assert app._tl.group_id == "" and app._tl.eval_no_cache
        return "還沒放行。"
    monkeypatch.setattr(exam, "load_cases", lambda *args: [dict(case)])
    monkeypatch.setattr(exam, "_ask", ask)
    monkeypatch.setattr(app, "_conversation_journal", lambda: pytest.fail("opened a live journal"))
    monkeypatch.setattr(app, "translate", app._conversation_translation_scope(translate))
    monkeypatch.setattr(app, "_update_last_translate_debug", lambda **kwargs: None)
    report = compare.run(app, limit=1, configs=[("current", "現行設定", None, None)],
                         judge_model="judge", variant_model="variant")
    assert seen == [("translation", "Tolong release data."), ("judge", True)]
    assert report["summary"][0]["directions"]["id"] == {"scored": 1, "wrong": 0}
    assert context.current_for("Belum selesai.") is None


def test_successful_delivery_metrics_separate_known_cost_and_original_receipt(monkeypatch):
    monkeypatch.setattr(app, "_LATENCY_SAMPLES", deque(maxlen=20))
    monkeypatch.setattr(app, "_LINE_ACCEPTANCE_SAMPLES", deque(maxlen=20))
    monkeypatch.setattr(app._tl, "eval_no_cache", False, raising=False)
    app._record_translation_latency("test", 100, "group", True, [])
    app._record_translation_latency("test", 700, "group", True, [{"priced": True, "cost_usd": .001}])
    app._record_translation_latency("test", 400, "group", True, [{"error": "TimeoutError"}])
    row = app._latency_report("group")["短句（30字內）"]
    assert (row["n"], row["calls"], row["priced"], row["p50"], row["p95"]) == (3, 2, 2, 400, 700)
    assert row["twd_per_1000"] == pytest.approx(.5 * app.USD_TO_TWD)
    monkeypatch.setattr(app.time, "time", lambda: 100.5)
    app._record_line_acceptance({"job_kind":"text", "group_id":"group", "webhook_received_at":100})
    app._record_line_acceptance({"job_kind":"image", "group_id":"group", "webhook_received_at":100})
    assert list(app._LINE_ACCEPTANCE_SAMPLES) == [(100.5, "group", 500)]
