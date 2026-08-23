import tempfile
import unittest
from pathlib import Path

from knowledge_vault.search import VaultSearch, related_candidates


class SearchTests(unittest.TestCase):
    def test_index_returns_real_path_and_skips_raw_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); note = root / "30 学习" / "人工智能.md"; note.parent.mkdir()
            note.write_text("# 人工智能\n\n模型上下文窗口的具体研究。", encoding="utf-8")
            raw = root / "90 系统" / "95 原始输入归档" / "秘密.md"; raw.parent.mkdir(parents=True)
            raw.write_text("绝不应检索", encoding="utf-8")
            search = VaultSearch(root / ".state" / "index.sqlite3")
            self.assertEqual(search.rebuild(root), 1)
            hits = search.query("模型")
            self.assertEqual(hits[0].relative_path, "30 学习/人工智能.md")
            self.assertEqual(search.query("绝不应检索"), [])
            search.close()

    def test_related_candidates_skip_manual_only_sop_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            normal = root / "20 工作/27 复盘与经验/经验.md"
            sop = root / "20 工作/29 SOP/专题 SOP.md"
            normal.parent.mkdir(parents=True)
            sop.parent.mkdir(parents=True)
            normal.write_text("庭审准备检查经验", encoding="utf-8")
            sop.write_text("庭审准备检查流程", encoding="utf-8")
            candidates = related_candidates(root, "庭审准备检查", excluded_paths=("20 工作/29 SOP",))
            self.assertEqual([item["path"] for item in candidates], ["20 工作/27 复盘与经验/经验.md"])


if __name__ == "__main__": unittest.main()
