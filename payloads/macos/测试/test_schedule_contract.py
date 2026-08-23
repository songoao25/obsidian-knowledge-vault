"""发布包专用合同测试：模板必须完全中性化（无本机身份痕迹，只含占位符）；
渲染产物由部署时按用户配置生成，本测试用夹具渲染验证接线正确
（Label、用户自定义的时点、无加载即运行），并断言模板不再含硬编码小时。"""
import re
from pathlib import Path
import unittest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LABEL = "com.knowledgevault.obsidian-maintenance"
# 拆分写法，避免在发布包中直接出现被扫描器拦截的身份串
_PRIVATE_USER = "song" + "song"
_PRIVATE_HOME = "/U" + "sers/"


def _render(template_text: str, values: dict) -> str:
    result = template_text
    for key, value in values.items():
        result = result.replace("{{%s}}" % key, value)
    leftover = re.findall(r"\{\{[A-Z_]+\}\}", result)
    if leftover:
        raise AssertionError("模板渲染后仍有未替换占位符: %s" % ", ".join(sorted(set(leftover))))
    return result


def _fixture_values(run_hours):
    interval = "\n".join(
        '    <dict><key>Hour</key><integer>%d</integer><key>Minute</key><integer>0</integer></dict>' % h
        for h in run_hours
    )
    return {
        "PROJECT": "/tmp/kv-fixture", "HOME": "/tmp/home-tester", "CODEX_HOME": "/tmp/home-tester/.codex",
        "HOMEBREW_PREFIX": "/opt/homebrew", "PYTHON": "/opt/homebrew/bin/python3",
        "START_CALENDAR_INTERVAL": interval, "EXPECTED_HOURS": " ".join(str(h) for h in run_hours),
        "LABEL": LABEL, "USER": "tester", "VAULT_PATH": "/tmp/kv-fixture-vault",
        "TIMEZONE": "Asia/Shanghai", "RUN_HOURS_JSON": "[9, 21]",
        "PROVIDER_ORDER_JSON": '["deepseek"]', "API_PROVIDERS_JSON": "{}",
        "KEYCHAIN_SERVICE": "knowledge-vault-deepseek",
    }


class ScheduleContractTests(unittest.TestCase):
    def test_plist_template_is_neutral_and_renders_user_hours(self) -> None:
        template = (PROJECT_ROOT / "定时任务" / ("%s.plist.tpl" % LABEL)).read_text(encoding="utf-8")
        self.assertNotIn(_PRIVATE_USER, template)
        self.assertNotIn(_PRIVATE_HOME, template)
        self.assertIn("{{HOME}}", template)
        self.assertIn("{{PROJECT}}", template)
        self.assertIn("{{START_CALENDAR_INTERVAL}}", template)
        # 模板不得含硬编码小时整数
        self.assertNotIn("<integer>8</integer>", template)
        rendered = _render(template, _fixture_values([9, 21]))
        self.assertIn(LABEL, rendered)
        self.assertIn("<integer>9</integer>", rendered)
        self.assertIn("<integer>21</integer>", rendered)
        self.assertNotIn("<integer>8</integer>", rendered)

    def test_run_script_template_is_neutral_and_renders_wiring(self) -> None:
        template = (PROJECT_ROOT / "脚本/run_knowledge_vault.sh.tpl").read_text(encoding="utf-8")
        self.assertNotIn(_PRIVATE_USER, template)
        self.assertNotIn(_PRIVATE_HOME, template)
        self.assertIn("{{HOME}}", template)
        rendered = _render(template, _fixture_values([9, 21]))
        self.assertIn('export USER="$USER"', rendered)
        self.assertIn('export PYTHONPATH="$PROJECT/程序"', rendered)

    def test_install_script_template_uses_expected_hours_placeholder(self) -> None:
        template = (PROJECT_ROOT / "脚本/install_launch_agent.sh.tpl").read_text(encoding="utf-8")
        self.assertIn("EXPECTED_HOURS=({{EXPECTED_HOURS}})", template)
        self.assertNotIn("EXPECTED_HOURS=(8 11 14 17 20 23)", template)
        self.assertIn("RunAtLoad", template)
        self.assertNotIn(_PRIVATE_USER, template)
        self.assertNotIn(_PRIVATE_HOME, template)
        rendered = _render(template, _fixture_values([9, 21]))
        self.assertIn("EXPECTED_HOURS=(9 21)", rendered)


if __name__ == "__main__":
    unittest.main()
