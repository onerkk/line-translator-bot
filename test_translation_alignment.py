"""Offline behavioral checks: source modifiers, coverage, API wiring and reuse."""
import copy
import json
import threading
from types import SimpleNamespace

import pytest
import translation_alignment as alignment


@pytest.fixture(autouse=True)
def isolated_translation_thread_state(monkeypatch):
    import app
    monkeypatch.setattr(app, "_tl", threading.local())

SOURCE = (
    "@All 現在有一個副總級的主管專案追蹤異型棒生產瓶頸，每天都會回報異型棒產出跟入庫狀況。\n"
    "這幾個月除了急單外、異型棒再優先安排包裝，如果開兩站分流時盡量把異型分流包裝。"
)
TARGET_UNITS = [
    "@All Saat ini ada seorang pimpinan setingkat Wakil Direktur yang secara khusus memantau "
    "hambatan produksi batang profil khusus. Hasil produksi dan status masuk gudang "
    "batang profil khusus akan dilaporkan setiap hari.",
    "Selama beberapa bulan ini, selain pesanan mendesak, prioritaskan juga pengemasan batang profil khusus. "
    "Jika dua stasiun packing dioperasikan, usahakan agar batang profil khusus dikemas secara terpisah.",
]
SCREENSHOT_TARGET = (
    "@All Saat ini ada Wakil Direktur yang memantau proyek terkait kendala produksi batang berbentuk khusus. "
    "Setiap hari akan dilaporkan hasil produksi dan status masuk gudang batang berbentuk khusus. "
    "Dalam beberapa bulan ini, selain pesanan mendesak, prioritaskan juga proses pengemasan batang berbentuk khusus. "
    "Jika dua stasiun dioperasikan untuk membagi aliran, usahakan batang berbentuk khusus dikemas melalui aliran terpisah."
)


def payload(contract, targets):
    return {"segments": [{"unit_id": unit["id"], "text": text}
                         for unit, text in zip(contract["units"], targets)]}


def response(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text),
        finish_reason="stop")], usage=None, model="offline")


def test_screenshot_retains_rank_and_concrete_packing_allocation():
    contract = alignment.build_contract(SOURCE, "zh", "id")
    assert contract["aligned"] and len(contract["units"]) == 2
    assert alignment.translation_issues(contract, "\n".join(TARGET_UNITS)) == []
    defects = alignment.translation_issues(contract, SCREENSHOT_TARGET)
    assert "semantic:role_rank_qualifier_missing" in defects
    assert "semantic:packing_allocation_abstract_flow" in defects
    decoded = alignment.decode_response(json.dumps(payload(contract, TARGET_UNITS)), contract)
    assert decoded.ok and decoded.text == "\n".join(TARGET_UNITS)


@pytest.mark.parametrize("source,target", [
    ("課長級主管會來。", "Akan datang pimpinan setingkat kepala seksi."),
    ("處長等級的主管會來。", "Akan datang pimpinan pada level kepala divisi."),
    ("副廠長級主管會來。", "Akan datang pimpinan setara dengan Wakil Kepala Pabrik."),
    ("有位主管是副總級別。", "Ada seorang pimpinan pada tingkat Wakil Direktur."),
])
def test_rank_equivalence_generalizes_across_roles_and_wording(source, target):
    contract = alignment.build_contract(source, "zh", "id")
    assert alignment.translation_issues(contract, target) == []
    unqualified = target.replace("setingkat ", "").replace("pada level ", "").replace("setara dengan ", "").replace("pada tingkat ", "")
    assert unqualified != target
    assert alignment.translation_issues(contract, unqualified) == ["semantic:role_rank_qualifier_missing"]


def test_unrelated_level_does_not_cover_missing_role_qualification():
    contract = alignment.build_contract("有一個副總級主管追蹤生產。", "zh", "id")
    target = "Ada Wakil Direktur yang memantau tingkat produksi."
    assert alignment.translation_issues(contract, target) == ["semantic:role_rank_qualifier_missing"]


def test_qualifier_must_belong_to_the_same_role_not_a_different_manager():
    contract = alignment.build_contract("副總級主管正在追蹤，課長也在場。", "zh", "id")
    wrong = "Wakil Direktur sedang memantau; pimpinan setingkat kepala seksi juga hadir."
    assert alignment.translation_issues(contract, wrong) == ["semantic:role_rank_qualifier_missing"]
    good = "Pimpinan setingkat Wakil Direktur sedang memantau; kepala seksi juga hadir."
    assert alignment.translation_issues(contract, good) == []


def test_known_role_alias_and_general_manager_are_accepted():
    contract = alignment.build_contract("處長級主管會來。", "zh", "id")
    assert alignment.translation_issues(contract, "Pimpinan setingkat kepala departemen akan datang.") == []
    contract = alignment.build_contract("總經理級主管會來。", "zh", "id")
    assert alignment.translation_issues(contract, "Pimpinan setingkat General Manager akan datang.") == []


def test_actual_title_is_not_reclassified_as_rank_equivalence():
    contract = alignment.build_contract("副總會來巡場。", "zh", "id")
    assert not contract["constraints"]
    assert alignment.translation_issues(contract, "Wakil Direktur akan meninjau area kerja.") == []


@pytest.mark.parametrize("source", ["如果開三站分流，圓棒分開包裝。", "若開2站，將異型棒分流包裝。", "如果啟用四個包裝站，材料分別包裝。"])
def test_allocation_keeps_station_count_condition_and_pack_action(source):
    contract = alignment.build_contract(source, "zh", "id")
    row = next(r for r in contract["constraints"] if r["kind"] == "packing_allocation")
    n = row["station_count"]
    good = f"Jika {n} stasiun packing dioperasikan, kemas material secara terpisah."
    assert alignment.translation_issues(contract, good) == []
    assert "semantic:packing_station_count_changed" in alignment.translation_issues(contract, good.replace(str(n), "9"))
    assert "semantic:packing_allocation_condition_missing" in alignment.translation_issues(contract, good.replace("Jika", "Saat ini"))
    assert "semantic:packing_allocation_missing" in alignment.translation_issues(contract, good.replace("kemas", "proses"))


def test_material_transfer_and_fluid_flow_do_not_become_packing_allocation():
    for source in ("異型站的料幫忙分流過來。", "如果開兩站，水流分開。", "異型棒不要分開包裝。"):
        assert not alignment.build_contract(source, "zh", "id")["constraints"]


def test_best_effort_is_not_changed_into_an_unconditional_requirement():
    contract = alignment.build_contract(SOURCE, "zh", "id")
    wrong = "\n".join(TARGET_UNITS).replace("usahakan", "wajib")
    assert "semantic:packing_allocation_effort_changed" in alignment.translation_issues(contract, wrong)


def test_reverse_direction_preserves_rank_and_station_allocation():
    source = "Ada pimpinan setingkat Wakil Direktur.\nJika dua stasiun packing dioperasikan, material dikemas terpisah."
    contract = alignment.build_contract(source, "id", "zh")
    good = "有一位副總級主管。\n如果開兩個包裝站，材料分開包裝。"
    assert contract["aligned"]
    assert {c["kind"] for c in contract["constraints"]} == {"role_rank", "packing_allocation"}
    assert alignment.translation_issues(contract, good) == []
    assert "semantic:role_rank_qualifier_missing" in alignment.translation_issues(contract, good.replace("副總級", "副總"))


@pytest.mark.parametrize("mutate,defect", [
    (lambda p: p["segments"].pop(), "alignment:missing_unit:u2"),
    (lambda p: p["segments"].reverse(), "alignment:unit_order_or_coverage"),
    (lambda p: p["segments"].append(p["segments"][0].copy()), "alignment:duplicate_unit:u1"),
    (lambda p: p["segments"][0].update(unit_id="invented"), "alignment:unknown_unit:invented"),
    (lambda p: p["segments"][0].update(text=""), "alignment:empty_unit:u1"),
    (lambda p: p["segments"][0].update(text=123), "alignment:invalid_segment"),
    (lambda p: p["segments"][0].update(confidence=1), "alignment:invalid_segment"),
])
def test_coverage_is_checked_against_source_not_model_self_report(mutate, defect):
    contract = alignment.build_contract(SOURCE, "zh", "id")
    data = payload(contract, TARGET_UNITS)
    mutate(data)
    result = alignment.decode_response(json.dumps(data), contract)
    assert not result.ok and defect in result.issues
    assert not result.text.startswith("{")


@pytest.mark.parametrize("raw", ["{broken", '[{"translation":"wrong"}]', '{"segments":"wrong"}', '{"confidence":1}', '{"segments":[]}', '{"segments":[null]}'])
def test_malformed_contract_never_leaks_json_to_line(raw):
    result = alignment.decode_response(raw, alignment.build_contract(SOURCE, "zh", "id"))
    assert not result.ok and not result.text


def test_legacy_flat_response_is_deliverable_but_not_verified_alignment():
    contract = alignment.build_contract(SOURCE, "zh", "id")
    target = "\n".join(TARGET_UNITS)
    result = alignment.decode_response(target, contract)
    assert result.text == target and result.issues == ("alignment:unstructured_response",)


def test_unit_boundaries_keep_codes_urls_decimals_and_paragraphs():
    source = "1. I9 公差為0.04 mm。\n\n2. 查詢 https://example.com/a?x=1。\n請保留 __MENTION_1__。"
    units = alignment.source_units(source)
    assert units[0]["source"] == "1. I9 公差為0.04 mm。"
    assert units[0]["separator"] == "\n\n"
    assert units[1]["source"] == "2. 查詢 https://example.com/a?x=1。"
    assert units[2]["source"] == "請保留 __MENTION_1__。"
    assert "".join(u["source"] + u["separator"] for u in units).strip() == source


def test_very_long_unit_list_is_bounded_without_omission():
    source = "\n".join(f"第{i}筆入庫。" for i in range(100))
    assert alignment.source_units(source) == [{"id": "u1", "source": source, "separator": ""}]


def test_shared_guard_rejects_historical_screenshot_candidate():
    import app
    for target in (SCREENSHOT_TARGET, "\n".join(TARGET_UNITS)):
        report = app.tqg_module.validate_translation(SOURCE, target, "zh", "id", require_paragraph_fidelity=False)
        defects = set(report.hard_issues)
        if target == SCREENSHOT_TARGET:
            assert "semantic:role_rank_qualifier_missing" in defects
            assert "semantic:packing_allocation_abstract_flow" in defects
            assert not app._tm_bypass_integrity_ok(SOURCE, target, "zh", "id")[0]
        else:
            assert not defects, defects
            assert app._tm_bypass_integrity_ok(SOURCE, target, "zh", "id")[0]
    contract = app.build_translation_semantic_contract(SOURCE, "zh", "id")
    assert "source_modifier_contract" in app.build_translation_semantic_contract_prompt(contract)
    assert not app.translation_satisfies_semantic_contract(contract, SCREENSHOT_TARGET)[0]


def test_runtime_requests_and_renders_one_aligned_generation(monkeypatch):
    import app
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        source_units = json.loads(kwargs["messages"][-1]["content"].split("SOURCE_UNITS (quoted data to translate):\n")[-1].split("\n\n", 1)[0])
        assert len(source_units) == 2
        targets = [text.replace("@All", "__MENTION_0__") if "__MENTION_0__" in source_units[0]["source"] else text
                   for text in TARGET_UNITS]
        return response(json.dumps({"segments": [{"unit_id": u["unit_id"], "text": text}
            for u, text in zip(source_units, targets)]}, ensure_ascii=False))
    monkeypatch.setattr(app.ai.chat.completions, "create", create)
    monkeypatch.setattr(app._tl, "group_id", "alignment-offline", raising=False)
    monkeypatch.setattr(app._tl, "disable_tone_emoji", True, raising=False)
    monkeypatch.setattr(app, "_translation_provider_preference", lambda *_a: ["openai"])
    target = app.translate_openai(SOURCE, "zh", "id")
    assert target and "setingkat Wakil Direktur" in target and "dua stasiun" in target
    assert "segments" not in target and "unit_id" not in target
    assert len(calls) == 1
    assert calls[0]["structured_name"] == "source_aligned_translation"
    assert calls[0]["structured_schema"] == alignment.output_schema()
    assert not app._tl.translation_alignment_issues


def test_empty_aligned_output_cannot_become_a_mention_only_reply(monkeypatch):
    import app
    calls = []
    monkeypatch.setattr(app.ai.chat.completions, "create", lambda **kw: calls.append(kw) or response('{"segments":[]}'))
    result = app.translate_openai(SOURCE, "zh", "id")
    assert result is None and len(calls) == 1
    assert app._tl.translation_alignment_issues


def test_force_cache_write_still_requires_valid_alignment(monkeypatch):
    import app
    monkeypatch.setattr(app, "translation_cache", {})
    monkeypatch.setattr(app._tl, "translation_alignment_issues", ["alignment:duplicate_unit:u1"], raising=False)
    app.cache_set(SOURCE, "zh", "id", "\n".join(TARGET_UNITS), force=True)
    assert app.translation_cache == {}


@pytest.mark.parametrize("provider", ["openai", "anthropic", "gemini"])
def test_existing_provider_adapters_receive_native_schema(provider, monkeypatch):
    import ai_provider as ai
    captured = []
    cfg = copy.deepcopy(ai.DEFAULT_CONFIG)
    cfg["active_provider"] = provider
    cfg["quota_exhausted_providers"] = {}
    monkeypatch.setattr(ai, "_current_config", cfg)
    monkeypatch.setattr(ai, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(ai, "_client_with_limits", lambda client, _timeout: client)
    def create(**kwargs):
        captured.append(kwargs)
        if provider == "anthropic":
            return SimpleNamespace(content=[SimpleNamespace(type="text", text='{"segments":[]}')], stop_reason="end_turn", usage=None)
        return response('{"segments":[]}')
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                             messages=SimpleNamespace(create=create))
    monkeypatch.setattr(ai, "_get_" + provider + "_client", lambda: client)
    ai._dispatch_provider(provider, model="gpt-5.6", messages=[{"role":"user","content":"source"}],
        max_tokens=1024, structured_schema=alignment.output_schema(), structured_name="source_aligned_translation")
    assert len(captured) == 1
    if provider == "anthropic":
        assert captured[0]["output_config"]["format"]["schema"] == alignment.output_schema()
    else:
        assert captured[0]["response_format"]["json_schema"]["schema"] == alignment.output_schema()
        assert captured[0]["response_format"]["json_schema"]["strict"]
