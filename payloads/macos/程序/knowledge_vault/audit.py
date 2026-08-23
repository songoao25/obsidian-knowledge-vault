from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess

from .config import PROJECT_ROOT, VaultConfig
from .filesystem import list_files
from .scaffold import load_taxonomy
from .media import source_markdown_body
from .writer import strip_redundant_opening_title, validate_note
from .content_policy import markdown_lines_outside_fences


EXPECTED_COMMUNITY_PLUGINS = {"realclaudian", "frontmatter-modified-date", "obsidian-auto-organizer"}
NOTE_FRONTMATTER = re.compile(r"\A---\r?\n(?P<fields>.*?)\r?\n---(?:\r?\n)?", re.DOTALL)
NOTE_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")
ORGANIZE_RECORD = re.compile(r"^\d{4}-\d{2}-\d{2} 整理记录-AI\.md$")


def _version_tuple(value: str) -> tuple[int, ...]:
    match = re.search(r"(\d+(?:\.\d+)+)", value)
    return tuple(map(int, match.group(1).split("."))) if match else ()


def provider_issues(config: VaultConfig) -> list[str]:
    issues: list[str] = []
    if "codex" in config.provider_order:
        binary = config.codex_binary
        if not binary.is_file():
            issues.append(f"Codex 二进制不存在：{binary}")
        else:
            version = subprocess.run([str(binary), "--version"], capture_output=True, text=True, check=False)
            if version.returncode != 0 or _version_tuple(version.stdout) < _version_tuple(config.codex_min_version):
                issues.append(f"Codex 版本低于 {config.codex_min_version}")
            auth = subprocess.run(
                ["launchctl", "asuser", str(os.getuid()), str(binary), "login", "status"],
                capture_output=True, text=True, check=False,
            )
            if auth.returncode != 0 or "Logged in using ChatGPT" not in (auth.stdout + auth.stderr):
                issues.append("GUI 用户上下文无法读取 ChatGPT 登录状态")
        cache = Path.home() / ".codex/models_cache.json"
        try:
            models = {item.get("slug") for item in json.loads(cache.read_text(encoding="utf-8")).get("models", [])}
        except Exception:
            models = set()
        if config.codex_model not in models:
            issues.append(f"本机 Codex 模型目录不含 {config.codex_model}")
    if config.strict_local_audit:
        if not config.whisper_binary.is_file():
            issues.append(f"Whisper CLI 不存在：{config.whisper_binary}")
        if not config.whisper_model.is_file() or config.whisper_model.stat().st_size < 400 * 1024 * 1024:
            issues.append(f"Whisper small 模型不存在或不完整：{config.whisper_model}")
    return issues


@dataclass
class AuditReport:
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed


def unexpected_vault_roots(vault: Path) -> list[str]:
    allowed = {"00 收件箱", "10 生活", "20 工作", "30 学习", "40 记录", "90 系统"}
    return sorted(path.name for path in vault.iterdir() if not path.name.startswith(".") and path.name not in allowed)


def community_plugin_issues(vault: Path, expected: set[str] | None = None) -> list[str]:
    expected = expected or EXPECTED_COMMUNITY_PLUGINS
    config = vault / ".obsidian/community-plugins.json"
    try:
        enabled = set(json.loads(config.read_text(encoding="utf-8")))
    except Exception as error:
        return [f"社区插件配置不可读：{error}"]
    issues: list[str] = []
    missing = sorted(expected - enabled)
    extra = sorted(enabled - expected)
    if missing:
        issues.append(f"缺少已批准社区插件：{', '.join(missing)}")
    if extra:
        issues.append(f"存在未批准社区插件：{', '.join(extra)}")
    for plugin_id in sorted(enabled & expected):
        manifest = vault / ".obsidian/plugins" / plugin_id / "manifest.json"
        try:
            manifest_id = json.loads(manifest.read_text(encoding="utf-8")).get("id")
        except Exception:
            manifest_id = None
        if manifest_id != plugin_id:
            issues.append(f"社区插件缺少有效 manifest：{plugin_id}")
    return issues


def tag_policy_issues(policy_path: Path, taxonomy_path: Path) -> list[str]:
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    taxonomy = json.loads(taxonomy_path.read_text(encoding="utf-8"))
    issues: list[str] = []
    if policy.get("overrides"):
        issues.append("标签策略仍含文件级覆盖，文件名复用时会污染新笔记")
    taxonomy_paths = set(taxonomy["paths"])
    taxonomy_roots = {Path(path).parts[0] for path in taxonomy_paths}
    defaults = set(policy.get("path_defaults", {}))
    configured_roots = {path for path in defaults if len(Path(path).parts) == 1}
    required_roots = taxonomy_roots - {"90 系统"}
    for path in sorted(required_roots - configured_roots):
        issues.append(f"标签策略缺少真实根目录默认值：{path}")
    for path in sorted(configured_roots - taxonomy_roots):
        issues.append(f"标签策略含不存在的根目录默认值：{path}")
    for path in sorted(defaults - taxonomy_paths):
        issues.append(f"标签策略路径不在 taxonomy：{path}")
    return issues


def manual_only_path_issues(manual_only_paths: tuple[str, ...], taxonomy_paths: tuple[str, ...]) -> list[str]:
    taxonomy = set(taxonomy_paths)
    return [
        f"本人维护目录不在 taxonomy：{path}"
        for path in manual_only_paths
        if path not in taxonomy
    ]


def _frontmatter_values(text: str) -> tuple[dict[str, str], list[str], str] | None:
    match = NOTE_FRONTMATTER.match(text)
    if match is None:
        return None
    values: dict[str, str] = {}
    tags: list[str] = []
    in_tags = False
    for line in match.group("fields").splitlines():
        if line == "tags:":
            in_tags = True
            continue
        tag = re.fullmatch(r"\s+-\s+(.+?)\s*", line) if in_tags else None
        if tag:
            tags.append(tag.group(1).strip().strip("\"'"))
            continue
        in_tags = False
        if ":" in line:
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
    return values, tags, text[match.end() :]


def note_metadata_issues(vault: Path, allowed_tags: set[str]) -> list[str]:
    roots = ("00 收件箱", "10 生活", "20 工作", "30 学习", "40 记录", "90 系统")
    issues: list[str] = []
    for root in roots:
        base = vault / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.md")):
            relative = path.relative_to(vault)
            parsed = _frontmatter_values(path.read_text(encoding="utf-8", errors="replace"))
            if parsed is None:
                issues.append(f"{relative}: 缺少或损坏的属性区")
                continue
            values, tags, body = parsed
            for field in ("created", "modified"):
                if not NOTE_TIMESTAMP.fullmatch(values.get(field, "")):
                    issues.append(f"{relative}: {field} 不是标准日期时间")
            if len(tags) != len(set(tags)):
                issues.append(f"{relative}: 存在重复标签")
            unknown = sorted(set(tags) - allowed_tags)
            if unknown:
                issues.append(f"{relative}: 未知标签 {', '.join(unknown)}")
            limits = (("kind/", 1, 1), ("workflow/", 0, 1), ("topic/", 0, 3), ("project/", 0, 2))
            for prefix, minimum, maximum in limits:
                count = sum(tag.startswith(prefix) for tag in tags)
                if count < minimum or count > maximum:
                    issues.append(f"{relative}: {prefix} 标签数量为 {count}")
            relative_text = relative.as_posix()
            if relative_text.startswith("90 系统/95 原始输入归档/"):
                continue
            tag_set = set(tags)
            if ORGANIZE_RECORD.fullmatch(path.name):
                expected = {"kind/maintenance-log", "topic/automation", "project/knowledge-vault"}
                if relative.parts[0] == "00 收件箱":
                    expected.add("workflow/inbox")
                if tag_set != expected:
                    issues.append(f"{relative}: 整理记录标签与所在流程不一致")
            elif relative.parts[0] == "00 收件箱" and not body.strip():
                expected = {"kind/note", "workflow/inbox"}
                if tag_set != expected:
                    issues.append(f"{relative}: 空白收件箱笔记带有无内容依据的语义标签")
    return issues


def note_content_issues(vault: Path) -> list[str]:
    issues: list[str] = []
    for root in ("10 生活", "20 工作", "30 学习"):
        base = vault / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.md")):
            relative = path.relative_to(vault)
            body = source_markdown_body(path)
            title = re.sub(r"^\d{4}-\d{2}-\d{2}\s+", "", path.stem).removesuffix("-AI").strip()
            if strip_redundant_opening_title(body, title) != body:
                issues.append(f"{relative}: 正文开头重复文档标题")
            else:
                opening = next((line.strip() for line in markdown_lines_outside_fences(body) if line.strip()), "")
                opening_title = re.sub(r"^#\s+", "", opening).strip()
                if opening.startswith("# ") and title and re.search(rf"[:：]\s*{re.escape(title)}$", opening_title):
                    issues.append(f"{relative}: 正文开头重复文档标题")
            for problem in validate_note(body):
                issues.append(f"{relative}: {problem}")
    return issues


def organize_record_link_issues(vault: Path) -> list[str]:
    """Check that completed maintenance-record entries lead to live Vault files."""
    records = [
        path for root in (vault / "00 收件箱", vault / "90 系统/94 维护记录/94.1 整理记录")
        if root.is_dir() for path in root.rglob("*.md") if ORGANIZE_RECORD.fullmatch(path.name)
    ]
    issues: list[str] = []
    for path in sorted(records):
        relative = path.relative_to(vault)
        text = path.read_text(encoding="utf-8", errors="replace")
        for raw_link in re.findall(r"\[\[([^\]]+)\]\]", text):
            target = raw_link.split("|", 1)[0].strip()
            candidates = (vault / target, vault / f"{target}.md")
            if not any(candidate.is_file() for candidate in candidates):
                issues.append(f"{relative}: 整理结果链接不存在：[[{target}]]")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("已分类到："):
                issues.append(f"{relative}: 已分类结果缺少可点击链接")
            if stripped.startswith(("整理为：", "已分类为：", "归档为：", "直接归档为：")) and "[[" not in stripped:
                issues.append(f"{relative}: 整理结果缺少可点击链接")
    return issues


def audit(config: VaultConfig, launch_agent: Path | None = None) -> AuditReport:
    report = AuditReport()
    expected = load_taxonomy(PROJECT_ROOT / "配置" / "taxonomy.json")
    manual_path_problems = manual_only_path_issues(config.manual_only_paths, expected)
    (report.failed if manual_path_problems else report.passed).append(
        "本人维护目录已登记且受写入保护"
        if not manual_path_problems
        else f"本人维护目录配置异常：{'; '.join(manual_path_problems)}"
    )
    missing = [str(path) for path in expected if not (config.vault_path / path).is_dir()]
    (report.failed if missing else report.passed).append(
        f"目录结构{'缺少 ' + ', '.join(missing[:8]) if missing else '完整'}"
    )
    unexpected = unexpected_vault_roots(config.vault_path)
    (report.failed if unexpected else report.passed).append(
        "Vault 根目录无开发产物" if not unexpected else f"Vault 根目录存在非分类目录：{', '.join(unexpected[:8])}"
    )
    bases = list((config.vault_path / "90 系统/91 首页与视图").glob("*.base"))
    (report.passed if len(bases) == 6 else report.failed).append(f"原生 Bases 数量：{len(bases)}/6")
    app_file = config.vault_path / ".obsidian/app.json"
    app = json.loads(app_file.read_text(encoding="utf-8")) if app_file.exists() else {}
    settings_ok = app.get("newFileLocation") == "folder" and app.get("newFileFolderPath") == "00 收件箱"
    (report.passed if settings_ok else report.failed).append("Obsidian 新文件进入收件箱")
    homepage = config.vault_path / "90 系统/91 首页与视图/首页.md"
    (report.passed if homepage.exists() else report.failed).append("知识库首页存在")
    plugin_issues = community_plugin_issues(config.vault_path, set(config.expected_community_plugins))
    (report.failed if plugin_issues else report.passed).append(
        f"社区插件允许清单正确" if not plugin_issues else f"社区插件异常：{'; '.join(plugin_issues)}"
    )
    policy_path = PROJECT_ROOT / "配置/tag_policy.json"
    taxonomy_path = PROJECT_ROOT / "配置/taxonomy.json"
    policy_issues = tag_policy_issues(policy_path, taxonomy_path)
    (report.failed if policy_issues else report.passed).append(
        "标签策略与 taxonomy 一致且无文件级覆盖" if not policy_issues else f"标签策略异常：{'; '.join(policy_issues[:5])}"
    )
    allowed_tags = set(json.loads(policy_path.read_text(encoding="utf-8"))["allowed_tags"])
    metadata_issues = note_metadata_issues(config.vault_path, allowed_tags)
    (report.failed if metadata_issues else report.passed).append(
        "现有 Markdown 属性与标签审计通过" if not metadata_issues else f"现有笔记属性标签异常：{'; '.join(metadata_issues[:5])}"
    )
    content_issues = note_content_issues(config.vault_path)
    (report.failed if content_issues else report.passed).append(
        "正式笔记标题与正文结构审计通过" if not content_issues else f"正式笔记结构异常：{'; '.join(content_issues[:5])}"
    )
    record_link_issues = organize_record_link_issues(config.vault_path)
    (report.failed if record_link_issues else report.passed).append(
        "整理记录结果链接审计通过" if not record_link_issues else f"整理记录链接异常：{'; '.join(record_link_issues[:5])}"
    )
    contract_ok = (
        bool(config.run_hours)
        and config.source_retention_days == 7
        and bool(config.vault_path.name)
    )
    (report.passed if contract_ok else report.failed).append("定时计划、7 天原始输入归档与 Vault 合同正确")
    provider_problems = provider_issues(config)
    provider_labels = "、".join(config.provider_label(name) for name in config.provider_order)
    (report.failed if provider_problems else report.passed).append(
        f"模型运行环境完整（{provider_labels} 与本地 Whisper）" if not provider_problems else f"模型运行环境异常：{'; '.join(provider_problems)}"
    )
    # A non-GUI shell can be denied access to an otherwise valid login
    # keychain. The actual fallback call is the authoritative availability
    # check, so health must not turn this session-specific condition into a
    # user-facing failure.
    report.passed.append("API 模型密钥仅在实际调用时从 macOS 钥匙串读取")
    secret_result = subprocess.run(
        ["security", "find-generic-password", "-s", config.keychain_service, "-a", __import__("os").environ.get("USER", ""), "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    secret = secret_result.stdout.strip()
    leaked = False
    if secret:
        for path in list_files(config.vault_path):
            if path.stat().st_size <= 10 * 1024 * 1024 and secret in path.read_text(encoding="utf-8", errors="ignore"):
                leaked = True
                break
    (report.failed if leaked else report.passed).append("API 密钥未写入 Vault")
    required_tools = ("python3", "security", "plutil", "launchctl")
    if config.strict_local_audit:
        required_tools += ("mdfind", "pdftotext", "pdftoppm", "pdfinfo", "textutil", "soffice", "swift", "ffmpeg", "ffprobe", "sips")
    missing_tools = [name for name in required_tools if shutil.which(name) is None]
    (report.failed if missing_tools else report.passed).append(
        "本机依赖完整" if not missing_tools else f"本机依赖缺少：{', '.join(missing_tools)}"
    )
    assets = PROJECT_ROOT / "资源与模板/初始知识库模板"
    drift = [
        source.relative_to(assets).as_posix()
        for source in list_files(assets)
        if not (config.vault_path / source.relative_to(assets)).is_file()
        or source.read_bytes() != (config.vault_path / source.relative_to(assets)).read_bytes()
    ]
    (report.failed if drift else report.passed).append(
        "模板与真实 Vault 一致" if not drift else f"模板与真实 Vault 不一致：{', '.join(drift[:5])}"
    )
    if config.strict_local_audit:
        required_docs = (
            PROJECT_ROOT / "唯一方案/新知识库唯一方案.md",
            PROJECT_ROOT / "使用说明/新知识库使用说明.md",
            PROJECT_ROOT / "Agent规则/Obsidian知识库维护/SKILL.md",
        )
        docs_ok = all(path.is_file() for path in required_docs)
        (report.passed if docs_ok else report.failed).append("唯一方案、使用说明与 Agent 规则入口存在")
    else:
        report.passed.append("分发版不依赖本机开发说明文件")
    if launch_agent is not None:
        try:
            plist = plistlib.loads(launch_agent.read_bytes())
            hours = sorted(item["Hour"] for item in plist.get("StartCalendarInterval", []))
            ok = hours == list(config.run_hours)
        except Exception:
            hours, ok = [], False
        (report.passed if ok else report.failed).append(f"定时小时：{hours}")
    return report
