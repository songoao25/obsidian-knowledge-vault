from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from knowledge_vault.config import VaultConfig
from knowledge_vault.model_router import ModelResult, ModelRouter
from knowledge_vault.models import ExtractedContent, InboxItem, InputBundle
from knowledge_vault.planner import plan_bundles


def raw_plan(input_id: str, title: str = "整理结果"):
    return {
        "input_id": input_id,
        "title": title,
        "target_dir": "30 学习/31.1",
        "action": "create",
        "body": "保留 18 元和 2.5 公里。",
        "source_type": "fragment",
        "source_urls": [],
        "attachment_names": [],
        "existing_note": "",
        "confidence": 0.9,
        "rationale": "学习资料",
        "continuous_maintenance": False,
    }


class FakeCodex:
    def __init__(self, payload): self.payload = payload
    def complete(self, request): return ModelResult(self.payload, "codex", "gpt-5.6-luna", .1)


class FakeDeepSeek:
    def __init__(self, payload=None): self.payload = payload or {}; self.calls = 0
    def json_completion(self, *args, **kwargs): self.calls += 1; return self.payload


class BatchPlannerTests(unittest.TestCase):
    def bundle(self, root: Path, name: str, bundle_id: str):
        path = root / name; path.write_text("通勤 18 元 2.5 公里", encoding="utf-8")
        item = InboxItem(path, Path(name), bundle_id * 8, path.stat().st_size, datetime.now(timezone.utc), "text")
        return InputBundle(bundle_id, [item], [ExtractedContent(path, "text", path.read_text())])

    def test_invalid_item_only_falls_back_for_that_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); first = self.bundle(root, "a.md", "a1"); second = self.bundle(root, "b.md", "b2")
            bad = raw_plan("b2"); bad["target_dir"] = "不存在"
            codex = FakeCodex({"plans": [raw_plan("a1"), bad], "relationships": []})
            deepseek = FakeDeepSeek({"plans": [raw_plan("b2", "备用结果")], "relationships": []})
            cfg = VaultConfig(root, "Asia/Shanghai", (8, 11, 14, 17, 20, 23))
            planned, relationships, failed = plan_bundles(ModelRouter(cfg, codex, deepseek), [first, second], ["30 学习/31.1"])
        self.assertEqual([entry.provider for entry in planned], ["codex", "deepseek"])
        self.assertEqual(deepseek.calls, 1)
        self.assertEqual(relationships, [])
        self.assertEqual(failed, [])

    def test_weak_relationship_is_discarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); first = self.bundle(root, "a.md", "a1"); second = self.bundle(root, "b.md", "b2")
            payload = {"plans": [raw_plan("a1"), raw_plan("b2")], "relationships": [{
                "file_ids": ["a1", "b2"], "relationship_type": "同主题", "confidence": .95,
                "strong_evidence": ["名称相似"], "weak_evidence": ["同日收集"]
            }]}
            cfg = VaultConfig(root, "Asia/Shanghai", (8, 11, 14, 17, 20, 23))
            _, relationships, _ = plan_bundles(ModelRouter(cfg, FakeCodex(payload), FakeDeepSeek()), [first, second], ["30 学习/31.1"])
        self.assertEqual(relationships, [])


if __name__ == "__main__": unittest.main()
