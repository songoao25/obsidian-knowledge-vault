import json
from pathlib import Path
import tempfile
import unittest

from knowledge_vault.config import VaultConfig


class VaultConfigTests(unittest.TestCase):
    def test_loads_fixed_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({
                "vault_path": str(Path(directory) / "vault"),
                "timezone": "Asia/Shanghai",
                "run_hours": [8, 11, 14, 17, 20, 23],
            }), encoding="utf-8")
            cfg = VaultConfig.load(path)
            self.assertEqual(cfg.run_hours, (8, 11, 14, 17, 20, 23))
            self.assertEqual(cfg.manual_only_paths, ())
            self.assertEqual(cfg.provider_order, ("codex", "deepseek"))
            self.assertEqual(cfg.codex_model, "gpt-5.6-luna")
            self.assertEqual(cfg.codex_reasoning_effort, "medium")
            self.assertFalse(cfg.deepseek_thinking)
            self.assertEqual(cfg.validate(), [])

    def test_loads_manual_only_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({
                "vault_path": str(Path(directory) / "vault"),
                "timezone": "Asia/Shanghai",
                "run_hours": [8, 11, 14, 17, 20, 23],
                "manual_only_paths": ["20 工作/29 SOP"],
            }, ensure_ascii=False), encoding="utf-8")
            cfg = VaultConfig.load(path)
            self.assertEqual(cfg.manual_only_paths, ("20 工作/29 SOP",))
            self.assertEqual(cfg.validate(), [])

    def test_custom_run_hours_accepted(self):
        cfg = VaultConfig(Path("/tmp/vault"), "Asia/Shanghai", (9, 21))
        self.assertEqual(cfg.validate(), [])

    def test_invalid_run_hours_rejected(self):
        for hours in ((), (8, 8), (24,), (8, 21, 9), (-1,)):
            cfg = VaultConfig(Path("/tmp/vault"), "Asia/Shanghai", hours)
            self.assertNotEqual(cfg.validate(), [], "run_hours=%r should fail validation" % (hours,))

    def test_provider_order_with_custom_provider_accepted(self):
        cfg = VaultConfig(
            Path("/tmp/vault"), "Asia/Shanghai", (8, 12, 18),
            provider_order=("deepseek", "kimi"),
            api_providers={"kimi": {"base_url": "https://api.example.com/v1", "model": "kimi-x"}},
        )
        self.assertEqual(cfg.validate(), [])
        self.assertEqual(cfg.provider_label("kimi"), "kimi")

    def test_unknown_provider_or_malformed_api_providers_rejected(self):
        bad_order = VaultConfig(
            Path("/tmp/vault"), "Asia/Shanghai", (8,),
            provider_order=("deepseek", "nope"),
        )
        self.assertNotEqual(bad_order.validate(), [])
        bad_spec = VaultConfig(
            Path("/tmp/vault"), "Asia/Shanghai", (8,),
            provider_order=("kimi",),
            api_providers={"kimi": {"model": "kimi-x"}},
        )
        self.assertNotEqual(bad_spec.validate(), [])


if __name__ == "__main__":
    unittest.main()
