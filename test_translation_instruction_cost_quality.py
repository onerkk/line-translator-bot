"""Actual reported meaning reversals and shared outbound-request budgets.

All external AI and LINE transport is stubbed. These measure code behavior,
not a live model's accuracy, response time or invoice.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import app
import ai_provider
import factory_instruction_semantics as instructions
import factory_semantic_audit as audit
import translation_casebook as casebook
from test_translation_notice_availability import SOURCE, TARGET


CORRECT_NOTICE = """Sistem belum selesai diperbaiki sepenuhnya. Untuk penyimpanan sementara data kategori bukan bulan ini, perhatikan hal-hal berikut agar atasan tidak menjadikannya alasan untuk menyalahkan kita.

1. Jangan menginput data lintas shift. Data kategori bukan bulan ini dari shift pagi, siang, dan malam harus dimasukkan sesuai shift masing-masing.

2. Keluarkan dulu catatan yang sudah dimasukkan ke kategori bukan bulan ini agar selisih waktu packing dan waktu input data tidak terlalu jauh.

3. Lebih peka dan hati-hati. Dalam waktu dekat akan ada pemeriksaan shift malam secara acak. Jangan sampai masalahnya membuat atasan memeriksa rekaman CCTV.

4. Perhatikan kewajaran jumlah produksi dan jangan biarkan persediaan tidak bisa turun. Selama tidak ada kejadian besar, atasan tidak akan terlalu mempermasalahkan waktu input stok.

5. Jangan mencampur material dan jangan salah menempelkan label produk. Pemeriksaan PMI wajib dilakukan."""

BAD_MUTATIONS = [
    ("jangan sampai stok sulit berkurang", "jangan biarkan persediaan turun"),
    ("atasan tidak akan terlalu mengawasi waktu masuk gudang", "atasan tidak akan menentukan waktu penyimpanannya"),
    ("Lebih peka terhadap situasi", "Terlihat lebih baik"),
    ("memeriksa rekaman CCTV", "menyesuaikan monitornya"),
    ("shift", "kelas"),
    ("Keluarkan dulu catatan material", "Hapus dulu akun material"),
    ("bukan untuk bulan ini", "bulan lalu"),
]


def response(text):
    return SimpleNamespace(choices=[SimpleNamespace(
        message=SimpleNamespace(content=text), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        model="offline", _jy_provider="anthropic")


@pytest.fixture(autouse=True)
def isolate_context(monkeypatch):
    previous = dict(app._tl.__dict__)
    app._tl.__dict__.clear()
    monkeypatch.setattr(app, "translation_cache", {})
    yield
    app._tl.__dict__.clear()
    app._tl.__dict__.update(previous)


@pytest.mark.parametrize("old,new", BAD_MUTATIONS)
def test_reported_bad_meanings_rejected_by_provider_and_final_boundary(old, new):
    bad = TARGET.replace(old, new)
    assert bad != TARGET
    app._tl.semantic_contract = app.build_translation_semantic_contract(SOURCE, "zh", "id")
    validate = app._build_translation_response_validator(SOURCE, "zh", "id")
    assert not validate(response(bad), "offline")[0]
    assert app._final_delivery_guard(SOURCE, bad, "zh", "id") is None


def test_user_reviewed_meaning_is_accepted_without_a_whole_sentence_shortcut():
    assert app.factory_translation_guard_module.exact_verified_target(SOURCE, "zh", "id") is None
    app._tl.semantic_contract = app.build_translation_semantic_contract(SOURCE, "zh", "id")
    assert app._build_translation_response_validator(SOURCE, "zh", "id")(response(CORRECT_NOTICE), "offline")[0]
    assert app._final_delivery_guard(SOURCE, CORRECT_NOTICE, "zh", "id") == CORRECT_NOTICE


@pytest.mark.parametrize("source", [
    "不要讓庫存降不下來。", "別讓存貨一直不下降。", "避免庫存無法降低。",
    "不要讓庫存降低不了。", "要讓庫存降得下來。", "請確保庫存可以下降。",
])
@pytest.mark.parametrize("target", [
    "Jangan biarkan persediaan tidak bisa turun.",
    "Jangan sampai persediaan macet, tidak turun-turun.",
    "Pastikan stok bisa berkurang.",
    "Stok harus dapat terus berkurang.",
])
def test_nested_negation_generalizes_to_rewording_and_natural_targets(source, target):
    frame = audit.build_source_frame(source, "zh", "id")
    assert frame["active"]
    assert audit.validate_translation(frame, target) == (True, [])
    assert not audit.validate_translation(frame, "Jangan biarkan stok turun.")[0]


@pytest.mark.parametrize("source,target,wrong", [
    ("不要讓庫存下降。", "Jangan biarkan stok turun.", "Pastikan stok bisa berkurang."),
    ("庫存降不下來。", "Stok sulit berkurang.", "Jangan sampai stok sulit berkurang."),
    ("早班先移出已存進去的資料。", "Shift pagi keluarkan dulu data yang sudah dimasukkan.", "Shift pagi hapus akun terlebih dahulu."),
    ("機靈一點，主管會調出監視器畫面。", "Lebih sigap. Atasan akan melihat rekaman CCTV.", "Terlihat lebih baik. Atasan menyesuaikan monitor."),
    ("主管不大會去盯入帳時間。", "Atasan tidak akan terlalu memeriksa waktu input data.", "Atasan tidak akan menentukan waktu penyimpanan."),
])
def test_opposite_state_or_different_action_is_not_the_same_claim(source, target, wrong):
    frame = audit.build_source_frame(source, "zh", "id")
    assert frame["active"]
    assert audit.validate_translation(frame, target)[0]
    assert not audit.validate_translation(frame, wrong)[0]


def test_numbered_item_cannot_borrow_another_items_correct_negation():
    source = "1.不要讓庫存降不下來。\n2.不要讓庫存下降。"
    bad = "1. Jangan biarkan stok turun.\n2. Pastikan stok bisa berkurang."
    issues = instructions.validate_relations(instructions.build_relations(source), bad)
    assert len(issues) == 2


@pytest.mark.parametrize("source", ["把監視器角度調整一下。", "這個月沒上早班。", "刪除舊帳號。", "班級裡的學生表現很好。"])
def test_unrelated_literal_actions_do_not_activate_notice_relations(source):
    assert instructions.build_relations(source) == []


def test_generic_notice_words_do_not_retrieve_equipment_rebuild_or_self_reporting():
    rows = app._retrieve_verified_translation_cases(SOURCE, "zh", "id", max_cases=8)
    assert not {"incident_voluntary_self_report", "automatic_electronic_to_natural_passive_pull"} & {r["case_id"] for r in rows}
    prompt = casebook.build_prompt(rows)
    assert len(prompt) <= 2400
    assert "Current source changes" not in prompt


@pytest.fixture
def offline_transport(monkeypatch):
    cfg = copy.deepcopy(ai_provider.DEFAULT_CONFIG)
    cfg["active_provider"] = "anthropic"
    cfg["provider_failover"] = True
    cfg["quota_exhausted_providers"] = {}
    for provider in ("anthropic", "openai", "gemini"):
        cfg[provider]["api_key"] = "offline-test-key"
    monkeypatch.setattr(ai_provider, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(ai_provider, "_current_config", cfg)
    monkeypatch.setattr(ai_provider, "_circuit_is_open", lambda *_a: False)
    monkeypatch.setattr(ai_provider, "_record_provider_success", lambda *_a: None)
    monkeypatch.setattr(ai_provider, "_record_provider_failure", lambda *_a: None)
    monkeypatch.setattr(ai_provider, "_notify_admin", lambda *_a, **_k: None)
    return cfg


def test_primary_repair_and_review_share_two_generations(offline_transport, monkeypatch):
    calls = []
    def dispatch(provider, **kwargs):
        calls.append((provider, kwargs))
        return response(TARGET.replace(*BAD_MUTATIONS[0]) if len(calls) == 1 else CORRECT_NOTICE)
    monkeypatch.setattr(ai_provider, "_dispatch_provider", dispatch)
    @ai_provider.translation_request_budget
    def run():
        app._tl.semantic_contract = app.build_translation_semantic_contract(SOURCE, "zh", "id")
        first = ai_provider.chat_complete(model=ai_provider.DEFAULT_OPENAI_UPGRADE_MODEL,
            messages=[{"role": "user", "content": SOURCE}], translation_max_generations=2,
            response_validator=app._build_translation_response_validator(SOURCE, "zh", "id"))
        assert first.choices[0].message.content == CORRECT_NOTICE
        with pytest.raises(TimeoutError):
            ai_provider.chat_complete(model=ai_provider.DEFAULT_OPENAI_UPGRADE_MODEL,
                messages=[{"role": "user", "content": "review"}])
        assert ai_provider.translation_budget_snapshot()["generations"] == 2
    run()
    assert len(calls) == 2
    assert any("inventory_change" in str(m) for m in calls[1][1]["messages"])
    assert ai_provider.translation_budget_snapshot() == {}


def test_budget_deadline_does_not_reset_for_review(offline_transport, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(ai_provider.time, "monotonic", lambda: clock[0])
    calls = []
    def dispatch(provider, **kwargs):
        calls.append(kwargs["timeout"])
        clock[0] += 32
        return response("ok")
    monkeypatch.setattr(ai_provider, "_dispatch_provider", dispatch)
    @ai_provider.translation_request_budget
    def run():
        for _ in range(2):
            ai_provider.chat_complete(model="test", messages=[{"role": "user", "content": "text"}], failover_total_timeout=90)
    run()
    assert calls[1] <= 3


def test_transport_failures_also_have_a_shared_limit(offline_transport, monkeypatch):
    calls = []
    def dispatch(provider, **kwargs):
        calls.append(provider)
        raise TimeoutError("offline transport timeout")
    monkeypatch.setattr(ai_provider, "_dispatch_provider", dispatch)
    @ai_provider.translation_request_budget
    def run():
        for _ in range(3):
            with pytest.raises(TimeoutError):
                ai_provider.chat_complete(model="test", messages=[{"role": "user", "content": "text"}])
    run()
    assert len(calls) == 3


def test_concurrent_requests_and_exceptions_do_not_share_budgets(offline_transport, monkeypatch):
    monkeypatch.setattr(ai_provider, "_dispatch_provider", lambda *_a, **_k: response("ok"))
    @ai_provider.translation_request_budget
    def run(_):
        ai_provider.chat_complete(model="test", messages=[{"role": "user", "content": "text"}])
        return ai_provider.translation_budget_snapshot()["generations"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert list(pool.map(run, range(6))) == [1] * 6
    @ai_provider.translation_request_budget
    def fail():
        raise RuntimeError("test")
    with pytest.raises(RuntimeError):
        fail()
    assert ai_provider.translation_budget_snapshot() == {}


def test_compiled_claude_prompt_never_caches_message_specific_claims(offline_transport, monkeypatch):
    recorder = Mock(return_value=SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok", citations=None)],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0),
        stop_reason="end_turn"))
    client = SimpleNamespace(messages=SimpleNamespace(create=recorder))
    monkeypatch.setattr(ai_provider, "_get_anthropic_client", lambda: client)
    monkeypatch.setattr(ai_provider, "_client_with_limits", lambda c, _t: c)
    monkeypatch.setattr(ai_provider, "_resolve_anthropic_model", lambda _: "claude-sonnet-5")
    stable = "<role>Translator.</role><translation_principles>" + "Preserve facts. " * 2000 + "</translation_principles>"
    dynamic = "<semantic_contract>per-message claims</semantic_contract>"
    ai_provider._chat_complete_anthropic("test", [{"role": "system", "content": stable + dynamic}, {"role": "user", "content": SOURCE}], 100, fast_quality=True)
    payload = recorder.call_args.kwargs
    cached = [b for b in payload["system"] if "cache_control" in b]
    assert cached and all("per-message claims" not in b["text"] for b in cached)
    assert all(b["cache_control"].get("ttl") != "1h" for b in cached)
    assert "<thinking_protocol>" not in str(payload)


def test_known_terminology_is_not_an_automatic_model_upgrade(monkeypatch):
    monkeypatch.setenv("TRANSLATION_CP_ROUTER_ENABLED", "1")
    source = "請檢查機台護罩。"
    app._tl.semantic_contract = app.build_translation_semantic_contract(source, "zh", "id")
    assert app._tl.semantic_contract["has_risk"]
    assert app.pick_model(source) == ai_provider.DEFAULT_OPENAI_MODEL
    assert app.pick_model(SOURCE) == ai_provider.DEFAULT_OPENAI_UPGRADE_MODEL


def test_rejected_routine_candidate_upgrades_within_the_same_budget(offline_transport, monkeypatch):
    models = []
    def dispatch(_provider, **kwargs):
        models.append(kwargs["model"])
        return response("bad" if len(models) == 1 else "ok")
    monkeypatch.setattr(ai_provider, "_dispatch_provider", dispatch)
    @ai_provider.translation_request_budget
    def run():
        return ai_provider.chat_complete(model=ai_provider.DEFAULT_OPENAI_MODEL,
            messages=[{"role": "user", "content": "text"}],
            translation_max_generations=2,
            translation_repair_model=ai_provider.DEFAULT_OPENAI_UPGRADE_MODEL,
            response_validator=lambda result, _: (result.choices[0].message.content == "ok", "wrong action"))
    assert run().choices[0].message.content == "ok"
    assert models == [ai_provider.DEFAULT_OPENAI_MODEL, ai_provider.DEFAULT_OPENAI_UPGRADE_MODEL]


def test_public_pipeline_repairs_notice_in_two_generations_and_keeps_it_cacheable(offline_transport, monkeypatch):
    calls = []
    cached = []
    def dispatch(provider, **kwargs):
        calls.append(kwargs)
        return response(TARGET.replace(*BAD_MUTATIONS[0]) if len(calls) == 1 else CORRECT_NOTICE)
    monkeypatch.setattr(ai_provider, "_dispatch_provider", dispatch)
    monkeypatch.setattr(app, "cache_get", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "cache_set", lambda *a, **_k: cached.append(a))
    monkeypatch.setattr(app, "get_recent_media_scene", lambda *_a, **_k: "")
    monkeypatch.setattr(app, "get_conv_context_enabled", lambda *_a, **_k: False)
    monkeypatch.setattr(app, "translate_google", lambda *_a, **_k: pytest.fail("valid repair must not fall through to NMT"))
    monkeypatch.setattr(app._BG_POST_EXECUTOR, "submit", lambda *_a, **_k: None)
    app._tl.disable_tone_emoji = True
    app._tl.group_id = "cost-quality-offline"
    result = app.translate(SOURCE, "zh", "id")
    assert result == CORRECT_NOTICE
    assert len(calls) == 2
    assert any(CORRECT_NOTICE in row for row in cached)
    primary_prompt = "\n".join(str(m["content"]) for m in calls[0]["messages"])
    assert "庫存" in primary_prompt and "jangan sampai stok sulit berkurang" in primary_prompt
    assert "班別不是學校班級" in primary_prompt
    assert "Current source changes" not in primary_prompt


def test_usage_observes_rejected_generations_and_review_once(offline_transport, monkeypatch):
    records = []
    monkeypatch.setattr(ai_provider, "_USAGE_OBSERVER", records.append)
    monkeypatch.setattr(ai_provider, "_dispatch_provider", lambda *_a, **_k: response("ok"))
    ai_provider.chat_complete(model="test", messages=[{"role": "user", "content": "a"}],
        translation_max_generations=2, response_validator=lambda *_a: (False, "wrong action"))
    ai_provider.chat_complete(model="test", messages=[{"role": "user", "content": "review"}])
    assert len(records) == 3
    assert len({id(r) for r in records}) == 3
    monkeypatch.setattr(app, "bot_stats", {})
    item = response("ok")
    item.model = "gpt-5.6-luna"
    item._jy_provider = "openai"
    app.track_tokens(item)
    app.track_tokens(item)
    assert app.bot_stats["tokens_prompt"] == 1


def test_clean_notice_stays_one_generation_with_full_semantic_validation(offline_transport, monkeypatch):
    calls = []
    monkeypatch.setattr(ai_provider, "_dispatch_provider", lambda *a, **k: calls.append(k) or response(CORRECT_NOTICE))
    monkeypatch.setattr(app, "cache_get", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "get_recent_media_scene", lambda *_a, **_k: "")
    monkeypatch.setattr(app, "get_conv_context_enabled", lambda *_a, **_k: False)
    monkeypatch.setattr(app._BG_POST_EXECUTOR, "submit", lambda *_a, **_k: None)
    monkeypatch.setattr(app.al_module, "assess_review_risk", lambda *_a, **_k: {"requires_review": False, "matches": []})
    app._tl.disable_tone_emoji = True
    app._tl.group_id = "clean-notice-offline"
    assert app.translate(SOURCE, "zh", "id") == CORRECT_NOTICE
    assert len(calls) == 1
    assert calls[0]["model"] == ai_provider.DEFAULT_OPENAI_UPGRADE_MODEL
    assert app._final_delivery_guard(SOURCE, TARGET.replace(*BAD_MUTATIONS[0]), "zh", "id") is None


def test_incidents_and_learned_errors_still_receive_review():
    policy = app.factory_translation_policy_module
    assert not policy.adaptive_review_risk(SOURCE, "zh", "id", quality_critical=True)
    assert policy.adaptive_review_risk("已經發生混料，不要再混料。", "zh", "id")
    assert policy.adaptive_review_risk(SOURCE, "zh", "id", quality_critical=True, learned_risk=True)


def test_repeated_list_numbers_keep_separate_claim_scopes():
    source = "1.不要讓庫存下降。\n2.確認資料。\n\n1.不要讓庫存降不下來。"
    target = "1. Jangan biarkan stok turun.\n2. Periksa data.\n\n1. Pastikan stok bisa berkurang."
    assert instructions.validate_relations(instructions.build_relations(source), target) == []


def test_explicit_previous_period_is_not_a_noncurrent_period_mistranslation():
    source = "非本月資料包含上個月資料。"
    target = "Data bukan bulan ini mencakup data bulan lalu."
    assert instructions.validate_relations(instructions.build_relations(source), target) == []
