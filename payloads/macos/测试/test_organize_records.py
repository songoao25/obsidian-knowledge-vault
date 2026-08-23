from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from knowledge_vault.organize_records import (
    append_run_entry,
    append_cleanup_entry,
    archive_daily_record,
    ensure_daily_record,
)


class OrganizeRecordTests(unittest.TestCase):
    def test_each_record_write_updates_modified_to_the_write_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp) / "00 收件箱"
            created_at = datetime(2026, 7, 14, 8, 0)
            updated_at = datetime(2026, 7, 14, 14, 0)
            record = ensure_daily_record(inbox, created_at)

            append_run_entry(record, updated_at, "下午整理完成。")

            self.assertIn("modified: 2026-07-14T14:00:00", record.read_text(encoding="utf-8"))

    def test_new_record_contains_only_run_result_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp) / "00 收件箱"
            now = datetime(2026, 7, 14, 8, 0)
            record = ensure_daily_record(inbox, now)

            append_run_entry(record, now, "收件箱没有待处理内容。")

            text = record.read_text(encoding="utf-8")
            self.assertIn("\n## 08:00\n", text)
            self.assertNotIn("待确认", text)
            self.assertNotIn("批准执行", text)

    def test_archives_at_twenty_three_without_carrying_approvals(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            record = ensure_daily_record(vault / "00 收件箱", datetime(2026, 7, 14, 8, 0))
            append_run_entry(record, datetime(2026, 7, 14, 8, 0), "早间整理。")

            archived = archive_daily_record(vault, datetime(2026, 7, 14, 23, 0))

            expected = vault / "90 系统/94 维护记录/94.1 整理记录/2026/07/2026-07-14 整理记录-AI.md"
            self.assertEqual(archived, expected)
            self.assertFalse(record.exists())
            text = expected.read_text(encoding="utf-8")
            self.assertIn("modified: 2026-07-14T23:00:00", text)
            self.assertNotIn("workflow/inbox", text)

    def test_second_twenty_three_run_merges_with_existing_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            now = datetime(2026, 7, 14, 23, 0)
            first = ensure_daily_record(vault / "00 收件箱", now)
            append_run_entry(first, now, "第一次整理。")
            archived = archive_daily_record(vault, now)
            second = ensure_daily_record(vault / "00 收件箱", now)
            append_run_entry(second, datetime(2026, 7, 14, 23, 30), "重复运行整理。")

            self.assertEqual(archive_daily_record(vault, now), archived)
            text = archived.read_text(encoding="utf-8")
            self.assertIn("第一次整理", text)
            self.assertIn("重复运行整理", text)
            self.assertEqual(text.count("\n---\n"), 1)
            self.assertFalse(second.exists())


class CleanupRecordTests(unittest.TestCase):
    def test_cleanup_entry_written_to_94_6_independent_of_organize_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            now = datetime(2026, 8, 7, 9, 30)
            append_cleanup_entry(
                vault, now,
                "已自动定期清理到期归档原件，并移入系统废纸篓：2 项\n"
                "- 90 系统/95 原始输入归档/2026/08/01/foo.md",
            )

            cleanup_path = vault / "90 系统/94 维护记录/94.6 清理记录/2026-08-07 清理记录-AI.md"
            self.assertTrue(cleanup_path.exists())
            text = cleanup_path.read_text(encoding="utf-8")
            self.assertIn("已自动定期清理到期归档原件，并移入系统废纸篓：2 项", text)
            self.assertIn("- 90 系统/95 原始输入归档/2026/08/01/foo.md", text)
            self.assertIn("\n## 09:30\n", text)
            # 清理记录独立成册，不得出现在整理记录中
            inbox_record = vault / "00 收件箱/2026-08-07 整理记录-AI.md"
            self.assertFalse(inbox_record.exists())

    def test_cleanup_entry_appends_multiple_runs_same_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            append_cleanup_entry(vault, datetime(2026, 8, 7, 9, 0), "清理：1 项")
            append_cleanup_entry(vault, datetime(2026, 8, 7, 18, 0), "清理：3 项")

            cleanup_path = vault / "90 系统/94 维护记录/94.6 清理记录/2026-08-07 清理记录-AI.md"
            text = cleanup_path.read_text(encoding="utf-8")
            self.assertEqual(text.count("\n## "), 2)
            self.assertIn("清理：1 项", text)
            self.assertIn("清理：3 项", text)


if __name__ == "__main__":
    unittest.main()
