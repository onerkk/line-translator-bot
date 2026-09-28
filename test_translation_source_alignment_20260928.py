"""Screenshot regressions for source scope, plant terms, and unsupported detail.

These tests exercise reusable interpretation/validation rules. The screenshots
are not registered as whole-sentence translation shortcuts.
"""
import factory_knowledge
import factory_order_semantics as orders
import factory_translation_policy as policy
import translation_quality_gate as quality


PACKING_SOURCE = (
    "@All 各站異型棒跟大成優先包裝這個月剩兩天，備註出貨急單也幫忙注意一下優先處理。\n"
    "本月目標3600噸，今天中班再入20噸，最後兩天平均一天148噸"
)
PACKING_SCREENSHOT_TRANSLATION = (
    "@All Batang berbentuk khusus dan 大成 dari setiap stasiun diprioritaskan untuk dikemas. "
    "Bulan ini tersisa dua hari. Mohon perhatikan juga catatan work order pengiriman mendesak "
    "dan prioritaskan penanganannya.\n"
    "Target bulan ini 3600 ton. Hari ini shift tengah harap tambahkan 20 ton lagi. "
    "Untuk dua hari terakhir, rata-rata targetnya 148 ton per hari."
)

PACKING_SOURCE_PARAPHRASE = (
    "大成的料和各站異型棒都先安排包裝，出貨備註有急單也請優先處理。"
)
PACKING_TARGET_PARAPHRASE = (
    "Prioritaskan pengemasan material 大成 dan batang profil khusus dari semua stasiun. "
    "Work order mendesak untuk pengiriman yang tercantum di catatan juga harus diprioritaskan."
)

LABEL_SOURCE = (
    "@All 包裝注意一下，今天有發現爐號標籤跟列印ID爐號不同的問題，目前已經修正。"
    "張貼標籤前記得核對"
)

INJURY_SOURCE = (
    "@All 今天二股發生一件排除擠料時造成夾傷的工傷案件。"
    "棒材生產時，卡料擠壓變型，可能會受力回彈或是機件暴衝的不可控因素，"
    "千萬不要站在危險的角度進行異常排除。有安全疑慮可以跟我討論怎麼排除。"
)


def test_factory_prompt_requires_claim_grouping_and_source_to_target_alignment():
    prompt = policy.build_prompt(PACKING_SOURCE, "zh", "id")
    assert "coordinated item" in prompt
    assert "modifier scope" in prompt
    assert "injured body part" in prompt


def test_customer_and_product_terms_are_retrieved_for_reworded_messages():
    cards = factory_knowledge.retrieve(PACKING_SOURCE_PARAPHRASE, "zh", "id", limit=5)
    ids = {card["id"] for card in cards}
    assert "customer_name_dacheng_identity" in ids
    assert "product_term_special_profile_bar" in ids
    ok, issues = factory_knowledge.validate_translation(
        cards, PACKING_SOURCE_PARAPHRASE, PACKING_TARGET_PARAPHRASE
    )
    assert ok, issues


def test_heat_number_is_a_retrieved_field_term_and_value_stays_separate():
    target = (
        "@All Saat melakukan pengemasan, mohon diperhatikan. Hari ini ditemukan "
        "ketidaksesuaian antara nomor heat pada label dan nomor heat pada ID hasil cetak. "
        "Masalah ini sudah diperbaiki. Sebelum menempelkan label, pastikan nomornya sesuai."
    )
    cards = factory_knowledge.retrieve(LABEL_SOURCE, "zh", "id", limit=5)
    assert "term_furnace_heat_number" in {card["id"] for card in cards}
    ok, issues = factory_knowledge.validate_translation(cards, LABEL_SOURCE, target)
    assert ok, issues


def test_indonesian_order_modifier_keeps_urgency_attached_to_the_order():
    frame = orders.build_frame(PACKING_SOURCE, "zh", "id")
    ok, issues = orders.validate_translation(frame, PACKING_SCREENSHOT_TRANSLATION)
    assert ok, issues
    assert orders.validate_translation(
        frame, PACKING_SCREENSHOT_TRANSLATION.replace("work order pengiriman mendesak", "work order pengiriman biasa")
    )[0] is False


def test_injury_translation_does_not_add_an_unspecified_body_part():
    screenshot_translation = (
        "Hari ini di Bagian Cold Drawing 2 terjadi kecelakaan kerja, "
        "tangan terjepit saat menangani material macet."
    )
    repaired_translation = (
        "Hari ini terjadi kecelakaan kerja di Bagian Cold Drawing 2, "
        "yaitu cedera terjepit saat menangani material yang macet."
    )
    assert "semantic:unsupported_injury_body_part:hand" in quality._unsupported_injury_body_part_issues(
        INJURY_SOURCE, screenshot_translation
    )[0]
    assert quality._unsupported_injury_body_part_issues(INJURY_SOURCE, repaired_translation) == []


def test_explicit_injury_anatomy_is_allowed_and_noninjury_messages_are_untouched():
    explicit_source = "員工手指遭夾傷，已安排就醫。"
    target = "Jari karyawan terjepit dan ia sudah dibawa berobat."
    assert quality._unsupported_injury_body_part_issues(explicit_source, target) == []
    assert quality._unsupported_injury_body_part_issues(
        "請把手套放在機台旁。", "Letakkan sarung tangan di samping mesin."
    ) == []

