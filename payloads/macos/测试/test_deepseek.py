import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from knowledge_vault.config import VaultConfig
from knowledge_vault.deepseek import DeepSeekClient, DeepSeekError, OpenAICompatibleClient


class DeepSeekTests(unittest.TestCase):
    def config(self, root: Path) -> VaultConfig:
        return VaultConfig(root, "Asia/Shanghai", (8, 11, 14, 17, 20, 23), 7, deepseek_timeout_seconds=5)

    @patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-secret"})
    @patch("knowledge_vault.deepseek.subprocess.run")
    def test_environment_key_is_ignored_in_favor_of_keychain(self, keychain):
        keychain.return_value.stdout = "keychain-secret\n"
        keychain.return_value.returncode = 0
        captured = {}

        def transport(url, headers, body, timeout):
            captured.update(headers)
            return 200, b'{"choices":[{"message":{"content":"{\\"ok\\":true}"}}]}'

        with tempfile.TemporaryDirectory() as tmp:
            DeepSeekClient(self.config(Path(tmp)), transport=transport).json_completion("json", "json")

        self.assertEqual(captured["Authorization"], "Bearer keychain-secret")

    @patch("knowledge_vault.deepseek.load_api_key", return_value="test-secret")
    def test_json_completion_retries_empty_content_without_leaking_key(self, _key):
        calls = []
        responses = [
            (200, json.dumps({"choices": [{"message": {"content": ""}}]}).encode()),
            (200, json.dumps({"choices": [{"message": {"content": '{"ok":true}'}}]}).encode()),
        ]

        def transport(url, headers, body, timeout):
            calls.append((url, headers, json.loads(body), timeout))
            return responses.pop(0)

        with tempfile.TemporaryDirectory() as tmp:
            client = DeepSeekClient(self.config(Path(tmp)), transport=transport, sleep=lambda _: None)
            self.assertEqual(client.json_completion("return json", "json input"), {"ok": True})
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][2]["response_format"], {"type": "json_object"})
        self.assertEqual(calls[0][2]["thinking"], {"type": "disabled"})
        self.assertNotIn("test-secret", json.dumps(calls[0][2]))

    @patch("knowledge_vault.deepseek.load_api_key", return_value="test-secret")
    def test_non_retryable_error_is_clear(self, _key):
        client = DeepSeekClient(self.config(Path("/tmp")), transport=lambda *_: (401, b'{"error":{"message":"bad"}}'))
        with self.assertRaisesRegex(DeepSeekError, "HTTP 401"):
            client.json_completion("json", "json")

    @patch("knowledge_vault.deepseek.load_api_key", return_value="test-secret")
    def test_custom_openai_compatible_provider_uses_configured_endpoint(self, _key):
        captured = {}

        def transport(url, headers, body, timeout):
            captured.update({"url": url, "body": json.loads(body)})
            return 200, b'{"choices":[{"message":{"content":"{\\"ok\\":true}"}}]}'

        cfg = VaultConfig(
            Path("/tmp/vault"), "Asia/Shanghai", (8, 12, 18),
            provider_order=("kimi",),
            api_providers={"kimi": {
                "base_url": "https://api.example.com/v1",
                "model": "kimi-x",
                "keychain_service": "knowledge-vault-kimi",
                "timeout_seconds": 5,
            }},
        )
        client = OpenAICompatibleClient(cfg, "kimi", transport=transport, sleep=lambda _: None)
        self.assertEqual(client.json_completion("json", "json"), {"ok": True})
        self.assertEqual(captured["url"], "https://api.example.com/v1/chat/completions")
        self.assertEqual(captured["body"]["model"], "kimi-x")

    @patch("knowledge_vault.deepseek.load_api_key", return_value="test-secret")
    def test_custom_provider_base_url_not_doubled_when_already_complete(self, _key):
        cfg = VaultConfig(
            Path("/tmp/vault"), "Asia/Shanghai", (8, 12, 18),
            provider_order=("kimi",),
            api_providers={"kimi": {
                "base_url": "https://api.example.com/v1/chat/completions",
                "model": "kimi-x",
                "keychain_service": "knowledge-vault-kimi",
            }},
        )
        client = OpenAICompatibleClient(cfg, "kimi", transport=lambda *_: (200, b'{"choices":[{"message":{"content":"{\\"ok\\":true}"}}]}'), sleep=lambda _: None)
        client.json_completion("json", "json")
        self.assertNotIn("/chat/completions/chat/completions", client.base_url)


if __name__ == "__main__":
    unittest.main()
