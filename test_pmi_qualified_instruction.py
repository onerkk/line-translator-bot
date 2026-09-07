"""Inspection purpose/material qualifiers must not hide the operation's modal."""
import pytest

import factory_pmi_semantics as pmi
import factory_source_understanding as understanding
from factory_instruction_semantics import build_relations


@pytest.mark.parametrize("target", [
    "Pemeriksaan PMI untuk memastikan jenis baja wajib dilakukan.",
    "PMI untuk verifikasi grade baja harus selalu dilakukan.",
    "PMI untuk memeriksa jenis baja wajib dilakukan.",
    "Pengujian PMI pada setiap bundel harus dilakukan.",
    "Pemeriksaan PMI terhadap material ini mesti dilaksanakan.",
])
def test_requirement_survives_inspection_qualifiers_in_both_directions(target):
    source = "PMI一定要檢測。"
    facts = pmi.build_facts(source, "zh")
    assert pmi.validate(facts, target, "id") == []
    reverse = pmi.build_facts(target, "id")
    assert [fact["mode"] for fact in reverse] == ["required"]
    assert pmi.validate(reverse, source, "zh") == []
    assert pmi.validate(reverse, "PMI已經做完。", "zh")


@pytest.mark.parametrize("ending,mode,zh", [
    ("belum dilakukan", "pending", "PMI還沒做。"),
    ("sudah dilakukan", "completed", "PMI已經做完。"),
    ("tidak boleh dilakukan", "prohibited", "不要做PMI。"),
    ("tidak perlu dilakukan", "optional", None),
])
def test_qualifiers_never_turn_a_status_or_negation_into_a_requirement(ending, mode, zh):
    target = "Pemeriksaan PMI untuk memastikan jenis baja " + ending + "."
    reverse = pmi.build_facts(target, "id")
    assert [fact["mode"] for fact in reverse] == [mode]
    assert pmi.validate(pmi.build_facts("PMI一定要檢測。", "zh"), target, "id")
    if zh:
        assert pmi.validate(pmi.build_facts(zh, "zh"), target, "id") == []


@pytest.mark.parametrize("target", [
    "Pemeriksaan PMI untuk laporan sudah dicatat dan pengemasan wajib dilakukan.",
    "Pemeriksaan PMI untuk memastikan jenis baja jika perlu dilakukan.",
    "Pemeriksaan PMI untuk memastikan jenis baja belum dilakukan dan pengemasan wajib dilakukan.",
])
def test_requirement_cannot_be_borrowed_from_a_condition_or_another_operation(target):
    assert pmi.validate(pmi.build_facts("PMI一定要檢測。", "zh"), target, "id")


def test_runtime_prompt_describes_pmi_as_an_operation_with_a_material_purpose():
    prompt = understanding.build_prompt(understanding.analyze("PMI一定要檢測。", "zh"))
    assert "Pemeriksaan PMI" in prompt
    assert "鋼種" in prompt


def test_noncurrent_temporary_records_keep_the_record_category_in_the_prompt():
    source = "系統還沒改好，非本月暫存先注意以下。\n1.不要跨班別存進去。\n2.非本月的帳先移出。"
    relations = build_relations(source)
    periods = [r for r in relations if r["kind"] == "noncurrent_period"]
    assert all("資料分類" in r["meaning_zh"] for r in periods)
    assert "catatan sementara" in periods[0]["required_target_meaning_id"]


def test_noncurrent_shipping_does_not_inherit_record_meaning_from_another_item():
    source = "1.非本月的木箱暫不裝箱。\n2.非本月的帳先移出。"
    periods = {r["item"]: r for r in build_relations(source) if r["kind"] == "noncurrent_period"}
    assert "資料分類" not in periods["1"]["meaning_zh"]
    assert "資料分類" in periods["2"]["meaning_zh"]


def test_physical_temporary_storage_does_not_become_an_account_entry():
    source = "非本月的材料暫存在儲區，請把材料移出儲區。"
    periods = [r for r in build_relations(source) if r["kind"] == "noncurrent_period"]
    assert len(periods) == 1
    assert "資料分類" not in periods[0]["meaning_zh"]
    assert "catatan" not in periods[0]["required_target_meaning_id"]
