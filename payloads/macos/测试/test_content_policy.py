from datetime import datetime, timezone
import hashlib
from pathlib import Path
import tempfile
import unittest

from knowledge_vault.content_policy import canonicalize_heading_levels, resolve_edit_mode
from knowledge_vault.models import InboxItem, InputBundle, OrganizePlan


class ContentPolicyTests(unittest.TestCase):
    def test_heading_normalization_preserves_fenced_code_and_supports_tildes(self):
        body = "# 第一部分\n\n#### 细节\n\n~~~powershell\n# 不能改\n#### 也不能改\n~~~\n"
        self.assertEqual(
            canonicalize_heading_levels(body),
            "## 第一部分\n\n### 细节\n\n~~~powershell\n# 不能改\n#### 也不能改\n~~~\n",
        )

    def bundle(self, root: Path, text: str) -> InputBundle:
        source = root / "输入.md"
        source.write_text(text, encoding="utf-8")
        item = InboxItem(
            source,
            Path("输入.md"),
            hashlib.sha256(source.read_bytes()).hexdigest(),
            source.stat().st_size,
            datetime.now(timezone.utc),
            "text",
        )
        return InputBundle("bundle", [item])

    def plan(self, source_type: str, confidence: float = 0.95) -> OrganizePlan:
        return OrganizePlan("标题", "30 学习", "create", "模型整理结果。", confidence=confidence, source_type=source_type)

    def test_web_clip_frontmatter_forces_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self.bundle(Path(tmp), "---\ntags:\n  - kind/web-clip\n---\n\n完整网页正文。")
            self.assertEqual(resolve_edit_mode(bundle, self.plan("fragment")), "preserve")

    def test_personal_analysis_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self.bundle(Path(tmp), "这是我的长段分析和独特判断。")
            self.assertEqual(resolve_edit_mode(bundle, self.plan("personal_analysis")), "preserve")

    def test_high_confidence_fragment_is_organized(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self.bundle(Path(tmp), "地铁 4.5\n单车 2\n全天 18")
            self.assertEqual(resolve_edit_mode(bundle, self.plan("fragment", 0.95)), "organize")

    def test_uncertain_fragment_falls_back_to_preservation(self):
        with tempfile.TemporaryDirectory() as tmp:
            bundle = self.bundle(Path(tmp), "可能是碎片，也可能是完整文章。")
            self.assertEqual(resolve_edit_mode(bundle, self.plan("fragment", 0.6)), "preserve")

    def test_non_markdown_reference_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "行业报告.pdf"
            source.write_bytes(b"%PDF-test")
            item = InboxItem(
                source,
                Path(source.name),
                hashlib.sha256(source.read_bytes()).hexdigest(),
                source.stat().st_size,
                datetime.now(timezone.utc),
                "pdf",
            )
            bundle = InputBundle("bundle", [item])
            self.assertEqual(resolve_edit_mode(bundle, self.plan("reference")), "preserve")


if __name__ == "__main__":
    unittest.main()
