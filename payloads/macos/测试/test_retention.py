from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
import hashlib

from knowledge_vault.retention import apply_retention


class RetentionTests(unittest.TestCase):
    def test_verified_raw_bundle_is_trashed_after_seven_days(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); raw = root / "90 系统/95 原始输入归档"; trash = root / "trash"
            archive = raw / "2026/07/01/bundle"; archive.mkdir(parents=True)
            note = root / "30 学习/文章.md"; note.parent.mkdir(parents=True)
            note.write_text("![图](https://example.com/a.png)", encoding="utf-8")
            (archive / "source.md").write_text("原文", encoding="utf-8")
            (archive / ".preservation.json").write_text(json.dumps({"formal_note": "30 学习/文章.md", "remote_urls": ["https://example.com/a.png"], "local_attachments": []}), encoding="utf-8")
            old = (now - timedelta(days=8)).timestamp()
            os.utime(archive, (old, old))
            report = apply_retention(now, raw, trash_dir=trash)
            self.assertIn(archive, report.trashed)
            self.assertFalse(archive.exists())

    def test_missing_media_does_not_block_automatic_cleanup(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); raw = root / "90 系统/95 原始输入归档"; archive = raw / "2026/07/01/bundle"; archive.mkdir(parents=True)
            note = root / "30 学习/文章.md"; note.parent.mkdir(parents=True); note.write_text("正文", encoding="utf-8")
            (archive / ".preservation.json").write_text(json.dumps({"formal_note": "30 学习/文章.md", "remote_urls": ["https://example.com/a.png"], "local_attachments": []}), encoding="utf-8")
            old = (now - timedelta(days=8)).timestamp(); os.utime(archive, (old, old))
            report = apply_retention(now, raw, trash_dir=root / "trash")
            self.assertIn(archive, report.trashed)
            self.assertFalse(archive.exists())

    def test_checked_no_delete_property_keeps_only_that_archive_page(self):
        now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); raw = root / "90 系统/95 原始输入归档"; archive = raw / "2026/07/01/原文"
            archive.mkdir(parents=True)
            note = root / "30 学习/原文.md"; note.parent.mkdir(parents=True); note.write_text("被改掉的内容", encoding="utf-8")
            source = archive / "原文.md"; source.write_text("---\nno_delete: true\n---\n必须保留的一句话", encoding="utf-8")
            required = hashlib.sha256("必须保留的一句话".encode("utf-8")).hexdigest()
            manifest = {
                "version": 2,
                "archived_at": "2026-07-01T12:00:00+00:00",
                "formal_note": "30 学习/原文.md",
                "source_files": [{"name": "原文.md", "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}],
                "required_line_hashes": [required],
                "remote_urls": [],
                "local_attachments": [],
            }
            (archive / ".preservation.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            report = apply_retention(now, raw, trash_dir=root / "trash")
            self.assertIn(archive, report.skipped)
            self.assertTrue(archive.exists())

    def test_unchecked_no_delete_property_is_trashed_after_seven_days(self):
        now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); raw = root / "90 系统/95 原始输入归档"; archive = raw / "2026/07/01/原文"; archive.mkdir(parents=True)
            note = root / "30 学习/原文.md"; note.parent.mkdir(parents=True); note.write_text("正式笔记", encoding="utf-8")
            source = archive / "原文.md"; source.write_text("---\nno_delete: false\n---\n原始内容", encoding="utf-8")
            manifest = {
                "version": 2,
                "archived_at": "2026-07-01T12:00:00+00:00",
                "formal_note": "30 学习/原文.md",
                "source_files": [{"name": "原文.md", "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}],
            }
            (archive / ".preservation.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

            report = apply_retention(now, raw, trash_dir=root / "trash")

            self.assertIn(archive, report.trashed)
            self.assertFalse(archive.exists())


if __name__ == "__main__": unittest.main()
