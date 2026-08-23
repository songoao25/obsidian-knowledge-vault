from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from knowledge_vault.audit import audit
from knowledge_vault.config import VaultConfig


class TaxonomyPathTests(unittest.TestCase):
    def test_audit_accepts_string_taxonomy_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "新知识库"
            vault.mkdir()
            config = VaultConfig(vault, "Asia/Shanghai", (8, 11, 14, 17, 20, 23))

            with (
                patch("knowledge_vault.audit.load_taxonomy", return_value=("90 系统/94 维护记录",)),
                patch("knowledge_vault.audit.provider_issues", return_value=[]),
                patch("knowledge_vault.audit.community_plugin_issues", return_value=[]),
                patch("knowledge_vault.audit.tag_policy_issues", return_value=[]),
                patch("knowledge_vault.audit.note_metadata_issues", return_value=[]),
                patch("knowledge_vault.audit.note_content_issues", return_value=[]),
                patch("knowledge_vault.audit.unexpected_vault_roots", return_value=[]),
            ):
                report = audit(config)

            self.assertTrue(any("目录结构缺少" in item for item in report.failed))


if __name__ == "__main__":
    unittest.main()
