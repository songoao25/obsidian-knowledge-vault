#!/usr/bin/env python3
"""Create the two public, empty-vault template profiles from audited payloads."""
from __future__ import annotations

import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

GENERAL_WORK_PATHS = [
    "20 工作",
    "20 工作/21 工作规则与质量",
    "20 工作/21 工作规则与质量/21.1 工作原则与质量标准",
    "20 工作/21 工作规则与质量/21.2 职业伦理与保密",
    "20 工作/21 工作规则与质量/21.3 文件命名与版本管理",
    "20 工作/21 工作规则与质量/21.4 检查清单与风险控制",
    "20 工作/22 项目与任务",
    "20 工作/22 项目与任务/22.1 需求与目标",
    "20 工作/22 项目与任务/22.2 计划与执行",
    "20 工作/22 项目与任务/22.3 沟通与决策",
    "20 工作/22 项目与任务/22.4 交付与归档",
    "20 工作/23 专业领域",
    "20 工作/23 专业领域/23.1 行业知识",
    "20 工作/23 专业领域/23.2 方法与实践",
    "20 工作/23 专业领域/23.3 标准与规范",
    "20 工作/24 职场与职业发展",
    "20 工作/24 职场与职业发展/24.1 工作技能",
    "20 工作/24 职场与职业发展/24.2 职业选择与发展",
    "20 工作/24 职场与职业发展/24.3 培训与资格",
    "20 工作/25 工具与模板",
    "20 工作/25 工具与模板/25.1 清单与表格",
    "20 工作/25 工具与模板/25.2 文档与演示",
    "20 工作/25 工具与模板/25.3 研究与分析模板",
    "20 工作/25 工具与模板/25.4 数字工具与自动化",
    "20 工作/26 沟通协作与运营",
    "20 工作/26 沟通协作与运营/26.1 团队协作",
    "20 工作/26 沟通协作与运营/26.2 外部沟通",
    "20 工作/26 沟通协作与运营/26.3 项目协调",
    "20 工作/27 复盘与经验",
    "20 工作/27 复盘与经验/27.1 项目复盘",
    "20 工作/27 复盘与经验/27.2 工作方法更新",
    "20 工作/27 复盘与经验/27.3 经验沉淀",
    "20 工作/28 工作成果与样本",
    "20 工作/28 工作成果与样本/28.1 研究与分析",
    "20 工作/28 工作成果与样本/28.2 项目交付",
    "20 工作/28 工作成果与样本/28.3 公开作品与分享",
    "20 工作/29 SOP",
]


def copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))


def write_general_taxonomy(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data["paths"] = [item for item in data["paths"] if not item.startswith("20 工作/") and item != "20 工作"]
    insert_at = data["paths"].index("30 学习")
    data["paths"][insert_at:insert_at] = GENERAL_WORK_PATHS
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def make_general_tree(root: Path) -> None:
    work = root / "20 工作"
    if work.exists():
        shutil.rmtree(work)
    for path in GENERAL_WORK_PATHS:
        (root / path).mkdir(parents=True, exist_ok=True)


def neutralize_general_rules(root: Path) -> None:
    rules = root / "90 系统" / "92 维护规范"
    replacements = {
        "工作中，“事情应该怎么做”进入办案与项目流程，“某个法律领域知道了什么”进入法律业务领域，“这次学到了什么”进入复盘与经验，成熟后再回写 SOP。":
            "工作中，项目过程资料进入项目与任务，专业资料进入专业领域，经验进入复盘与经验，成熟后再回写 SOP。",
        "`NN[.M[.K]] 名称`（如 `22 办案与项目流程`、`22.2 事实与法律分析`、`22.2.2 争议焦点与法律问题`）":
            "`NN[.M[.K]] 名称`（如 `22 项目与任务`、`22.2 计划与执行`、`22.2.1 里程碑与行动`）",
    }
    for path in rules.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        for old, new in replacements.items():
            text = text.replace(old, new)
        path.write_text(text, encoding="utf-8")


def build_macos() -> None:
    payload = ROOT / "payloads" / "macos"
    profiles = payload / "模板"
    shutil.rmtree(profiles, ignore_errors=True)
    legal = profiles / "legal"
    copy_tree(payload / "资源与模板", legal / "资源与模板")
    (legal / "配置").mkdir(parents=True, exist_ok=True)
    for name in ("taxonomy.json", "tag_policy.json"):
        shutil.copy2(payload / "配置" / name, legal / "配置" / name)

    general = profiles / "general"
    copy_tree(legal, general)
    template = general / "资源与模板" / "初始知识库模板"
    make_general_tree(template)
    write_general_taxonomy(general / "配置" / "taxonomy.json")
    vault_taxonomy = template / "90 系统" / "92 维护规范" / "taxonomy.json"
    if vault_taxonomy.is_file():
        write_general_taxonomy(vault_taxonomy)
    neutralize_general_rules(template)


def build_windows() -> None:
    payload = ROOT / "payloads" / "windows"
    profiles = payload / "模板"
    shutil.rmtree(profiles, ignore_errors=True)
    legal = profiles / "legal"
    copy_tree(payload / "知识库模板", legal / "知识库模板")
    (legal / "配置").mkdir(parents=True, exist_ok=True)
    for name in ("taxonomy.json", "tag_policy.json"):
        shutil.copy2(payload / "配置" / name, legal / "配置" / name)

    general = profiles / "general"
    copy_tree(legal, general)
    template = general / "知识库模板"
    make_general_tree(template)
    write_general_taxonomy(general / "配置" / "taxonomy.json")
    vault_taxonomy = template / "90 系统" / "92 维护规范" / "taxonomy.json"
    if vault_taxonomy.is_file():
        write_general_taxonomy(vault_taxonomy)
    neutralize_general_rules(template)


def main() -> None:
    build_macos()
    build_windows()
    print("Created general and legal profiles for macOS and Windows payloads.")


if __name__ == "__main__":
    main()
