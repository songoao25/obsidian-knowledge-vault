from __future__ import annotations

from datetime import date, datetime
import os
from pathlib import Path
import re
import tempfile

RECORD_NAME = re.compile(r"^\d{4}-\d{2}-\d{2} 整理记录-AI\.md$")
FRONTMATTER = re.compile(r"\A---\r?\n(?P<fields>.*?)\r?\n---(?:\r?\n)?", re.DOTALL)
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")


def _atomic_write(path: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=".organize-record-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        Path(tmp_name).unlink(missing_ok=True)


def _record_text(text: str, now: datetime, *, archived: bool, update_modified: bool = False) -> str:
    match = FRONTMATTER.match(text)
    fields = match.group("fields").splitlines() if match else []
    body = text[match.end() :] if match else text
    created = next((line.split(":", 1)[1].strip() for line in fields if line.startswith("created:")), "")
    modified = next((line.split(":", 1)[1].strip() for line in fields if line.startswith("modified:")), "")
    if not TIMESTAMP.fullmatch(created):
        created = now.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")
    if update_modified:
        modified = now.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")
    elif not TIMESTAMP.fullmatch(modified):
        modified = created
    managed = [f"created: {created}", f"modified: {modified}", "tags:", "  - kind/maintenance-log"]
    if not archived:
        managed.append("  - workflow/inbox")
    managed.extend(("  - topic/automation", "  - project/knowledge-vault"))
    suffix = body if body.startswith("\n") or not body else "\n" + body
    return "---\n" + "\n".join(managed) + "\n---\n" + suffix


def _rewrite_record(path: Path, now: datetime, body: str, *, archived: bool = False) -> None:
    """Atomically write a changed record and stamp its logical modification time."""
    _atomic_write(path, _record_text(body, now, archived=archived, update_modified=True))


def _record_body(text: str) -> str:
    match = FRONTMATTER.match(text)
    return text[match.end() :] if match else text


def is_organize_record(path: Path) -> bool:
    return bool(RECORD_NAME.fullmatch(path.name))


def daily_record_path(inbox: Path, now: datetime) -> Path:
    return inbox / f"{now:%Y-%m-%d} 整理记录-AI.md"


def ensure_daily_record(inbox: Path, now: datetime) -> Path:
    path = daily_record_path(inbox, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _atomic_write(path, _record_text("", now, archived=False))
    else:
        current = path.read_text(encoding="utf-8", errors="replace")
        normalized = _record_text(current, now, archived=False)
        if normalized != current:
            _rewrite_record(path, now, current)
    return path


def append_run_entry(path: Path, now: datetime, body: str) -> None:
    path = ensure_daily_record(path.parent, now)
    current = path.read_text(encoding="utf-8", errors="replace")
    _rewrite_record(path, now, current + f"## {now:%H:%M}\n\n{body.strip()}\n\n")


CLEANUP_DIR = "90 系统/94 维护记录/94.6 清理记录"
CLEANUP_NAME = re.compile(r"^\d{4}-\d{2}-\d{2} 清理记录-AI\.md$")


def is_cleanup_record(path: Path) -> bool:
    return bool(CLEANUP_NAME.fullmatch(path.name))


def cleanup_record_path(vault: Path, now: datetime) -> Path:
    return vault / CLEANUP_DIR / f"{now:%Y-%m-%d} 清理记录-AI.md"


def ensure_cleanup_record(vault: Path, now: datetime) -> Path:
    path = cleanup_record_path(vault, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _atomic_write(path, _record_text("", now, archived=True))
    else:
        current = path.read_text(encoding="utf-8", errors="replace")
        normalized = _record_text(current, now, archived=True)
        if normalized != current:
            _rewrite_record(path, now, current, archived=True)
    return path


def append_cleanup_entry(vault: Path, now: datetime, body: str) -> None:
    """把定期清理结果独立写入「94.6 清理记录」当日文件，不进入整理记录。"""
    path = ensure_cleanup_record(vault, now)
    current = path.read_text(encoding="utf-8", errors="replace")
    _rewrite_record(path, now, current + f"## {now:%H:%M}\n\n{body.strip()}\n\n", archived=True)


def _record_date(path: Path) -> date | None:
    if not is_organize_record(path):
        return None
    try:
        return date.fromisoformat(path.name[:10])
    except ValueError:
        return None


def _archive_record(vault: Path, source: Path, record_date: date, now: datetime) -> Path:
    target = vault / "90 系统/94 维护记录/94.1 整理记录" / f"{record_date:%Y}" / f"{record_date:%m}" / source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    if source == target:
        return target
    source_text = _record_text(source.read_text(encoding="utf-8", errors="replace"), now, archived=True, update_modified=True)
    if target.exists():
        existing = _record_text(target.read_text(encoding="utf-8", errors="replace"), now, archived=True).rstrip()
        addition = _record_body(source_text).strip()
        merged = existing + ("\n\n" if existing and addition else "") + addition + "\n"
        _rewrite_record(target, now, merged, archived=True)
        source.unlink()
    else:
        _atomic_write(source, source_text)
        source.replace(target)
    return target


def archive_daily_record(vault: Path, now: datetime) -> Path | None:
    inbox = vault / "00 收件箱"
    if not inbox.is_dir():
        return None
    eligible: list[tuple[date, Path]] = []
    for source in inbox.glob("* 整理记录-AI.md"):
        record_date = _record_date(source)
        if record_date is not None and record_date == now.date() and now.hour >= 23:
            eligible.append((record_date, source))
    archived: Path | None = None
    for record_date, source in sorted(eligible):
        archived = _archive_record(vault, source, record_date, now)
    return archived
