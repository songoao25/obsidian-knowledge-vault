from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from knowledge_vault.filesystem import list_files
from knowledge_vault.scanner import group_items, scan_inbox
from knowledge_vault.state import StateStore, inspect_file


class ScannerTests(unittest.TestCase):
    def test_icloud_default_listing_uses_content_type_query(self):
        root = Path(tempfile.mkdtemp(prefix="kv-vault-")) / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Vault/00 收件箱"
        completed = Mock(returncode=0, stdout=f"{root}/资料.md\n", stderr="")
        with patch("knowledge_vault.filesystem.platform.system", return_value="Darwin"), \
             patch("knowledge_vault.filesystem.subprocess.run", return_value=completed) as run, \
             patch("pathlib.Path.exists", return_value=True), \
             patch("pathlib.Path.is_file", return_value=True), \
             patch("pathlib.Path.iterdir", return_value=iter(())):
            self.assertEqual(list_files(root), [root / "资料.md"])
        query = run.call_args.args[0][-1]
        self.assertIn('kMDItemContentTypeTree == "public.item"', query)
        self.assertNotEqual(query, 'kMDItemFSName == "*"')

    def test_icloud_listing_reports_indexing_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Vault/00 收件箱"
            root.mkdir(parents=True)
            completed = Mock(returncode=1, stdout="", stderr="metadata unavailable")
            with patch("knowledge_vault.filesystem.platform.system", return_value="Darwin"), \
                 patch("knowledge_vault.filesystem.subprocess.run", return_value=completed):
                self.assertEqual(list_files(root), [])

    def test_icloud_listing_falls_back_to_root_files_when_indexing_times_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Vault/00 收件箱"
            root.mkdir(parents=True)
            note = root / "刚创建.md"
            note.write_text("内容", encoding="utf-8")
            with patch("knowledge_vault.filesystem.platform.system", return_value="Darwin"), \
                 patch(
                     "knowledge_vault.filesystem.subprocess.run",
                     side_effect=subprocess.TimeoutExpired(["mdfind"], 20),
                 ):
                self.assertEqual(list_files(root), [note])

    def test_icloud_listing_allows_unindexed_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Vault/归档"
            (root / "2026/07").mkdir(parents=True)
            completed = Mock(returncode=0, stdout="", stderr="")
            with patch("knowledge_vault.filesystem.platform.system", return_value="Darwin"), \
                 patch("knowledge_vault.filesystem.subprocess.run", return_value=completed):
                self.assertEqual(list_files(root), [])

    def test_icloud_listing_includes_unindexed_root_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Library/Mobile Documents/iCloud~md~obsidian/Documents/Vault/00 收件箱"
            root.mkdir(parents=True)
            note = root / "刚创建.md"
            note.write_text("内容", encoding="utf-8")
            completed = Mock(returncode=0, stdout="", stderr="")
            with patch("knowledge_vault.filesystem.platform.system", return_value="Darwin"), \
                 patch("knowledge_vault.filesystem.subprocess.run", return_value=completed):
                self.assertEqual(list_files(root), [note])

    def test_scanner_ignores_ai_organize_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); inbox = root / "00 收件箱"; inbox.mkdir()
            record = inbox / "2026-07-14 整理记录-AI.md"; record.write_text("记录\n", encoding="utf-8")
            old = datetime(2026, 7, 14, 7, 0, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()
            os.utime(record, (old, old))
            items = scan_inbox(inbox, datetime(2026, 7, 14, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
            self.assertEqual(items, [])
    def test_scanner_ignores_empty_draft_until_user_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp)
            draft = inbox / "未命名.md"
            draft.write_text("", encoding="utf-8")
            old = datetime(2026, 7, 14, 7, 0, tzinfo=timezone.utc).timestamp()
            os.utime(draft, (old, old))
            now = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)
            self.assertEqual(scan_inbox(inbox, now), [])

    def test_scanner_ignores_frontmatter_only_draft_until_user_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp)
            draft = inbox / "未命名.md"
            draft.write_text("---\ncreated: 2026-07-29\ntags:\n  - kind/note\n  - workflow/inbox\n---\n", encoding="utf-8")
            old = datetime(2026, 7, 14, 7, 0, tzinfo=timezone.utc).timestamp()
            os.utime(draft, (old, old))
            now = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)
            self.assertEqual(scan_inbox(inbox, now), [])

    def test_scanner_keeps_markdown_draft_with_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            inbox = Path(tmp)
            note = inbox / "想法.md"
            note.write_text("---\ntags:\n  - kind/note\n---\n\n要补充的内容。\n", encoding="utf-8")
            old = datetime(2026, 7, 14, 7, 0, tzinfo=timezone.utc).timestamp()
            os.utime(note, (old, old))
            now = datetime(2026, 7, 14, 8, 0, tzinfo=timezone.utc)
            self.assertEqual([item.path.name for item in scan_inbox(inbox, now)], ["想法.md"])

    def test_scanner_skips_unstable_files(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory)
            path = inbox / "正在编辑.md"; path.write_text("内容", encoding="utf-8")
            os.utime(path, (now.timestamp(), now.timestamp()))
            self.assertEqual(scan_inbox(inbox, now), [])

    def test_markdown_reference_groups_attachment(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory)
            note = inbox / "分析.md"; note.write_text("![[证据.pdf]]", encoding="utf-8")
            pdf = inbox / "证据.pdf"; pdf.write_bytes(b"pdf")
            old = (now - timedelta(hours=1)).timestamp()
            os.utime(note, (old, old)); os.utime(pdf, (old, old))
            bundles = group_items(scan_inbox(inbox, now))
            self.assertEqual(len(bundles), 1)
            self.assertEqual({item.path.name for item in bundles[0].items}, {"分析.md", "证据.pdf"})

    def test_similarly_named_notes_are_not_grouped_without_an_explicit_reference(self):
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory)
            first = inbox / "想法.md"; first.write_text("一条独立想法", encoding="utf-8")
            second = inbox / "想法补充资料.md"; second.write_text("另一份独立资料", encoding="utf-8")
            old = (now - timedelta(hours=1)).timestamp()
            os.utime(first, (old, old)); os.utime(second, (old, old))
            bundles = group_items(scan_inbox(inbox, now))
            self.assertEqual([{item.path.name for item in bundle.items} for bundle in bundles], [{"想法.md"}, {"想法补充资料.md"}])

    def test_scanner_skips_ai_written_files_but_keeps_deferred_ones(self):
        # 修复回归：被「暂缓」(defer) 的文件未写入 ai_written_hash，必须仍可被扫描到，
        # 不能像旧逻辑那样按 known_hash 永久跳过。
        now = datetime.now(timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory)
            done = inbox / "已整理.md"; done.write_text("内容", encoding="utf-8")
            pending = inbox / "待处理.md"; pending.write_text("内容", encoding="utf-8")
            old = (now - timedelta(hours=1)).timestamp()
            os.utime(done, (old, old)); os.utime(pending, (old, old))
            state_path = Path(directory) / "state.json"
            state = StateStore(state_path)
            done_state = inspect_file(done)
            state.remember(done.relative_to(inbox), done_state, done_state.sha256)  # 已成功处理
            state.mark_deferred(pending.relative_to(inbox))  # 仅暂缓，未写 ai_written_hash
            state.save()
            reloaded = StateStore(state_path)
            names = {item.path.name for item in scan_inbox(inbox, now, state=reloaded)}
            self.assertNotIn("已整理.md", names)
            self.assertIn("待处理.md", names)


if __name__ == "__main__": unittest.main()
