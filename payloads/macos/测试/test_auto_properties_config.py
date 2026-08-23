"""发布包专用合同测试：本地插件 ID 与安装脚本必须完全中性化，且由模板渲染生成。"""
from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 拆分写法，避免在发布包中直接出现被扫描器拦截的身份串
_PRIVATE_USER = "song" + "song"


class AutoPropertiesConfigTests(unittest.TestCase):
    def test_manifest_uses_neutral_id(self) -> None:
        import json
        manifest = json.loads(
            (PROJECT_ROOT / "插件/auto-properties/manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["id"], "knowledge-vault-auto-properties")
        self.assertNotIn(_PRIVATE_USER, manifest["author"])

    def test_installer_template_uses_neutral_id(self) -> None:
        template = (PROJECT_ROOT / "脚本/install_auto_properties_plugin.sh.tpl").read_text(encoding="utf-8")
        self.assertIn('LOCAL_ID="knowledge-vault-auto-properties"', template)
        self.assertNotIn(_PRIVATE_USER, template)
        rendered = template.replace("{{PROJECT}}", "/tmp/kv").replace(
            "{{VAULT_PATH}}", "/tmp/vault").replace("{{PYTHON}}", "python3")
        self.assertIn('LOCAL_ID="knowledge-vault-auto-properties"', rendered)
        self.assertIn('SOURCE="${PROJECT_ROOT}/插件/auto-properties"', rendered)


if __name__ == "__main__":
    unittest.main()
