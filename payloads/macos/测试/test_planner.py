from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path

from knowledge_vault.models import ExtractedContent, InboxItem, InputBundle
from knowledge_vault.planner import load_catalog, validate_plan


class PlannerTests(unittest.TestCase):
    def bundle(self, root: Path) -> InputBundle:
        path = root / "分析.md"
        path.write_text("我的分析 https://example.com/a", encoding="utf-8")
        item = InboxItem(path, Path("分析.md"), "a" * 64, path.stat().st_size, datetime.now(timezone.utc), "text")
        return InputBundle("bundle", [item], [ExtractedContent(path, "text", path.read_text())])

    def raw(self):
        return {"title": "一个判断", "target_dir": "30 学习/31.1", "action": "create", "body": "保留分析。", "source_type": "personal_analysis", "source_urls": ["https://example.com/a"], "attachment_names": [], "existing_note": "", "confidence": .8, "rationale": "学习资料", "continuous_maintenance": False}

    def test_validates_existing_catalog_and_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = validate_plan(self.raw(), ["30 学习/31.1"], self.bundle(Path(tmp)))
            self.assertEqual(plan.title, "一个判断")

    def test_rejects_new_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = self.raw(); raw["target_dir"] = "30 学习/新目录"
            with self.assertRaisesRegex(ValueError, "既有分类"):
                validate_plan(raw, ["30 学习/31.1"], self.bundle(Path(tmp)))

    def test_allows_a_web_clip_without_a_source_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = self.raw(); raw["source_urls"] = []
            plan = validate_plan(raw, ["30 学习/31.1"], self.bundle(Path(tmp)))
            self.assertEqual(plan.source_urls, ())

    def test_rejects_an_invented_source_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = self.raw(); raw["source_urls"] = ["https://example.com/not-in-input"]
            with self.assertRaisesRegex(ValueError, "不存在的来源 URL"):
                validate_plan(raw, ["30 学习/31.1"], self.bundle(Path(tmp)))

    def test_rejects_any_existing_note_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = self.raw(); raw.update({"action": "update", "existing_note": "30 学习/不存在-AI.md"})
            with self.assertRaisesRegex(ValueError, "action 必须是 create 或 classify"):
                validate_plan(raw, ["30 学习/31.1"], self.bundle(Path(tmp)), {"30 学习/实际-AI.md"})

    def test_allows_empty_generated_body_for_preserved_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = self.raw(); raw["body"] = ""
            plan = validate_plan(raw, ["30 学习/31.1"], self.bundle(Path(tmp)))
            self.assertEqual(plan.source_type, "personal_analysis")

    def test_fragment_requires_an_organized_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = self.raw(); raw.update({"source_type": "fragment", "body": ""})
            with self.assertRaisesRegex(ValueError, "碎片整理结果为空"):
                validate_plan(raw, ["30 学习/31.1"], self.bundle(Path(tmp)))

    def test_rejects_unknown_source_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = self.raw(); raw["source_type"] = "guess"
            with self.assertRaisesRegex(ValueError, "source_type"):
                validate_plan(raw, ["30 学习/31.1"], self.bundle(Path(tmp)))

    def test_allows_heading_like_comments_inside_fenced_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = self.raw()
            raw["body"] = "执行脚本。\n\n```powershell\n# OneDrive 卸载脚本\n```"
            plan = validate_plan(raw, ["30 学习/31.1"], self.bundle(Path(tmp)))
            self.assertIn("# OneDrive", plan.body)

    def test_allows_classify_with_empty_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = self.raw(); raw.update({"action": "classify", "body": "", "source_type": "reference"})
            plan = validate_plan(raw, ["30 学习/31.1"], self.bundle(Path(tmp)))
            self.assertEqual(plan.action, "classify")

    def test_rejects_create_for_non_markdown_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "报告.pdf"; pdf.write_bytes(b"%PDF")
            item = InboxItem(pdf, Path("报告.pdf"), "b" * 64, pdf.stat().st_size, datetime.now(timezone.utc), "pdf")
            bundle = InputBundle("p", [item], [ExtractedContent(pdf, "pdf", "文本")])
            raw = {"title": "报告", "target_dir": "30 学习/31.1", "action": "create", "body": "", "source_type": "reference", "source_urls": [], "attachment_names": [], "existing_note": "", "confidence": .8, "rationale": "x", "continuous_maintenance": False}
            with self.assertRaisesRegex(ValueError, "非 Markdown 资料只能直接分类"):
                validate_plan(raw, ["30 学习/31.1"], bundle)

    def test_normalizes_model_heading_levels_before_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            raw = self.raw()
            raw["body"] = "# 邮政编码\n\n#### 中国格式\n\n内容。\n\n~~~text\n#### 代码示例\n~~~"
            plan = validate_plan(raw, ["30 学习/31.1"], self.bundle(Path(tmp)))
            self.assertIn("## 邮政编码", plan.body)
            self.assertIn("### 中国格式", plan.body)
            self.assertIn("#### 代码示例", plan.body)

    def test_catalog_excludes_manual_only_paths_and_children(self):
        with tempfile.TemporaryDirectory() as tmp:
            taxonomy = Path(tmp) / "taxonomy.json"
            taxonomy.write_text(__import__("json").dumps({"paths": [
                "20 工作/28 工作成果与样本",
                "20 工作/29 SOP",
                "20 工作/29 SOP/专题",
            ]}, ensure_ascii=False), encoding="utf-8")
            catalog = load_catalog(taxonomy, ("20 工作/29 SOP",))
            self.assertEqual(catalog, ["20 工作/28 工作成果与样本"])


if __name__ == "__main__": unittest.main()
