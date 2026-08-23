import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from knowledge_vault.config import VaultConfig
from knowledge_vault.model_router import CodexClient, ModelError, ModelRequest, ModelResult, ModelRouter


SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


class FakeCodex:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class FakeDeepSeek:
    def __init__(self, payload=None):
        self.payload = payload or {"ok": True}
        self.calls = 0

    def json_completion(self, *args, **kwargs):
        self.calls += 1
        return self.payload


class ModelRouterTests(unittest.TestCase):
    def config(self, root: Path):
        return VaultConfig(root, "Asia/Shanghai", (8, 11, 14, 17, 20, 23))

    def test_codex_success_never_calls_deepseek(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = ModelResult({"ok": True}, "codex", "gpt-5.6-luna", 1.0)
            codex = FakeCodex(result=result); deepseek = FakeDeepSeek()
            actual = ModelRouter(self.config(Path(tmp)), codex, deepseek).complete(ModelRequest("s", "u", SCHEMA))
        self.assertEqual(actual.provider, "codex")
        self.assertEqual(deepseek.calls, 0)

    def test_codex_error_falls_back_exactly_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex = FakeCodex(error=ModelError("quota", "额度不足")); deepseek = FakeDeepSeek()
            actual = ModelRouter(self.config(Path(tmp)), codex, deepseek).complete(ModelRequest("s", "u", SCHEMA))
        self.assertEqual(actual.provider, "deepseek")
        self.assertEqual(actual.fallback_reason, "quota")
        self.assertTrue(actual.degraded)
        self.assertEqual(deepseek.calls, 1)

    def test_codex_cli_is_read_only_and_parses_output_file(self):
        captured = {}

        def runner(command, **kwargs):
            captured["command"] = command
            captured.update(kwargs)
            output = Path(command[command.index("--output-last-message") + 1])
            output.write_text(json.dumps({"ok": True}), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, stdout="events", stderr="diagnostic")

        with tempfile.TemporaryDirectory() as tmp:
            cfg = self.config(Path(tmp))
            result = CodexClient(cfg, runner=runner).complete(ModelRequest("system", "user", SCHEMA))
        self.assertEqual(result.payload, {"ok": True})
        self.assertIn("read-only", captured["command"])
        self.assertIn("--ignore-user-config", captured["command"])
        self.assertEqual(captured["input"].count("user"), 1)
        self.assertNotEqual(captured["command"].index("-a"), -1)

    def test_codex_timeout_is_classified(self):
        def runner(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ModelError) as caught:
                CodexClient(self.config(Path(tmp)), runner=runner).complete(ModelRequest("s", "u", SCHEMA))
        self.assertEqual(caught.exception.category, "timeout")

    def test_visual_only_failure_does_not_fall_back_to_text_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            codex = FakeCodex(error=ModelError("unavailable", "network")); deepseek = FakeDeepSeek()
            request = ModelRequest("s", "u", SCHEMA, (Path(tmp) / "image.png",), allow_text_only_fallback=False)
            with self.assertRaisesRegex(ModelError, "缺少可供备用模型判断的文本证据"):
                ModelRouter(self.config(Path(tmp)), codex, deepseek).complete(request)
        self.assertEqual(deepseek.calls, 0)

    def test_deepseek_only_order_uses_deepseek_as_primary(self):
        class FakeAPI:
            def __init__(self, payload):
                self.payload = payload
                self.calls = 0

            def json_completion(self, *args, **kwargs):
                self.calls += 1
                return self.payload

        with tempfile.TemporaryDirectory() as tmp:
            cfg = VaultConfig(Path(tmp), "Asia/Shanghai", (8, 12, 18),
                              provider_order=("deepseek", "kimi"),
                              api_providers={"kimi": {"base_url": "https://api.example.com/v1", "model": "kimi-x"}})
            router = ModelRouter(cfg, deepseek=FakeDeepSeek({"ok": True}))
            router._clients["kimi"] = FakeAPI({"ok": True})
            result = router.complete(ModelRequest("s", "u", SCHEMA))
        self.assertEqual(result.provider, "deepseek")
        self.assertFalse(result.degraded)

    def test_all_providers_fail_raises_with_reasons(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = VaultConfig(Path(tmp), "Asia/Shanghai", (8, 12, 18), provider_order=("deepseek",))
            deepseek = FakeDeepSeek()
            deepseek.json_completion = lambda *a, **k: (_ for _ in ()).throw(ModelError("quota", "额度"))
            with self.assertRaisesRegex(ModelError, "所有模型均不可用"):
                ModelRouter(cfg, deepseek=deepseek).complete(ModelRequest("s", "u", SCHEMA))

    def test_codex_empty_result_is_invalid_response(self):
        def runner(command, **kwargs):
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ModelError) as caught:
                CodexClient(self.config(Path(tmp)), runner=runner).complete(ModelRequest("s", "u", SCHEMA))
        self.assertEqual(caught.exception.category, "invalid_response")


if __name__ == "__main__":
    unittest.main()
