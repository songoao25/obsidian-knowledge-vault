#!/usr/bin/env python3
"""隐私泄漏扫描器 —— 打包门禁 / pre-commit / CI 共用的唯一实现。

纯标准库，Python 3.8+ 可运行。

用法:
    python3 scan_privacy.py --root <DIR> [--scope public|local] [--json out.json] [--md out.md]

规则:
    1. 名称规则（文件名或任何路径段）: 禁止 .state、__pycache__、.DS_Store、
       .workbuddy 等目录，禁止 *.pyc *.pyo *.log *.sqlite* *.db 等后缀。
    2. 内容规则:
       - BLOCK（密钥类，local 与 public 范围都拦）: API 密钥、GitHub/Slack/AWS
         令牌、私钥块、长 base64 JWT 形 token。
       - PRIVATE（身份/本机痕迹，仅 public 范围拦）: songsong、com.songsong、
         /Users/、/Volumes/、iCloud~md~obsidian、obsidian-knowledge-deepseek、
         内网 IP、MAC 地址。
    3. 白名单（allowlist）: (相对路径 glob, 正则) 同时命中才豁免，必须给出理由。
    4. zip 归档会被解包扫描其成员名与文本内容。

退出码: 0 = 干净；1 = 有命中；2 = 用法错误。
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

FORBIDDEN_PARTS = (".state", "__pycache__", ".DS_Store", ".workbuddy", ".venv")
FORBIDDEN_SUFFIXES = (".pyc", ".pyo", ".log", ".sqlite", ".sqlite3", ".db",
                      ".dylib", ".so", ".tmp", ".bak", ".env", ".pem", ".key")

# 密钥类：任何范围都拦截。
SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9]{16,}"), "疑似 OpenAI/第三方 API 密钥 (sk-*)"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "疑似 GitHub 令牌"),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), "疑似 Slack 令牌 (xox*)"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "疑似 AWS Access Key"),
    (re.compile(r"AIza[0-9A-Za-z_-]{30,}"), "疑似 Google API Key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "疑似私钥文件内容"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"), "疑似 JWT/长签名令牌"),
    (re.compile(r"bearer\s+[A-Za-z0-9._-]{24,}", re.IGNORECASE), "疑似 Bearer 令牌"),
    (re.compile(r"(?:api[_-]?key|token|secret)\s*[:=]\s*[\"'][A-Za-z0-9._-]{16,}[\"']", re.IGNORECASE),
     "疑似硬编码密钥或令牌赋值"),
]

# 身份/本机痕迹：public 范围拦截（发布件不允许出现）。
PRIVATE_PATTERNS = [
    (re.compile(r"songsong", re.IGNORECASE), "用户名/标识 songsong"),
    (re.compile(r"com\.songsong", re.IGNORECASE), "反向域名 com.songsong"),
    (re.compile(r"/Users/"), "macOS 用户绝对路径 /Users/"),
    (re.compile(r"/Volumes/"), "macOS 卷路径 /Volumes/"),
    (re.compile(r"iCloud~md~obsidian"), "iCloud Obsidian 容器路径"),
    (re.compile(r"obsidian-knowledge-deepseek"), "钥匙串服务名"),
    (re.compile(r"\b192\.168\.\d{1,3}\.\d{1,3}\b"), "内网 IP 192.168.x.x"),
    (re.compile(r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "内网 IP 10.x.x.x"),
    (re.compile(r"\b(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}\b"), "MAC 地址"),
    (re.compile(r"(?:本人已允许|本人明确要求|经本人批准|经本人授权)"), "原维护者个人授权或决定"),
]

# 白名单: (fnmatch 相对路径, 正则, 理由)
ALLOWLIST: List[Tuple[str, re.Pattern, str]] = []

# 预置白名单：iCloud 容器格式串在 macOS 程序里是探测 iCloud 布局所需的功能字符串，
# 非真实个人路径。仅豁免测试夹具中构造 iCloud 路径的用例，其余仍拦截。
_PRESET_ALLOW = {
    (re.compile("/Users/"), "macOS 用户绝对路径 /Users/"): (
        "*测试/test_scanner.py", "测试夹具假路径，非真实本机信息"
    ),
    (re.compile(r"iCloud~md~obsidian"), "iCloud Obsidian 容器路径"): (
        "*测试/test_scanner.py", "测试夹具中构造 iCloud 容器格式串的功能用例，非真实个人路径"
    ),
}


def allowlist_for(rule: str) -> Optional[Tuple[str, str]]:
    for (pat, r), (glob, reason) in _PRESET_ALLOW.items():
        if r == rule:
            return (glob, reason)
    return None


@dataclass
class Hit:
    path: str          # 相对路径（zip 内为 zip!/member）
    kind: str          # "name" | "secret" | "private"
    rule: str          # 规则描述
    match: str         # 命中的原文片段
    line: int = 0

    def to_dict(self) -> dict:
        return {"path": self.path, "kind": self.kind, "rule": self.rule,
                "match": self.match, "line": self.line}


@dataclass
class Report:
    root: str
    scope: str
    scanned_files: int = 0
    scanned_zips: int = 0
    hits: List[Hit] = field(default_factory=list)
    started: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def clean(self) -> bool:
        return not self.hits

    def to_dict(self) -> dict:
        return {
            "root": self.root, "scope": self.scope,
            "scanned_files": self.scanned_files, "scanned_zips": self.scanned_zips,
            "hits": [h.to_dict() for h in self.hits], "clean": self.clean,
            "started": self.started,
        }


def _is_allowed(rel_path: str, pattern: re.Pattern) -> bool:
    for glob, regex, _reason in ALLOWLIST:
        if fnmatch.fnmatch(rel_path, glob) and regex.search(rel_path):
            if regex.pattern == pattern.pattern:
                return True
    return False


def _scan_text(rel_path: str, text: str, scope: str, hits: List[Hit]) -> None:
    patterns: List[Tuple[re.Pattern, str, str]] = [
        (p, d, "secret") for p, d in SECRET_PATTERNS
    ]
    if scope == "public":
        patterns += [(p, d, "private") for p, d in PRIVATE_PATTERNS]
    for line_no, line in enumerate(text.splitlines(), 1):
        for pattern, desc, kind in patterns:
            m = pattern.search(line)
            if not m:
                continue
            preset = allowlist_for(desc)
            if preset and fnmatch.fnmatch(rel_path, preset[0]):
                continue  # 预置白名单：测试夹具假路径/功能 iCloud 格式串，非真实信息
            if not _is_allowed(rel_path, pattern):
                hits.append(Hit(rel_path, kind, desc, m.group(0), line_no))


def _read_text(data: bytes) -> Optional[str]:
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    return None


def _scan_name(rel_path: str, hits: List[Hit], scope: str) -> None:
    parts = Path(rel_path).parts
    for part in parts:
        if part in FORBIDDEN_PARTS:
            hits.append(Hit(rel_path, "name", f"禁止的路径段: {part}", part))
    suffix = Path(rel_path).suffix.lower()
    if suffix in FORBIDDEN_SUFFIXES:
        hits.append(Hit(rel_path, "name", f"禁止的后缀: {suffix}", suffix))


def _scan_zip(zip_path: Path, rel_prefix: str, scope: str, report: Report) -> None:
    report.scanned_zips += 1
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                rel = f"{rel_prefix}/{member.filename}"
                _scan_name(rel, report.hits, scope)
                if member.filename.lower().endswith((".zip", ".jar")):
                    continue
                data = zf.read(member)
                if len(data) > 5_000_000:  # 超过 5MB 视为二进制大文件，只查名字
                    continue
                text = _read_text(data)
                if text is None:
                    continue
                report.scanned_files += 1
                _scan_text(rel, text, scope, report.hits)
    except zipfile.BadZipFile:
        report.hits.append(Hit(rel_prefix, "name", "损坏的 zip 归档", zip_path.name))


def scan(root: Path, scope: str = "public") -> Report:
    report = Report(root=str(root), scope=scope)
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        if any(part in {".git", "evidence"} for part in path.parts):
            continue
        # 扫描器自身豁免：规则正则字面量会被自身命中，属预期。
        if path.name == "scan_privacy.py":
            continue
        if path.is_dir():
            _scan_name(rel, report.hits, scope)
            continue
        _scan_name(rel, report.hits, scope)
        if path.suffix.lower() == ".zip":
            _scan_zip(path, rel, scope, report)
            continue
        if path.stat().st_size > 5_000_000:
            continue
        data = path.read_bytes()
        text = _read_text(data)
        if text is None:
            continue
        report.scanned_files += 1
        _scan_text(rel, text, scope, report.hits)
    return report


def render_md(report: Report) -> str:
    lines = [
        f"# 隐私泄漏扫描报告", "",
        f"- 扫描根: `{report.root}`", f"- 范围: {report.scope}",
        f"- 扫描文件: {report.scanned_files}，扫描 zip: {report.scanned_zips}",
        f"- 结论: **{'干净 ✅' if report.clean else '存在命中 ❌（共 %d 处）' % len(report.hits)}**",
        "",
    ]
    if report.hits:
        lines.append("| 文件 | 类型 | 规则 | 命中片段 | 行 |")
        lines.append("|---|---|---|---|---|")
        for h in report.hits:
            lines.append(f"| {h.path} | {h.kind} | {h.rule} | `{h.match[:60]}` | {h.line} |")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="隐私泄漏扫描器")
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--scope", choices=("public", "local"), default="public")
    parser.add_argument("--json", type=Path, help="导出 JSON 报告")
    parser.add_argument("--md", type=Path, help="导出 Markdown 报告")
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        print(f"ERROR: 扫描根不存在: {args.root}", file=sys.stderr)
        return 2

    report = scan(args.root, args.scope)
    print(render_md(report))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
                             encoding="utf-8")
    if args.md:
        args.md.parent.mkdir(parents=True, exist_ok=True)
        args.md.write_text(render_md(report), encoding="utf-8")
    return 0 if report.clean else 1


if __name__ == "__main__":
    sys.exit(main())
