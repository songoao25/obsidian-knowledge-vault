from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from knowledge_vault.archive import compile_archive_no_delete_property_changes, compile_archive_migration_changes


class ArchiveTests(unittest.TestCase):
    def test_existing_archive_markdown_gets_an_unchecked_no_delete_property(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "2026/07/16/原文.md"
            source.parent.mkdir(parents=True)
            source.write_text("原始内容", encoding="utf-8")

            changes = compile_archive_no_delete_property_changes(root)

            self.assertEqual(len(changes), 1)
            self.assertEqual(changes[0].target, source)
            self.assertEqual(changes[0].content.decode("utf-8"), "---\nno_delete: false\n---\n原始内容")

    def test_existing_checked_no_delete_property_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "2026/07/16/原文.md"
            source.parent.mkdir(parents=True)
            source.write_text("---\nno_delete: true\n---\n原始内容", encoding="utf-8")

            self.assertEqual(compile_archive_no_delete_property_changes(root), [])

    def test_hash_directory_is_flattened_to_original_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "2026/07/16/cd7f47e34c35ae03"
            archive.mkdir(parents=True)
            (archive / "通勤.md").write_text("原始内容", encoding="utf-8")
            (archive / ".preservation.json").write_text(json.dumps({"version": 1, "formal_note": "10 生活/通勤.md", "remote_urls": [], "local_attachments": []}), encoding="utf-8")
            changes = compile_archive_migration_changes(root, now=datetime(2026, 7, 16, 18, 0, tzinfo=timezone.utc))
            self.assertEqual(changes[0].source, archive / "通勤.md")
            self.assertEqual(changes[0].target.name, "通勤.md")
            self.assertEqual(changes[1].target.parent.name, ".preservation")
            manifest = json.loads(changes[1].content.decode("utf-8"))
            self.assertEqual(manifest["bundle_id"], "cd7f47e34c35ae03")
            self.assertEqual(manifest["archive_name"], "16")
            self.assertEqual(manifest["source_files"][0]["name"], "通勤.md")
            self.assertEqual(changes[2].operation, "delete")

    def test_existing_single_file_readable_directory_is_also_flattened(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "2026/07/16/通勤"
            archive.mkdir(parents=True)
            (archive / "通勤.md").write_text("原始内容", encoding="utf-8")
            changes = compile_archive_migration_changes(root)
            self.assertEqual(changes[0].target, root / "2026/07/16/通勤.md")
            self.assertEqual(changes[-1].operation, "delete")

    def test_legacy_hash_directory_without_manifest_is_still_renamed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "2026/07/15/8c50b369d04109dc"
            archive.mkdir(parents=True)
            (archive / "想法.md").write_text("个人分析", encoding="utf-8")
            changes = compile_archive_migration_changes(root)
            self.assertEqual(len(changes), 2)
            self.assertEqual(changes[0].source, archive / "想法.md")
            self.assertEqual(changes[0].target.name, "想法.md")
            self.assertFalse(any(change.target.parent.name == ".preservation" for change in changes))


if __name__ == "__main__":
    unittest.main()
