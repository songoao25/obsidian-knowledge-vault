#!/usr/bin/env python3
"""Windows 端自动化整理脚本（Obsidian Knowledge Vault 的可分发部分）。

由 Windows 任务计划程序在使用者选择的时间点调用；到点后调用已配置的 AI API
（OpenAI 兼容接口）整理收件箱：完整内容保全，碎片才重新组织，只做分类/新建笔记，
原始输入归档 7 天清理。纯标准库，Python 3.8+。

用法:
    python windows_maintenance.py <config.json> run           # 一轮整理
    python windows_maintenance.py <config.json> verify        # 只读验收检查
    python windows_maintenance.py <config.json> check-config  # 校验并打印配置摘要

安全边界（与 Vault 内维护规范一致；本脚本无合并、删除、改写已有笔记的能力）:
    - 只处理 00 收件箱中停止修改 >=10 分钟的资料；空白 Markdown 草稿跳过。
    - 只有 create（新建笔记+归档原件）与 classify（原样搬动，不归档）两种动作。
    - 不覆盖任何已有文件：目标重名自动加序号；写入用临时文件+原子替换。
    - 密钥只经环境变量或 DPAPI 加密文件读取，绝不写入 Vault。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

VERSION = "1.2.1"
RUN_HOURS = (8, 11, 14, 17, 20, 23)
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
WIKILINK_RE = re.compile(r"!\[\[([^\]|#]+)(?:#[^\]]*)?(?:\|[^\]]*)?\]\]")
MDLINK_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
TITLE_BAD = re.compile(r'[\\/:*?"<>|\r\n]')
RECORD_NAME_RE = re.compile(r"\d{4}-\d{2}-\d{2} 整理记录-AI\.md$")


class ConfigError(Exception):
    pass


class AlreadyRunning(Exception):
    pass


def log(msg: str) -> None:
    print("%s %s" % (datetime.now().strftime("%H:%M:%S"), msg), flush=True)


# ---------------------------------------------------------------- 配置与密钥

def load_config(path: Path) -> dict:
    if not path.is_file():
        raise ConfigError("配置文件不存在: %s" % path)
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError("配置不是合法 JSON: %s" % exc)
    for key in ("vault_path", "providers", "order", "state_dir"):
        if key not in cfg:
            raise ConfigError("配置缺少字段: %s" % key)
    run_hours = cfg.get("run_hours", list(RUN_HOURS))
    if (not isinstance(run_hours, list) or not run_hours or len(run_hours) > 24
            or any(not isinstance(hour, int) or hour < 0 or hour > 23 for hour in run_hours)
            or len(set(run_hours)) != len(run_hours)):
        raise ConfigError("run_hours 必须是 1-24 个不重复的 0-23 整数")
    cfg["run_hours"] = sorted(run_hours)
    cfg["vault"] = Path(cfg["vault_path"]).expanduser()
    cfg["state_dir"] = Path(cfg["state_dir"]).expanduser()
    return cfg


def _powershell(script: str) -> str:
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.stdout.strip()


def load_api_key(provider: dict) -> Optional[str]:
    env_name = provider.get("key_env") or ""
    if env_name and os.environ.get(env_name):
        return os.environ[env_name]
    key_file = provider.get("key_file")
    if key_file and Path(key_file).is_file():
        script = (
            "Add-Type -AssemblyName System.Security;"
            "$enc=[IO.File]::ReadAllBytes('%s');"
            "$bytes=[Security.Cryptography.ProtectedData]::Unprotect($enc,$null,"
            "[Security.Cryptography.DataProtectionScope]::CurrentUser);"
            "[Text.Encoding]::UTF8.GetString($bytes)"
            % str(Path(key_file)).replace("'", "''")
        )
        out = _powershell(script)
        if out:
            return out
    return None


def store_api_key_dpapi(plain_key: str, key_file: Path) -> None:
    key_file.parent.mkdir(parents=True, exist_ok=True)
    script = (
        "Add-Type -AssemblyName System.Security;"
        "$bytes=[Text.Encoding]::UTF8.GetBytes('%s');"
        "$enc=[Security.Cryptography.ProtectedData]::Protect($bytes,$null,"
        "[Security.Cryptography.DataProtectionScope]::CurrentUser);"
        "[IO.File]::WriteAllBytes('%s',$enc)"
        % (plain_key.replace("'", "''"), str(key_file).replace("'", "''"))
    )
    _powershell(script)


# ---------------------------------------------------------------- 扫描与提取

def scan_inbox(vault: Path, stable_minutes: int) -> Tuple[List[Tuple[Path, str]], List[Path]]:
    inbox = vault / "00 收件箱"
    items: List[Tuple[Path, str]] = []
    skipped: List[Path] = []
    if not inbox.is_dir():
        return items, skipped
    now = time.time()
    for p in sorted(inbox.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(inbox)
        if any(part.startswith(".") for part in rel.parts) or p.name.startswith("."):
            continue
        if p.name.lower() == ".ds_store":
            continue
        if RECORD_NAME_RE.match(p.name):
            continue
        if now - p.stat().st_mtime < stable_minutes * 60:
            skipped.append(p)
            continue
        if p.suffix.lower() == ".md":
            text = p.read_text(encoding="utf-8", errors="replace")
            m = FRONTMATTER_RE.match(text)
            body = text[m.end():].strip() if m else text.strip()
            if not body:
                continue  # 空白草稿：保持不动，不记失败
            items.append((p, text))
        elif p.suffix.lower() == ".txt":
            items.append((p, p.read_text(encoding="utf-8", errors="replace")))
        elif p.suffix.lower() == ".pdf":
            text = extract_pdf(p)
            if text is None:
                skipped.append(p)
                continue
            items.append((p, text))
        else:
            skipped.append(p)  # 图片/音视频/文档：无可靠本地提取能力，留在收件箱
    return items, skipped


def extract_pdf(path: Path) -> Optional[str]:
    binary = shutil.which("pdftotext")
    if not binary:
        return None
    try:
        proc = subprocess.run(
            [binary, "-layout", str(path), "-"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout


def bundle_items(items: List[Tuple[Path, str]]) -> List[Tuple[Optional[Path], List[Path]]]:
    """Markdown 与其显式引用的附件归为一组；其余文件各自独立。"""
    non_md: List[Tuple[Path, str]] = [i for i in items if i[0].suffix.lower() != ".md"]
    used: set = set()
    groups: List[Tuple[Optional[Path], List[Path]]] = []
    for p, text in [i for i in items if i[0].suffix.lower() == ".md"]:
        refs = set()
        refs.update(m.group(1).strip() for m in WIKILINK_RE.finditer(text))
        refs.update(m.group(1).strip() for m in MDLINK_RE.finditer(text))
        attached: List[Path] = [p]
        for other, _ in non_md:
            if other in used:
                continue
            base = other.name
            stem = other.stem
            if base in refs or stem in refs or any(
                base == r or stem == r or Path(r).name in (base, stem) for r in refs
            ):
                attached.append(other)
                used.add(other)
        groups.append((p, attached))
    for other, _ in non_md:
        if other not in used:
            groups.append((None, [other]))
    return groups


# ---------------------------------------------------------------- 提示词与模型调用

SYSTEM_PROMPT_HEAD = """你是“新知识库”的整理助手，只输出一个 JSON 对象，不要输出任何其他文字。

你的任务：把使用者放进 00 收件箱的资料分类到合适目录（classify），或把明显碎片组织成一篇干净笔记（create）。所有写入都由本地程序按你的方案执行，你无权直接修改文件。

【维护规范（最高规则）】
{rules}

【允许的目标目录（唯一白名单）】
{paths}

【允许的标签（唯一词表）】
{allowed_tags}

【输出 JSON 结构】
{{"items": [{{"source": "收件箱内文件相对路径", "action": "create|classify", "target_dir": "必须取自上列目录", "title": "仅 create 必填，不含路径分隔符", "tags": ["kind/...", ...], "body": "仅 create 必填，Markdown 正文"}}]}}

【硬约束】
1. 完整网页文章、个人长分析、其他完整资料：action 只能为 classify，原样保留，绝不重写正文。
2. 只有明显碎片才 create；create 的 body 必须完整保留人名、地名、时间、金额、数字、条件、例外、URL、链接、图片与附件引用；正文禁止一级标题（用二级标题表达结构）。
3. 非 Markdown 文件（图片/PDF/音视频/文档）：action 只能为 classify，title/body 留空。
4. 已成稿的 Markdown：classify。空白草稿不要出现在 items 里。
5. 每篇恰好一个 kind/ 标签；tags 只能取上列值。
6. 无法确定位置的内容不要放进 items（留在收件箱）。
7. 不编造来源、事实、经历、情绪、趋势、法律结论、医疗结论或投资建议。
"""


def build_system_prompt(cfg: dict) -> str:
    rule_dir = cfg["vault"] / "90 系统" / "92 维护规范"
    parts: List[str] = []
    for name in ("Agent 维护规范.md", "Windows Agent 部署规范.md", "分类与归档规范.md",
                 "标签与属性规范.md", "标签词表.md", "笔记整理与写作规范.md"):
        path = rule_dir / name
        if path.is_file():
            parts.append("## %s\n%s" % (name, path.read_text(encoding="utf-8")))
    paths = json.loads((rule_dir / "taxonomy.json").read_text(encoding="utf-8"))["paths"]
    allowed_tags = json.loads((rule_dir / "tag_policy.json").read_text(encoding="utf-8"))["allowed_tags"]
    return SYSTEM_PROMPT_HEAD.format(
        rules="\n\n".join(parts) or "（Vault 内维护规范缺失；从严处理：无法确定就保留在收件箱）",
        paths="\n".join(paths),
        allowed_tags=", ".join(allowed_tags),
    )


class ApiError(Exception):
    pass


def call_api(provider: dict, system_prompt: str, user_prompt: str) -> dict:
    key = load_api_key(provider)
    if not key:
        raise ApiError("provider %s 没有可用密钥（key_env/key_file 均未配置）" % provider.get("name", "?"))
    url = provider["base_url"].rstrip("/") + "/chat/completions"
    payload = {
        "model": provider["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
    }
    if provider.get("json_mode", True):
        payload["response_format"] = {"type": "json_object"}
    else:
        payload["messages"][0]["content"] += "\n\n必须只输出一个 JSON 对象。"
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=provider.get("timeout", 180)) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise ApiError("HTTP %s: %s" % (exc.code, detail))
    except urllib.error.URLError as exc:
        raise ApiError("网络错误: %s" % exc.reason)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
        if not match:
            raise ApiError("模型返回的不是 JSON")
        data = json.loads(match.group(1))
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", content, re.DOTALL)
        if not match:
            raise ApiError("模型正文不是 JSON")
        parsed = json.loads(match.group(1))
    if not isinstance(parsed, dict) or not isinstance(parsed.get("items"), list):
        raise ApiError("模型 JSON 缺少 items 列表")
    return parsed


def build_user_prompt(groups, cfg: dict) -> str:
    inbox = cfg["vault"] / "00 收件箱"
    parts = ["以下是本轮收件箱中的资料（编号后接文件相对路径与内容）："]
    used = 0
    budget = int(cfg.get("batch_max_chars", 80000))
    for counter, (primary, sources) in enumerate(groups, 1):
        block = ["", "[%d] 文件：" % counter]
        for src in sources:
            block.append("- %s" % src.relative_to(inbox))
        if primary is not None:
            block.append("内容：")
            block.append(primary.read_text(encoding="utf-8", errors="replace")[:30000])
        else:
            for src in sources:
                if src.suffix.lower() == ".txt":
                    block.append("内容：")
                    block.append(src.read_text(encoding="utf-8", errors="replace")[:8000])
                elif src.suffix.lower() == ".pdf":
                    text = extract_pdf(src)
                    if text:
                        block.append("内容：")
                        block.append(text[:8000])
        text = "\n".join(block)
        if used + len(text) > budget:
            break
        parts.append(text)
        used += len(text)
    parts.append("")
    parts.append("请输出 JSON 方案。")
    return "\n".join(parts)


def validate_plan(plan: dict, cfg: dict, groups) -> List[str]:
    errors: List[str] = []
    paths = set(cfg["paths"])
    allowed_tags = set(cfg["allowed_tags"])
    inbox = cfg["vault"] / "00 收件箱"
    sources = {source_key(g, inbox) for g in groups}
    for index, item in enumerate(plan.get("items", [])):
        if not isinstance(item, dict):
            errors.append("items[%d]: 不是对象" % index)
            continue
        source = item.get("source", "")
        if source not in sources:
            errors.append("items[%d]: source '%s' 不在本轮收件箱清单中" % (index, source))
        action = item.get("action")
        if action not in ("create", "classify"):
            errors.append("items[%d]: action 必须为 create 或 classify" % index)
        target = item.get("target_dir", "")
        if target not in paths:
            errors.append("items[%d]: target_dir '%s' 不在允许目录中" % (index, target))
        tags = item.get("tags") or []
        if not isinstance(tags, list) or any(tag not in allowed_tags for tag in tags):
            errors.append("items[%d]: tags 只能取允许词表" % index)
        if len([t for t in tags if t.startswith("kind/")]) != 1:
            errors.append("items[%d]: 必须恰好一个 kind/ 标签" % index)
        if action == "create":
            title = (item.get("title") or "").strip()
            if not title:
                errors.append("items[%d]: create 必须有 title" % index)
            if "/" in title or "\\" in title:
                errors.append("items[%d]: title 不能含路径分隔符" % index)
            body = item.get("body") or ""
            if not body.strip():
                errors.append("items[%d]: create 必须有非空 body" % index)
            if re.search(r"(?m)^# ", body):
                errors.append("items[%d]: body 禁止一级标题" % index)
    return errors


# ---------------------------------------------------------------- 属性与文件工具


def parse_simple_frontmatter(block: str) -> dict:
    data: dict = {}
    current: Optional[str] = None
    for line in block.splitlines():
        if not line.strip():
            continue
        if line[0] in (" ", "\t"):
            if current is None:
                continue
            value = line.strip().lstrip("- ").strip().strip('"\'')
            if current not in data:
                data[current] = [value]
            elif isinstance(data[current], list):
                data[current].append(value)
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                data[key] = [v.strip().strip('"\'') for v in value[1:-1].split(",") if v.strip()]
            else:
                data[key] = value.strip('"\'')
            current = key
    return data


def build_frontmatter(fields: dict) -> str:
    lines = ["---"]
    for key, value in fields.items():
        if isinstance(value, list):
            lines.append("%s:" % key)
            for item in value:
                lines.append("  - %s" % item)
        else:
            lines.append("%s: %s" % (key, value))
    lines.append("---")
    return "\n".join(lines) + "\n"


def unique_path(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    counter = 2
    while target.exists():
        stem = Path(name).stem
        target = directory / ("%s (%d)%s" % (stem, counter, Path(name).suffix))
        counter += 1
    return target


def safe_move(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(source, target)
    except OSError:
        shutil.move(str(source), str(target))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rewrite_links(body: str, attach_names: set, attach_dir_name: str) -> str:
    def repl(match: re.Match) -> str:
        link = match.group(1)
        if Path(link).name in attach_names:
            return "![](%s/%s)" % (attach_dir_name, Path(link).name)
        return match.group(0)

    return MDLINK_RE.sub(repl, body)


def source_key(group, inbox: Path) -> str:
    primary = group[0]
    if primary is not None:
        return str(primary.relative_to(inbox))
    return str(group[1][0].relative_to(inbox))


# ---------------------------------------------------------------- 执行（create / classify）


def do_create(cfg: dict, item: dict, group, now: datetime) -> dict:
    vault = cfg["vault"]
    inbox = vault / "00 收件箱"
    primary = group[0]
    attachments = [s for s in group[1] if s != primary]
    target_dir = vault / item["target_dir"]
    title = TITLE_BAD.sub("", (item.get("title") or "").strip()) or "未命名"
    tags = item.get("tags") or []
    body = (item.get("body") or "").strip()
    created = now.strftime("%Y-%m-%dT%H:%M:%S")
    if primary is not None and primary.suffix.lower() == ".md":
        match = FRONTMATTER_RE.match(primary.read_text(encoding="utf-8", errors="replace"))
        if match:
            fm = parse_simple_frontmatter(match.group(1))
            if fm.get("created"):
                created = str(fm["created"])
    note_path = unique_path(target_dir, "%s %s.md" % (now.strftime("%Y-%m-%d"), title))
    attach_dir_name = "%s-附件" % note_path.stem
    attach_dir = note_path.parent / attach_dir_name
    attach_names = {a.name for a in attachments}
    body = rewrite_links(body, attach_names, attach_dir_name)
    content = build_frontmatter({
        "created": created,
        "modified": now.strftime("%Y-%m-%dT%H:%M:%S"),
        "tags": tags,
    }) + "\n" + body.rstrip() + "\n"
    note_path.write_text(content, encoding="utf-8")
    moved = []
    for att in attachments:
        dest = unique_path(attach_dir, att.name)
        safe_move(att, dest)
        moved.append(dest.relative_to(vault))
    archived = []
    bundle_id = now.strftime("%Y%m%d-%H%M%S-") + hashlib.sha256(str(note_path).encode()).hexdigest()[:8]
    if primary is not None:
        day = vault / "95 原始输入归档" / now.strftime("%Y") / now.strftime("%m") / now.strftime("%d")
        dest = unique_path(day, primary.name)
        text = primary.read_text(encoding="utf-8", errors="replace")
        match = FRONTMATTER_RE.match(text)
        if match:
            fm = parse_simple_frontmatter(match.group(1))
            fm["no_delete"] = False
            text = build_frontmatter(fm) + text[match.end():]
        else:
            text = build_frontmatter({"no_delete": False}) + text
        dest.write_text(text, encoding="utf-8")
        primary.unlink()
        archived.append({"name": primary.name, "sha256": sha256_file(dest),
                         "archived_to": str(dest.relative_to(vault))})
    pres = vault / "95 原始输入归档" / ".preservation"
    pres.mkdir(parents=True, exist_ok=True)
    manifest = {
        "bundle_id": bundle_id,
        "note": str(note_path.relative_to(vault)),
        "archived_at": now.isoformat(timespec="seconds"),
        "sources": archived,
        "attachments": [str(m) for m in moved],
    }
    (pres / ("%s.json" % bundle_id)).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"action": "create", "source": source_key(group, inbox),
            "title": title, "target": item["target_dir"]}


def do_classify(cfg: dict, item: dict, group, now: datetime) -> dict:
    vault = cfg["vault"]
    inbox = vault / "00 收件箱"
    primary = group[0]
    target_dir = vault / item["target_dir"]
    moved = []
    for src in group[1]:
        rel = src.relative_to(inbox)
        if src == primary and primary.suffix.lower() == ".md" and (item.get("title") or "").strip():
            title = TITLE_BAD.sub("", item["title"].strip())
            dest = unique_path(target_dir / rel.parent, "%s %s.md" % (now.strftime("%Y-%m-%d"), title))
        else:
            dest = unique_path(target_dir / rel.parent, rel.name)
        safe_move(src, dest)
        moved.append(str(dest.relative_to(vault)))
    return {"action": "classify", "source": source_key(group, inbox),
            "target": item["target_dir"], "moved": moved}


# ---------------------------------------------------------------- 整理记录与归档


def append_record(cfg: dict, results: List[dict], skipped: List[Path],
                  error_text: Optional[str], now: datetime) -> None:
    vault = cfg["vault"]
    inbox = vault / "00 收件箱"
    rec = inbox / ("%s 整理记录-AI.md" % now.strftime("%Y-%m-%d"))
    stamp = now.strftime("%Y-%m-%dT%H:%M:%S")
    if rec.exists():
        text = rec.read_text(encoding="utf-8").rstrip()
    else:
        text = build_frontmatter({
            "created": stamp, "modified": stamp,
            "tags": ["kind/maintenance-log", "topic/automation",
                     "project/knowledge-vault", "workflow/inbox"],
        }).rstrip()
    lines = [text, "", "## %s" % now.strftime("%H:%M")]
    if results:
        lines.append("已整理：")
        for index, result in enumerate(results, 1):
            if result["action"] == "create":
                lines.append("%d. 《%s》已整理为《%s》，放入「%s」"
                             % (index, result["source"], result["title"], result["target"]))
            else:
                lines.append("%d. 《%s》已分类放入「%s」" % (index, result["source"], result["target"]))
    if skipped:
        lines.append("暂未整理：")
        lines.append("、".join("《%s》" % p.name for p in skipped)
                     + "（未稳定、无法提取或无法确定位置，原件仍在收件箱）")
    if error_text and not results:
        lines.append("其他情况：")
        lines.append("模型不可用，资料保持不动。")
    rec.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    if now.hour == 23:
        text = rec.read_text(encoding="utf-8")
        match = FRONTMATTER_RE.match(text)
        if match:
            fm = parse_simple_frontmatter(match.group(1))
            tags = [t for t in (fm.get("tags") or []) if t != "workflow/inbox"]
            fm["tags"] = tags
            fm["modified"] = now.strftime("%Y-%m-%dT%H:%M:%S")
            text = build_frontmatter(fm) + text[match.end():]
        day = vault / "90 系统" / "94 维护记录" / "94.1 整理记录" / now.strftime("%Y") / now.strftime("%m")
        dest = unique_path(day, rec.name)
        dest.write_text(text, encoding="utf-8")
        rec.unlink()


# ---------------------------------------------------------------- 7 天清理


def recycle_file(path: Path) -> bool:
    script = ("Add-Type -AssemblyName Microsoft.VisualBasic;"
              "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile('%s',"
              "'OnlyErrorDialogs','SendToRecycleBin')"
              % str(path).replace("'", "''"))
    proc = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc.returncode == 0


def run_retention(cfg: dict, now: datetime) -> None:
    vault = cfg["vault"]
    pres = vault / "95 原始输入归档" / ".preservation"
    if not pres.is_dir():
        return
    cutoff = now - timedelta(days=int(cfg.get("source_retention_days", 7)))
    recycled: List[str] = []
    kept: List[str] = []
    for manifest_path in sorted(pres.glob("*.json")):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        try:
            archived_at = datetime.fromisoformat(data.get("archived_at", ""))
        except ValueError:
            continue
        if archived_at > cutoff or data.get("recycled_at"):
            continue
        for src in data.get("sources", []):
            path = vault / src.get("archived_to", "")
            if not path.is_file():
                continue
            if path.suffix.lower() == ".md":
                match = FRONTMATTER_RE.match(path.read_text(encoding="utf-8", errors="replace"))
                if match and parse_simple_frontmatter(match.group(1)).get("no_delete") is True:
                    kept.append(path.name)
                    continue
            if recycle_file(path):
                path.unlink(missing_ok=True)
                recycled.append(path.name)
        data["recycled_at"] = now.isoformat(timespec="seconds")
        manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if recycled or kept:
        day_dir = vault / "90 系统" / "94 维护记录" / "94.6 清理记录"
        day_dir.mkdir(parents=True, exist_ok=True)
        rec = day_dir / ("%s.md" % now.strftime("%Y-%m-%d"))
        lines = [rec.read_text(encoding="utf-8").rstrip()] if rec.exists() else []
        lines.append("## %s" % now.strftime("%H:%M"))
        if recycled:
            lines.append("已自动定期清理到期归档原件 %d 份，并移入系统废纸篓。" % len(recycled))
        if kept:
            lines.append("保留（本人已勾选 no_delete）：%s。" % "、".join(kept))
        rec.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 锁 / 验收 / 入口


class RunLock:
    def __init__(self, state_dir: Path):
        self.path = state_dir / "run.lock"
        self.fd: Optional[int] = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.time() - self.path.stat().st_mtime > 6 * 3600:
                self.path.unlink(missing_ok=True)
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            else:
                raise AlreadyRunning("上一轮整理仍在运行或未正常退出（锁文件存在）")
        return self

    def __exit__(self, *exc_info) -> None:
        if self.fd is not None:
            os.close(self.fd)
        self.path.unlink(missing_ok=True)


def verify(cfg: dict) -> bool:
    checks: List[Tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    vault = cfg["vault"]
    check("Vault 存在", vault.is_dir(), str(vault))
    for name in ("00 收件箱", "10 生活", "20 工作", "30 学习", "40 记录", "90 系统"):
        check("目录 %s" % name, (vault / name).is_dir())
    check("94.6 清理记录", (vault / "90 系统/94 维护记录/94.6 清理记录").is_dir())
    rule_dir = vault / "90 系统" / "92 维护规范"
    check("taxonomy.json 在 Vault 内", (rule_dir / "taxonomy.json").is_file())
    check("tag_policy.json 在 Vault 内", (rule_dir / "tag_policy.json").is_file())
    community = vault / ".obsidian" / "community-plugins.json"
    if community.is_file():
        ids = json.loads(community.read_text(encoding="utf-8"))
        check("插件 frontmatter-modified-date 已启用", "frontmatter-modified-date" in ids)
        check("插件 obsidian-auto-organizer 已启用", "obsidian-auto-organizer" in ids)
    else:
        check("community-plugins.json 存在", False)
        check("插件 frontmatter-modified-date 已启用", False)
        check("插件 obsidian-auto-organizer 已启用", False)
    plugin_dir = vault / ".obsidian" / "plugins" / "obsidian-auto-organizer"
    for name in ("manifest.json", "main.js", "rules.js", "data.json"):
        check("本地插件文件 %s" % name, (plugin_dir / name).is_file())
    check("Update modified date 设置",
          (vault / ".obsidian/plugins/frontmatter-modified-date/data.json").is_file())
    app = vault / ".obsidian" / "app.json"
    if app.is_file():
        data = json.loads(app.read_text(encoding="utf-8"))
        check("新文件位置为 00 收件箱", data.get("newFileFolderPath") == "00 收件箱")
    else:
        check("app.json 存在", False)
    ok = all(item[1] for item in checks)
    for name, passed, detail in checks:
        print(("✅" if passed else "❌") + " " + name + (("（%s）" % detail) if detail and not passed else ""))
    return ok


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Windows 端自动化整理")
    parser.add_argument("config", type=Path)
    parser.add_argument("mode", choices=("run", "verify", "check-config"))
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
        rule_dir = cfg["vault"] / "90 系统" / "92 维护规范"
        cfg["paths"] = json.loads((rule_dir / "taxonomy.json").read_text(encoding="utf-8"))["paths"]
        cfg["allowed_tags"] = json.loads((rule_dir / "tag_policy.json").read_text(encoding="utf-8"))["allowed_tags"]
    except (ConfigError, OSError, json.JSONDecodeError) as exc:
        print("❌ 配置或 Vault 检查失败: %s" % exc)
        return 2
    if args.mode == "check-config":
        print("✅ 配置结构合法（密钥不显示）。")
        return 0
    if args.mode == "verify":
        return 0 if verify(cfg) else 5
    state = cfg["state_dir"]
    state.mkdir(parents=True, exist_ok=True)
    log_line: dict = {"ts": datetime.now().isoformat(timespec="seconds"), "mode": "run"}
    try:
        with RunLock(state):
            now = datetime.now()
            items, skipped = scan_inbox(cfg["vault"], int(cfg.get("stable_file_minutes", 10)))
            if not items:
                log("没有待处理资料。")
                log_line.update({"items": 0, "processed": 0})
                return 0
            groups = bundle_items(items)
            plan = None
            last_error = None
            provider_used = None
            for name in cfg["order"]:
                provider = cfg["providers"].get(name)
                if not provider:
                    continue
                try:
                    plan = call_api(provider, build_system_prompt(cfg), build_user_prompt(groups, cfg))
                    provider_used = name
                    break
                except ApiError as exc:
                    last_error = "%s: %s" % (name, exc)
                    log("⚠️ provider %s 失败: %s" % (name, exc))
            if plan is None:
                log("❌ 所有已配置模型都不可用，资料保持不动。")
                log_line.update({"items": len(items), "processed": 0, "error": last_error})
                return 3
            errors = validate_plan(plan, cfg, groups)
            if errors:
                log("❌ 模型方案未通过本地校验，资料保持不动。")
                for error in errors:
                    log(" - " + error)
                log_line.update({"items": len(items), "processed": 0, "validation_errors": errors[:5]})
                return 4
            inbox = cfg["vault"] / "00 收件箱"
            source_map = {source_key(g, inbox): g for g in groups}
            results: List[dict] = []
            for item in plan["items"]:
                group = source_map.get(item.get("source", ""))
                if group is None:
                    continue
                try:
                    if item["action"] == "create":
                        results.append(do_create(cfg, item, group, now))
                    else:
                        results.append(do_classify(cfg, item, group, now))
                except OSError as exc:
                    log("⚠️ 写入失败（原件保留在收件箱）: %s: %s" % (item.get("source", ""), exc))
            if results or skipped or (plan is None and last_error):
                append_record(cfg, results, skipped, last_error, now)
            run_retention(cfg, now)
            log("✅ 本轮完成：处理 %d 项，收件箱保留 %d 项。" % (len(results), len(skipped)))
            log_line.update({"items": len(items), "processed": len(results),
                             "skipped": len(skipped), "provider": provider_used})
            return 0
    except AlreadyRunning as exc:
        log("ℹ️ " + str(exc))
        log_line.update({"skipped": "already-running"})
        return 0
    finally:
        with (state / "run.log").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(log_line, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    sys.exit(main())
