import types

import pytest
import ai_provider
import app
import translation_quality_gate as tqg


def test_lowercase_line_display_name_is_not_language_leakage():
    source = (
        "@蘇比 sobirin @(杰弗) Jika Anda menemukan bahwa Anda tidak akan "
        "menyemprot cat, kinerjanya akan dikurangi 0,5 secara langsung."
    )
    candidate = "@蘇比 sobirin @(杰弗) 如果發現你沒有噴漆，績效將直接扣除0.5。"

    report = tqg.validate_translation(source, candidate, "id", "zh")

    assert report.ok, report.issues
    assert app._final_delivery_guard(source, candidate, "id", "zh") == candidate


def test_final_guard_withholds_objectively_corrupt_result_for_provider_fallback():
    result = app._final_delivery_guard(
        "Jangan masuk.",
        "禁止 masuk。",
        "id",
        "zh",
    )

    assert result is None
    assert not app._is_translation_failure_sentinel("正常翻譯")
    assert app._is_translation_failure_sentinel("翻譯服務暫時未取得可用結果")


def test_inner_pipeline_defers_unverified_factory_provider_text_to_authoritative_gate(monkeypatch):
    provider_text = "粗磨（ROUGH GRINDING）每次至少 0.04 mm。"
    cached = []

    monkeypatch.setattr(app, "translate_openai", lambda *_args, **_kwargs: provider_text)
    monkeypatch.setattr(app, "translate_google", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app, "finalize_factory_translation", lambda _s, value, _sl, _tl: value)
    monkeypatch.setattr(app, "is_translation_acceptable", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(app, "cache_set", lambda *args, **kwargs: cached.append((args, kwargs)))

    previous = getattr(app._tl, "quality_gate_critical", None)
    app._tl.quality_gate_critical = True
    try:
        actual = app._translate_inner(
            "ROUGH GRINDING minimal 0,04 mm.",
            "id",
            "zh",
        )
    finally:
        if previous is None:
            try:
                delattr(app._tl, "quality_gate_critical")
            except AttributeError:
                pass
        else:
            app._tl.quality_gate_critical = previous

    assert actual == provider_text
    assert cached == []


def test_pure_equipment_code_is_intentional_skip_not_translation_failure(monkeypatch):
    provider_calls = []
    monkeypatch.setattr(
        app,
        "translate_openai",
        lambda *_args, **_kwargs: provider_calls.append(True) or "不應呼叫",
    )

    actual = app.translate("BF 2", "id", "zh")

    assert actual is None
    assert provider_calls == []
    assert app._translation_was_intentionally_skipped() is True
    assert app._get_translation_outcome() == {
        "status": "skipped",
        "reason": "pure_equipment_code",
    }


def test_single_target_multi_translation_preserves_intentional_skip_outcome():
    actual = app.translate_multi("BF 2", "id", ["zh"])

    assert actual == []
    assert app._translation_was_intentionally_skipped() is True
    assert app._get_translation_outcome().get("reason") == "pure_equipment_code"


def test_post_restore_guard_accepts_valid_line_mentions():
    placeholders = {
        "__MENTION_0__": "@蘇比 sobirin",
        "__MENTION_1__": "@(杰弗)",
    }
    candidate = "@蘇比 sobirin @(杰弗) 如果發現沒有噴漆，績效將直接扣除0.5。"

    assert app._post_restore_mentions_guard(candidate, placeholders) == candidate


def test_parenthesized_plain_text_mention_is_detected_and_protected():
    source = "@(杰弗) 噴漆執行上有什麼問題？"

    assert app.extract_mentions(source) == ["@(杰弗)"]
    protected, placeholders = app.protect_mentions(source)

    assert protected == "__MENTION_0__ 噴漆執行上有什麼問題？"
    assert placeholders == {"__MENTION_0__": "@(杰弗)"}
    assert app.strip_mentions_for_detect(source).strip() == "噴漆執行上有什麼問題？"


def test_repeated_same_mention_gets_one_placeholder_per_occurrence():
    source = "@(杰弗) 先確認，@(杰弗) 再回報。"

    protected, placeholders = app.protect_mentions(source)

    assert protected == "__MENTION_0__ 先確認，__MENTION_1__ 再回報。"
    assert placeholders == {
        "__MENTION_0__": "@(杰弗)",
        "__MENTION_1__": "@(杰弗)",
    }
    restored = app.restore_mentions(protected, placeholders)
    assert restored == source
    assert app._post_restore_mentions_guard(restored, placeholders) == source


def test_malformed_line_mention_shell_is_repaired_from_user_profile(monkeypatch):
    mentionee = types.SimpleNamespace(
        index=0,
        length=3,
        type="user",
        user_id="U-mentioned-user",
    )
    message = types.SimpleNamespace(
        mention=types.SimpleNamespace(mentionees=[mentionee])
    )
    monkeypatch.setattr(app, "get_display_name", lambda group_id, user_id: "杰弗")

    normalized, mentions = app.normalize_line_mentions(
        "@() 噴漆執行上有什麼問題？",
        message,
        "group-1",
    )

    assert normalized == "@(杰弗) 噴漆執行上有什麼問題？"
    assert mentions == ["@(杰弗)"]


def test_plain_text_translation_actions_are_visible_without_flex():
    qr = app._build_translation_action_quick_reply(
        "group-1",
        "噴漆執行上有什麼問題？",
        "Apa kendala dalam pelaksanaan proses pengecatan?",
        "zh",
        "id",
        "message-1",
    )

    assert qr is not None
    labels = [item.action.label for item in qr.items]
    assert labels[:5] == [
        "✨ 自然/Alami",
        "🔎 直譯/Harfiah",
        "📢 正式/Formal",
        "↩ 回譯/Cek balik",
        "👤 我的語言/Bahasa",
    ]
    assert "📋 交班摘要/Serah" in labels
    assert "🎙 即時口譯/Interpret" in labels


def test_default_quick_reply_menu_exposes_new_commands():
    entries = {item["id"]: item for item in app.QUICK_REPLY_DEFAULTS}
    assert entries["handover"]["text"] == "/交班摘要"
    assert entries["interpreter"]["text"] == "/即時口譯"
    assert entries["handover"]["enabled"] is True
    assert entries["interpreter"]["enabled"] is True


def test_single_provider_transient_error_retries_once(monkeypatch):
    calls = []

    class TemporaryError(RuntimeError):
        status_code = 503

    response = types.SimpleNamespace(choices=[], usage=None, model="test-model")

    def fake_dispatch(provider, **kwargs):
        calls.append((provider, kwargs.get("timeout")))
        if len(calls) == 1:
            raise TemporaryError("service unavailable")
        return response

    monkeypatch.setattr(ai_provider, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(ai_provider, "get_available_providers", lambda *a, **k: ["openai"])
    monkeypatch.setattr(ai_provider, "_dispatch_provider", fake_dispatch)
    monkeypatch.setattr(ai_provider, "_record_provider_success", lambda *a, **k: None)
    monkeypatch.setattr(ai_provider, "_record_provider_failure", lambda *a, **k: None)
    monkeypatch.setattr(ai_provider.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        ai_provider,
        "_current_config",
        {
            "provider_failover": True,
            "failover_policy": {
                "single_provider_retry": True,
                "total_timeout_seconds": 5,
                "per_provider_timeout_seconds": 2,
            },
        },
    )

    actual = ai_provider.chat_complete(
        model="test-model",
        messages=[{"role": "user", "content": "hello"}],
        timeout=2,
    )

    assert actual is response
    assert len(calls) == 2
    assert response._jy_provider == "openai"
    assert response._jy_failover_attempts[0]["kind"] == "transient_retry"


def test_single_provider_retry_preserves_usable_response_validator(monkeypatch):
    response = types.SimpleNamespace(choices=[], usage=None, model="test-model")

    monkeypatch.setattr(ai_provider, "_ensure_initialized", lambda: None)
    monkeypatch.setattr(ai_provider, "get_available_providers", lambda *a, **k: ["openai"])
    monkeypatch.setattr(ai_provider, "_dispatch_provider", lambda *a, **k: response)
    monkeypatch.setattr(ai_provider, "_record_provider_success", lambda *a, **k: None)
    monkeypatch.setattr(
        ai_provider,
        "_current_config",
        {
            "provider_failover": True,
            "failover_policy": {
                "single_provider_retry": True,
                "total_timeout_seconds": 5,
                "per_provider_timeout_seconds": 2,
            },
        },
    )

    actual = ai_provider.chat_complete(
        model="test-model",
        messages=[{"role": "user", "content": "hello"}],
        timeout=2,
        response_validator=lambda _response, _provider: (True, "ok"),
    )

    assert actual is response
    assert response._jy_provider == "openai"


def _fake_translation_response(content):
    choice = types.SimpleNamespace(
        message=types.SimpleNamespace(content=content),
        finish_reason="stop",
        logprobs=None,
    )
    return types.SimpleNamespace(
        choices=[choice],
        usage=None,
        model="test-model",
        _jy_provider="openai",
        _jy_failover_attempts=[],
    )


def test_spray_paint_runtime_policy_matches_central_glossary():
    rule = next(
        item for item in app._FACTORY_DOMAIN_TERM_RULES_ZH_ID
        if item.get("key") == "spray_cat"
    )

    assert rule["preferred_id"] == "pengecatan semprot"
    assert "spray cat" in rule["forbidden_id_terms"]
    assert "噴漆" not in app.ZH_TO_ID_HARD
    assert app._validate_terminology_policy_consistency() is True


def test_terminology_policy_invariant_rejects_future_deprecated_hard_rule(monkeypatch):
    monkeypatch.setitem(app.ZH_TO_ID_HARD, "測試噴漆衝突", "spray cat")

    with pytest.raises(RuntimeError, match="Terminology policy conflict"):
        app._validate_terminology_policy_consistency()


def test_screenshot_sentence_repairs_old_spray_cat_output_instead_of_failing(monkeypatch):
    """Regression for the exact LINE screenshot failure.

    Older prompt/semantic rules asked the provider to emit ``spray cat`` while
    the final delivery gate rejected that same phrase as deprecated.  Even when
    a provider still returns the stale phrase, the local semantic layer must now
    migrate it to the canonical Indonesian term and keep the @mention intact.
    """
    stale_provider_output = (
        "__MENTION_0__ Apa masalah dalam pelaksanaan spray cat?"
    )
    monkeypatch.setattr(
        app.ai.chat.completions,
        "create",
        lambda **_kwargs: _fake_translation_response(stale_provider_output),
    )
    # Production factory mode requires one source-grounded adjudication
    # before a newly generated result may be delivered.  This regression is
    # about deterministic terminology repair, so emulate an independent
    # reviewer agreeing with the locally normalized candidate.
    monkeypatch.setattr(
        tqg,
        "review_translation",
        lambda _source, reviewed_candidate, *_args, **_kwargs: reviewed_candidate,
    )
    app.translation_cache.clear()
    app._tl.group_id = "regression-group"

    # Use the same raw text shown in LINE.  There is intentionally no LINE
    # mention metadata and no prebuilt placeholder map: the public translation
    # boundary must recognize and preserve the pasted @(name) form itself.
    actual = app.translate_multi(
        "@(杰弗) 噴漆執行上有什麼問題？",
        "zh",
        ["id"],
    )

    assert actual == [
        ("id", "@(杰弗) Apa kendala dalam proses pengecatan semprot?")
    ]
    report = tqg.validate_translation(
        "@(杰弗) 噴漆執行上有什麼問題？",
        actual[0][1],
        "zh",
        "id",
    )
    assert report.ok, report.issues


def test_natural_variant_preserves_parenthesized_mention(monkeypatch):
    seen = {}

    def fake_translate_openai(text, src, tgt, **_kwargs):
        seen["text"] = text
        return "__MENTION_0__ Apa kendala dalam proses pengecatan semprot?"

    monkeypatch.setattr(app, "translate_openai", fake_translate_openai)
    monkeypatch.setattr(
        app,
        "_final_delivery_guard",
        lambda source, candidate, src, tgt: candidate,
    )

    actual = app._translate_variant_preserving_mentions(
        "@(杰弗) 噴漆執行上有什麼問題？",
        "zh",
        "id",
    )

    assert seen["text"].startswith("__MENTION_0__")
    assert actual == "@(杰弗) Apa kendala dalam proses pengecatan semprot?"


def test_spray_paint_prompt_hint_never_recommends_deprecated_term():
    hint = app.build_factory_context_hint_zh_id("噴漆執行上有什麼問題？")

    assert "噴漆=pengecatan semprot/mengecat" in hint
    assert "禁止 spray cat" in hint


def test_spaced_lowercase_known_equipment_code_is_canonicalized():
    normalized, replacements = app.normalize_known_equipment_codes(
        "Mesin i 9 masih dalam perbaikan"
    )

    assert normalized == "Mesin I9 masih dalam perbaikan"
    assert replacements == [("i 9", "I9")]


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Mesin i 9 masih dalam perbaikan", "I9 機台還在維修中"),
        ("Mesin i9 masih dalam perbaikan", "I9 機台還在維修中"),
        ("Mesin I 9 masih dalam perbaikan", "I9 機台還在維修中"),
        ("Mesin I9 masih dalam perbaikan", "I9 機台還在維修中"),
        ("Mesin bf 2 sedang dalam perbaikan", "BF2 機台正在維修中"),
        ("Mesin C 3 - R belum selesai diperbaiki", "C3-R 機台尚未維修完成"),
        ("Mesinnya masih diperbaiki", "機台還在維修中"),
    ],
)
def test_equipment_status_translation_is_deterministic(source, expected):
    assert app.factory_semantic_translate_equipment_status_id_zh(source) == expected



def test_structured_measurement_report_bypasses_entire_ai_pipeline(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("AI/TM/reviewer pipeline must not run for a source-verifiable report")

    monkeypatch.setattr(app, "_translate_core", fail_if_called)
    app.translation_cache.clear()

    actual = app.translate(
        "i9\nDepan 22,17\nTengah 22,15\nBelakang 22,16\nKebulatan ok.",
        "id",
        "zh",
    )

    assert actual == "I9\n前端：22,17\n中間：22,15\n後端：22,16\n圓度：正常"


def test_equipment_status_bypasses_entire_ai_pipeline(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("AI/TM/NMT pipeline must not run for deterministic equipment status")

    monkeypatch.setattr(app, "_translate_core", fail_if_called)
    app.translation_cache.clear()

    actual = app.translate(
        "Mesin i 9 masih dalam perbaikan",
        "id",
        "zh",
    )

    assert actual == "I9 機台還在維修中"


def test_equipment_code_normalizer_does_not_modify_mention_display_name():
    source = "@(I 9) Mesin i 9 masih dalam perbaikan"

    normalized, replacements = app.normalize_known_equipment_codes(source)
    translated = app.factory_semantic_translate_equipment_status_id_zh(source)

    assert normalized == "@(I 9) Mesin I9 masih dalam perbaikan"
    assert replacements == [("i 9", "I9")]
    assert translated == "@(I 9) I9 機台還在維修中"


def test_unknown_or_longer_codes_are_not_falsely_collapsed():
    assert app.normalize_known_equipment_codes(
        "Mesin X 9 masih dalam perbaikan"
    )[0] == "Mesin X 9 masih dalam perbaikan"
    assert app.normalize_known_equipment_codes(
        "Mesin i 90 masih dalam perbaikan"
    )[0] == "Mesin i 90 masih dalam perbaikan"


def test_equipment_status_semantic_path_does_not_drop_extra_information():
    source = "Mesin I9 masih dalam perbaikan sampai besok"

    assert app.factory_semantic_translate_equipment_status_id_zh(source) is None


def test_pipeline_recovers_two_provider_dropped_line_mentions_before_quality_gate(monkeypatch):
    """Regression for the 10:52 LINE screenshot with two addressees.

    The provider returned a usable Chinese translation but omitted both outer
    ``__MENTION_n__`` tokens.  The old first-pass validator rejected it as
    ``missing_pipeline_token`` before translate() could restore display names.
    """
    source = (
        "@蘇比 sobirin @(杰弗) Jika Anda menemukan bahwa Anda tidak akan "
        "menyemprot cat, kinerjanya akan dikurangi 0,5 secara langsung."
    )
    provider_text = "如果發現你沒有噴漆，績效將直接扣除0.5。"

    monkeypatch.setattr(app.tm_module, "tm_lookup", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "cache_get", lambda *_a, **_k: None)
    monkeypatch.setattr(
        app.ai.chat.completions,
        "create",
        lambda **_kwargs: _fake_translation_response(provider_text),
    )
    monkeypatch.setattr(
        tqg,
        "review_translation",
        lambda _source, reviewed_candidate, *_args, **_kwargs: reviewed_candidate,
    )
    app.translation_cache.clear()
    app._tl.group_id = "mention-recovery-regression"
    app._tl.line_mentions = ["@蘇比 sobirin", "@(杰弗)"]

    actual = app.translate(source, "id", "zh")

    assert actual == "@蘇比 sobirin @(杰弗) 如果發現你沒有噴漆，績效將直接扣除0.5。"
    assert "__MENTION_" not in actual


def test_pipeline_recovers_only_the_missing_mention_without_duplication(monkeypatch):
    source = "@蘇比 sobirin @(杰弗) Tolong periksa mesin."
    provider_text = "__MENTION_1__ 請檢查機台。"

    monkeypatch.setattr(app.tm_module, "tm_lookup", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "cache_get", lambda *_a, **_k: None)
    monkeypatch.setattr(
        app.ai.chat.completions,
        "create",
        lambda **_kwargs: _fake_translation_response(provider_text),
    )
    monkeypatch.setattr(
        tqg,
        "review_translation",
        lambda _source, reviewed_candidate, *_args, **_kwargs: reviewed_candidate,
    )
    app.translation_cache.clear()
    app._tl.group_id = "mention-recovery-partial"
    app._tl.line_mentions = ["@蘇比 sobirin", "@(杰弗)"]
    app._tl.disable_tone_emoji = True

    try:
        actual = app.translate(source, "id", "zh")
    finally:
        try:
            delattr(app._tl, "disable_tone_emoji")
        except AttributeError:
            pass

    assert actual == "@蘇比 sobirin @(杰弗) 請檢查機台"
    assert actual.count("@蘇比 sobirin") == 1
    assert actual.count("@(杰弗)") == 1


def test_visible_name_echo_is_canonicalized_before_restore_without_duplicate():
    source = "__MENTION_0__ Tolong periksa mesin."
    app._tl.protected_name_map = {"__MENTION_0__": "@蘇比 sobirin"}

    repaired = app._repair_pipeline_mention_placeholders(
        source,
        "@蘇比 sobirin 請檢查機台。",
    )

    assert repaired == "__MENTION_0__ 請檢查機台。"


def test_mention_recovery_does_not_relax_nonmention_immutable_integrity():
    raw_source = "Periksa mesin I9 pada 10:30."
    envelope = tqg.protect_immutable_spans(raw_source)
    candidate = "請檢查機台。"

    repaired = app._repair_pipeline_mention_placeholders(
        envelope.protected,
        candidate,
    )
    report = tqg.validate_translation(
        envelope.protected,
        repaired,
        "id",
        "zh",
        immutable_literals=envelope.mapping.values(),
    )

    assert repaired == candidate
    assert not report.ok
    assert any(issue.startswith("missing_pipeline_token:") for issue in report.hard_issues)


def test_rejected_deterministic_inner_shortcut_falls_through_to_provider(monkeypatch):
    """A broken local shortcut must not be misreported as a provider outage."""
    logged_models = []
    monkeypatch.setattr(app.factory_translation_guard_module, "exact_verified_target", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "factory_semantic_translate_id_zh", lambda *_a, **_k: "禁止 masuk。")
    monkeypatch.setattr(app, "translate_openai", lambda *_a, **_k: "機台目前禁止進入。")
    monkeypatch.setattr(app, "finalize_factory_translation", lambda _s, value, _sl, _tl: value)
    monkeypatch.setattr(
        app,
        "is_translation_acceptable",
        lambda _s, value, _sl, _tl: value == "機台目前禁止進入。",
    )
    monkeypatch.setattr(app, "cache_set", lambda *_a, **_k: None)
    monkeypatch.setattr(
        app,
        "_log_translation",
        lambda _src, _tgt, _sl, _tl, model, *_args, **_kwargs: logged_models.append(model),
    )

    previous = getattr(app._tl, "quality_gate_critical", None)
    app._tl.quality_gate_critical = False
    try:
        actual = app._translate_inner("Mesin tidak boleh dimasuki sekarang.", "id", "zh")
    finally:
        if previous is None:
            try:
                delattr(app._tl, "quality_gate_critical")
            except AttributeError:
                pass
        else:
            app._tl.quality_gate_critical = previous

    assert actual == "機台目前禁止進入。"
    assert "factory-semantic" not in logged_models


def test_rejected_public_deterministic_shortcut_falls_through_to_core(monkeypatch):
    """Every deterministic fast path is optional; rejection continues to LLM."""
    source = "I9\nDepan 22,17\nTengah 22,15\nBelakang 22,16\nKebulatan ok."
    monkeypatch.setattr(
        app.factory_structured_report_module,
        "translate_id_zh_measurement_report",
        lambda _text: "被最終邊界拒絕的本地結果",
    )
    monkeypatch.setattr(app, "factory_semantic_translate_equipment_status_id_zh", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_translate_core", lambda *_a, **_k: "供應商翻譯結果")
    monkeypatch.setattr(
        app,
        "_final_delivery_guard",
        lambda _source, candidate, _src, _tgt: (
            None if candidate == "被最終邊界拒絕的本地結果" else candidate
        ),
    )
    monkeypatch.setattr(app, "cache_set", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "get_auto_tone_emoji_enabled", lambda *_a, **_k: False)

    assert app.translate(source, "id", "zh") == "供應商翻譯結果"


def test_tag_installation_notice_is_delivered_instead_of_generic_failure(monkeypatch):
    """End-to-end regression for the uploaded LINE screenshot."""
    source = """@budi santoso 山多 @Irwan 布納萬 @伊努滿 Sumertha @迪弟 kampret @Hasim

📢 PEMBERITAHUAN PENTING – STANDAR PEMASANGAN TAG

Mulai saat ini, seluruh operator WAJIB memasang TAG sesuai dengan ketentuan dari pihak manajemen.

Ketentuan pemasangan TAG:

1. Mesin Grinding dan Polishing
    * TAG harus dipasang pada barang pertama yang keluar dari mesin.
    * TAG juga harus dipasang pada barang terakhir dari proses produksi.
    * Ketentuan ini berlaku untuk seluruh mesin Grinding dan Polishing tanpa pengecualian.
2. Cleaning Station
    * Work Order dan TAG wajib dijepit pada tali crane menggunakan penjepit (clip) yang telah disediakan.
    * Dilarang meletakkan Work Order atau TAG di sembarang tempat.
    * Apabila penjepit hilang atau rusak, segera minta penggantinya kepada Ketua Regu agar standar kerja tetap terjaga.

Mohon seluruh rekan kerja menjalankan ketentuan ini dengan disiplin. Hal-hal yang terlihat sederhana seperti pemasangan TAG sangat berpengaruh terhadap ketertelusuran produk, kelancaran proses produksi, dan hasil audit. Jangan menunggu ditegur atau terjadi masalah terlebih dahulu. Mari bersama-sama menjaga standar kerja yang telah ditetapkan oleh manajemen."""
    candidate = """@budi santoso 山多 @Irwan 布納萬 @伊努滿 Sumertha @迪弟 kampret @Hasim

📢 重要通知－TAG 安裝標準

從現在起，所有操作員都必須依照管理階層的規定安裝 TAG。

TAG 安裝規定：

1. Grinding 與 Polishing 機台
    * TAG 必須裝在機台產出的第一件產品上。
    * TAG 也必須裝在生產流程的最後一件產品上。
    * 此規定適用於所有 Grinding 與 Polishing 機台，沒有例外。
2. Cleaning Station
    * Work Order 與 TAG 必須使用已提供的夾具（clip）夾在天車繩索上。
    * 禁止將 Work Order 或 TAG 隨意放置。
    * 夾具遺失或損壞時，請立即向班長申請更換，以維持作業標準。

請所有同仁確實遵守這項規定。安裝 TAG 看似簡單，卻會直接影響產品追溯、製程順暢與稽核結果。不要等到被提醒或發生問題才處理。請大家共同維護管理階層所制定的作業標準。"""

    monkeypatch.setattr(app.tm_module, "tm_lookup", lambda *_a, **_k: None)
    monkeypatch.setattr(app.vec_tm_module, "vector_lookup", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "cache_get", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "cache_set", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "translate_openai", lambda *_a, **_k: candidate)
    monkeypatch.setattr(tqg, "review_translation", lambda _s, reviewed, *_a, **_k: reviewed)
    monkeypatch.setattr(app, "_log_translation", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_event_log_write", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "_persist_last_translate_debug", lambda *_a, **_k: None)
    monkeypatch.setattr(app, "get_auto_tone_emoji_enabled", lambda *_a, **_k: False)

    previous_group = getattr(app._tl, "group_id", None)
    previous_disable = getattr(app._tl, "disable_tone_emoji", None)
    app._tl.group_id = "tag-installation-regression"
    app._tl.disable_tone_emoji = True
    try:
        actual = app.translate(source, "id", "zh")
    finally:
        if previous_group is None:
            try:
                delattr(app._tl, "group_id")
            except AttributeError:
                pass
        else:
            app._tl.group_id = previous_group
        if previous_disable is None:
            try:
                delattr(app._tl, "disable_tone_emoji")
            except AttributeError:
                pass
        else:
            app._tl.disable_tone_emoji = previous_disable

    assert actual
    assert "TAG 安裝標準" in actual
    assert "夾具（clip）" in actual
    for mention in ("@budi santoso", "@Irwan", "@伊努滿", "@迪弟 kampret", "@Hasim"):
        assert mention in actual
    assert "暫時無法完成安全翻譯" not in actual
    assert "belum dapat diterjemahkan dengan aman" not in actual
