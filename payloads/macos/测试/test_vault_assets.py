import json
from pathlib import Path
import tempfile
import unittest

from knowledge_vault.scaffold import install_vault_assets, merge_obsidian_settings
from knowledge_vault.prompts import ORGANIZE_SYSTEM


class VaultAssetTests(unittest.TestCase):
    def test_assets_install_without_overwriting_changed_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "资源与模板"
            vault = root / "vault"
            (source / "90 系统").mkdir(parents=True)
            (source / "90 系统/规范.md").write_text("新", encoding="utf-8")
            (vault / "90 系统").mkdir(parents=True)
            target = vault / "90 系统/规范.md"
            target.write_text("人工修改", encoding="utf-8")
            report = install_vault_assets(source, vault)
            self.assertEqual(target.read_text(encoding="utf-8"), "人工修改")
            self.assertIn(target, report.conflicts)

    def test_obsidian_settings_preserve_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            obsidian = vault / ".obsidian"
            obsidian.mkdir()
            workspace = obsidian / "workspace.json"
            workspace.write_text('{"user": true}', encoding="utf-8")
            (obsidian / "app.json").write_text("{}", encoding="utf-8")
            (obsidian / "core-plugins.json").write_text("{}", encoding="utf-8")
            merge_obsidian_settings(vault)
            app = json.loads((obsidian / "app.json").read_text(encoding="utf-8"))
            core = json.loads((obsidian / "core-plugins.json").read_text(encoding="utf-8"))
            self.assertEqual(app["newFileFolderPath"], "00 收件箱")
            self.assertTrue(core["bases"])
            self.assertEqual(workspace.read_text(encoding="utf-8"), '{"user": true}')

    def test_asset_bundle_contains_seven_bases(self):
        assets = Path(__file__).parents[1] / "资源与模板" / "初始知识库模板"
        self.assertEqual(len(list(assets.rglob("*.base"))), 6)

    def test_prompts_and_writing_rules_forbid_repeated_opening_title(self):
        assets = Path(__file__).parents[1] / "资源与模板" / "初始知识库模板"
        rule = (assets / "90 系统/92 维护规范/笔记整理与写作规范.md").read_text(encoding="utf-8")
        required = "正文开头不得重复文件标题"
        self.assertIn(required, ORGANIZE_SYSTEM)

    def test_prompts_and_rules_define_intelligent_content_routing(self):
        assets = Path(__file__).parents[1] / "资源与模板" / "初始知识库模板"
        rule = (assets / "90 系统/92 维护规范/笔记整理与写作规范.md").read_text(encoding="utf-8")
        for required in ("web_article", "personal_analysis", "fragment", "不确定时"):
            self.assertIn(required, ORGANIZE_SYSTEM)
        self.assertIn("完整网页文章", rule)
        self.assertIn("高置信度", rule)

    def test_writing_rules_define_a_single_body_baseline_and_numbered_records(self):
        assets = Path(__file__).parents[1] / "资源与模板" / "初始知识库模板"
        rules = (assets / "90 系统/92 维护规范/笔记整理与写作规范.md").read_text(encoding="utf-8")
        self.assertIn("正文是唯一基线", rules)
        self.assertIn("有先后顺序、多项整理结果", rules)


if __name__ == "__main__":
    unittest.main()
