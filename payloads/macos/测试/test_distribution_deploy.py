from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


DEPLOY_PATH = Path(__file__).resolve().parents[1] / "发布" / "deploy.py"
if not DEPLOY_PATH.is_file():
    DEPLOY_PATH = Path(__file__).resolve().parents[1] / "deploy.py"
deploy = None
if DEPLOY_PATH.is_file():
    SPEC = importlib.util.spec_from_file_location("distribution_deploy", DEPLOY_PATH)
    deploy = importlib.util.module_from_spec(SPEC)
    assert SPEC.loader is not None
    SPEC.loader.exec_module(deploy)


class RecordingRunner:
    def __init__(self, audit_code: int = 0, smoke_code: int = 0):
        self.calls = []
        self.audit_code = audit_code
        self.smoke_code = smoke_code

    def run(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs))
        code = self.audit_code if "audit" in cmd else self.smoke_code if "provider-smoke" in cmd else 0
        return subprocess.CompletedProcess(cmd, code, "FAIL test" if code else "OK", "")


@unittest.skipUnless(deploy is not None, "已安装项目不携带发布入口 deploy.py")
class DistributionDeployTests(unittest.TestCase):
    def test_public_topic_vocabulary_matches_runtime_tag_policy(self):
        root = Path(__file__).resolve().parents[1]
        runtime = json.loads((root / "配置/tag_policy.json").read_text(encoding="utf-8"))
        windows = json.loads((root / "发布/source_windows/配置/tag_policy.json").read_text(encoding="utf-8"))
        vocabulary = (root / "发布/templates/维护规范中性/标签词表.md").read_text(encoding="utf-8")
        expected = {
            "topic/obsidian", "topic/knowledge-management", "topic/automation", "topic/ai-agent",
            "topic/consumer-rights", "topic/vehicle-rental", "topic/huawei", "topic/harmonyos",
        }
        self.assertTrue(expected.issubset(set(runtime["allowed_tags"])))
        self.assertTrue(expected.issubset(set(windows["allowed_tags"])))
        for tag in expected:
            self.assertIn(f"`{tag}`", vocabulary)

    def test_reconfiguration_updates_managed_settings_and_preserves_other_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(json.dumps({"run_hours": [8], "provider_order": ["codex"], "user_note": "keep"}))
            values = {
                "VAULT_PATH": "/tmp/vault", "TIMEZONE": "Asia/Shanghai",
                "RUN_HOURS_JSON": "[9, 12, 18]", "PROVIDER_ORDER_JSON": '["deepseek"]',
                "API_PROVIDERS_JSON": "{}", "KEYCHAIN_SERVICE": "knowledge-vault-deepseek",
            }
            template = json.dumps({
                "vault_path": "{{VAULT_PATH}}", "timezone": "{{TIMEZONE}}",
                "run_hours": "RUN_HOURS", "provider_order": "PROVIDERS",
                "api_providers": {}, "keychain_service": "{{KEYCHAIN_SERVICE}}",
                "manual_only_paths": ["20 工作/29 SOP"],
                "expected_community_plugins": ["knowledge-vault-auto-properties"],
                "strict_local_audit": False,
            }).replace('"RUN_HOURS"', "{{RUN_HOURS_JSON}}").replace('"PROVIDERS"', "{{PROVIDER_ORDER_JSON}}")
            result = deploy._write_macos_settings(path, template, values)
            self.assertEqual(result["run_hours"], [9, 12, 18])
            self.assertEqual(result["provider_order"], ["deepseek"])
            self.assertEqual(result["user_note"], "keep")

    def test_keychain_secret_never_enters_command_arguments(self):
        runner = RecordingRunner()
        deploy._store_keychain_secret(runner, "knowledge-vault-deepseek", "super-secret-value")
        command, kwargs = runner.calls[0]
        self.assertEqual(command[-1], "-w")
        self.assertNotIn("super-secret-value", command)
        self.assertEqual(kwargs["input_text"], "super-secret-value\n")

    def test_model_flag_uses_hidden_local_input(self):
        args = argparse.Namespace(
            custom_provider=None, model="deepseek", yes=False,
            keychain_service=None,
        )
        with patch.object(deploy.getpass, "getpass", return_value="hidden-secret"):
            order, providers, keys = deploy._resolve_macos_providers(args)
        self.assertEqual(order, ["deepseek"])
        self.assertEqual(providers, {})
        self.assertEqual(keys, {"knowledge-vault-deepseek": "hidden-secret"})

    def _acceptance_fixture(self, audit_code=0, smoke_code=0):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        project = root / "project"
        vault = root / "vault"
        (project / "配置").mkdir(parents=True)
        (project / "程序").mkdir()
        (project / "测试").mkdir()
        (project / "配置" / "taxonomy.json").write_text('{"paths": ["00 收件箱"]}')
        (project / "配置" / "settings.json").write_text('{"run_hours": [8, 12, 18]}')
        (vault / "00 收件箱").mkdir(parents=True)
        runner = RecordingRunner(audit_code=audit_code, smoke_code=smoke_code)
        args = argparse.Namespace()
        info = {"project_dir": project, "python": "python3", "vault": vault, "scheduled": False}
        return temporary, runner, info, args

    def test_health_audit_failure_blocks_acceptance(self):
        temporary, runner, info, args = self._acceptance_fixture(audit_code=1)
        try:
            problems = deploy.macos_acceptance(runner, info, args)
        finally:
            temporary.cleanup()
        self.assertTrue(any("健康审计失败" in problem for problem in problems))

    def test_model_smoke_failure_blocks_acceptance(self):
        temporary, runner, info, args = self._acceptance_fixture(smoke_code=1)
        try:
            problems = deploy.macos_acceptance(runner, info, args)
        finally:
            temporary.cleanup()
        self.assertTrue(any("模型不可用" in problem for problem in problems))

    def test_no_secret_or_verification_bypass_flags_remain(self):
        source = DEPLOY_PATH.read_text(encoding="utf-8")
        for unsafe in ("--deepseek-key", "--chatgpt-key", "--skip-ai-check", "--skip-audit"):
            self.assertNotIn(unsafe, source)


if __name__ == "__main__":
    unittest.main()
