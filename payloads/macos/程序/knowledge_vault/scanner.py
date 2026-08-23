from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
from pathlib import Path
import re

from .models import InboxItem, InputBundle
from .state import StateStore, sha256_file
from .filesystem import list_files
from .organize_records import is_organize_record


TEXT_SUFFIXES = {".md", ".txt", ".html", ".htm", ".url"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".heic", ".webp", ".tiff"}
AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".aac", ".flac"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm"}
DOCUMENT_SUFFIXES = {".doc", ".docx", ".rtf", ".odt"}
_FRONTMATTER = re.compile(r"\A(?:\ufeff)?---[ \t]*\r?\n.*?^---[ \t]*\r?\n?", re.DOTALL | re.MULTILINE)


def _kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf": return "pdf"
    if suffix in DOCUMENT_SUFFIXES: return "document"
    if suffix in IMAGE_SUFFIXES: return "image"
    if suffix in AUDIO_SUFFIXES: return "audio"
    if suffix in VIDEO_SUFFIXES: return "video"
    if suffix in TEXT_SUFFIXES: return "text"
    return "binary"


def _is_markdown_draft_without_body(path: Path) -> bool:
    if path.suffix.lower() != ".md":
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    frontmatter = _FRONTMATTER.match(text)
    return frontmatter is not None and not text[frontmatter.end():].strip()


def scan_inbox(inbox: Path, now: datetime, stable_minutes: int = 10, state: StateStore | None = None) -> list[InboxItem]:
    cutoff = now - timedelta(minutes=stable_minutes)
    items: list[InboxItem] = []
    if not inbox.exists():
        return items
    for path in (p for p in list_files(inbox) if not p.name.startswith(".")):
        if is_organize_record(path):
            continue
        if path.suffix.lower() == ".icloud":
            continue
        stat = path.stat()
        if stat.st_size == 0:
            continue
        if _is_markdown_draft_without_body(path):
            continue
        modified = datetime.fromtimestamp(stat.st_mtime, tz=now.tzinfo)
        if modified > cutoff:
            continue
        relative = path.relative_to(inbox)
        digest = sha256_file(path)
        # 仅当文件曾被 AI 成功写入/创建时才跳过；被「暂缓」(defer) 的文件不写
        # 已处理指纹，必须允许后续运行重新评估，避免永久滞留收件箱。
        if state and state.was_ai_written(relative):
            continue
        items.append(InboxItem(path, relative, digest, stat.st_size, modified, _kind(path)))
    return items


def _bundle_id(paths: list[Path]) -> str:
    joined = "\n".join(sorted(path.as_posix() for path in paths)).encode("utf-8")
    return hashlib.sha256(joined).hexdigest()[:16]


def group_items(items: list[InboxItem]) -> list[InputBundle]:
    by_relative = {item.relative_path.as_posix(): item for item in items}
    assigned: set[Path] = set()
    bundles: list[InputBundle] = []
    wiki_pattern = re.compile(r"!?\[\[([^\]|#]+)")
    markdown_pattern = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
    for item in items:
        if item.relative_path in assigned or item.path.suffix.lower() != ".md":
            continue
        members = [item]
        text = item.path.read_text(encoding="utf-8", errors="replace")
        refs = wiki_pattern.findall(text) + markdown_pattern.findall(text)
        for ref in refs:
            normalized = Path(ref.strip()).as_posix()
            candidate = by_relative.get(normalized) or by_relative.get((item.relative_path.parent / normalized).as_posix())
            if candidate and candidate.relative_path not in assigned:
                members.append(candidate)
        unique = list({member.relative_path: member for member in members}.values())
        assigned.update(member.relative_path for member in unique)
        bundles.append(InputBundle(_bundle_id([m.relative_path for m in unique]), unique))
    for item in items:
        if item.relative_path not in assigned:
            assigned.add(item.relative_path)
            bundles.append(InputBundle(_bundle_id([item.relative_path]), [item]))
    return bundles
