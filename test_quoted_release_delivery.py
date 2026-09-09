"""Reported follow-up: translate the current release, even without old context."""
import pytest

import app
import translation_retry_queue as queue
from test_translation_notice_availability import (
    runtime, clean_translation_context, event, delivered_text, retry_pending,
)

SOURCE = "這些研發單位已確認完，\n三把都可以生產"
TARGET = "Bagian R&D sudah selesai memeriksa material-material ini. Ketiga bundel tersebut sudah boleh diproduksi."
# Reconstructed context for the regression; not a copy of a webhook log.
EARLIER = "7J821007\n7J821008\n7J821009\n先不要生產，等研發單位量完圓度再生產。"


@pytest.mark.parametrize("cached", [False, True])
def test_quoted_release_reaches_line_with_current_positive_instruction(runtime, cached):
    runtime.provider_result = TARGET
    current = event(SOURCE)
    current.message.quoted_message_id = "yesterday-message"
    if cached:
        app.message_cache["yesterday-message"] = {"text": EARLIER, "tr": {}}
    app.handle_message(current)
    assert TARGET in delivered_text(runtime)
    assert runtime.generations == [(SOURCE, "zh", "id")]
    assert queue.pending_count() == 0


def test_quoted_release_survives_provider_outage_and_recovers(runtime):
    runtime.provider_result = TARGET
    runtime.provider_down = True
    current = event(SOURCE)
    current.message.quoted_message_id = "yesterday-message"
    app.handle_message(current)
    job = queue.get("notice-group:notice-message")
    assert job["payload"]["source_text"] == SOURCE
    assert job["payload"]["quoted_context_message_id"] == "yesterday-message"
    assert not runtime.sends
    runtime.provider_down = False
    assert retry_pending()
    assert TARGET in delivered_text(runtime)
    assert queue.pending_count() == 0
