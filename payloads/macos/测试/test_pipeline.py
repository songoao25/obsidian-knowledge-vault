from datetime import datetime
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from knowledge_vault.config import VaultConfig
from knowledge_vault.pipeline import run_maintenance


class FakeClient:
    def json_completion(self, system, user, **kwargs):
        return {
            "title": "完整保留的个人分析",
            "target_dir": "30 学习/31 知识领域/31.9 计算机与人工智能",
            "action": "create",
            "body": "这是用户自己的具体判断，不能被压缩。\n\n原始链接：https://example.com/a",
            "source_type": "personal_analysis",
            "source_urls": ["https://example.com/a"],
            "attachment_names": [],
            "existing_note": "",
            "confidence": 0.9,
            "rationale": "内容讨论人工智能",
            "continuous_maintenance": False,
        }


class FakeUpdateClient:
    def json_completion(self, system, user, **kwargs):
        return {
            "title": "已有主题",
            "target_dir": "30 学习/31 知识领域/31.9 计算机与人工智能",
            "action": "update",
            "body": "补充内容。",
            "source_type": "fragment",
            "source_urls": [],
            "attachment_names": [],
            "existing_note": "30 学习/31 知识领域/31.9 计算机与人工智能/已有主题-AI.md",
            "confidence": 0.95,
            "rationale": "需要补充既有主题",
            "continuous_maintenance": False,
        }


class PipelineTests(unittest.TestCase):
    def test_early_setup_failure_returns_original_failure_instead_of_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root / "project"; vault = root / "not-a-directory"
            project.mkdir()
            vault.write_text("占位文件", encoding="utf-8")
            config = VaultConfig(vault, "Asia/Shanghai", (8, 11, 14, 17, 20, 23))
            now = datetime(2026, 7, 14, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

            with patch("knowledge_vault.pipeline.PROJECT_ROOT", project):
                result = run_maintenance(config, now=now, client=FakeClient())

            self.assertEqual(result["failed"], 1)

    def test_twenty_three_run_archives_only_organize_record_after_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root / "project"; vault = root / "vault"
            (project / "配置").mkdir(parents=True)
            (project / "配置/taxonomy.json").write_text(json.dumps({"paths": ["30 学习/31 知识领域/31.9 计算机与人工智能"]}, ensure_ascii=False), encoding="utf-8")
            (project / "配置/tag_policy.json").write_text(json.dumps({"allowed_tags": ["kind/note", "kind/maintenance-log", "workflow/inbox", "topic/automation", "project/knowledge-vault"], "path_defaults": {"00 收件箱": ["kind/note", "workflow/inbox"]}}, ensure_ascii=False), encoding="utf-8")
            inbox = vault / "00 收件箱"; inbox.mkdir(parents=True)
            target = vault / "30 学习/31 知识领域/31.9 计算机与人工智能"; target.mkdir(parents=True)
            source = inbox / "分析.md"; source.write_text("我的长分析 https://example.com/a", encoding="utf-8")
            old = datetime(2026, 7, 14, 18, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
            os.utime(source, (old, old))
            config = VaultConfig(vault, "Asia/Shanghai", (8, 11, 14, 17, 20, 23), stable_file_minutes=10)
            now = datetime(2026, 7, 14, 23, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
            with patch("knowledge_vault.pipeline.PROJECT_ROOT", project):
                result = run_maintenance(config, now=now, client=FakeClient())
            record = vault / "90 系统/94 维护记录/94.1 整理记录/2026/07/2026-07-14 整理记录-AI.md"
            self.assertEqual(result, {"processed": 1, "failed": 0, "deferred": 0})
            self.assertTrue(record.exists())
            record_text = record.read_text(encoding="utf-8")
            self.assertIn("modified: 2026-07-14T23:00:00", record_text)
            self.assertIn("**已整理**", record_text)
            self.assertIn("1. **《分析》**", record_text)
            self.assertIn("位置：30 学习 / 31 知识领域 / 31.9 计算机与人工智能", record_text)
            self.assertFalse(list(vault.rglob("*运行日志-AI.md")))
            self.assertFalse(list(vault.rglob("*每日记录-AI.md")))

    def test_success_is_atomic_and_archives_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root / "project"; vault = root / "vault"
            (project / "配置").mkdir(parents=True)
            (project / "配置/taxonomy.json").write_text(json.dumps({"paths": ["30 学习/31 知识领域/31.9 计算机与人工智能"]}, ensure_ascii=False), encoding="utf-8")
            (project / "配置/tag_policy.json").write_text(json.dumps({"allowed_tags": ["kind/note", "workflow/inbox"], "path_defaults": {"00 收件箱": ["kind/note", "workflow/inbox"]}}, ensure_ascii=False), encoding="utf-8")
            inbox = vault / "00 收件箱"; inbox.mkdir(parents=True)
            target = vault / "30 学习/31 知识领域/31.9 计算机与人工智能"; target.mkdir(parents=True)
            source = inbox / "分析.md"; source.write_text("我的长分析 https://example.com/a", encoding="utf-8")
            old = datetime(2026, 7, 14, 7, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
            os.utime(source, (old, old))
            config = VaultConfig(vault, "Asia/Shanghai", (8, 11, 14, 17, 20, 23), stable_file_minutes=10)
            now = datetime(2026, 7, 14, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
            with patch("knowledge_vault.pipeline.PROJECT_ROOT", project):
                result = run_maintenance(config, now=now, client=FakeClient())
            self.assertEqual(result, {"processed": 1, "failed": 0, "deferred": 0})
            note = target / "2026-07-14 完整保留的个人分析.md"
            self.assertIn("我的长分析 https://example.com/a", note.read_text(encoding="utf-8"))
            self.assertFalse(source.exists())
            self.assertTrue(list((vault / "90 系统/95 原始输入归档/2026/07/14").rglob("分析.md")))

    def test_update_plan_is_rejected_and_original_remains_in_inbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root / "project"; vault = root / "vault"
            (project / "配置").mkdir(parents=True)
            (project / "配置/taxonomy.json").write_text(json.dumps({"paths": ["30 学习/31 知识领域/31.9 计算机与人工智能"]}, ensure_ascii=False), encoding="utf-8")
            (project / "配置/tag_policy.json").write_text(json.dumps({"allowed_tags": ["kind/note", "kind/maintenance-log", "workflow/inbox", "topic/automation", "project/knowledge-vault"], "path_defaults": {"00 收件箱": ["kind/note", "workflow/inbox"]}}, ensure_ascii=False), encoding="utf-8")
            inbox = vault / "00 收件箱"; inbox.mkdir(parents=True)
            target = vault / "30 学习/31 知识领域/31.9 计算机与人工智能"; target.mkdir(parents=True)
            (target / "已有主题-AI.md").write_text("已有主题内容。", encoding="utf-8")
            source = inbox / "补充.md"; source.write_text("已有主题补充资料", encoding="utf-8")
            old = datetime(2026, 7, 14, 7, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
            os.utime(source, (old, old))
            config = VaultConfig(vault, "Asia/Shanghai", (8, 11, 14, 17, 20, 23), stable_file_minutes=10)
            now = datetime(2026, 7, 14, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

            with patch("knowledge_vault.pipeline.PROJECT_ROOT", project):
                first = run_maintenance(config, now=now, client=FakeUpdateClient())
                second = run_maintenance(config, now=datetime(2026, 7, 14, 11, 0, tzinfo=ZoneInfo("Asia/Shanghai")), client=FakeUpdateClient())

            self.assertEqual(first, {"processed": 0, "failed": 1, "deferred": 0})
            self.assertEqual(second, {"processed": 0, "failed": 0, "deferred": 0})
            self.assertTrue(source.exists())
            records = list((vault / "90 系统/94 维护记录/94.4 高影响操作记录/2026").glob("*.md"))
            self.assertEqual(len(records), 0)
            daily = (vault / "00 收件箱/2026-07-14 整理记录-AI.md").read_text(encoding="utf-8")
            self.assertIn("action 必须是 create 或 classify", daily)

    def test_sensitive_text_is_sent_to_the_organizer_and_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root / "project"; vault = root / "vault"
            (project / "配置").mkdir(parents=True)
            (project / "配置/taxonomy.json").write_text(json.dumps({"paths": ["30 学习/31 知识领域/31.9 计算机与人工智能"]}, ensure_ascii=False), encoding="utf-8")
            (project / "配置/tag_policy.json").write_text(json.dumps({"allowed_tags": ["kind/note", "workflow/inbox"], "path_defaults": {"00 收件箱": ["kind/note", "workflow/inbox"]}}, ensure_ascii=False), encoding="utf-8")
            inbox = vault / "00 收件箱"; inbox.mkdir(parents=True)
            target = vault / "30 学习/31 知识领域/31.9 计算机与人工智能"; target.mkdir(parents=True)
            source = inbox / "种子地图.md"; source.write_text("银行卡号码 6222021234567890123 https://example.com/a", encoding="utf-8")
            old = datetime(2026, 7, 14, 7, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
            os.utime(source, (old, old))
            config = VaultConfig(vault, "Asia/Shanghai", (8, 11, 14, 17, 20, 23), stable_file_minutes=10)
            now = datetime(2026, 7, 14, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

            with patch("knowledge_vault.pipeline.PROJECT_ROOT", project):
                result = run_maintenance(config, now=now, client=FakeClient())

            self.assertEqual(result, {"processed": 1, "failed": 0, "deferred": 0})
            self.assertFalse(source.exists())

    def test_markdown_with_referenced_note_is_processed_not_deferred(self):
        # 回归测试：Markdown 通过 Wikilink 明确引用的同收件箱文件，应与引用它的笔记作为
        # 同一输入正常整理归档，不再被一刀切 defer 而永久滞留。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root / "project"; vault = root / "vault"
            (project / "配置").mkdir(parents=True)
            (project / "配置/taxonomy.json").write_text(json.dumps({"paths": ["30 学习/31 知识领域/31.9 计算机与人工智能"]}, ensure_ascii=False), encoding="utf-8")
            (project / "配置/tag_policy.json").write_text(json.dumps({"allowed_tags": ["kind/note", "workflow/inbox"], "path_defaults": {"00 收件箱": ["kind/note", "workflow/inbox"]}}, ensure_ascii=False), encoding="utf-8")
            inbox = vault / "00 收件箱"; inbox.mkdir(parents=True)
            target = vault / "30 学习/31 知识领域/31.9 计算机与人工智能"; target.mkdir(parents=True)
            main = inbox / "主笔记.md"; main.write_text("参见 ![[从笔记.md]] 的补充。https://example.com/a", encoding="utf-8")
            side = inbox / "从笔记.md"; side.write_text("被引用的补充资料。", encoding="utf-8")
            old = datetime(2026, 7, 14, 7, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
            os.utime(main, (old, old)); os.utime(side, (old, old))
            config = VaultConfig(vault, "Asia/Shanghai", (8, 11, 14, 17, 20, 23), stable_file_minutes=10)
            now = datetime(2026, 7, 14, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

            with patch("knowledge_vault.pipeline.PROJECT_ROOT", project):
                result = run_maintenance(config, now=now, client=FakeClient())

            self.assertEqual(result, {"processed": 1, "failed": 0, "deferred": 0})
            self.assertFalse(main.exists())
            self.assertFalse(side.exists())

    def test_non_markdown_is_classified_not_organized(self):
        # 回归测试：纯非 Markdown（如 PDF）应直接分类搬入目标目录，不建笔记、不归档原件。
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); project = root / "project"; vault = root / "vault"
            (project / "配置").mkdir(parents=True)
            (project / "配置/taxonomy.json").write_text(json.dumps({"paths": ["30 学习/31.1"]}, ensure_ascii=False), encoding="utf-8")
            (project / "配置/tag_policy.json").write_text(json.dumps({"allowed_tags": ["kind/note", "workflow/inbox"], "path_defaults": {"00 收件箱": ["kind/note", "workflow/inbox"]}}, ensure_ascii=False), encoding="utf-8")
            inbox = vault / "00 收件箱"; inbox.mkdir(parents=True)
            target = vault / "30 学习/31.1"; target.mkdir(parents=True)
            source = inbox / "报告.txt"; source.write_text("一些待分类的内容。", encoding="utf-8")
            old = datetime(2026, 7, 14, 7, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
            os.utime(source, (old, old))
            config = VaultConfig(vault, "Asia/Shanghai", (8, 11, 14, 17, 20, 23), stable_file_minutes=10)
            now = datetime(2026, 7, 14, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

            class FakeClassifyClient:
                def json_completion(self, system, user, **kwargs):
                    return {"title": "报告", "target_dir": "30 学习/31.1", "action": "classify", "body": "", "source_type": "reference", "source_urls": [], "attachment_names": [], "existing_note": "", "confidence": 0.9, "rationale": "x", "continuous_maintenance": False}

            with patch("knowledge_vault.pipeline.PROJECT_ROOT", project):
                result = run_maintenance(config, now=now, client=FakeClassifyClient())

            self.assertEqual(result, {"processed": 1, "failed": 0, "deferred": 0})
            self.assertFalse(source.exists())
            self.assertTrue((target / "报告.txt").exists())
            record = (inbox / "2026-07-14 整理记录-AI.md").read_text(encoding="utf-8")
            self.assertIn("已分类为：[[30 学习/31.1/报告.txt|报告]]", record)
            # 不应新建笔记或归档原件。
            self.assertFalse(any(p.suffix == ".md" for p in target.iterdir()))
            self.assertFalse((vault / "90 系统/95 原始输入归档").exists())


if __name__ == "__main__": unittest.main()
