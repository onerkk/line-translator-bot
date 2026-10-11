"""Catalog grounding, numeric roles, source-unit scope and reuse boundaries.

All provider responses below are offline fixtures, not translation evaluations
by another model. Runtime tests use the real text/OCR route and verify requests.
"""
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
import factory_station_catalog as stations
import factory_terminology as terms
import factory_translation_guard as guard
import prompt_optimizer as prompts
import translation_alignment as alignment
import translation_learning_policy as learning
import translation_quality_gate as quality

SOURCE = "Bayak barang yang di tolak 480"
BAD = "有很多料件被拒收，數量為480"
GOOD = "有很多料件被480檢驗站拒收。"
RELEASE_SOURCE = "有降速粗拋過，生產完成放行資料給480時備註：\n表面不佳，已降速粗拋。"
RELEASE_TARGET = ("Sudah dilakukan poles kasar dengan kecepatan diturunkan. "
                  "Setelah produksi selesai, saat data di-release ke stasiun inspeksi 480, beri catatan:\n"
                  "Permukaan kurang baik, sudah dilakukan poles kasar dengan kecepatan diturunkan.")


def prompt_document(source, src="id", tgt="zh", **kwargs):
    original = terms.build_translation_prompt(source, terms.approved_glossary(), src, tgt)
    prompt, stats = prompts.compile_translation_prompt(original, source, src, tgt, **kwargs)
    content = prompt.split("<factory_station_catalog>", 1)[1].split("</factory_station_catalog>", 1)[0]
    return json.JSONDecoder().raw_decode(content.lstrip())[0], prompt


def test_existing_asset_keeps_all_departments_names_equipment_and_aliases():
    # This literal-count assertion protects migration loss; runtime users share
    # the same objects rather than maintaining another mutable code dictionary.
    data = stations.catalog()
    assert len(data["departments"]) == 7
    assert len(data["names"]) == 63
    assert len(data["equipment"]) == 82
    assert data["names"]["檢驗站"] == {"no": 480, "dept": "品質管理課", "id": "Stasiun inspeksi"}
    import app
    assert app.STATION_NAMES is stations.STATION_NAMES
    assert app.STATION_CODES is stations.STATION_CODES
    assert app.STATION_DEPARTMENTS is stations.STATION_DEPARTMENTS
    assert app.FACTORY_STATION_ALIAS_RULES is stations.FACTORY_STATION_ALIAS_RULES


@pytest.mark.parametrize("variant", [
    SOURCE, "Banyak barang ditolak 480.", "Banyak barang ditolak oleh 480.",
    "Banyak barang di tolak oleh stasiun inspeksi 480.",
    "480 menolak banyak barang.", "Stasiun inspeksi 480 menolak banyak barang.",
])
def test_rejection_actor_is_catalog_identity_even_with_mobile_typing(variant):
    facts = stations.station_facts(variant, "id")
    assert [(row["code"], row["role"], row["event"]) for row in facts] == [("480", "actor", "reject")]
    assert not quality.validate_translation(variant, BAD, "id", "zh").ok
    assert quality.validate_translation(variant, GOOD, "id", "zh").ok


NUMBERS = sorted({str(row["no"]) for row in stations.STATION_NAMES.values() if row["no"] is not None} |
                 {str(row["station"]) for row in stations.STATION_CODES.values()})


@pytest.mark.parametrize("code", NUMBERS)
def test_rejection_and_destination_contract_is_not_one_station_patch(code):
    source, good, bad = f"Banyak barang ditolak {code}.", f"很多料件被{code}站拒收。", f"很多料件被拒收，數量為{code}。"
    assert stations.translation_issues(source, bad, "id", "zh")
    assert not stations.translation_issues(source, good, "id", "zh")
    source = f"放行資料給{code}"
    assert not stations.translation_issues(source, f"Release data ke stasiun {code}.", "zh", "id")
    assert stations.translation_issues(source, f"Stasiun {code} me-release data.", "zh", "id")


@pytest.mark.parametrize("source,src", [
    ("Banyak barang ditolak, jumlah 480.", "id"),
    ("Banyak barang ditolak sebanyak 480 buah.", "id"),
    ("Jumlah barang ditolak adalah 480.", "id"),
    ("480件料件被拒收。", "zh"), ("生產480分鐘。", "zh"),
    ("Proses produksi selama 480 menit.", "id"),
    ("拋光輪#480×2。", "zh"), ("Roda poles #480 x 2.", "id"),
    ("尺寸Ø480mm。", "zh"), ("速度480 rpm。", "zh"),
    ("480.5件。", "zh"), ("@480 請回報。", "zh"),
    ("工單480放行。", "zh"), ("Jenis 480 ditolak.", "id"),
    ('站名標籤是「480」。', "zh"), ("Banyak barang ditolak 999.", "id"),
    ("品管拒收480", "zh"),
])
def test_units_specs_quotes_mentions_and_unknown_codes_do_not_become_stations(source, src):
    assert not stations.station_facts(source, src)


def test_same_number_can_be_count_and_station_in_one_sentence():
    source = "給480檢驗站檢驗480件"
    target = "Kirim 480 potong ke stasiun inspeksi 480 untuk diperiksa."
    assert quality.validate_translation(source, target, "zh", "id").ok
    assert [row["role"] for row in stations.station_facts(source, "zh")] == ["destination"]
    assert [row["role"] for row in stations.station_facts(target, "id")] == ["destination"]


def test_station_inspection_name_is_not_itself_a_completed_inspection_action():
    facts = stations.station_facts("Stasiun inspeksi 480.", "id")
    assert [(row["role"], row["event"]) for row in facts] == [("identifier", "")]


def test_bad_station_quantity_never_enters_runtime_cache(monkeypatch):
    import app
    monkeypatch.setattr(app, "translation_cache", {})
    app.cache_set(SOURCE, "id", "zh", BAD)
    assert app.cache_get(SOURCE, "id", "zh") is None
    app.cache_set(SOURCE, "id", "zh", GOOD)
    assert app.cache_get(SOURCE, "id", "zh") == GOOD


@pytest.mark.parametrize("source,target", [
    ("大量品質問題麻煩降速改成粗拋生產",
     "Karena banyak masalah kualitas, mohon kurangi kecepatan dan ubah proses produksi menjadi poles kasar."),
    ("拋光輪需換成＃240×2（紅色）", "Roda poles harus diganti menjadi # 240×2 (warna merah)."),
    (RELEASE_SOURCE, RELEASE_TARGET),
    ("480都不驗", "Stasiun 480 tidak melakukan pemeriksaan."),
])
def test_correct_process_specification_and_release_targets_still_pass(source, target):
    report = quality.validate_translation(source, target, "zh", "id")
    assert report.ok, report.issues


@pytest.mark.parametrize("spec", ["#240×2", "#270×2", "＃480×3"])
def test_specification_expression_is_preserved_and_never_calculated(spec):
    source = "拋光輪換成" + spec
    assert not stations.station_facts(source, "zh")
    assert not stations.translation_issues(source, "Ganti roda poles menjadi " + spec.replace("＃", "#") + ".", "zh", "id")
    assert any("specification_changed" in issue for issue in stations.translation_issues(source, "Ganti roda poles menjadi 480.", "zh", "id"))


def test_role_cannot_be_satisfied_by_another_source_unit():
    source = "Banyak barang ditolak 480.\nKirim datanya ke 490."
    contract = alignment.build_contract(source, "id", "zh")
    assert contract["aligned"]
    good = {"u1": "很多料件被480站拒收。", "u2": "資料送給490站。"}
    assert not alignment.translation_issues(contract, "\n".join(good.values()), segments=good)
    swapped = {"u1": good["u2"], "u2": good["u1"]}
    assert alignment.translation_issues(contract, "\n".join(swapped.values()), segments=swapped)


def test_catalog_survives_tight_prompt_budget_and_stays_source_relevant():
    data, prompt = prompt_document(SOURCE, max_chars=1000)
    assert data["facts"][0]["code"] == "480"
    assert data["facts"][0]["role"] == "actor"
    assert data["facts"][0]["event"] == "reject"
    assert "檢驗站" in data["facts"][0]["names_zh"]
    assert "數量為480" not in prompt and "數量為480" not in Path(stations.__file__).read_text()
    assert "E1-1" not in prompt and "直棒連續退火爐" not in prompt
    assert prompt.count("<factory_station_catalog>") == 1
    assert prompts.prompt_contains_required_invariants(prompt)


@pytest.mark.parametrize("code,row", list(stations.STATION_CODES.items()))
def test_every_existing_equipment_row_is_retrievable(code, row):
    data, prompt = prompt_document("Mesin " + code + " rusak.")
    matches = [fact for fact in data["facts"] if fact.get("equipment") == code]
    assert matches
    if code in stations.catalog().get("equipment_ambiguities", {}):
        assert set(matches[0]["station_options"]) == {452, 453}
        assert matches[0]["process_requires_context"] and "station" not in matches[0]
    else:
        assert matches[0]["zh"] == row["zh"] and matches[0]["id"] == row["id"]


def test_equipment_only_station_number_is_in_the_shared_number_catalog():
    data, prompt = prompt_document("Banyak barang ditolak 420.")
    assert data["facts"][0]["code"] == "420"
    assert any("冷抽" in name for name in data["facts"][0]["names_zh"])


def test_quantity_cannot_be_reclassified_as_known_station():
    report = quality.validate_translation("480件料件被拒收。", "Stasiun 480 menolak banyak barang.", "zh", "id")
    assert not report.ok and "factory_station:quantity_as_identifier:480" in report.issues


def test_equipment_compound_code_does_not_import_its_prefix_equipment():
    data, prompt = prompt_document("Mesin E1-1 rusak.")
    assert [fact["equipment"] for fact in data["facts"] if "equipment" in fact] == ["E1-1"]


@pytest.mark.parametrize("source,expected", [
    ("異型那站幫忙", "異型包裝站"), ("研磨股那邊", "Bagian Grinding"),
    ("冷抽二股那邊", "Bagian Cold Drawing 2"), ("二股那邊", "Bagian Cold Drawing 2"),
    ("削皮站優先放行", "Stasiun peeling"),
])
def test_existing_station_shorthand_and_department_names_survive_compilation(source, expected):
    data, prompt = prompt_document(source, "zh", "id")
    assert expected in prompt


def test_bad_role_is_rejected_at_correction_guard_and_learning_boundaries():
    import active_learning
    assert not guard.validate_translation(SOURCE, BAD, "id", "zh").ok
    assert guard.validate_translation(SOURCE, GOOD, "id", "zh").ok
    assert not active_learning.validate_correction(SOURCE, BAD, "id", "zh")["ok"]
    valid = active_learning.validate_correction(SOURCE, GOOD, "id", "zh")
    assert valid["ok"], valid
    def validator(source, target, src, tgt):
        report = quality.validate_translation(source, target, src, tgt)
        return {"ok": report.ok, "issues": report.issues}
    lessons = learning.derive(SOURCE, BAD, GOOD, "id", "zh", reviewed=True, cacheable=True, validator=validator)
    assert "station_identity" in {row["category"] for row in lessons}
    assert "c:station_identity" in lessons[0]["features"]
    assert not learning.derive(SOURCE, GOOD, BAD, "id", "zh", reviewed=True, cacheable=True, validator=validator)


def test_catalog_edit_invalidates_validator_memoization_and_policy_identity(tmp_path, monkeypatch):
    import active_learning
    original = stations.catalog()
    data = json.loads(json.dumps(original))
    local = tmp_path / "stations.json"
    local.write_text(json.dumps(data))
    monkeypatch.setattr(stations, "_PATH", local)
    first = stations.fingerprint()
    policy_before = active_learning._validator_fingerprint()
    assert not quality.validate_translation(SOURCE, BAD, "id", "zh").ok
    data["names"]["檢驗站"]["no"] = 999
    local.write_text(json.dumps(data) + "\n")
    assert stations.fingerprint() != first
    assert active_learning._validator_fingerprint() != policy_before
    assert quality.validate_translation(SOURCE, BAD, "id", "zh").ok


@pytest.mark.parametrize("ocr", [False, True], ids=["text", "ocr"])
def test_real_entry_gets_station_fact_in_one_generation_and_repeat_uses_zero(monkeypatch, ocr):
    import app
    monkeypatch.setattr(app, "_tl", threading.local())
    monkeypatch.setattr(app, "translation_cache", {})
    monkeypatch.setattr(app, "_event_log_write", lambda *args, **kwargs: None)
    monkeypatch.setattr(app, "_post_translation_async", lambda *args, **kwargs: None)
    app._tl.group_id = "G-oct12-stations-" + str(ocr)
    app._tl.from_image_ocr = ocr
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        prompt = kwargs["messages"][0]["content"]
        assert "<factory_station_catalog>" in prompt and '"code":"480"' in prompt
        assert '"role":"actor"' in prompt and "檢驗站" in prompt
        assert "SOURCE_UNITS" not in kwargs["messages"][-1]["content"]
        response = SimpleNamespace(model="offline-fixture", choices=[SimpleNamespace(
            message=SimpleNamespace(content=GOOD), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0))
        ok, reason = kwargs["response_validator"](response, "fixture")
        assert ok, reason
        return response
    monkeypatch.setattr(app.ai.chat.completions, "create", create)
    result = app.translate(SOURCE, "id", "zh")
    assert "480" in result and "數量為" not in result
    assert quality.validate_translation(SOURCE, result, "id", "zh").ok
    assert len(calls) == 1 and calls[0]["translation_max_generations"] == 1
    assert app.translate(SOURCE, "id", "zh") == result
    assert len(calls) == 1
