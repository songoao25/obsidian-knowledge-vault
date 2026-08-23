from pathlib import Path
import tempfile
import unittest

from knowledge_vault.audit import (
    community_plugin_issues,
    note_content_issues,
    note_metadata_issues,
    tag_policy_issues,
    unexpected_vault_roots,
    manual_only_path_issues,
    organize_record_link_issues,
)


class AuditTests(unittest.TestCase):
    def test_reports_development_directory_at_vault_root(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            for name in ("00 收件箱", "10 生活", "20 工作", "30 学习", "40 记录", "90 系统", ".obsidian", "docs"):
                (vault / name).mkdir()
            self.assertEqual(unexpected_vault_roots(vault), ["docs"])

    def test_accepts_only_the_approved_community_plugin_set(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            obsidian = vault / ".obsidian"
            obsidian.mkdir()
            approved = ["realclaudian", "frontmatter-modified-date", "obsidian-auto-organizer"]
            (obsidian / "community-plugins.json").write_text(__import__("json").dumps(approved), encoding="utf-8")
            for plugin_id in approved:
                plugin = obsidian / "plugins" / plugin_id
                plugin.mkdir(parents=True)
                (plugin / "manifest.json").write_text(__import__("json").dumps({"id": plugin_id}), encoding="utf-8")
            self.assertEqual(community_plugin_issues(vault), [])

    def test_reports_stale_file_overrides_and_swapped_root_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / "tag_policy.json"
            taxonomy = root / "taxonomy.json"
            policy.write_text(__import__("json").dumps({
                "path_defaults": {
                    "00 收件箱": ["kind/note", "workflow/inbox"],
                    "10 生活": ["kind/note"],
                    "20 学习": ["kind/note"],
                    "30 工作": ["kind/note"],
                    "40 记录": ["kind/record"],
                },
                "overrides": {"00 收件箱/未命名.md": ["kind/analysis"]},
            }, ensure_ascii=False), encoding="utf-8")
            taxonomy.write_text(__import__("json").dumps({
                "paths": ["00 收件箱", "10 生活", "20 工作", "30 学习", "40 记录", "90 系统"]
            }, ensure_ascii=False), encoding="utf-8")
            issues = tag_policy_issues(policy, taxonomy)
            self.assertTrue(any("文件级覆盖" in issue for issue in issues))
            self.assertTrue(any("20 工作" in issue for issue in issues))
            self.assertTrue(any("20 学习" in issue for issue in issues))

    def test_reports_manual_only_path_missing_from_taxonomy(self):
        issues = manual_only_path_issues(
            ("20 工作/29 SOP",),
            ("20 工作/28 工作成果与样本",),
        )
        self.assertEqual(issues, ["本人维护目录不在 taxonomy：20 工作/29 SOP"])

    def test_reports_semantically_wrong_empty_draft_and_organize_record(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            inbox = vault / "00 收件箱"
            archive = vault / "90 系统/94 维护记录/94.1 整理记录/2026/07"
            inbox.mkdir(parents=True)
            archive.mkdir(parents=True)
            base = "created: 2026-07-16T10:00:00\nmodified: 2026-07-16T10:00:00\ntags:\n"
            (inbox / "未命名.md").write_text(
                f"---\n{base}  - kind/analysis\n  - workflow/inbox\n  - topic/ai-agent\n---\n",
                encoding="utf-8",
            )
            (archive / "2026-07-15 整理记录-AI.md").write_text(
                f"---\n{base}  - kind/note\n  - workflow/inbox\n---\n\n## 23:00\n",
                encoding="utf-8",
            )
            allowed = {"kind/note", "kind/analysis", "kind/maintenance-log", "workflow/inbox", "topic/ai-agent", "topic/automation", "project/knowledge-vault"}
            issues = note_metadata_issues(vault, allowed)
            self.assertTrue(any("空白收件箱笔记" in issue for issue in issues))
            self.assertTrue(any("整理记录" in issue for issue in issues))

    def test_reports_repeated_document_title_and_invalid_heading_depth(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            notes = vault / "30 学习"; notes.mkdir(parents=True)
            (notes / "2026-07-16 标题.md").write_text(
                "---\ncreated: 2026-07-16T10:00:00\nmodified: 2026-07-16T10:00:00\ntags:\n  - kind/note\n---\n\n## 标题\n\n#### 过深\n\n正文。",
                encoding="utf-8",
            )
            issues = note_content_issues(vault)
            self.assertTrue(any("重复文档标题" in issue for issue in issues))
            self.assertFalse(any("标题层级超过三级" in issue for issue in issues))

    def test_reports_prefixed_opening_h1_that_is_not_an_exact_filename_match(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            notes = vault / "30 学习"; notes.mkdir(parents=True)
            (notes / "2026-08-08 macOS 15.x 更新屏蔽方案.md").write_text(
                "---\ncreated: 2026-08-08T20:40:09\nmodified: 2026-08-08T20:40:09\ntags:\n  - kind/method\n---\n\n# macOS Update Shield：macOS 15.x 更新屏蔽方案\n\n正文。",
                encoding="utf-8",
            )
            issues = note_content_issues(vault)
            self.assertTrue(any("重复文档标题" in issue for issue in issues))

    def test_does_not_mistake_a_more_specific_opening_h1_for_a_duplicate_title(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            notes = vault / "30 学习"; notes.mkdir(parents=True)
            (notes / "恋爱知识库导航.md").write_text(
                "---\ncreated: 2026-08-08T20:40:09\nmodified: 2026-08-08T20:40:09\ntags:\n  - kind/note\n---\n\n# 狗头军师恋爱知识库导航\n\n正文。",
                encoding="utf-8",
            )
            self.assertEqual(note_content_issues(vault), [])

    def test_reports_missing_or_non_clickable_organize_record_results(self):
        with tempfile.TemporaryDirectory() as directory:
            vault = Path(directory)
            records = vault / "90 系统/94 维护记录/94.1 整理记录/2026/08"
            records.mkdir(parents=True)
            (records / "2026-08-08 整理记录-AI.md").write_text(
                "**已整理**\n\n1. **《报告》**\n\n已分类到：30 学习/31.1。\n\n"
                "2. **《材料》**\n\n整理为：[[30 学习/31.1/不存在|材料]]。\n",
                encoding="utf-8",
            )
            issues = organize_record_link_issues(vault)
            self.assertTrue(any("缺少可点击链接" in issue for issue in issues))
            self.assertTrue(any("链接不存在" in issue for issue in issues))


if __name__ == "__main__":
    unittest.main()
