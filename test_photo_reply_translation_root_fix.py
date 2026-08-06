from __future__ import annotations

import importlib

import factory_translation_policy as policy
import prompt_optimizer
import translation_quality_gate as qg
import translation_retry_queue as retry_queue


SOURCE = '''@All Perhatian kepada seluruh operator.

Jika pada Work Order terdapat tulisan "NO Kondom", tetapi di bagian lain juga terdapat instruksi berbahasa Taiwan dengan tanda (Y) yang berarti WAJIB menggunakan kondom, jangan langsung menjalankan proses.

Segera laporkan kepada Ketua Regu untuk dilakukan pengecekan dan penanganan terlebih dahulu. Terkadang terdapat kesalahan pada Work Order, sehingga kita semua wajib lebih teliti sebelum memulai produksi.

Jangan mengambil keputusan sendiri apabila terdapat informasi yang saling bertentangan. Ketelitian setiap operator sangat penting untuk mencegah kesalahan proses dan menjaga kualitas produk. Terima kasih atas kerja samanya.'''

VALID_ZH = '''@All 請所有作業員注意。

如果工單上寫著「NO Kondom」，但其他欄位又有台灣中文指示並標示（Y），代表必須使用保護套，請勿直接開始作業。

請立即向班長回報，先進行確認與處理。工單偶爾可能出現錯誤，因此大家在開始生產前務必更加仔細。

若資訊互相矛盾，不要自行做決定。每位作業員的細心對防止製程錯誤及維持產品品質非常重要。感謝大家配合。'''


def test_multiword_quoted_control_label_and_parenthesized_flag_are_immutable():
    envelope = qg.inspect_immutable_spans(SOURCE)
    literals = list(envelope.mapping.values())

    assert "@All" in literals
    assert "NO Kondom" in literals
    assert "Y" in literals


def test_correct_full_translation_is_not_rejected_as_untranslated_source():
    envelope = qg.inspect_immutable_spans(SOURCE)
    report = qg.validate_translation(
        SOURCE,
        VALID_ZH,
        "id",
        "zh",
        immutable_literals=envelope.mapping.values(),
        require_paragraph_fidelity=True,
    )

    assert report.ok, report.issues
    assert "untranslated_source_word:NO" not in report.issues
    assert "untranslated_source_word:Y" not in report.issues


def test_missing_control_label_is_still_an_objective_integrity_failure():
    envelope = qg.inspect_immutable_spans(SOURCE)
    candidate = VALID_ZH.replace("NO Kondom", "禁止使用")
    report = qg.validate_translation(
        SOURCE,
        candidate,
        "id",
        "zh",
        immutable_literals=envelope.mapping.values(),
        require_paragraph_fidelity=True,
    )

    assert not report.ok
    assert "missing_literal:NO Kondom" in report.hard_issues


def test_normal_quoted_sentence_remains_translatable_not_immutable():
    source = 'Dia berkata "tolong tunggu sebentar" sebelum pergi.'
    envelope = qg.inspect_immutable_spans(source)

    assert "tolong tunggu sebentar" not in envelope.mapping.values()
    assert not qg.is_quality_critical(source, "id", "zh", factory_domain=True)


def test_control_rule_is_compositional_not_bound_to_one_complete_sentence():
    variant = 'ERP menunjukkan "HOLD LOT 7", tetapi kolom lain bertanda (N). Jangan proses; laporkan ke atasan.'
    envelope = qg.inspect_immutable_spans(variant)
    literals = set(envelope.mapping.values())

    assert "HOLD LOT 7" in literals
    assert "N" in literals
    rules = prompt_optimizer._matching_historical_rules(variant, "id>zh", limit=10)
    assert any("work-order-control-label-conflict" in rule for rule in rules)
    prompt = policy.build_prompt(variant, "id", "zh")
    assert "quoted control label" in prompt
    assert "single-letter flag" in prompt
    assert "conflict" in prompt


def test_retry_queue_survives_module_reload(tmp_path, monkeypatch):
    db_path = tmp_path / "translation-retry.db"
    monkeypatch.setattr(retry_queue, "DB_PATH", str(db_path))
    payload = {
        "group_id": "group-1",
        "message_id": "message-1",
        "source_text": SOURCE,
        "src_lang": "id",
        "target_langs": ["zh"],
    }

    assert retry_queue.enqueue("group-1:message-1", payload, delay_seconds=0) is True
    assert retry_queue.pending_count() == 1

    reloaded = importlib.reload(retry_queue)
    monkeypatch.setattr(reloaded, "DB_PATH", str(db_path))
    pending = reloaded.list_pending()
    assert len(pending) == 1
    assert pending[0]["payload"]["message_id"] == "message-1"

    reloaded.reschedule("group-1:message-1", delay_seconds=1, error="provider_timeout")
    job = reloaded.get("group-1:message-1")
    assert job is not None
    assert job["attempts"] == 1
    assert job["last_error"] == "provider_timeout"

    reloaded.mark_delivered("group-1:message-1")
    assert reloaded.pending_count() == 0
