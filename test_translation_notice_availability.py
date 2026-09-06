"""Production notice regression, with real guards, outbox and LINE framing.

External translation and LINE transport are replaced; no messages are sent.
The notice is deliberately absent from the exact-translation knowledge base.
"""
import copy
from types import SimpleNamespace

import pytest

import app
import factory_knowledge as knowledge
import factory_semantic_audit as audit
import translation_casebook as casebook
import translation_retry_queue as queue


SOURCE = """系統還沒完全改好，目前非本月暫存先注意以下，避免主管拿來針對。

1.不要跨班別存進去，早、中、晚的非本月就在因應的班別入。

2.先丟進去非本月的帳先移出，避免包的時間跟入帳時間差距太大。

3.眼色好一點，短期內就是會有夜間隨機查班，不要鬧到讓主管調監視器。

4.注意合理產量跟不要讓庫存降不下來，只要沒有重大事件發生，上面就不太會去釘入庫時間。

5.不要混料、不要貼錯標籤，PMI一定要檢測。"""

TARGET = """Sistem belum selesai diperbaiki sepenuhnya. Untuk penyimpanan sementara material yang bukan untuk bulan ini, perhatikan hal-hal berikut agar atasan tidak menjadikannya alasan untuk menyalahkan kita.

1. Jangan memasukkannya ke shift lain. Material yang bukan untuk bulan ini dari shift pagi, siang, dan malam harus dimasukkan pada shift masing-masing.

2. Keluarkan dulu catatan material yang sudah dimasukkan ke kategori bukan untuk bulan ini agar selisih antara waktu pengemasan dan waktu pencatatan tidak terlalu besar.

3. Lebih peka terhadap situasi. Dalam waktu dekat akan ada pemeriksaan shift malam secara acak. Jangan sampai membuat atasan memeriksa rekaman CCTV.

4. Perhatikan kewajaran jumlah produksi dan jangan sampai stok sulit berkurang. Selama tidak ada kejadian besar, atasan tidak akan terlalu mengawasi waktu masuk gudang.

5. Jangan mencampur material dan jangan salah menempelkan label produk. Pemeriksaan PMI wajib dilakukan."""


@pytest.fixture(autouse=True)
def clean_translation_context():
    previous = dict(app._tl.__dict__)
    app._tl.__dict__.clear()
    yield
    app._tl.__dict__.clear()
    app._tl.__dict__.update(previous)


def response(text):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=text), finish_reason="stop",
    )])


def test_full_notice_passes_provider_contract_and_final_delivery():
    assert app.factory_translation_guard_module.exact_verified_target(SOURCE, "zh", "id") is None
    app._tl.semantic_contract = app.build_translation_semantic_contract(SOURCE, "zh", "id")
    validate = app._build_translation_response_validator(SOURCE, "zh", "id")
    assert validate(response(TARGET), "test") == (True, "ok")
    assert app._final_delivery_guard(SOURCE, TARGET, "zh", "id") == TARGET


@pytest.mark.parametrize("source", [
    SOURCE,
    "本月入庫時間請記清楚。",
    "月底入庫時間平均一點，不要集中入帳。",
    "非本月暫存，每日要注意入庫時間。",
    "月底有目標要追，另外通知入庫時間。",
])
def test_time_notices_do_not_retrieve_quantity_sense_or_its_old_examples(source):
    store = knowledge.get_store()
    assert "warehouse_intake_target_and_forecast" not in {
        card["id"] for card in store.retrieve(source, "zh", "id", limit=50)
    }
    cases = casebook.retrieve(source, "zh", "id", examples=store.casebook_examples(), max_cases=50)
    assert not any("warehouse_intake_target_and_forecast" in str(item.get("case_id"))
                   for item in cases)


def test_quantity_sense_still_rejects_time_substitution():
    source = "本月入庫量100噸。"
    cards = knowledge.retrieve(source, "zh", "id", limit=50)
    assert any(card["id"] == "warehouse_intake_target_and_forecast" for card in cards)
    assert knowledge.validate_translation(cards, source, "Jumlah pemasukan gudang bulan ini 100 ton.")[0]
    ok, issues = knowledge.validate_translation(cards, source, "Waktu masuk gudang bulan ini 100 ton.")
    assert not ok
    assert any("forbidden:waktu masuk gudang" in issue for issue in issues)


def test_mixed_notice_keeps_quantity_and_time_without_contradictory_prompt():
    source = "本月入庫量100噸。\n入庫時間請記錄清楚。"
    target = "Jumlah pemasukan gudang bulan ini 100 ton.\nCatat waktu masuk gudang dengan jelas."
    cards = knowledge.retrieve(source, "zh", "id", limit=50)
    assert any(card["id"] == "warehouse_intake_target_and_forecast" for card in cards)
    assert knowledge.validate_translation(cards, source, target) == (True, [])
    assert "Forbidden target wording for this sense: waktu masuk gudang" not in knowledge.build_prompt(cards)
    assert app._final_delivery_guard(source, target, "zh", "id") == target


def test_cards_from_another_source_cannot_veto_the_current_notice():
    old_cards = knowledge.retrieve("本月入庫量100噸。", "zh", "id", limit=50)
    assert knowledge.validate_translation(old_cards, SOURCE, TARGET) == (True, [])


def test_invalid_historical_scope_is_ineligible_without_stopping_translation():
    example = {"zh": SOURCE, "id": TARGET, "dir": "zh2id",
               "source_match": {"regex_any": ["["], "min_score": 1}}
    assert casebook.retrieve(SOURCE, "zh", "id", examples=[example]) == []


def test_admin_rejects_malformed_mandatory_sense_rules():
    with pytest.raises(knowledge.KnowledgeError):
        knowledge.validate_document({"schema_version": 1, "entries": [{
            "id": "bad-rule", "directions": ["zh-id"],
            "match": {"any_terms": ["入庫"], "required_regex_any": ["["]},
        }]})


def test_numbered_items_allow_punctuation_changes_but_not_omission_or_reordering():
    source = "1.先停機。\n2.再檢查。"
    good = "１）Hentikan mesin dahulu.\n（２）Kemudian periksa."
    assert app.tqg_module.validate_translation(source, good, "zh", "id").ok
    for bad in ("1. Hentikan mesin dahulu.",
                "2. Kemudian periksa.\n1. Hentikan mesin dahulu."):
        report = app.tqg_module.validate_translation(source, bad, "zh", "id")
        assert any(issue.startswith("numbered_item_sequence:") for issue in report.hard_issues)
    report = app.tqg_module.validate_translation("1.5 mm\n2.3 mm", "1.5 mm dan 2.3 mm", "zh", "id")
    assert not any(issue.startswith("numbered_item_sequence:") for issue in report.issues)


@pytest.mark.parametrize("target", [
    "Akan ada pemeriksaan shift malam secara acak.",
    "Pengecekan acak akan dilakukan pada shift malam.",
    "Akan ada inspeksi malam secara random.",
    "Petugas akan mengecek shift malam secara acak.",
])
def test_random_inspection_accepts_equivalent_grammar(target):
    frame = audit.build_source_frame("夜間會隨機查班。", "zh", "id")
    assert audit.validate_translation(frame, target) == (True, [])
    prompt = audit.build_prompt(frame)
    assert "unscheduled_inspection_timing" not in prompt


def test_inspection_method_cannot_be_dropped_or_borrowed_from_other_clause():
    frame = audit.build_source_frame("夜間會隨機查班。", "zh", "id")
    for target in ("Akan ada pemeriksaan shift malam.",
                   "Pilih material secara acak. Akan ada pemeriksaan shift malam."):
        ok, issues = audit.validate_translation(frame, target)
        assert not ok
        assert "factory_semantic_audit:random_inspection_method_missing" in issues


def test_unscheduled_timing_is_distinct_from_random_sampling():
    frame = audit.build_source_frame("安衛不定時入廠抽查。", "zh", "id")
    target = "Bagian K3 melakukan pemeriksaan acak di pabrik"
    assert "factory_semantic_audit:unscheduled_inspection_timing_missing" in audit.validate_translation(frame, target)[1]
    assert audit.validate_translation(frame, target + " tanpa jadwal tetap.")[0]


@pytest.mark.parametrize("source", [
    "隨機挑料，夜間查班。", "機台不定時停機，班長檢查設備。", "隨機抽取材料做PMI。",
])
def test_unrelated_modifiers_do_not_invent_inspection_schedule(source):
    frame = audit.build_source_frame(source, "zh", "id")
    assert not frame["flags"].get("unscheduled_check")
    assert not frame["flags"].get("random_inspection")


@pytest.mark.parametrize("candidate", [
    TARGET.replace("5. Jangan mencampur material dan jangan salah menempelkan label produk. Pemeriksaan PMI wajib dilakukan.", ""),
    TARGET.replace("PMI", ""),
    TARGET.replace("3. Lebih peka", "7. Lebih peka"),
    TARGET.replace("secara acak", "sesuai jadwal tetap"),
])
def test_complete_notice_gate_still_rejects_lost_points_codes_or_changed_facts(candidate):
    assert app._final_delivery_guard(SOURCE, candidate, "zh", "id") is None


def test_validator_exception_does_not_discard_an_available_translation(monkeypatch):
    def broken(*_args, **_kwargs):
        raise RuntimeError("local validator unavailable")
    monkeypatch.setattr(app.tqg_module, "validate_translation", broken)
    monkeypatch.setattr(app, "translation_cache", {})
    assert app._final_delivery_guard(SOURCE, TARGET, "zh", "id") == TARGET
    assert app._tl.delivery_degraded is True
    assert not app.is_translation_acceptable(SOURCE, TARGET, "zh", "id")
    app.cache_set(SOURCE, "zh", "id", TARGET)
    assert app.translation_cache == {}


@pytest.mark.parametrize("source", ["!請注意PMI檢測", "/交辦 請先清潔設備", "Я", "Да", "مرحبا", "မင်္ဂလာပါ", "é", "好"])
def test_input_accepts_language_content_without_an_alphabet_allowlist(source):
    assert app.has_translatable_content(source)


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "retry.db"))
    monkeypatch.setattr(app, "_ensure_translation_retry_worker", lambda: False)
    monkeypatch.setattr(app, "_TRANSLATION_RETRY_INFLIGHT", set())
    monkeypatch.setattr(app, "_processed_msg_ids", app._collections_dedup.OrderedDict())
    monkeypatch.setattr(app, "message_cache", {})
    monkeypatch.setattr(app, "translation_cache", {})
    monkeypatch.setitem(app.group_tracking, "notice-group", {"name": "研磨C班", "joined_at": 1})
    monkeypatch.setitem(app.group_settings, "notice-group", True)
    monkeypatch.setitem(app.group_skip_users, "notice-group", set())
    monkeypatch.setitem(app.group_target_lang, "notice-group", "id")
    for name in ("record_user_name", "show_loading", "mark_as_read", "track_group_usage",
                 "_stats_inc", "_event_log_write", "_record_recent_group_message"):
        monkeypatch.setattr(app, name, lambda *_a, **_k: None)
    monkeypatch.setattr(app, "get_group_feature", lambda *_a, **_k: False)
    monkeypatch.setattr(app, "get_group_target_langs", lambda _gid: ["id"])
    monkeypatch.setattr(app, "get_group_tone", lambda *_a: ("natural", ""))
    monkeypatch.setattr(app, "get_tts_enabled", lambda *_a: False)
    monkeypatch.setattr(app, "get_auto_tone_emoji_enabled", lambda *_a, **_k: False)
    monkeypatch.setattr(app, "_build_translation_action_quick_reply", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_build_expression_visual_message", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "get_display_name", lambda *_a: "主管")
    monkeypatch.setattr(app, "get_user_picture_url", lambda *_a: "")
    monkeypatch.setattr(app, "get_sender_object", lambda *_a, **_k: None)
    # A paid provider boundary is the only substituted translation stage.
    state = SimpleNamespace(generations=[], sends=[], provider_down=False, reply_down=False,
                            push_down=False, provider_result=TARGET)
    def provider(text, src, tgt):
        state.generations.append((text, src, tgt))
        return None if state.provider_down else state.provider_result
    monkeypatch.setattr(app, "translate_openai", provider)
    monkeypatch.setattr(app.nmt_module, "nmt_translate", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "translate_google", lambda *_a, **_k: None)
    class Client:
        def __init__(self, *_a, **_k): pass
        def __enter__(self): return self
        def __exit__(self, *_a): return False
    class Api:
        def reply_message(self, req, **kwargs):
            if state.reply_down:
                raise TimeoutError("LINE reply timeout")
            state.sends.append(("reply", copy.deepcopy(req), kwargs))
            return SimpleNamespace(sent_messages=[])
        def push_message(self, req, **kwargs):
            if state.push_down:
                raise TimeoutError("LINE push timeout")
            state.sends.append(("push", copy.deepcopy(req), kwargs))
            return SimpleNamespace(sent_messages=[])
    monkeypatch.setattr(app, "ApiClient", Client)
    monkeypatch.setattr(app, "MessagingApi", lambda _client: Api())
    yield state


def event(text=SOURCE):
    return SimpleNamespace(
        message=SimpleNamespace(id="notice-message", text=text, quote_token="quote",
                                quoted_message_id=None, mention=None, mark_as_read_token=None),
        source=SimpleNamespace(group_id="notice-group", room_id=None, user_id="supervisor"),
        reply_token="reply", delivery_context=None,
    )


def delivered_text(state):
    return "".join(message.text for _, request, _ in state.sends for message in request.messages)


def retry_pending():
    key = "notice-group:notice-message"
    assert queue.claim_job(key, owner="recovery")
    return app._run_translation_retry_job(queue.get(key), "recovery")


def test_actual_text_handler_sends_all_five_points_with_one_primary_generation(runtime):
    app.handle_message(event())
    assert len(runtime.generations) == 1
    assert runtime.generations[0] == (SOURCE, "zh", "id")
    assert TARGET in delivered_text(runtime)
    assert queue.pending_count() == 0
    app.handle_message(event())
    assert len(runtime.generations) == 1
    assert len(runtime.sends) == 1


def test_translation_outage_retains_whole_source_and_recovers_without_resend(runtime):
    runtime.provider_down = True
    app.handle_message(event())
    assert not runtime.sends
    pending = queue.get("notice-group:notice-message")
    assert pending["payload"]["source_text"] == SOURCE
    assert pending["payload"]["target_langs"] == ["id"]
    runtime.provider_down = False
    assert retry_pending()
    assert TARGET in delivered_text(runtime)
    assert queue.pending_count() == 0


def test_line_outage_reuses_completed_notice_without_paying_for_translation_again(runtime):
    runtime.reply_down = runtime.push_down = True
    # The webhook returns an error so LINE may redeliver; the durable outbox
    # also recovers independently if that redelivery never arrives.
    with pytest.raises(TimeoutError):
        app.handle_message(event())
    assert len(runtime.generations) == 1
    assert not runtime.sends
    pending = queue.get("notice-group:notice-message")
    assert TARGET in pending["payload"]["delivery"]["text"]
    runtime.push_down = False
    assert retry_pending()
    assert len(runtime.generations) == 1
    assert TARGET in delivered_text(runtime)
    assert queue.pending_count() == 0


@pytest.mark.parametrize("prefix", ["!", "/交辦 "])
def test_punctuation_prefix_is_not_a_silent_translation_opt_out(runtime, prefix):
    app.handle_message(event(prefix + SOURCE))
    assert len(runtime.generations) == 1
    assert TARGET in delivered_text(runtime)


def test_known_commands_and_explicit_translation_controls_remain_functional(runtime, monkeypatch):
    monkeypatch.setattr(app, "handle_command", lambda *_a: "COMMAND RESULT")
    app.handle_message(event("/pkg U"))
    assert not runtime.generations
    assert delivered_text(runtime) == "COMMAND RESULT"


@pytest.mark.parametrize("source,target", [("Да", "是"), ("مرحبا", "你好"), ("မင်္ဂလာပါ", "你好")])
def test_unrecognized_scripts_reach_auto_detection_and_are_delivered(runtime, source, target):
    runtime.provider_result = target
    app.handle_message(event(source))
    assert len(runtime.generations) == 1
    assert runtime.generations[0] == (source, "auto", "zh")
    assert target in delivered_text(runtime)
    assert queue.pending_count() == 0
