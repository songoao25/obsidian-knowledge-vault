from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from knowledge_vault.high_impact_records import build_unexecuted_record
from knowledge_vault.transaction import VaultTransaction


class HighImpactRecordTests(unittest.TestCase):
    def test_unexecuted_operation_is_audit_record_not_a_daily_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "vault"
            now = datetime(2026, 7, 14, 8, 0)
            change = build_unexecuted_record(vault, now, "未执行：更新已有笔记《主题》", ["需要本人在 Codex 中明确指令。"])

            result = VaultTransaction(Path(tmp) / "rollback").apply([change], now=now)

            self.assertTrue(result.ok)
            expected = vault / "90 系统/94 维护记录/94.4 高影响操作记录/2026"
            records = list(expected.glob("*.md"))
            self.assertEqual(len(records), 1)
            text = records[0].read_text(encoding="utf-8")
            self.assertIn("操作：未执行", text)
            self.assertIn("Codex 对话中明确指令", text)
            self.assertFalse((vault / "00 收件箱").exists())


if __name__ == "__main__":
    unittest.main()
