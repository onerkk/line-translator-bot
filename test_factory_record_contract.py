"""Real screenshot regressions plus unseen values, paraphrases and countercases.

These are offline acceptance/delivery tests, not live model accuracy scores.
"""
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

import app
import factory_record_contract as records
import translation_quality_gate as quality
import translation_retry_queue as queue
from test_translation_notice_availability import runtime, event, delivered_text, retry_pending


SCREENSHOT_QUESTION = "這是入非本月沒有換TAG嗎？"
QUESTION_TARGET = "Apakah data ini sudah dimasukkan ke kategori bukan untuk bulan ini, tetapi TAG-nya belum diganti?"
WEIGHTS = "實重682 TAG入677"


@pytest.fixture(autouse=True)
def fresh_context():
    previous = dict(app._tl.__dict__)
    app._tl.__dict__.clear()
    yield
    app._tl.__dict__.clear()
    app._tl.__dict__.update(previous)


@pytest.mark.parametrize("actual,tag", [(682, 677), (941, 936), (1180, 1172), (205, 198)])
@pytest.mark.parametrize("source_format", ["實重{actual} TAG入{tag}", "TAG寫{tag}，實際重量{actual}", "實重 {actual}\nTAG 記錄 {tag}"])
def test_values_stay_bound_to_their_fields_not_just_present(actual, tag, source_format):
    source = source_format.format(actual=actual, tag=tag)
    good = f"Berat aktual {actual}, angka pada TAG tercatat {tag}."
    bad = f"Berat aktual {tag}, angka pada TAG tercatat {actual}."
    assert quality.validate_translation(source, good, "zh", "id").ok
    rejected = quality.validate_translation(source, bad, "zh", "id")
    assert not rejected.ok
    assert any(issue.startswith("record_field_value:") for issue in rejected.hard_issues)
    assert records.render_complete(records.build_frame(source, "zh", "id"))


@pytest.mark.parametrize("target", [
    "Berat aktual 682, TAG dicatat 677.",
    "Berat sebenarnya 682; berat yang tertera pada TAG 677.",
    "Berat pada TAG tercatat 677, sedangkan berat aktual 682.",
    "Hasil penimbangan 682, nilai pada TAG tertulis 677.",
    "Berat aktual dan berat pada TAG masing-masing 682 dan 677.",
])
def test_natural_wording_does_not_cause_false_translation_outage(target):
    assert quality.validate_translation(WEIGHTS, target, "zh", "id").ok
    assert app._final_delivery_guard(WEIGHTS, target, "zh", "id") == target


@pytest.mark.parametrize("source,target,ok", [
    (WEIGHTS, "Berat aktual 682 kg; berat pada TAG 677 kg.", False),
    ("實重682公斤 TAG入677公斤", "Berat aktual 682 kg; berat pada TAG 677 kg.", True),
    ("實重682公斤 TAG入677公斤", "Berat aktual 682 ton; berat pada TAG 677 kg.", False),
    ("實重682.5 TAG入677.5", "Berat aktual 682,5; berat pada TAG 677,5.", True),
    ("毛重805kg 淨重792kg", "Berat kotor 805 kg; berat bersih 792 kg.", True),
    ("毛重805kg 淨重792kg", "Berat kotor 792 kg; berat bersih 805 kg.", False),
])
def test_unit_presence_and_field_roles(source, target, ok):
    assert records.validate_translation(records.build_frame(source, "zh", "id"), target)[0] is ok


@pytest.mark.parametrize("target,ok", [
    (QUESTION_TARGET, True),
    ("Apakah ini sudah dicatat dalam kategori bukan untuk bulan ini dan TAG-nya belum diganti?", True),
    ("Ini masuk bukan bulan ini, TAG-nya belum diganti ya?", False),
    ("Apakah data ini masuk ke kategori bulan lalu dan TAG-nya belum diganti?", False),
    ("Apakah data ini sudah dimasukkan ke kategori bukan untuk bulan ini, tetapi TAG-nya sudah diganti?", False),
    ("Data ini sudah dimasukkan ke kategori bukan untuk bulan ini, tetapi TAG-nya belum diganti.", False),
])
def test_record_category_is_not_arrival_time_and_preserves_question(target, ok):
    result = quality.validate_translation(SCREENSHOT_QUESTION, target, "zh", "id")
    assert result.ok is ok, result.issues
    contract = app.build_translation_semantic_contract(SCREENSHOT_QUESTION, "zh", "id")
    assert app.translation_satisfies_semantic_contract(contract, target)[0] is ok
    assert "record_facts" in app.build_translation_semantic_contract_prompt(contract)


@pytest.mark.parametrize("source", [
    "這把有印象為什麼TAG要寫682嗎？",  # question about a TAG, no weight assertion
    "實重682 TAG入677，可以改嗎？",  # cannot silently omit an added question
    "如果實重682 TAG入677，先不要生產。",  # condition, not a completed event
    "請把實重682 TAG入677的資料刪除。",
    "TAG 7J821007",  # identifier, not a weight
    "非本月的材料明天入庫。",  # physical inventory, no record-category assertion
    "放入非本月的材料儲區。",  # explicit physical destination overrides bare 入
])
def test_partial_or_ambiguous_sources_never_take_local_full_translation(source):
    frame = records.build_frame(source, "zh", "id")
    assert records.render_complete(frame) is None
    if "入庫" in source or "儲區" in source:
        assert not frame["record_category"]


def test_reverse_direction_keeps_data_roles():
    source = "Berat aktual 941, angka pada TAG tercatat 936."
    assert quality.validate_translation(source, "實重941，TAG登錄值936。", "id", "zh").ok
    assert not quality.validate_translation(source, "實重936，TAG登錄值941。", "id", "zh").ok
    assert records.render_complete(records.build_frame(source, "id", "zh"))


def test_field_only_message_is_delivered_even_when_all_providers_are_down(runtime):
    runtime.provider_down = True
    app.handle_message(event(WEIGHTS))
    text = delivered_text(runtime)
    assert "Berat aktual 682" in text and "TAG tercatat 677" in text
    assert not runtime.generations
    assert not queue.pending_count()


def test_verified_fields_survive_line_outage_without_regeneration(runtime):
    runtime.provider_down = runtime.reply_down = runtime.push_down = True
    with pytest.raises(TimeoutError):
        app.handle_message(event(WEIGHTS))
    assert not runtime.sends and not runtime.generations
    assert queue.get("notice-group:notice-message")["payload"]["delivery"]
    runtime.push_down = False
    assert retry_pending()
    assert "Berat aktual 682" in delivered_text(runtime)
    assert not runtime.generations and not queue.pending_count()


def test_bad_category_does_not_get_cached_and_recovery_delivers_correct_question(runtime):
    runtime.provider_result = "Ini masuk bukan bulan ini, TAG-nya belum diganti ya?"
    app.handle_message(event(SCREENSHOT_QUESTION))
    assert not runtime.sends
    assert queue.get("notice-group:notice-message")["payload"]["source_text"] == SCREENSHOT_QUESTION
    assert app.cache_get(SCREENSHOT_QUESTION, "zh", "id") is None
    runtime.provider_result = QUESTION_TARGET
    assert retry_pending()
    assert QUESTION_TARGET in delivered_text(runtime)
    assert not queue.pending_count()


def test_source_facts_reach_first_provider_prompt(runtime, monkeypatch):
    # Restore the real prompt-building boundary; substitute only remote API.
    from pathlib import Path
    import ast
    source = Path(app.__file__).read_text()
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "translate_openai")
    namespace = dict(vars(app))
    exec(compile(ast.Module(body=[node], type_ignores=[]), app.__file__, "exec"), namespace)
    seen = []
    def provider(**kwargs):
        seen.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=QUESTION_TARGET), finish_reason="stop")])
    monkeypatch.setattr(app.ai.chat.completions, "create", provider)
    monkeypatch.setattr(app, "translate_openai", namespace["translate_openai"])
    runtime.provider_result = QUESTION_TARGET
    app.handle_message(event(SCREENSHOT_QUESTION))
    assert len(seen) == 1
    prompt = "\n".join(row["content"] for row in seen[0]["messages"])
    assert "<record_facts>" in prompt and "RECORD CATEGORY" in prompt
    assert QUESTION_TARGET in delivered_text(runtime)


def test_queue_reads_use_one_connection_without_losing_schema_recovery(monkeypatch, tmp_path):
    path = tmp_path / "queue.db"
    monkeypatch.setattr(queue, "DB_PATH", str(path))
    queue.enqueue("job", {"source_text": WEIGHTS})
    original = queue._connect
    connects = []
    def connect():
        connection = original()
        connects.append(connection)
        return connection
    monkeypatch.setattr(queue, "_connect", connect)
    assert queue.get("job")["payload"]["source_text"] == WEIGHTS
    assert len(connects) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        connects[0].execute("SELECT 1")  # no lingering connection after read
    # Changing the path in the same process must still initialize the new DB.
    monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "replacement.db"))
    queue.enqueue("replacement", {"source_text": "new"})
    assert queue.get("replacement")
    with original() as connection:
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2


def test_concurrent_claims_still_deliver_each_job_to_one_owner(monkeypatch, tmp_path):
    monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "queue.db"))
    for index in range(8):
        queue.enqueue(str(index), {"source_text": str(index)})
    with ThreadPoolExecutor(max_workers=4) as workers:
        claimed = list(workers.map(lambda index: queue.claim_due_jobs(owner=str(index), limit=8), range(4)))
    keys = [job["job_key"] for batch in claimed for job in batch]
    assert len(keys) == len(set(keys)) == 8


@pytest.mark.parametrize("inspection", ["next_ready_delay", "pending_count"])
def test_startup_database_glitch_cannot_strand_persisted_translation(monkeypatch, tmp_path, inspection):
    from durable_workers import WorkerPool
    monkeypatch.setattr(queue, "DB_PATH", str(tmp_path / "queue.db"))
    queue.enqueue("persisted", {"source_text": WEIGHTS})
    real_inspect = getattr(queue, inspection)
    first = [True]
    def transient(**kwargs):
        if first.pop() if first else False:
            raise sqlite3.OperationalError("temporary storage failure after durable enqueue")
        return real_inspect(**kwargs)
    monkeypatch.setattr(queue, inspection, transient)
    completed = threading.Event()
    def deliver(job, owner):
        assert job["payload"]["source_text"] == WEIGHTS
        assert queue.mark_delivered(job["job_key"], owner=owner)
        completed.set()
        return True
    pool = WorkerPool("record-recovery", deliver, workers=1, include_kinds=("text",))
    try:
        assert pool.ensure_started()
        assert completed.wait(3), "persisted translation was stranded without a new webhook"
        assert queue.was_delivered("persisted")
    finally:
        pool.stop()


def test_completed_and_question_states_are_not_interchangeable():
    source = "這是入非本月已換TAG嗎？"
    good = "Apakah data ini sudah dimasukkan ke kategori bukan untuk bulan ini dan TAG-nya sudah diganti?"
    bad = good.replace("TAG-nya sudah", "TAG-nya belum")
    frame = records.build_frame(source, "zh", "id")
    assert records.validate_translation(frame, good)[0]
    assert not records.validate_translation(frame, bad)[0]
    frame = records.build_frame("實重682，TAG入677嗎？", "zh", "id")
    assert not records.validate_translation(frame, "Berat aktual 682, TAG dicatat 677.")[0]
    frame = records.build_frame(good, "id", "zh")
    assert not records.validate_translation(frame, "資料已入非本月，但TAG還沒換嗎？")[0]


def test_coordinated_fields_cannot_swallow_unparsed_followup_instructions():
    source = "Berat aktual dan berat pada TAG masing-masing 941 dan 936. Jangan produksi dulu."
    assert records.render_complete(records.build_frame(source, "id", "zh")) is None


def test_current_explicit_values_do_not_inherit_previous_units_or_numbers(runtime, monkeypatch):
    monkeypatch.setattr(app, "get_conv_context_enabled", lambda *_: True)
    app._conversation_journal().capture("notice-group", "previous", "實重100公斤 TAG入90公斤", author="supervisor", lang="zh")
    runtime.provider_down = True
    app.handle_message(event(WEIGHTS))
    text = delivered_text(runtime)
    assert "Berat aktual 682" in text and "TAG tercatat 677" in text
    assert "100" not in text and "kg" not in text
    assert not runtime.generations


def test_revalidation_and_policy_identity_prevent_bad_cache_reuse(runtime, monkeypatch):
    wrong = "Berat aktual 677, TAG dicatat 682."
    key = (WEIGHTS, "zh", "id", app._translation_cache_scope())
    app.translation_cache[key] = (wrong, time.time(), app._translation_cache_asset_fingerprint())
    assert app.cache_get(WEIGHTS, "zh", "id") is None
    assert key not in app.translation_cache
    correct = "Berat aktual 682, TAG dicatat 677."
    app.cache_set(WEIGHTS, "zh", "id", correct)
    assert app.cache_get(WEIGHTS, "zh", "id") == correct
    monkeypatch.setattr(records, "BUILD_ID", "next-record-meaning-revision")
    assert app.cache_get(WEIGHTS, "zh", "id") is None


def test_record_prohibition_accepts_natural_active_indonesian():
    source = "不要入非本月，TAG還沒換。"
    target = "Jangan memasukkan data ke kategori bukan untuk bulan ini, TAG-nya belum diganti."
    assert quality.validate_translation(source, target, "zh", "id").ok
    assert app._final_delivery_guard(source, target, "zh", "id") == target


@pytest.mark.parametrize("source", ["Bersih 805 dan 806 dulu.", "Kotor 941, tolong bersihkan dulu."])
def test_clean_dirty_words_without_weight_context_are_not_weight_fields(source):
    assert not records.build_frame(source, "id", "zh")["active"]
