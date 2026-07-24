import unittest
from types import SimpleNamespace
from unittest.mock import patch

import ai_provider


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {"x": {"type": "string"}},
    "required": ["x"],
}


class Recorder:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class StructuredTransportTests(unittest.TestCase):
    def test_openai_uses_strict_json_schema_without_xml_translation_wrapper(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"x":"ok"}'))]
        )
        recorder = Recorder(response)
        client = SimpleNamespace(chat=SimpleNamespace(completions=recorder))
        config = {
            "claude_features": {"output_translation_tag": True},
            "openai_features": {},
        }
        with patch.object(ai_provider, "_get_openai_client", return_value=client), \
             patch.object(ai_provider, "_client_with_limits", side_effect=lambda c, _t: c), \
             patch.object(ai_provider, "_ensure_initialized", return_value=None), \
             patch.object(ai_provider, "_current_config", config):
            ai_provider._chat_complete_openai(
                "gpt-4.1",
                [{"role": "system", "content": "Return JSON."}, {"role": "user", "content": "x"}],
                max_tokens=100,
                structured_schema=SCHEMA,
                structured_name="factory_audit",
            )
        call = recorder.calls[0]
        self.assertEqual(call["response_format"]["type"], "json_schema")
        self.assertTrue(call["response_format"]["json_schema"]["strict"])
        self.assertNotIn("<translation>", call["messages"][0]["content"])

    def test_gemini_openai_compatibility_uses_same_schema_contract(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content='{"x":"ok"}'))]
        )
        recorder = Recorder(response)
        client = SimpleNamespace(chat=SimpleNamespace(completions=recorder))
        with patch.object(ai_provider, "_get_gemini_client", return_value=client), \
             patch.object(ai_provider, "_client_with_limits", side_effect=lambda c, _t: c), \
             patch.object(ai_provider, "_resolve_gemini_model", return_value="gemini-test"), \
             patch.object(ai_provider, "_current_config", {"gemini_features": {}}):
            ai_provider._chat_complete_gemini(
                "test",
                [{"role": "user", "content": "x"}],
                max_tokens=100,
                structured_schema=SCHEMA,
                structured_name="factory_audit",
            )
        self.assertEqual(recorder.calls[0]["response_format"]["json_schema"]["schema"], SCHEMA)

    def test_anthropic_native_api_uses_output_config_json_schema(self):
        block = SimpleNamespace(type="text", text='{"x":"ok"}', citations=None)
        response = SimpleNamespace(
            content=[block],
            usage=SimpleNamespace(
                input_tokens=1,
                output_tokens=2,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=0,
            ),
            stop_reason="end_turn",
        )
        recorder = Recorder(response)
        client = SimpleNamespace(messages=recorder)
        features = {
            "prompt_caching": False,
            "extended_thinking": False,
            "adaptive_thinking": False,
            "glossary_grounding": False,
            "stop_sequences": True,
            "xml_system_prompt": False,
            "citations": True,
            "extended_cache_1h": False,
            "multi_block_caching": False,
            "assistant_prefill": True,
            "assistant_prefill_text": "translation:",
            "output_translation_tag": True,
        }
        config = {"claude_features": features, "anthropic": {"default_model": "claude-test"}}
        with patch.object(ai_provider, "_get_anthropic_client", return_value=client), \
             patch.object(ai_provider, "_client_with_limits", side_effect=lambda c, _t: c), \
             patch.object(ai_provider, "_ensure_initialized", return_value=None), \
             patch.object(ai_provider, "_current_config", config), \
             patch.object(ai_provider, "_resolve_anthropic_model", return_value="claude-3-5-sonnet"):
            result = ai_provider._chat_complete_anthropic(
                "test",
                [{"role": "system", "content": "Return JSON."}, {"role": "user", "content": "x"}],
                100,
                structured_schema=SCHEMA,
                structured_name="factory_audit",
            )
        call = recorder.calls[0]
        self.assertEqual(call["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(call["output_config"]["format"]["schema"], SCHEMA)
        self.assertNotIn("stop_sequences", call)
        self.assertEqual(result.choices[0].message.content, '{"x":"ok"}')


if __name__ == "__main__":
    unittest.main()
