#!/usr/bin/env python3
"""Obsidian Knowledge Vault 跨平台部署脚本。

用法:
    python3 deploy.py [选项]

一次授权后自动完成：前置条件自检 → 安装 Obsidian（缺失时从官方渠道下载）→
复制目录框架/维护程序 → 配置定时任务（macOS LaunchAgent / Windows 任务计划程序）→
验收自测 → 输出部署结论。重复运行幂等，绝不覆盖已有笔记或配置。

平台: 自动识别 macOS（darwin）与 Windows（win32）。
退出码: 0 成功；1 一般错误；2 前置条件缺失；3 未获授权；4 Vault 非空；5 验收失败。
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import platform as _platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

VERSION = "1.2.0"
LABEL = "com.knowledgevault.obsidian-maintenance"
RUN_HOURS = (8, 11, 14, 17, 20, 23)
WINDOWS_PLUGIN_IDS = ("frontmatter-modified-date", "obsidian-auto-organizer")
WINDOWS_TOP_DIRS = ("00 收件箱", "10 生活", "20 工作", "30 学习", "40 记录", "90 系统")
TEMPLATE_CHOICES = {
    "general": "通用模板",
    "legal": "法律模板",
}

# macOS 模型选择：--model 取值 → (说明, provider_order)。provider 名与
# settings.json 一致：codex=ChatGPT 桌面版内置 CLI，deepseek=DeepSeek API，
# custom=用户在 api_providers 里定义的自定义 OpenAI 兼容模型。
MACOS_MODEL_CHOICES = {
    "chatgpt-first": ("ChatGPT 优先，DeepSeek 备用", ["codex", "deepseek"]),
    "deepseek-first": ("DeepSeek 优先，ChatGPT 备用", ["deepseek", "codex"]),
    "chatgpt-only": ("只启用 ChatGPT", ["codex"]),
    "deepseek-only": ("只启用 DeepSeek", ["deepseek"]),
    "chatgpt": ("只启用 ChatGPT", ["codex"]),
    "deepseek": ("只启用 DeepSeek", ["deepseek"]),
    "custom": ("只启用自定义 OpenAI 兼容模型", ["custom"]),
}


def _parse_run_hours(text: str) -> Optional[List[int]]:
    """从自然语言/逗号分隔文本中提取 0-23 的小时，去重升序。

    优先认“X 点”形式（如“9 点、12 点、18 点”）；其次认 “HH:00” 形式；
    最后回退到裸数字列表，并排除“每天 N 次”里的次数 N。
    """
    if "点" in text:
        hours = sorted({int(m) for m in re.findall(r"(\d+)\s*点", text) if 0 <= int(m) <= 23})
    elif ":" in text:
        hours = sorted({int(m) for m in re.findall(r"(\d+):", text) if 0 <= int(m) <= 23})
    else:
        numbers = re.findall(r"\d+", text)
        filtered = [n for n in numbers if not re.search(rf"{n}\s*次", text)]
        hours = sorted({int(n) for n in filtered if 0 <= int(n) <= 23})
    return hours or None


def _local_timezone() -> str:
    try:
        target = os.readlink("/etc/localtime")
        marker = "/zoneinfo/"
        if marker in target:
            return target.split(marker, 1)[1]
    except OSError:
        pass
    return "Asia/Shanghai"


def _start_calendar_interval(run_hours: List[int]) -> str:
    return "\n".join(
        '    <dict><key>Hour</key><integer>%d</integer><key>Minute</key><integer>0</integer></dict>' % hour
        for hour in run_hours
    )


def _resolve_run_hours(args) -> List[int]:
    """解析用户定义的整理时间；未指定时用默认六时点。"""
    if args.run_hours:
        hours = _parse_run_hours(args.run_hours)
        if hours is None:
            raise DeployError("--run-hours 无法解析：%r（示例：8,11,14,17,20,23）" % args.run_hours)
        return hours
    if args.skip_schedule or args.yes:
        return list(RUN_HOURS)
    log("")
    log("每天整理几次、分别在几点？")
    log("  默认：08/11/14/17/20/23 点（每天 6 次）。直接回车用默认。")
    log("  示例：每天 3 次、9 点 12 点 18 点 → 输入：9,12,18")
    try:
        answer = input("整理时间（回车用默认）: ").strip()
    except EOFError:
        return list(RUN_HOURS)
    if not answer:
        return list(RUN_HOURS)
    hours = _parse_run_hours(answer)
    if hours is None:
        log("⚠️ 无法从「%s」识别时间，使用默认 08/11/14/17/20/23。" % answer)
        return list(RUN_HOURS)
    log("✅ 整理时间已设为：%s（每天 %d 次）" % ("/".join("%02d:00" % h for h in hours), len(hours)))
    return hours


def _resolve_macos_providers(args) -> Tuple[List[str], Dict[str, dict], Dict[str, str]]:
    """macOS 模型选择与密钥收集。

    返回 (provider_order, api_providers, keychain_keys)：
    keychain_keys 为 {钥匙串服务名: API 密钥}，只在部署时写入钥匙串，绝不进 settings.json。
    """
    api_providers: Dict[str, dict] = {}
    keychain_keys: Dict[str, str] = {}
    if args.custom_provider:
        name, _, spec = args.custom_provider.partition("=")
        base_url, _, model = spec.partition(",")
        if not name or not base_url or not model:
            raise DeployError("--custom-provider 格式应为 name=base_url,model")
        api_providers[name.strip()] = {
            "base_url": base_url.strip(),
            "model": model.strip(),
            "keychain_service": "knowledge-vault-%s" % name.strip(),
        }
    order: List[str]
    if args.model:
        if args.model not in MACOS_MODEL_CHOICES:
            raise DeployError("未知模型选择: %s（可选：%s）" % (args.model, "、".join(MACOS_MODEL_CHOICES)))
        _, order = MACOS_MODEL_CHOICES[args.model]
        if "custom" in order and not api_providers:
            raise DeployError("--model custom 需要同时提供 --custom-provider name=base_url,model")
    elif api_providers:
        order = ["custom"]
    else:
        codex_present = Path("/Applications/ChatGPT.app/Contents/Resources/codex").is_file()
        if args.yes:
            return (["codex", "deepseek"] if codex_present else ["deepseek"]), api_providers, keychain_keys
        log("")
        log("整理模型选择（收件箱资料会发送给所选模型处理，请确认你信任该服务）：")
        options = [
            ("1", "DeepSeek（推荐，只需 API 密钥）", ["deepseek"]),
            ("2", "ChatGPT 桌面版", ["codex"]),
            ("3", "DeepSeek 优先，ChatGPT 备用", ["deepseek", "codex"]),
            ("4", "ChatGPT 优先，DeepSeek 备用", ["codex", "deepseek"]),
            ("5", "自定义 OpenAI 兼容模型", ["custom"]),
            ("0", "暂不配置（整理功能不可用，不推荐）", []),
        ]
        if not codex_present:
            log("  （未检测到 ChatGPT 桌面版，选项 2/4 不可用）")
        for num, label, _ in options:
            log("  %s) %s" % (num, label))
        try:
            choice = input("请选择模型（回车默认 1）: ").strip() or "1"
        except EOFError:
            choice = "1"
        selected = next((option for option in options if option[0] == choice), None)
        if selected is None:
            raise DeployError("无效的模型选择：%s" % choice)
        _, _, order = selected
    if not order:
        return [], api_providers, keychain_keys
    if "deepseek" in order and not args.yes:
        try:
            key = getpass.getpass(
                "请输入 DeepSeek API 密钥（输入不显示；只写入本机钥匙串；已有密钥可直接回车）: "
            ).strip()
        except EOFError:
            key = ""
        if key:
            keychain_keys[args.keychain_service or "knowledge-vault-deepseek"] = key
        else:
            log("ℹ️ 未输入新密钥；将使用钥匙串中已有的 DeepSeek 密钥，若不存在则验收失败。")
    if "custom" in order and not api_providers:
        name = input("自定义模型名称（如 kimi）: ").strip()
        base_url = input("接口地址 base_url（如 https://api.moonshot.cn/v1）: ").strip()
        model = input("模型名（如 kimi-k2-0711-preview）: ").strip()
        if name and base_url and model:
            service = "knowledge-vault-%s" % name
            api_providers[name] = {"base_url": base_url, "model": model, "keychain_service": service}
            key = getpass.getpass(
                "请输入该模型的 API 密钥（输入不显示；只写入本机钥匙串；已有密钥可直接回车）: "
            ).strip()
            if key:
                keychain_keys[service] = key
            else:
                log("⚠️ 未提供 %s 密钥；该模型将不可用（部署会因此不通过验收）。" % name)
        else:
            log("⚠️ 自定义模型信息不完整，跳过自定义模型。")
            order = [p for p in order if p != "custom"] or ["deepseek"]
    elif "custom" in order and not args.yes:
        name = next(iter(api_providers))
        service = api_providers[name]["keychain_service"]
        key = getpass.getpass(
            "请输入 %s API 密钥（输入不显示；只写入本机钥匙串；已有密钥可直接回车）: " % name
        ).strip()
        if key:
            keychain_keys[service] = key
        else:
            log("ℹ️ 未输入新密钥；将使用钥匙串中已有的 %s 密钥，若不存在则验收失败。" % name)
    return order, api_providers, keychain_keys

_OBSIDIAN_RELEASES_API = "https://api.github.com/repos/obsidianmd/obsidian-releases/releases/latest"


def log(message: str) -> None:
    print(message, flush=True)


def _reconfigure_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass


_reconfigure_stdout()


class DeployError(Exception):
    code = 1


class PrereqError(DeployError):
    code = 2


class NotAuthorized(DeployError):
    code = 3


class VaultNotEmptyError(DeployError):
    code = 4


class VerifyFailed(DeployError):
    code = 5


class Runner:
    """命令执行器；simulate_win32 用于在非 Windows 机器上演练 Windows 分支。"""

    def __init__(self, simulate_win32: bool = False):
        self.simulate = simulate_win32
        self.calls: List[List[str]] = []

    @property
    def windows(self) -> bool:
        return self.simulate or _platform.system().lower().startswith("win")

    def run(self, cmd: List[str], cwd: Optional[Path] = None,
            env: Optional[dict] = None, timeout: Optional[int] = None,
            input_text: Optional[str] = None) -> subprocess.CompletedProcess:
        self.calls.append([str(c) for c in cmd])
        if self.simulate and not _platform.system().lower().startswith("win"):
            return self._fake(cmd)
        try:
            return subprocess.run(cmd, cwd=str(cwd) if cwd else None, env=env,
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", input=input_text, timeout=timeout)
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(cmd, 124, "", "timeout")

    def _fake(self, cmd: List[str]) -> subprocess.CompletedProcess:
        exe = Path(cmd[0]).name.lower()
        stdout = ""
        if exe.startswith("powershell"):
            stdout = ""
        elif exe == "schtasks":
            stdout = "任务名:                          obsidian_maintenance"
        elif exe in ("where", "which"):
            stdout = "C:\\Windows\\System32\\cmd.exe"
        elif exe in ("py", "python", "python3"):
            stdout = "3.11.9"
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    def powershell(self, script: str) -> str:
        proc = self.run(["powershell.exe", "-NoProfile", "-NonInteractive",
                         "-Command", script])
        return proc.stdout.strip()


def detect_platform(runner: Runner) -> str:
    if runner.windows:
        return "windows"
    if _platform.system().lower().startswith("darwin"):
        return "macos"
    raise DeployError("不支持的操作系统: %s（只支持 macOS 与 Windows）" % _platform.system())


def find_payload(base: Path, platform_name: str) -> Path:
    """载荷优先取当前目录，其次解包 release/ 下对应平台的 zip。"""
    marker = "知识库模板" if platform_name == "windows" else "程序"
    if (base / marker).is_dir():
        return base
    release_dir = base / "release"
    if release_dir.is_dir():
        candidates = sorted(
            (item for item in release_dir.glob("*.zip") if platform_name in item.name.lower()),
            reverse=True,
        )
        if candidates:
            tmp = Path(tempfile.mkdtemp(prefix="kv-deploy-"))
            with zipfile.ZipFile(candidates[0]) as archive:
                archive.extractall(tmp)
            roots = [p for p in tmp.iterdir() if p.is_dir()]
            if not roots:
                raise DeployError("发布包解压后没有顶层目录: %s" % candidates[0].name)
            log("ℹ️ 已从发布包解压载荷: %s" % candidates[0].name)
            return roots[0]
    raise DeployError(
        "找不到部署载荷：请在仓库根目录（有 deploy.py 的目录）或解压后的包内运行；"
        "或把对应平台的发布包放进 release/ 目录。")


def unique_path(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    counter = 2
    while target.exists():
        stem = Path(name).stem
        target = directory / ("%s (%d)%s" % (stem, counter, Path(name).suffix))
        counter += 1
    return target


def copy_tree_merge(source: Path, target: Path, overwrite: bool = True) -> List[str]:
    created: List[str] = []
    for item in sorted(source.rglob("*")):
        rel = item.relative_to(source)
        dest = target / rel
        if item.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        if dest.exists() and not overwrite:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, dest)
        created.append(str(rel))
    return created


def authorize(plan: Dict[str, str], args) -> None:
    log("")
    log("即将执行以下操作（一次授权，其余步骤自动完成）：")
    for key, value in plan.items():
        log("  - %s: %s" % (key, value))
    if args.yes:
        log("已授权（--yes）。")
        return
    try:
        answer = input("是否允许？(y/n): ").strip().lower()
    except EOFError:
        raise NotAuthorized("无法读取授权输入；如用于自动化请加 --yes")
    if answer not in ("y", "yes", "是"):
        raise NotAuthorized("未获授权，已停止。")


# ---------------------------------------------------------------- 前置条件自检


def _engine_python_macos(runner: Runner) -> str:
    candidates: List[str] = []
    env_py = os.environ.get("KV_PYTHON")
    if env_py:
        candidates.append(env_py)
    candidates += ["/opt/homebrew/bin/python3", "/usr/local/bin/python3"]
    found = shutil.which("python3")
    if found:
        candidates.append(found)
    for cand in candidates:
        if not Path(cand).is_file():
            continue
        try:
            proc = runner.run([cand, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"])
            major, minor = proc.stdout.strip().split(".")
            if (int(major), int(minor)) >= (3, 11):
                return cand
        except (OSError, ValueError):
            continue
    raise PrereqError(
        "找不到 Python 3.11+（维护程序要求）。修复命令：brew install python@3.11，然后重跑本脚本；"
        "或设置环境变量 KV_PYTHON 指向 3.11+ 的解释器。")


def check_macos_prereqs(runner: Runner, args) -> List[str]:
    warnings: List[str] = []
    python = _engine_python_macos(runner)
    log("✅ 引擎 Python: %s" % python)
    tools = [
        ("plutil", True, "系统自带，异常时修复系统"),
        ("launchctl", True, "系统自带，异常时修复系统"),
        ("security", False, "macOS 钥匙串；备用模型密钥需要"),
        ("mdfind", False, "Spotlight 搜索"),
        ("textutil", False, "Word/RTF 提取"),
        ("sips", False, "图片转换"),
        ("swift", False, "本机 Vision OCR；缺失时 xcode-select --install"),
        ("pdftotext", False, "brew install poppler"),
        ("pdfinfo", False, "brew install poppler"),
        ("ffmpeg", False, "brew install ffmpeg"),
        ("ffprobe", False, "brew install ffmpeg"),
        ("soffice", False, "brew install --cask libreoffice"),
        ("whisper-cli", False, "brew install whisper-cpp"),
    ]
    for name, required, fix in tools:
        if shutil.which(name):
            log("✅ 工具 %s" % name)
        elif required:
            raise PrereqError("缺少必需系统命令 %s。修复：%s" % (name, fix))
        else:
            warnings.append("%s 缺失（%s）——相关能力会降级，其余功能不受影响" % (name, fix))
    codex = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
    if codex.is_file():
        log("✅ ChatGPT App 内置 Codex 存在")
    else:
        warnings.append(
            "未找到 ChatGPT 桌面版（内置 Codex）。可改用 DeepSeek 等 API 模型——"
            "部署时会询问模型选择并通过本机隐藏输入读取 API 密钥。")
    return warnings


def _github_latest_assets() -> List[dict]:
    request = urllib.request.Request(_OBSIDIAN_RELEASES_API,
                                     headers={"User-Agent": "knowledge-vault-deploy"})
    with urllib.request.urlopen(request, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("assets", [])


def _download(url: str, dest: Path) -> None:
    log("  下载 %s" % url)
    request = urllib.request.Request(url, headers={"User-Agent": "knowledge-vault-deploy"})
    with urllib.request.urlopen(request, timeout=600) as resp, dest.open("wb") as handle:
        shutil.copyfileobj(resp, handle)


def _find_obsidian_windows(runner: Runner) -> Optional[Path]:
    local = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    candidates = [
        Path(local) / "Programs" / "Obsidian" / "Obsidian.exe",
        Path(local) / "Obsidian" / "Obsidian.exe",
        Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Obsidian" / "Obsidian.exe",
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    proc = runner.run(["where", "obsidian"])
    for line in proc.stdout.splitlines():
        cand = Path(line.strip())
        if cand.is_file():
            return cand
    return None


def _ensure_obsidian(runner: Runner, platform_name: str, args) -> None:
    if args.skip_obsidian:
        log("⚠️ 已跳过 Obsidian 检查（--skip-obsidian）")
        return
    if platform_name == "macos":
        app = Path("/Applications/Obsidian.app")
        if app.is_dir():
            log("✅ Obsidian 已安装: /Applications/Obsidian.app")
            return
        log("⚠️ 未检测到 Obsidian，将尝试从官方渠道下载安装…")
        try:
            assets = _github_latest_assets()
            machine = _platform.machine().lower()
            wanted = "arm64" if machine in ("arm64", "aarch64") else "x64"
            name: Optional[str] = None
            for asset in assets:
                a_name = asset["name"]
                if a_name.endswith(".dmg") and ("-" + wanted) in a_name.lower():
                    name = a_name
                    break
            if not name:
                for asset in assets:
                    a_name = asset["name"]
                    if a_name.endswith(".dmg") and "arm64" not in a_name.lower():
                        name = a_name
                        break
            if not name:
                raise PrereqError(
                    "官方发布里没有可用 dmg；请到 https://obsidian.md/download 手动安装后重跑。")
            url = "https://github.com/obsidianmd/obsidian-releases/releases/latest/download/" + name
            tmp = Path(tempfile.mkdtemp(prefix="obsidian-"))
            dmg = tmp / name
            _download(url, dmg)
            mount = tmp / "mount"
            mount.mkdir()
            runner.run(["hdiutil", "attach", str(dmg), "-nobrowse", "-mountpoint", str(mount)])
            app_src = mount / "Obsidian.app"
            if not app_src.is_dir():
                raise PrereqError("dmg 内未找到 Obsidian.app，安装中止（已下载: %s）" % dmg)
            try:
                shutil.copytree(app_src, Path("/Applications/Obsidian.app"))
                log("✅ Obsidian 已安装到 /Applications")
            except PermissionError:
                home_apps = Path.home() / "Applications"
                home_apps.mkdir(exist_ok=True)
                shutil.copytree(app_src, home_apps / "Obsidian.app")
                log("✅ Obsidian 已安装到 ~/Applications（无系统目录权限）")
            finally:
                runner.run(["hdiutil", "detach", str(mount)])
        except PrereqError:
            raise
        except Exception as exc:
            raise PrereqError(
                "自动安装 Obsidian 失败: %s。请到 https://obsidian.md/download 手动安装后重跑本脚本。" % exc)
    else:
        if runner.simulate:
            log("✅ Obsidian 已安装（演练模式假定存在）")
            return
        exe = _find_obsidian_windows(runner)
        if exe:
            log("✅ Obsidian 已安装: %s" % exe)
            return
        log("⚠️ 未检测到 Obsidian，将尝试从官方渠道下载安装…")
        try:
            assets = _github_latest_assets()
            exe_name = next((a["name"] for a in assets if a["name"].endswith(".exe")), None)
            if not exe_name:
                raise PrereqError(
                    "官方发布里没有可用安装包；请到 https://obsidian.md/download 手动安装后重跑。")
            url = "https://github.com/obsidianmd/obsidian-releases/releases/latest/download/" + exe_name
            tmp = Path(tempfile.mkdtemp(prefix="obsidian-"))
            installer = tmp / exe_name
            _download(url, installer)
            proc = runner.run([str(installer), "/S"])
            if proc.returncode == 0 or _find_obsidian_windows(runner):
                log("✅ Obsidian 已安装")
            else:
                raise PrereqError(
                    "Obsidian 静默安装失败（exit=%s）。请运行下载好的安装包手动安装：%s"
                    % (proc.returncode, installer))
        except PrereqError:
            raise
        except Exception as exc:
            raise PrereqError(
                "自动安装 Obsidian 失败: %s。请到 https://obsidian.md/download 手动安装后重跑。" % exc)


def check_windows_prereqs(runner: Runner, args) -> List[str]:
    version = sys.version_info
    if version < (3, 8):
        raise PrereqError(
            "Python 版本过低（%s）。用 `winget install Python.Python.3.11` 安装后重跑。" % version)
    log("✅ Python %d.%d.%d" % version[:3])
    warnings: List[str] = []
    for name in ("powershell.exe", "schtasks"):
        if runner.simulate:
            log("✅ 系统命令 %s（演练模式）" % name)
        elif shutil.which(name):
            log("✅ 系统命令 %s" % name)
        else:
            raise PrereqError("缺少系统命令 %s，无法注册定时任务。请在正常 Windows 系统运行。" % name)
    if runner.simulate or shutil.which("pdftotext"):
        log("✅ pdftotext（PDF 分类可用）")
    else:
        warnings.append(
            "未找到 pdftotext——PDF 无法自动提取文字，会留在收件箱。"
            "安装 xpdf 并把 bin 目录加入 PATH 后可启用。")
    return warnings


# ---------------------------------------------------------------- 模板渲染与 macOS 安装


def render(template_text: str, values: Dict[str, str]) -> str:
    result = template_text
    for key, value in values.items():
        result = result.replace("{{%s}}" % key, value)
    leftover = re.findall(r"\{\{[A-Z_]+\}\}", result)
    if leftover:
        raise DeployError("模板渲染后仍有未替换占位符: %s" % ", ".join(sorted(set(leftover))))
    return result


def macos_values(args, project_dir: Path, python: str, vault: Path) -> Dict[str, str]:
    home = str(Path.home())
    prefix = "/opt/homebrew" if Path("/opt/homebrew").is_dir() else "/usr/local"
    run_hours: List[int] = list(getattr(args, "run_hours", None) or RUN_HOURS)
    provider_order: List[str] = list(getattr(args, "provider_order", None) or ["codex", "deepseek"])
    api_providers: Dict[str, dict] = dict(getattr(args, "api_providers", None) or {})
    return {
        "PROJECT": str(project_dir),
        "VAULT_PATH": str(vault),
        "KEYCHAIN_SERVICE": args.keychain_service or "knowledge-vault-deepseek",
        "HOMEBREW_PREFIX": prefix,
        "HOME": home,
        "USER": os.environ.get("USER", "user"),
        "CODEX_HOME": home + "/.codex",
        "PYTHON": python,
        "LABEL": LABEL,
        "TIMEZONE": _local_timezone(),
        "RUN_HOURS_JSON": json.dumps(run_hours, ensure_ascii=False),
        "START_CALENDAR_INTERVAL": _start_calendar_interval(run_hours),
        "EXPECTED_HOURS": " ".join(str(hour) for hour in run_hours),
        "PROVIDER_ORDER_JSON": json.dumps(provider_order, ensure_ascii=False),
        "API_PROVIDERS_JSON": json.dumps(api_providers, ensure_ascii=False),
    }


def _ensure_vault_obsidian_config(vault: Path, plugin_ids: Tuple[str, ...] = ()) -> None:
    obsidian = vault / ".obsidian"
    obsidian.mkdir(parents=True, exist_ok=True)
    app = obsidian / "app.json"
    if not app.exists():
        app.write_text(json.dumps({
            "newFileLocation": "folder", "newFileFolderPath": "00 收件箱",
            "attachmentFolderPath": "00 收件箱", "alwaysUpdateLinks": True,
            "useMarkdownLinks": False,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    core = obsidian / "core-plugins.json"
    if not core.exists():
        core.write_text(json.dumps({
            "file-explorer": True, "global-search": True, "tag-pane": True,
            "properties": True, "templates": True, "command-palette": True,
            "bases": True,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    community = obsidian / "community-plugins.json"
    ids: List[str] = []
    if community.exists():
        ids = json.loads(community.read_text(encoding="utf-8"))
    changed = False
    for pid in plugin_ids:
        if pid not in ids:
            ids.append(pid)
            changed = True
    if changed or not community.exists():
        community.write_text(json.dumps(ids, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_macos_settings(settings_path: Path, template: str, values: Dict[str, str]) -> dict:
    """Atomically update fields managed by deployment, including reconfiguration."""
    desired = json.loads(render(template, values))
    current: dict = {}
    if settings_path.exists():
        try:
            current = json.loads(settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise DeployError("既有 配置/settings.json 无法读取，未作覆盖：%s" % exc) from exc
    managed = {
        "vault_path", "timezone", "run_hours", "provider_order", "api_providers",
        "keychain_service", "manual_only_paths", "expected_community_plugins",
        "strict_local_audit",
    }
    for key in managed:
        current[key] = desired[key]
    for key, value in desired.items():
        current.setdefault(key, value)
    temporary = settings_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(settings_path)
    return current


def _store_keychain_secret(runner: Runner, service: str, secret: str) -> None:
    """Store a secret without placing it in argv, shell history, or command logs."""
    proc = runner.run(
        ["security", "add-generic-password", "-U", "-s", service,
         "-a", os.environ.get("USER", ""), "-w"],
        input_text=secret + "\n",
    )
    if proc.returncode != 0:
        raise DeployError("写入 macOS 钥匙串失败（服务名 %s）：%s" % (service, proc.stderr[-300:]))


def _require_empty_vault(vault: Path) -> None:
    """Public v1 only installs into a genuinely empty target directory."""
    if not vault.exists():
        return
    entries = list(vault.iterdir())
    if entries:
        raise VaultNotEmptyError(
            "目标 Vault 不是空的（发现 %d 项，如「%s」）。为保护已有笔记，"
            "Obsidian Knowledge Vault v1.2 只允许安装到新的空白目录；"
            "请新建空白 Vault 后重试。" % (len(entries), entries[0].name)
        )


def _resolve_template(args) -> str:
    if args.template:
        return args.template
    if args.yes:
        return "general"
    log("")
    log("请选择目录模板：")
    log("  1) 通用模板（适合所有 Obsidian 用户）")
    log("  2) 法律模板（仅工作目录采用法律专业分类）")
    try:
        choice = input("模板（回车默认通用）: ").strip() or "1"
    except EOFError:
        choice = "1"
    if choice == "1":
        return "general"
    if choice == "2":
        return "legal"
    raise DeployError("无效的模板选择；请输入 1 或 2。")


def _profile_root(payload: Path, profile: str) -> Path:
    root = payload / "模板" / profile
    if not root.is_dir():
        raise DeployError("发布包缺少 %s 模板，请重新下载完整 v%s 安装包。" % (profile, VERSION))
    return root


def macos_install(runner: Runner, payload: Path, args) -> dict:
    python = _engine_python_macos(runner)
    project_dir = Path(args.project_dir or (str(Path.home()) + "/Code/Obsidian")).expanduser()
    if args.vault:
        vault = Path(args.vault).expanduser()
    else:
        vault = Path.home() / "Documents" / "Obsidian Knowledge Vault"
    vault = vault.resolve()  # 消除 macOS /tmp→/private/tmp 类符号链接差异
    _require_empty_vault(vault)
    profile = args.template
    values = macos_values(args, project_dir, python, vault)
    run_hours: List[int] = list(getattr(args, "run_hours", None) or RUN_HOURS)
    provider_order: List[str] = list(getattr(args, "provider_order", None) or [])
    model_labels = " + ".join(
        {"codex": "ChatGPT", "deepseek": "DeepSeek"}.get(name, name) for name in provider_order
    ) if provider_order else "未配置（整理功能不可用）"
    plan = {
        "平台": "macOS",
        "目录模板": TEMPLATE_CHOICES[profile],
        "项目目录": str(project_dir),
        "Vault 路径": str(vault),
        "Python": python,
        "整理模型": model_labels,
        "定时任务": ("跳过（--skip-schedule）" if args.skip_schedule
                      else "LaunchAgent（%s，%s 点，每天 %d 次）" % (
                          LABEL, "/".join("%02d" % h for h in run_hours), len(run_hours))),
        "钥匙串": "安全写入所选模型的本机钥匙串项目（密钥不进入命令参数）"
                  if getattr(args, "keychain_keys", None)
                  else "不写入新密钥（使用已有钥匙串；不可用时部署验收失败）",
    }
    authorize(plan, args)
    project_dir.mkdir(parents=True, exist_ok=True)
    created = 0
    for name in ("程序", "测试", "资源与模板", "插件", "脚本", "定时任务", "配置"):
        source = payload / name
        if source.is_dir():
            created += len(copy_tree_merge(source, project_dir / name))
    profile_root = _profile_root(payload, profile)
    for name in ("资源与模板", "配置"):
        source = profile_root / name
        if source.is_dir():
            created += len(copy_tree_merge(source, project_dir / name))
    pyproject = payload / "pyproject.toml"
    if pyproject.is_file():
        shutil.copy2(pyproject, project_dir / "pyproject.toml")
        created += 1
    log("✅ 载荷复制完成（%d 个文件）→ %s" % (created, project_dir))
    settings_path = project_dir / "配置" / "settings.json"
    template = (payload / "配置" / "settings.json.tpl").read_text(encoding="utf-8")
    written_settings = _write_macos_settings(settings_path, template, values)
    run_hours = list(written_settings["run_hours"])
    values["RUN_HOURS_JSON"] = json.dumps(run_hours, ensure_ascii=False)
    values["START_CALENDAR_INTERVAL"] = _start_calendar_interval(run_hours)
    values["EXPECTED_HOURS"] = " ".join(str(hour) for hour in run_hours)
    log("✅ 配置/settings.json 已按本次选择安全更新（保留非部署管理字段）")
    plist_tpl = payload / "定时任务" / ("%s.plist.tpl" % LABEL)
    plist_text = render(plist_tpl.read_text(encoding="utf-8"), values)
    (project_dir / "定时任务" / ("%s.plist" % LABEL)).write_text(plist_text, encoding="utf-8")
    for name in ("run_knowledge_vault.sh", "install_launch_agent.sh",
                 "install_auto_properties_plugin.sh"):
        tpl = payload / "脚本" / (name + ".tpl")
        rendered = render(tpl.read_text(encoding="utf-8"), values)
        target = project_dir / "脚本" / name
        target.write_text(rendered, encoding="utf-8")
        target.chmod(target.stat().st_mode | 0o111)
    log("✅ 已渲染定时任务与启动脚本（Label: %s）" % LABEL)
    (project_dir / ".state").mkdir(exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_dir / "程序")
    proc = runner.run([python, "-m", "knowledge_vault.cli", "--settings",
                       str(settings_path), "scaffold", "--apply"], env=env, timeout=600)
    if proc.returncode != 0:
        raise DeployError("Vault 骨架创建失败（scaffold）: %s" % proc.stderr[-600:])
    log("✅ Vault 目录骨架就绪（只创建缺失目录）: %s" % vault)
    _ensure_vault_obsidian_config(vault)
    proc = runner.run(["bash", str(project_dir / "脚本" / "install_auto_properties_plugin.sh")])
    if proc.returncode != 0:
        raise DeployError("本地 Auto Properties 插件安装失败: %s" % proc.stderr[-500:])
    log("✅ 本地插件 knowledge-vault-auto-properties 已安装")
    scheduled = False
    if not args.skip_schedule:
        proc = runner.run(["bash", str(project_dir / "脚本" / "install_launch_agent.sh")], timeout=900)
        if proc.returncode != 0:
            raise DeployError("LaunchAgent 安装失败（旧任务已自动恢复）: %s" % proc.stderr[-500:])
        scheduled = True
        log("✅ LaunchAgent 已安装并验收")
    for service, key in (getattr(args, "keychain_keys", None) or {}).items():
        _store_keychain_secret(runner, service, key)
        log("✅ 模型密钥已安全写入钥匙串（服务名 %s）" % service)
    return {"project_dir": project_dir, "vault": vault, "python": python,
            "values": values, "scheduled": scheduled, "template": profile}


# ---------------------------------------------------------------- 验收自测（macOS）


def acceptance_temp_note(vault: Path) -> str:
    inbox = vault / "00 收件箱"
    inbox.mkdir(parents=True, exist_ok=True)
    name = "部署验收-%s.md" % datetime.now().strftime("%Y%m%d-%H%M%S")
    note = inbox / name
    stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    content = ("---\ncreated: %s\nmodified: %s\ntags:\n  - kind/note\n  - workflow/inbox\n---\n\n"
               "部署验收测试笔记。\n" % (stamp, stamp))
    note.write_text(content, encoding="utf-8")
    back = note.read_text(encoding="utf-8")
    if "kind/note" not in back:
        note.unlink(missing_ok=True)
        raise VerifyFailed("验收笔记写入后读回不一致")
    note.unlink()
    return name


def macos_acceptance(runner: Runner, info: dict, args) -> List[str]:
    problems: List[str] = []
    project_dir = info["project_dir"]
    python = info["python"]
    vault = info["vault"]
    log("")
    log("---- 验收自测 ----")
    proc = runner.run([python, "-m", "compileall", "-q",
                       str(project_dir / "程序"), str(project_dir / "测试")])
    log("✅ 编译检查（程序/测试）" if proc.returncode == 0 else "❌ 编译检查")
    if proc.returncode != 0:
        problems.append("compileall 失败")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_dir / "程序")
    proc = runner.run([python, "-m", "unittest", "discover", "-s",
                       str(project_dir / "测试"), "-q"], env=env, timeout=900)
    log("✅ 自动测试全部通过" if proc.returncode == 0 else "❌ 自动测试存在失败：\n%s" % proc.stdout[-800:])
    if proc.returncode != 0:
        problems.append("unittest 存在失败")
    taxonomy = json.loads((project_dir / "配置" / "taxonomy.json").read_text(encoding="utf-8"))["paths"]
    missing = [p for p in taxonomy if not (vault / p).is_dir()]
    log("✅ Vault 目录骨架完整（%d 个目录）" % (len(taxonomy) - len(missing))
        if not missing else "❌ Vault 缺少目录: %s" % ", ".join(missing[:10]))
    if missing:
        problems.append("Vault 目录骨架不完整")
    settings_path = project_dir / "配置" / "settings.json"
    final_settings = json.loads(settings_path.read_text(encoding="utf-8"))
    run_hours: List[int] = list(final_settings["run_hours"])
    if info.get("scheduled"):
        plist = Path.home() / "Library" / "LaunchAgents" / ("%s.plist" % LABEL)
        if plist.is_file():
            hours = []
            for i in range(len(run_hours)):
                proc = runner.run(["/usr/libexec/PlistBuddy", "-c",
                                   "Print :StartCalendarInterval:%d:Hour" % i, str(plist)])
                hours.append(proc.stdout.strip())
            hours_ok = (len(hours) == len(run_hours)
                        and all(h == str(exp) for h, exp in zip(hours, run_hours)))
            log("✅ 定时任务时点正确（%s，每天 %d 次）" % (
                "/".join("%02d" % h for h in run_hours), len(run_hours)) if hours_ok
                else "❌ 定时任务时点不正确（应为 %s）" % ",".join(str(h) for h in run_hours))
            if not hours_ok:
                problems.append("定时任务时点不正确")
            proc = runner.run(["plutil", "-p", str(plist)])
            log("✅ 无加载即运行（无 RunAtLoad）" if "RunAtLoad" not in proc.stdout
                else "❌ 存在 RunAtLoad")
            if "RunAtLoad" in proc.stdout:
                problems.append("存在 RunAtLoad")
            proc = runner.run(["launchctl", "print", "gui/%s/%s" % (os.getuid(), LABEL)])
            log("✅ macOS 已加载任务" if proc.returncode == 0 else "❌ launchctl 未加载任务")
            if proc.returncode != 0:
                problems.append("launchctl 未加载任务")
        else:
            log("❌ 未找到已安装的 LaunchAgent plist")
            problems.append("LaunchAgent plist 缺失")
    name = acceptance_temp_note(vault)
    log("✅ 临时验收笔记创建/读取/删除（已删除：%s）" % name)
    audit_cmd = [python, "-m", "knowledge_vault.cli", "--settings", str(settings_path), "audit"]
    if info.get("scheduled"):
        audit_cmd += ["--launch-agent", str(Path.home() / "Library" / "LaunchAgents" / ("%s.plist" % LABEL))]
    proc = runner.run(audit_cmd, env=env, timeout=600)
    if proc.returncode == 0:
        log("✅ 健康审计通过")
    else:
        detail = (proc.stdout or proc.stderr or "").strip().splitlines()
        log("❌ 健康审计失败：\n%s" % "\n".join(detail[-12:]))
        problems.append("健康审计失败（必须修复全部 FAIL 后重跑）")
    proc = runner.run([python, "-m", "knowledge_vault.cli", "--settings",
                       str(settings_path), "provider-smoke"], env=env, timeout=600)
    if proc.returncode == 0:
        log("✅ 模型可用性验证通过（至少一个已配置模型可正常调用）")
    else:
        detail = (proc.stdout or proc.stderr or "").strip().splitlines()
        log("❌ 模型不可用：%s" % "；".join(detail[-3:]))
        problems.append("模型不可用（需在本机安全输入 API 密钥或安装/登录 ChatGPT 桌面版，然后重跑）")
    return problems


# ---------------------------------------------------------------- Windows 安装

WINDOWS_MODEL_CHOICES = {
    "openai-first": ("OpenAI 兼容服务优先，失败时 DeepSeek 备用", ["openai", "deepseek"]),
    "deepseek-first": ("DeepSeek 优先，失败时 OpenAI 兼容服务备用", ["deepseek", "openai"]),
    "openai-only": ("只启用 OpenAI 兼容服务", ["openai"]),
    "deepseek-only": ("只启用 DeepSeek", ["deepseek"]),
    "custom": ("只启用自定义 OpenAI 兼容服务", ["custom"]),
    # 兼容早期自动化参数；Windows 不把它表述为 ChatGPT 桌面版。
    "chatgpt-first": ("OpenAI 兼容服务优先，失败时 DeepSeek 备用", ["openai", "deepseek"]),
    "chatgpt-only": ("只启用 OpenAI 兼容服务", ["openai"]),
}


def _windows_provider_config(workspace: Path, name: str, custom_provider: Optional[str]) -> dict:
    if name == "openai":
        return {"name": "openai", "base_url": "https://api.openai.com/v1",
                "model": os.environ.get("KV_CHATGPT_MODEL", "gpt-4o-mini"),
                "key_env": "KV_OPENAI_KEY",
                "key_file": str(workspace / ".secrets" / "openai.key"), "timeout": 180}
    if name == "deepseek":
        return {"name": "deepseek", "base_url": "https://api.deepseek.com",
                "model": os.environ.get("KV_DEEPSEEK_MODEL", "deepseek-chat"),
                "key_env": "KV_DEEPSEEK_KEY",
                "key_file": str(workspace / ".secrets" / "deepseek.key"), "timeout": 180}
    provider_name, separator, spec = (custom_provider or "").partition("=")
    base_url, comma, model = spec.partition(",")
    if not separator or not comma or not provider_name.strip() or not base_url.strip() or not model.strip():
        raise DeployError("Windows 自定义服务请使用 --custom-provider name=base_url,model")
    return {"name": provider_name.strip(), "base_url": base_url.strip(), "model": model.strip(),
            "key_env": "KV_CUSTOM_KEY",
            "key_file": str(workspace / ".secrets" / "custom.key"), "timeout": 180}


def _store_windows_key(key: str, key_file: Path, runner: Runner) -> None:
    """经标准输入交给 DPAPI；密钥不会出现在命令参数、配置或日志中。"""
    if not key:
        return
    if runner.simulate:
        return
    script = (
        "Add-Type -AssemblyName System.Security;"
        "$plain=[Console]::In.ReadToEnd();"
        "$bytes=[Text.Encoding]::UTF8.GetBytes($plain);"
        "$enc=[Security.Cryptography.ProtectedData]::Protect($bytes,$null,"
        "[Security.Cryptography.DataProtectionScope]::CurrentUser);"
        "$dir=Split-Path -Parent '%s'; New-Item -ItemType Directory -Force -Path $dir | Out-Null;"
        "[IO.File]::WriteAllBytes('%s',$enc)"
        % (str(key_file).replace("'", "''"), str(key_file).replace("'", "''"))
    )
    proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                          input=key, text=True, capture_output=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise DeployError("Windows 无法安全保存 API 密钥：%s" % proc.stderr[-300:])


def _resolve_windows_keys(args, order: List[str]) -> Dict[str, str]:
    if args.yes:
        return {}
    labels = {"openai": "OpenAI 兼容服务", "deepseek": "DeepSeek", "custom": "自定义 OpenAI 兼容服务"}
    keys: Dict[str, str] = {}
    for name in order:
        key = getpass.getpass("请输入 %s 的 API 密钥（输入不显示；仅用 DPAPI 保存在本机）: " % labels[name]).strip()
        if key:
            keys[name] = key
        else:
            log("ℹ️ 未输入 %s 密钥；将仅尝试已有本机密钥或环境变量。" % labels[name])
    return keys


def windows_install(runner: Runner, payload: Path, args) -> dict:
    vault = (Path(args.vault).expanduser() if args.vault
             else Path.home() / "Documents" / "Obsidian Knowledge Vault")
    vault = vault.resolve()  # 统一规范路径，避免符号链接差异
    _require_empty_vault(vault)
    profile = args.template
    workspace = (Path(args.workspace).expanduser() if args.workspace
                 else vault.parent / "Obsidian Knowledge Vault Maintenance")
    model_key = args.model or "chatgpt-first"
    if model_key not in WINDOWS_MODEL_CHOICES:
        raise DeployError("未知模型选择: %s" % model_key)
    choice_label, order = WINDOWS_MODEL_CHOICES[model_key]
    run_hours: List[int] = list(getattr(args, "run_hours", None) or RUN_HOURS)
    plan = {
        "平台": "Windows",
        "目录模板": TEMPLATE_CHOICES[profile],
        "Vault 路径": str(vault),
        "维护工作目录": str(workspace),
        "模型": choice_label,
        "定时任务": ("跳过（--skip-schedule）" if args.skip_schedule
                      else "任务计划程序 obsidian_maintenance（本机时区 %s 点，每天 %d 次）" % (
                          "/".join("%02d" % h for h in run_hours), len(run_hours))),
        "API 密钥": "仅经本机隐藏输入收集，并用 Windows DPAPI 加密保存；不进入命令参数或配置文件",
    }
    authorize(plan, args)
    vault.mkdir(parents=True, exist_ok=True)
    profile_root = _profile_root(payload, profile)
    template_dir = profile_root / "知识库模板"
    created_files = 0
    skipped_files = 0
    for item in sorted(template_dir.rglob("*")):
        rel = item.relative_to(template_dir)
        dest = vault / rel
        if item.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        if dest.exists():
            skipped_files += 1
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, dest)
        created_files += 1
    log("✅ 目录框架已复制（新建 %d 个文件，跳过已存在 %d 个，零覆盖）"
        % (created_files, skipped_files))
    community = vault / ".obsidian" / "community-plugins.json"
    ids = json.loads(community.read_text(encoding="utf-8")) if community.is_file() else []
    changed = False
    for pid in WINDOWS_PLUGIN_IDS:
        if pid not in ids:
            ids.append(pid)
            changed = True
    if changed:
        community.parent.mkdir(parents=True, exist_ok=True)
        community.write_text(json.dumps(ids, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log("✅ 两个插件已加入启用列表（合并，不丢已有项）")
    rules_dir = vault / "90 系统" / "92 维护规范"
    for name in ("taxonomy.json", "tag_policy.json"):
        source = profile_root / "配置" / name
        dest = rules_dir / name
        if source.is_file() and not dest.exists():
            rules_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            log("✅ %s 已放进 Vault 规范目录（%s）" % (name, dest))
    taxonomy_path = profile_root / "配置" / "taxonomy.json"
    if not taxonomy_path.is_file():
        raise DeployError("发布包缺少目录清单 taxonomy.json，请重新下载完整安装包。")
    try:
        taxonomy_paths = json.loads(taxonomy_path.read_text(encoding="utf-8"))["paths"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise DeployError("发布包目录清单无效：%s" % exc) from exc
    for relative in taxonomy_paths:
        (vault / relative).mkdir(parents=True, exist_ok=True)
    log("✅ 已按模板目录清单创建 %d 个目录" % len(taxonomy_paths))
    workspace.mkdir(parents=True, exist_ok=True)
    for name in ("维护程序", "Agent作业模板"):
        source = payload / name
        if source.is_dir():
            copy_tree_merge(source, workspace / name)
    log("✅ 维护程序与作业模板已放入工作目录: %s" % workspace)
    config = {
        "version": 1,
        "vault_path": str(vault),
        "run_hours": run_hours,
        "stable_file_minutes": 10,
        "source_retention_days": 7,
        "batch_max_items": 10,
        "batch_max_chars": 80000,
        "model_preference": choice_label,
        "providers": {name: _windows_provider_config(workspace, name, args.custom_provider)
                      for name in order},
        "order": order,
        "state_dir": str(workspace / ".state"),
    }
    (workspace / "部署配置.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, key in getattr(args, "windows_keys", {}).items():
        _store_windows_key(key, Path(config["providers"][name]["key_file"]), runner)
    if getattr(args, "windows_keys", {}):
        log("✅ API 密钥已用 Windows DPAPI 加密保存（仅当前 Windows 用户可读取）")
    scheduled = False
    if not args.skip_schedule:
        py_exe = sys.executable
        runner_script = workspace / "维护程序" / "windows_maintenance.py"
        config_path = workspace / "部署配置.json"
        script = ("$action = New-ScheduledTaskAction -Execute '%s' -Argument '\"%s\" \"%s\" run';"
                  "$triggers = @(%s) | ForEach-Object { New-ScheduledTaskTrigger -Daily -At \"$($_):00\" };"
                  "Register-ScheduledTask -TaskName 'obsidian_maintenance' -Action $action "
                  "-Trigger $triggers -Description 'Obsidian Knowledge Vault 定时整理（本机时区 %s 点）' -Force"
                  % (py_exe.replace("'", "''"), str(runner_script).replace("'", "''"),
                     str(config_path).replace("'", "''"), ",".join(str(h) for h in run_hours),
                     "/".join("%02d" % h for h in run_hours)))
        proc = runner.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script])
        if proc.returncode != 0:
            raise DeployError("注册定时任务失败: %s" % proc.stderr[:500])
        query = runner.run(["schtasks", "/query", "/tn", "obsidian_maintenance", "/fo", "LIST"])
        if "obsidian_maintenance" not in query.stdout:
            raise DeployError("定时任务注册后未能查到 obsidian_maintenance，请检查任务计划程序。")
        scheduled = True
        log("✅ 定时任务 obsidian_maintenance 已注册（%s 点、每天 %d 次、本机时区、不补跑）"
            % ("/".join("%02d" % h for h in run_hours), len(run_hours)))
    manifest = {
        "version": VERSION, "platform": "windows", "template": profile,
        "installed_at": datetime.now().isoformat(timespec="seconds"),
        "vault": str(vault), "workspace": str(workspace), "model": choice_label,
        "scheduled": scheduled, "created_files": created_files, "skipped_files": skipped_files,
    }
    return {"vault": vault, "workspace": workspace, "manifest": manifest,
            "scheduled": scheduled, "config": config, "template": profile}


# ---------------------------------------------------------------- 验收自测（Windows）


def windows_acceptance(runner: Runner, info: dict) -> List[str]:
    problems: List[str] = []
    vault = info["vault"]
    workspace = info["workspace"]
    log("")
    log("---- 验收自测 ----")
    for name in WINDOWS_TOP_DIRS:
        ok = (vault / name).is_dir()
        log("✅ 目录 %s" % name if ok else "❌ 目录 %s 缺失" % name)
        if not ok:
            problems.append("目录 %s 缺失" % name)
    ok = (vault / "90 系统" / "94 维护记录" / "94.6 清理记录").is_dir()
    log("✅ 94.6 清理记录" if ok else "❌ 94.6 清理记录缺失")
    if not ok:
        problems.append("94.6 清理记录缺失")
    rules_dir = vault / "90 系统" / "92 维护规范"
    for name in ("taxonomy.json", "tag_policy.json"):
        ok = (rules_dir / name).is_file()
        log("✅ %s 在 Vault 内" % name if ok else "❌ %s 不在 Vault 内" % name)
        if not ok:
            problems.append("%s 不在 Vault 内" % name)
    community = vault / ".obsidian" / "community-plugins.json"
    if community.is_file():
        ids = json.loads(community.read_text(encoding="utf-8"))
        for pid in WINDOWS_PLUGIN_IDS:
            ok = pid in ids
            log("✅ 插件 %s 已启用" % pid if ok else "❌ 插件 %s 未启用" % pid)
            if not ok:
                problems.append("插件 %s 未启用" % pid)
    else:
        log("❌ community-plugins.json 缺失")
        problems.append("community-plugins.json 缺失")
    plugin_dir = vault / ".obsidian" / "plugins" / "obsidian-auto-organizer"
    for name in ("manifest.json", "main.js", "rules.js", "data.json"):
        ok = (plugin_dir / name).is_file()
        log("✅ 本地插件文件 %s" % name if ok else "❌ 本地插件文件 %s 缺失" % name)
        if not ok:
            problems.append("本地插件文件 %s 缺失" % name)
    ok = (vault / ".obsidian" / "plugins" / "frontmatter-modified-date" / "data.json").is_file()
    log("✅ Update modified date 设置" if ok else "❌ Update modified date 设置缺失")
    if not ok:
        problems.append("Update modified date 设置缺失")
    app = vault / ".obsidian" / "app.json"
    if app.is_file():
        data = json.loads(app.read_text(encoding="utf-8"))
        ok = data.get("newFileFolderPath") == "00 收件箱"
        log("✅ 新文件位置为 00 收件箱" if ok else "❌ 新文件位置不是 00 收件箱")
        if not ok:
            problems.append("新文件位置不是 00 收件箱")
    else:
        log("❌ app.json 缺失")
        problems.append("app.json 缺失")
    ok = (workspace / "部署配置.json").is_file()
    log("✅ 维护程序配置存在" if ok else "❌ 维护程序配置缺失")
    if not ok:
        problems.append("维护程序配置缺失")
    if info.get("scheduled"):
        query = runner.run(["schtasks", "/query", "/tn", "obsidian_maintenance", "/fo", "LIST"])
        ok = "obsidian_maintenance" in query.stdout
        log("✅ 定时任务已注册且可查询" if ok else "❌ 定时任务查询失败")
        if not ok:
            problems.append("定时任务查询失败")
    name = acceptance_temp_note(vault)
    log("✅ 临时验收笔记创建/读取/删除（已删除：%s）" % name)
    log("")
    log("GUI 验收步骤（由部署 Agent 补做；无法操作 GUI 时如实说明未验证）：")
    log("  1. 打开 Obsidian →「打开本地 Vault」→ 选择目录：%s" % vault)
    log("  2. 新建一篇笔记，确认落入「00 收件箱」，属性面板出现 created/modified/kind/note/workflow/inbox")
    log("  3. 验证后删除该测试笔记")
    return problems


# ---------------------------------------------------------------- 清单 / 卸载 / 入口


def write_manifest(base_dir: Path, manifest: dict) -> None:
    deploy_dir = base_dir / ".deploy"
    deploy_dir.mkdir(parents=True, exist_ok=True)
    (deploy_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def cmd_uninstall(runner: Runner, platform_name: str, args) -> int:
    if platform_name == "macos":
        project_dir = Path(args.project_dir or (str(Path.home()) + "/Code/Obsidian")).expanduser()
        runner.run(["launchctl", "bootout", "gui/%s/%s" % (os.getuid(), LABEL)])
        plist = Path.home() / "Library" / "LaunchAgents" / ("%s.plist" % LABEL)
        if plist.is_file():
            plist.unlink()
            log("✅ 已移除 LaunchAgent plist: %s" % plist)
        log("ℹ️ 程序文件与 Vault 内容未删除（安全起见）。如需彻底移除请手动删除：%s" % project_dir)
    else:
        runner.run(["schtasks", "/delete", "/tn", "obsidian_maintenance", "/f"])
        log("✅ 已删除定时任务 obsidian_maintenance")
        log("ℹ️ Vault 与维护工作目录未删除（安全起见）。")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Obsidian Knowledge Vault 跨平台一键部署")
    parser.add_argument("--yes", action="store_true", help="跳过授权确认（自动化用）")
    parser.add_argument("--vault", type=str, help="Vault 路径（默认: ~/Documents/Obsidian Knowledge Vault）")
    parser.add_argument("--project-dir", type=str, help="macOS 项目目录（默认 ~/Code/Obsidian）")
    parser.add_argument("--workspace", type=str, help="Windows 维护工作目录（默认 Vault 同级 Obsidian Knowledge Vault Maintenance）")
    parser.add_argument("--template", choices=tuple(TEMPLATE_CHOICES),
                        help="目录模板：general（通用）或 legal（法律）；默认交互选择")
    parser.add_argument("--model", choices=tuple(sorted(set(MACOS_MODEL_CHOICES) | set(WINDOWS_MODEL_CHOICES))),
                        help="模型选择（macOS/Windows 均可用；默认交互询问，--yes 时自动选择）")
    parser.add_argument("--run-hours", type=str,
                        help="每天整理时间（小时，逗号分隔，如 8,12,18；macOS/Windows 均可用）")
    parser.add_argument("--custom-provider", type=str,
                        help="自定义 OpenAI 兼容模型 name=base_url,model（如 kimi=https://api.moonshot.cn/v1,kimi-k2-0711-preview）")
    parser.add_argument("--keychain-service", type=str, help="macOS 钥匙串服务名（默认 knowledge-vault-deepseek）")
    parser.add_argument("--skip-schedule", action="store_true", help="不安装定时任务")
    parser.add_argument("--skip-obsidian", action="store_true", help="跳过 Obsidian 检测/安装")
    parser.add_argument("--task-command", type=str, help="自定义 Windows 定时任务命令（默认用维护脚本）")
    parser.add_argument("--simulate-win32", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--uninstall", action="store_true", help="卸载定时任务")
    args = parser.parse_args(argv)
    runner = Runner(simulate_win32=args.simulate_win32)
    try:
        log("Obsidian Knowledge Vault v%s" % VERSION)
        platform_name = detect_platform(runner)
        log("检测到平台: %s" % ("Windows（演练模式）" if runner.simulate else platform_name))
        base = Path(__file__).resolve().parent
        payload = find_payload(base, platform_name)
        if args.uninstall:
            return cmd_uninstall(runner, platform_name, args)
        if platform_name == "macos":
            warnings = check_macos_prereqs(runner, args)
            args.run_hours = _resolve_run_hours(args)
            order, api_providers, keychain_keys = _resolve_macos_providers(args)
            args.provider_order = order
            args.api_providers = api_providers
            args.keychain_keys = keychain_keys
            args.template = _resolve_template(args)
            _ensure_obsidian(runner, platform_name, args)
            info = macos_install(runner, payload, args)
            problems = macos_acceptance(runner, info, args)
            manifest = {
                "version": VERSION, "platform": "macos",
                "installed_at": datetime.now().isoformat(timespec="seconds"),
                "project_dir": str(info["project_dir"]), "vault": str(info["vault"]),
                "python": info["python"], "scheduled": info["scheduled"],
                "run_hours": args.run_hours, "provider_order": args.provider_order,
                "template": args.template,
            }
            write_manifest(info["project_dir"], manifest)
        else:
            args.run_hours = _parse_run_hours(args.run_hours) if args.run_hours else list(RUN_HOURS)
            args.template = _resolve_template(args)
            warnings = check_windows_prereqs(runner, args)
            model_key = args.model or "openai-first"
            if model_key not in WINDOWS_MODEL_CHOICES:
                raise DeployError("此模型选项不适用于 Windows：%s" % model_key)
            args.windows_keys = _resolve_windows_keys(args, WINDOWS_MODEL_CHOICES[model_key][1])
            _ensure_obsidian(runner, platform_name, args)
            info = windows_install(runner, payload, args)
            problems = windows_acceptance(runner, info)
            write_manifest(info["workspace"], info["manifest"])
        if warnings:
            log("")
            log("⚠️ 非阻断提示（能力降级，不影响部署）：")
            for item in warnings:
                log("  - " + item)
        log("")
        if problems:
            log("❌ 部署未完全通过验收（%d 项）。请按上方 ❌ 项修复后重跑本脚本（幂等）。" % len(problems))
            return 5
        log("✅✅✅ 部署成功：本次选择安装的组件、目录框架、维护程序、插件、模型可用性与验收自测全部通过。")
        return 0
    except DeployError as exc:
        log("❌ " + str(exc))
        return exc.code


if __name__ == "__main__":
    sys.exit(main())
