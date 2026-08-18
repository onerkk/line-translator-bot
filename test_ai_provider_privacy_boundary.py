import unittest
from unittest import mock

import ai_provider


class AIProviderPrivacyBoundaryTests(unittest.TestCase):
    def test_provider_receives_masked_values_and_local_response_is_restored(self):
        observed = {}

        def fake_dispatch(provider, timeout, **kwargs):
            content = kwargs["messages"][-1]["content"]
            observed["content"] = content
            return ai_provider._UnifiedResponse(content, "fake-model")

        config = {
            "provider_failover": True,
            "failover_policy": {
                "total_timeout_seconds": 10,
                "per_provider_timeout_seconds": 5,
                "single_provider_retry": False,
            },
        }
        with mock.patch.object(ai_provider, "_ensure_initialized"), mock.patch.object(
            ai_provider, "_current_config", config
        ), mock.patch.object(
            ai_provider, "get_available_providers", return_value=["openai"]
        ), mock.patch.object(
            ai_provider, "_dispatch_provider", side_effect=fake_dispatch
        ), mock.patch.object(
            ai_provider, "_record_provider_success"
        ):
            response = ai_provider.chat_complete(
                model="fake",
                messages=[
                    {"role": "system", "content": "只輸出翻譯"},
                    {"role": "user", "content": "王小明 0912-345-678 worker@example.com"},
                ],
                privacy_literals=["王小明"],
            )

        self.assertNotIn("王小明", observed["content"])
        self.assertNotIn("0912-345-678", observed["content"])
        self.assertNotIn("worker@example.com", observed["content"])
        restored = response.choices[0].message.content
        self.assertIn("王小明", restored)
        self.assertIn("0912-345-678", restored)
        self.assertIn("worker@example.com", restored)


if __name__ == "__main__":
    unittest.main()
