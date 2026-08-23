from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from knowledge_vault.models import FileChange
from knowledge_vault.transaction import VaultTransaction


class TransactionTests(unittest.TestCase):
    def test_transaction_stamps_modified_only_for_content_writes_marked_for_update(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            note = root / "主题-AI.md"
            note.write_text("正文\n", encoding="utf-8")

            result = VaultTransaction(root / "rollback").apply(
                [FileChange.write(note, "正文\n\n![[图片.png]]\n", update_modified=True)],
                now=datetime(2026, 7, 14, 9, 30),
            )

            self.assertTrue(result.ok)
            content = note.read_text(encoding="utf-8")
            self.assertIn("modified: 2026-07-14T09:30:00", content)
            self.assertIn("![[图片.png]]", content)

    def test_transaction_restores_deleted_file_when_later_change_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            removed = root / "错误生成的笔记.md"; removed.write_text("错误内容", encoding="utf-8")
            failed = root / "other.md"
            result = VaultTransaction(root / "rollback").apply([
                FileChange.delete(removed), FileChange.fail_for_test(failed)
            ])
            self.assertFalse(result.ok)
            self.assertEqual(removed.read_text(encoding="utf-8"), "错误内容")

    def test_transaction_rolls_back_all_files_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "a.md"; first.write_text("old-a", encoding="utf-8")
            second = root / "b.md"; second.write_text("old-b", encoding="utf-8")
            result = VaultTransaction(root / "rollback").apply([
                FileChange.write(first, "new-a"), FileChange.fail_for_test(second)
            ])
            self.assertFalse(result.ok)
            self.assertEqual(first.read_text(encoding="utf-8"), "old-a")
            self.assertEqual(second.read_text(encoding="utf-8"), "old-b")


if __name__ == "__main__": unittest.main()
