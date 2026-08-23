from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from knowledge_vault.metadata import TagPolicy, compile_metadata_changes, normalize_note


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 15, 9, 26, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.policy = TagPolicy(
            allowed_tags={"kind/note", "kind/web-clip", "workflow/inbox", "topic/example-a", "topic/example-b"},
            path_defaults={"00 收件箱": ("kind/note", "workflow/inbox")},
            overrides={"00 收件箱/示例.md": ("kind/web-clip", "workflow/inbox", "topic/example-a", "topic/example-b")},
            migrations={"clippings": ("kind/web-clip", "workflow/inbox")},
        )

    def test_normalize_adds_iso_dates_and_preserves_body_verbatim(self):
        original = "第一段。\n\n## 我的补充\n\n这段必须原样保留。\n"
        changed, result = normalize_note(
            original,
            Path("00 收件箱/未命名.md"),
            self.policy,
            created_at=self.now,
        )
        self.assertTrue(result.changed)
        self.assertIn("created: 2026-07-15T09:26:00", changed)
        self.assertIn("modified: 2026-07-15T09:26:00", changed)
        self.assertIn("  - kind/note", changed)
        self.assertIn("  - workflow/inbox", changed)
        self.assertTrue(changed.endswith(original))

    def test_reused_file_name_never_inherits_content_specific_override(self):
        self.assertEqual(
            self.policy.defaults_for(Path("00 收件箱/示例.md")),
            ("kind/note", "workflow/inbox"),
        )

    def test_normalize_migrates_clippings_and_keeps_other_frontmatter(self):
        original = "---\ntitle: 示例\ntags:\n  - clippings\n---\n\n正文。\n"
        changed, result = normalize_note(
            original,
            Path("00 收件箱/示例.md"),
            self.policy,
            created_at=self.now,
        )
        self.assertTrue(result.changed)
        self.assertIn("title: 示例", changed)
        self.assertNotIn("clippings", changed)
        self.assertIn("  - kind/web-clip", changed)
        self.assertNotIn("  - topic/example-b", changed)
        self.assertTrue(changed.endswith("\n正文。\n"))

    def test_normalize_does_not_overwrite_existing_timestamps_or_valid_tags(self):
        original = "---\ncreated: 2026-07-14T08:00:00\nmodified: 2026-07-14T08:01:00\ntags:\n  - kind/web-clip\n  - workflow/inbox\n---\n\n正文。\n"
        changed, result = normalize_note(
            original,
            Path("00 收件箱/示例.md"),
            self.policy,
            created_at=self.now,
        )
        self.assertFalse(result.changed)
        self.assertEqual(changed, original)
        self.assertEqual(result.unknown_tags, ())

    def test_unknown_tags_are_reported_but_not_deleted(self):
        original = "---\ntags:\n  - kind/note\n  - unknown/tag\n---\n\n正文。\n"
        changed, result = normalize_note(
            original,
            Path("00 收件箱/未命名.md"),
            self.policy,
            created_at=self.now,
        )
        self.assertIn("unknown/tag", changed)
        self.assertEqual(result.unknown_tags, ("unknown/tag",))

    def test_repair_replaces_only_the_known_bad_modified_timestamp(self):
        original = "---\ncreated: 2026-07-01T08:00:00\nmodified: 2026-07-15T10:11:45\ntags:\n  - kind/note\n---\n正文\n"
        changed, result = normalize_note(
            original,
            Path("00 收件箱/未命名.md"),
            self.policy,
            created_at=datetime(2026, 7, 1, 8, 0, 0),
            modified_at=datetime(2026, 7, 2, 9, 30, 0),
            replace_modified_if="2026-07-15T10:11:45",
        )
        self.assertTrue(result.changed)
        self.assertIn("created: 2026-07-01T08:00:00", changed)
        self.assertIn("modified: 2026-07-02T09:30:00", changed)

    def test_date_only_values_are_normalized_to_the_standard_timestamp(self):
        original = "---\ncreated: 2026-07-15\nmodified: 2026-07-15\ntags:\n  - kind/web-clip\n---\n正文\n"
        changed, result = normalize_note(
            original,
            Path("00 收件箱/示例.md"),
            self.policy,
            created_at=datetime(2026, 7, 15, 8, 0, 0),
            modified_at=datetime(2026, 7, 15, 8, 1, 0),
        )
        self.assertTrue(result.changed)
        self.assertIn("created: 2026-07-15T08:00:00", changed)
        self.assertIn("modified: 2026-07-15T08:00:00", changed)

    def test_missing_modified_uses_created_time_not_a_later_filesystem_time(self):
        original = "---\ncreated: 2026-07-15T08:00:00\ntags:\n  - kind/web-clip\n---\n正文\n"
        changed, result = normalize_note(
            original,
            Path("00 收件箱/示例.md"),
            self.policy,
            created_at=datetime(2026, 7, 15, 7, 0, 0),
            modified_at=datetime(2026, 7, 15, 9, 0, 0),
        )
        self.assertTrue(result.changed)
        self.assertIn("modified: 2026-07-15T08:00:00", changed)

    def test_compile_changes_scans_only_formal_roots_and_uses_transaction_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            note = vault / "00 收件箱/未命名.md"
            hidden = vault / ".claudian/internal.md"
            note.parent.mkdir(parents=True); hidden.parent.mkdir(parents=True)
            note.write_text("正文。\n", encoding="utf-8")
            hidden.write_text("不要修改。\n", encoding="utf-8")
            changes, report = compile_metadata_changes(vault, self.policy, now=self.now)
            self.assertEqual([change.target for change in changes], [note])
            self.assertTrue(changes[0].update_modified)
            self.assertEqual(report.changed_paths, (Path("00 收件箱/未命名.md"),))
            self.assertEqual(report.unknown_tags, {})

    def test_compile_changes_scans_real_work_and_learning_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            work = vault / "20 工作/规则.md"
            study = vault / "30 学习/课程.md"
            work.parent.mkdir(parents=True)
            study.parent.mkdir(parents=True)
            work.write_text("工作正文。\n", encoding="utf-8")
            study.write_text("学习正文。\n", encoding="utf-8")

            changes, report = compile_metadata_changes(vault, self.policy, now=self.now)

            self.assertEqual([change.target for change in changes], [work, study])
            self.assertEqual(
                report.changed_paths,
                (Path("20 工作/规则.md"), Path("30 学习/课程.md")),
            )


if __name__ == "__main__":
    unittest.main()
