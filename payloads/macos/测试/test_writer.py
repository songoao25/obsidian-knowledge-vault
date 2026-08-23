from datetime import date, datetime, timezone
import hashlib
import tempfile
import unittest
from pathlib import Path

from knowledge_vault.models import ExtractedContent, InboxItem, InputBundle, OrganizePlan
from knowledge_vault.writer import (
    can_ai_update,
    compile_append_changes,
    compile_classify_changes,
    compile_create_changes,
    note_filename,
    render_note,
    source_created_at,
    strip_redundant_opening_title,
    validate_note,
)


class WriterTests(unittest.TestCase):
    def test_source_created_time_is_preserved_but_archive_time_starts_at_ingestion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); inbox = root / "00 收件箱"; inbox.mkdir()
            source = inbox / "通勤.md"
            source.write_text(
                "---\ncreated: 2026-07-16T16:34:35\nmodified: 2026-07-16T16:35:17\n---\n\n地铁 4.5 元。\n",
                encoding="utf-8",
            )
            ingested = datetime(2026, 7, 16, 20, 45, 11, tzinfo=timezone.utc)
            item = InboxItem(source, Path("通勤.md"), hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_size, ingested, "text")
            bundle = InputBundle("bundle", [item])
            captured = source_created_at(bundle, ingested)
            changes = compile_create_changes(
                root,
                bundle,
                OrganizePlan("通勤", "10 生活", "create", "地铁 4.5 元。", confidence=0.95, source_type="fragment"),
                date(2026, 7, 16),
                created_at=captured,
                archived_at=ingested,
            )
            note = changes[0].content.decode("utf-8")
            manifest = next(change for change in changes if change.target.parent.name == ".preservation").content.decode("utf-8")
            self.assertIn("created: 2026-07-16T16:34:35", note)
            self.assertIn('"archived_at": "2026-07-16T20:45:11+00:00"', manifest)

    def test_append_archives_same_named_sources_without_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "30 学习/主题.md"
            target.parent.mkdir(parents=True)
            target.write_text("原文。\n", encoding="utf-8")
            first = root / "00 收件箱/a/资料.md"
            second = root / "00 收件箱/b/资料.md"
            first.parent.mkdir(parents=True)
            second.parent.mkdir(parents=True)
            first.write_text("一", encoding="utf-8")
            second.write_text("二", encoding="utf-8")
            items = [
                InboxItem(first, Path("a/资料.md"), hashlib.sha256("一".encode()).hexdigest(), 3, datetime.now(timezone.utc), "text"),
                InboxItem(second, Path("b/资料.md"), hashlib.sha256("二".encode()).hexdigest(), 3, datetime.now(timezone.utc), "text"),
            ]
            plan = OrganizePlan("主题", "30 学习", "append", "新增内容。", existing_note="30 学习/主题.md")

            changes = compile_append_changes(root, InputBundle("bundle", items), plan, date(2026, 7, 14))
            archive_targets = [change.target for change in changes if change.operation == "move"]

            self.assertEqual(len(set(archive_targets)), 2)
            self.assertTrue(changes[0].update_modified)

    def test_filename_and_natural_body(self):
        self.assertEqual(note_filename(date(2026, 7, 14), "法律/研究"), "2026-07-14 法律 研究.md")
        plan = OrganizePlan("标题", "30 学习/x", "create", "这是我的具体判断。", ("https://example.com",))
        body = render_note(plan)
        self.assertTrue(body.startswith("这是我的具体判断。"))
        self.assertIn("https://example.com", body)
        self.assertNotIn("# 标题", body)

    def test_attachment_is_colocated_and_ai_update_protects_human_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); inbox = root / "00 收件箱"; inbox.mkdir()
            image = inbox / "图.png"; image.write_bytes(b"png")
            item = InboxItem(image, Path("图.png"), hashlib.sha256(b"png").hexdigest(), 3, datetime.now(timezone.utc), "image")
            bundle = InputBundle("b", [item])
            plan = OrganizePlan("图示分析", "30 学习/主题", "create", "分析正文。", (), ("图.png",))
            changes = compile_create_changes(root, bundle, plan, date(2026, 7, 14))
            self.assertEqual(changes[1].target.parent.name, "2026-07-14 图示分析-附件")
            self.assertIn("created: 2026-07-14T00:00:00", changes[0].content.decode("utf-8"))
            self.assertIn("  - kind/note", changes[0].content.decode("utf-8"))
            note = root / "note.md"; note.write_text("AI", encoding="utf-8")
            from knowledge_vault.state import sha256_file
            ai_hash = sha256_file(note)
            self.assertTrue(can_ai_update(note, ai_hash))
            note.write_text("人工改过", encoding="utf-8")
            self.assertFalse(can_ai_update(note, ai_hash))

    def test_markdown_source_keeps_images_iframe_and_local_embed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); inbox = root / "00 收件箱"; inbox.mkdir()
            source = inbox / "网页.md"
            source.write_text("# 原文\n\n![](https://example.com/a.png)\n\n<iframe src=\"https://player.bilibili.com/player.html?bvid=BV1\"></iframe>\n\n![[图.png]]", encoding="utf-8")
            image = inbox / "图.png"; image.write_bytes(b"png")
            now = datetime.now(timezone.utc)
            bundle = InputBundle("clip", [
                InboxItem(source, Path("网页.md"), hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_size, now, "text"),
                InboxItem(image, Path("图.png"), hashlib.sha256(b"png").hexdigest(), 3, now, "image"),
            ])
            changes = compile_create_changes(root, bundle, OrganizePlan("网页", "30 学习", "create", "", source_type="web_article"), date(2026, 7, 14))
            note = changes[0].content.decode("utf-8")
            manifest = next(change for change in changes if change.target.parent.name == ".preservation").content.decode("utf-8")
            self.assertIn("https://example.com/a.png", note)
            self.assertIn("  - kind/web-clip", note)
            self.assertIn("https://player.bilibili.com/player.html?bvid=BV1", note)
            self.assertIn("![[2026-07-14 网页-附件/图.png]]", note)
            self.assertNotIn("模型摘要。", note)
            self.assertIn('"formal_note": "30 学习/2026-07-14 网页.md"', manifest)

    def test_single_pdf_is_classified_directly_without_note_or_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); inbox = root / "00 收件箱"; inbox.mkdir()
            source = inbox / "行业报告.pdf"; source.write_bytes(b"%PDF-test")
            now = datetime.now(timezone.utc)
            item = InboxItem(
                source,
                Path(source.name),
                hashlib.sha256(source.read_bytes()).hexdigest(),
                source.stat().st_size,
                now,
                "pdf",
            )
            extracted = "## 市场规模\n\n2026 年行业规模为 100 亿元。"
            bundle = InputBundle("report", [item], [ExtractedContent(source, "pdf", text=extracted)])
            plan = OrganizePlan("行业报告", "30 学习", "classify", "", confidence=0.98, source_type="reference")

            changes = compile_classify_changes(root, bundle, plan, date(2026, 7, 17))

            self.assertEqual(len(changes), 1)
            self.assertEqual(changes[0].operation, "move")
            self.assertEqual(changes[0].source, source)
            self.assertEqual(changes[0].target, root / "30 学习/行业报告.pdf")

    def test_classify_finished_markdown_renamed_with_attachment_beside_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); inbox = root / "00 收件箱"; inbox.mkdir()
            source = inbox / "分析.md"; source.write_text("参见 ![[图.png]]。", encoding="utf-8")
            image = inbox / "图.png"; image.write_bytes(b"png")
            now = datetime.now(timezone.utc)
            bundle = InputBundle("b", [
                InboxItem(source, Path("分析.md"), hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_size, now, "text"),
                InboxItem(image, Path("图.png"), hashlib.sha256(b"png").hexdigest(), 3, now, "image"),
            ])
            plan = OrganizePlan("我的分析", "30 学习", "classify", "", confidence=0.9, source_type="personal_analysis")
            changes = compile_classify_changes(root, bundle, plan, date(2026, 7, 14))
            moves = {change.source.name: change.target for change in changes if change.operation == "move"}
            self.assertEqual(moves["分析.md"], root / "30 学习/2026-07-14 我的分析.md")
            self.assertEqual(moves["图.png"], root / "30 学习/图.png")
            # 链接关系：图与重命名后的笔记在同一目录，Obsidian 按文件名全局解析，链接不断。
            self.assertEqual(moves["图.png"].parent, moves["分析.md"].parent)

    def test_preserved_article_has_one_document_title_and_canonical_headings(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); inbox = root / "00 收件箱"; inbox.mkdir()
            source = inbox / "WPS 教程.md"
            source.write_text(
                "## WPS 云盘残留项删除教程\n\n## WPS 云盘残留项删除教程\n\n> 适用场景。\n\n### 1. 问题现象\n\n正文不能丢。\n\n#### 1.1 表现\n\n详细内容。",
                encoding="utf-8",
            )
            item = InboxItem(source, Path("WPS 教程.md"), hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_size, datetime.now(timezone.utc), "text")
            changes = compile_create_changes(
                root,
                InputBundle("bundle", [item]),
                OrganizePlan("WPS 云盘残留项删除教程", "30 学习", "create", "", confidence=0.95, source_type="web_article"),
                date(2026, 7, 16),
            )
            note = changes[0].content.decode("utf-8")
            self.assertNotIn("## WPS 云盘残留项删除教程", note)
            self.assertIn("> 适用场景。", note)
            self.assertIn("## 1. 问题现象", note)
            self.assertIn("### 1.1 表现", note)
            self.assertIn("正文不能丢。", note)

    def test_fragment_uses_organized_body_and_keeps_original_only_in_readable_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); inbox = root / "00 收件箱"; inbox.mkdir()
            source = inbox / "通勤.md"
            source.write_text("地铁 4.5\n单车 2\n全天 18", encoding="utf-8")
            item = InboxItem(source, Path("通勤.md"), hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_size, datetime.now(timezone.utc), "text")
            plan = OrganizePlan("通勤备忘录", "10 生活", "create", "## 每日费用\n\n地铁 4.5 元，单车 2 元，全天 18 元。", confidence=0.95, source_type="fragment")
            changes = compile_create_changes(root, InputBundle("internal-hash", [item]), plan, date(2026, 7, 16))
            note = changes[0].content.decode("utf-8")
            self.assertIn("## 每日费用", note)
            self.assertNotIn("地铁 4.5\n单车 2", note)
            archive_targets = [change.target for change in changes if "95 原始输入归档" in change.target.as_posix()]
            self.assertTrue(any(target.name == "通勤.md" and target.parent.name == "16" for target in archive_targets))
            visible_targets = [target for target in archive_targets if target.parent.name != ".preservation"]
            self.assertFalse(any("internal-hash" in target.as_posix() for target in visible_targets))

    def test_archived_markdown_gets_an_unchecked_no_delete_property(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); inbox = root / "00 收件箱"; inbox.mkdir()
            source = inbox / "原文.md"; source.write_text("原始内容", encoding="utf-8")
            item = InboxItem(source, Path("原文.md"), hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_size, datetime.now(timezone.utc), "text")

            changes = compile_create_changes(root, InputBundle("id", [item]), OrganizePlan("原文", "30 学习", "create", "整理后。", confidence=0.95, source_type="fragment"), date(2026, 7, 16))

            archive_write = next(change for change in changes if change.operation == "write" and change.target.name == "原文.md")
            self.assertEqual(archive_write.content.decode("utf-8"), "---\nno_delete: false\n---\n原始内容")

    def test_archive_name_collision_uses_human_readable_counter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); inbox = root / "00 收件箱"; inbox.mkdir()
            day = root / "90 系统/95 原始输入归档/2026/07/16"
            day.mkdir(parents=True)
            (day / "通勤.md").write_text("旧资料", encoding="utf-8")
            source = inbox / "通勤.md"; source.write_text("碎片", encoding="utf-8")
            item = InboxItem(source, Path("通勤.md"), hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_size, datetime.now(timezone.utc), "text")
            changes = compile_create_changes(root, InputBundle("id", [item]), OrganizePlan("通勤", "10 生活", "create", "整理后。", confidence=0.95, source_type="fragment"), date(2026, 7, 16))
            archive_targets = [change.target for change in changes if "95 原始输入归档" in change.target.as_posix()]
            self.assertTrue(any(target.name == "通勤 (2).md" for target in archive_targets))

    def test_fragment_is_rejected_when_a_number_disappears(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); inbox = root / "00 收件箱"; inbox.mkdir()
            source = inbox / "费用.md"; source.write_text("全天费用 18 元", encoding="utf-8")
            item = InboxItem(source, Path("费用.md"), hashlib.sha256(source.read_bytes()).hexdigest(), source.stat().st_size, datetime.now(timezone.utc), "text")
            plan = OrganizePlan("费用", "10 生活", "create", "这是每日通勤费用。", confidence=0.95, source_type="fragment")
            with self.assertRaisesRegex(ValueError, "丢失关键数据"):
                compile_create_changes(root, InputBundle("id", [item]), plan, date(2026, 7, 16))

    def test_strips_only_redundant_opening_title(self):
        self.assertEqual(strip_redundant_opening_title("标题\n\n正文。", "标题"), "正文。")
        self.assertEqual(strip_redundant_opening_title("# 标题\n\n正文。", "标题"), "正文。")
        self.assertEqual(
            strip_redundant_opening_title("标题\n\n正文。", "2026-07-14 标题-AI", ai_marked=True),
            "正文。",
        )
        self.assertEqual(
            strip_redundant_opening_title(
                "2026-07-14 AI 整理记录\n\n## 08:00 整理结果",
                "2026-07-14 整理记录-AI",
                ai_marked=True,
            ),
            "## 08:00 整理结果",
        )
        original = "不同开头。\n\n## 标题\n\n正文。"
        self.assertEqual(strip_redundant_opening_title(original, "标题"), original)

    def test_render_note_strips_plain_text_title_from_model_body(self):
        plan = OrganizePlan("标题", "30 学习/x", "create", "标题\n\n这是正文。")
        self.assertEqual(render_note(plan), "这是正文。\n")

    def test_render_note_keeps_title_only_input_as_plain_body(self):
        plan = OrganizePlan("未命名", "30 学习/x", "create", "未命名")
        self.assertEqual(render_note(plan), "未命名\n")

    def test_render_note_normalizes_model_heading_levels(self):
        plan = OrganizePlan("标题", "30 学习/x", "create", "# 第一部分\n\n#### 细节\n\n正文。")
        rendered = render_note(plan)
        self.assertIn("## 第一部分", rendered)
        self.assertIn("### 细节", rendered)
        self.assertEqual(validate_note(rendered), [])

    def test_layout_only_heading_issues_are_not_rejected(self):
        self.assertEqual(validate_note("#### 过深标题\n\n正文。"), [])
        self.assertEqual(validate_note("### 孤立标题\n\n正文。"), [])

    def test_heading_validation_ignores_powershell_comments_in_fenced_code(self):
        body = "执行下面的命令。\n\n```powershell\n# OneDrive 卸载脚本\n#### 这也是 PowerShell 注释\n### \n```\n"
        self.assertEqual(validate_note(body), [])


if __name__ == "__main__": unittest.main()
