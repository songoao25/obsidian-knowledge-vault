from pathlib import Path
import tempfile
import unittest

from knowledge_vault.scaffold import load_taxonomy, scaffold_directories


class ScaffoldTests(unittest.TestCase):
    def test_scaffold_is_idempotent_and_preserves_user_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            welcome = root / "欢迎.md"
            welcome.write_text("我的内容", encoding="utf-8")
            paths = ("00 收件箱", "10 生活/11 身心健康", "90 系统/91 首页与视图")
            scaffold_directories(root, paths)
            self.assertEqual(scaffold_directories(root, paths), [])
            self.assertEqual(welcome.read_text(encoding="utf-8"), "我的内容")
            self.assertTrue(all((root / path).is_dir() for path in paths))

    def test_real_taxonomy_has_six_roots_and_no_duplicates(self):
        taxonomy = Path(__file__).parents[1] / "配置" / "taxonomy.json"
        paths = load_taxonomy(taxonomy)
        roots = {Path(path).parts[0] for path in paths}
        self.assertEqual(roots, {"00 收件箱", "10 生活", "20 工作", "30 学习", "40 记录", "90 系统"})
        self.assertEqual(len(paths), len(set(paths)))


if __name__ == "__main__":
    unittest.main()
