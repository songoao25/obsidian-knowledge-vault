from __future__ import annotations

import argparse
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("okv_deploy", ROOT / "deploy.py")
deploy = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(deploy)


class ReleaseContractTests(unittest.TestCase):
    def test_release_runtime_versions_are_consistent(self) -> None:
        root_deploy = (ROOT / "deploy.py").read_text(encoding="utf-8")
        windows_runtime = (ROOT / "payloads" / "windows" / "维护程序" / "windows_maintenance.py").read_text(encoding="utf-8")
        builder = (ROOT / "scripts" / "build_release.py").read_text(encoding="utf-8")
        for source in (root_deploy, windows_runtime, builder):
            self.assertIn('"1.2.1"', source)

    def test_release_factory_test_is_not_shipped_in_runtime_payload(self) -> None:
        self.assertFalse((ROOT / "payloads" / "macos" / "测试" / "test_distribution_deploy.py").exists())

    def test_both_profiles_exist_for_both_platforms(self) -> None:
        for platform, template_root in (("macos", "资源与模板/初始知识库模板"), ("windows", "知识库模板")):
            for profile in ("general", "legal"):
                root = ROOT / "payloads" / platform / "模板" / profile
                self.assertTrue((root / template_root).is_dir(), f"{platform}/{profile}")
                self.assertTrue((root / "配置" / "taxonomy.json").is_file())

    def test_general_profile_has_no_legal_work_taxonomy(self) -> None:
        for platform in ("macos", "windows"):
            path = ROOT / "payloads" / platform / "模板" / "general" / "配置" / "taxonomy.json"
            paths = json.loads(path.read_text(encoding="utf-8"))["paths"]
            self.assertIn("20 工作/22 项目与任务", paths)
            self.assertFalse(any("法律业务领域" in item or "办案与项目流程" in item for item in paths))

    def test_legal_profile_keeps_legal_work_taxonomy(self) -> None:
        path = ROOT / "payloads" / "macos" / "模板" / "legal" / "配置" / "taxonomy.json"
        paths = json.loads(path.read_text(encoding="utf-8"))["paths"]
        self.assertIn("20 工作/22 办案与项目流程", paths)
        self.assertIn("20 工作/23 法律业务领域", paths)

    def test_nonempty_vault_is_rejected_before_installation(self) -> None:
        with tempfile.TemporaryDirectory() as location:
            vault = Path(location) / "vault"
            vault.mkdir()
            (vault / "existing-note.md").write_text("do not touch", encoding="utf-8")
            with self.assertRaises(deploy.VaultNotEmptyError):
                deploy._require_empty_vault(vault)
            self.assertEqual((vault / "existing-note.md").read_text(encoding="utf-8"), "do not touch")

    def test_automatic_mode_uses_general_template(self) -> None:
        self.assertEqual(deploy._resolve_template(argparse.Namespace(template=None, yes=True)), "general")

    def test_windows_general_install_uses_disposable_empty_vault(self) -> None:
        with tempfile.TemporaryDirectory() as location:
            root = Path(location)
            args = argparse.Namespace(
                vault=str(root / "vault"), workspace=str(root / "workspace"),
                model="openai-first", run_hours=[9, 18], skip_schedule=True,
                yes=True, template="general",
                custom_provider=None, windows_keys={},
            )
            info = deploy.windows_install(deploy.Runner(simulate_win32=True), ROOT / "payloads" / "windows", args)
            self.assertEqual(info["template"], "general")
            self.assertTrue((root / "vault" / "20 工作" / "22 项目与任务").is_dir())
            self.assertFalse((root / "vault" / "20 工作" / "22 办案与项目流程").exists())


if __name__ == "__main__":
    unittest.main()
