from __future__ import annotations

from datetime import date, datetime, time
import hashlib
import json
from pathlib import Path
import re

from .models import FileChange, InboxItem, InputBundle
from .state import sha256_file


_FRONTMATTER = re.compile(r"\A---\n(?P<fields>.*?)\n---(?P<body>\n.*|\Z)", re.DOTALL)
_NO_DELETE = re.compile(r"^no_delete:\s*.*$", re.MULTILINE)


def archive_markdown_with_no_delete_property(
    text: str,
    *,
    no_delete: bool = False,
    overwrite_existing: bool = True,
) -> str:
    """Add the user-facing retention checkbox without changing the archive body."""
    value = f"no_delete: {'true' if no_delete else 'false'}"
    match = _FRONTMATTER.match(text)
    if match is None:
        return f"---\n{value}\n---\n{text}"
    existing = _NO_DELETE.search(match.group("fields"))
    if existing and not overwrite_existing:
        return text
    fields = _NO_DELETE.sub(value, match.group("fields"))
    if existing is None:
        fields = f"{fields}\n{value}" if fields else value
    return f"---\n{fields}\n---{match.group('body')}"


def _safe_component(value: str) -> str:
    cleaned = re.sub(r'[/\\\n\r\t<>:"|?*]', " ", value)
    return re.sub(r"\s+", " ", cleaned).strip().strip(".")[:120] or "未命名资料"


def archive_base_name(bundle: InputBundle) -> str:
    primary = next((item for item in bundle.items if item.path.suffix.lower() == ".md"), bundle.items[0])
    return _safe_component(primary.path.stem)


def archive_day(vault: Path, collected_on: date) -> Path:
    return vault / "90 系统" / "95 原始输入归档" / f"{collected_on:%Y}" / f"{collected_on:%m}" / f"{collected_on:%d}"


def choose_archive_directory(vault: Path, bundle: InputBundle, collected_on: date) -> Path:
    """Return the source container: the day itself for one file, a readable folder for a group."""
    day_root = archive_day(vault, collected_on)
    if len(bundle.items) == 1:
        return day_root
    base = archive_base_name(bundle)
    candidate = day_root / base
    counter = 2
    while candidate.exists():
        candidate = day_root / f"{base} ({counter})"
        counter += 1
    return candidate


def choose_archive_file(archive: Path, item: InboxItem, used: set[Path]) -> Path:
    candidate = archive / item.relative_path.name
    counter = 2
    while candidate.exists() or candidate in used:
        candidate = archive / f"{item.path.stem} ({counter}){item.path.suffix}"
        counter += 1
    used.add(candidate)
    return candidate


def manifest_path(vault: Path, bundle: InputBundle, collected_on: date) -> Path:
    return archive_day(vault, collected_on) / ".preservation" / f"{bundle.bundle_id}.json"


def compile_archive_no_delete_property_changes(raw_archive: Path) -> list[FileChange]:
    """Make the retention checkbox visible on every existing archived Markdown page."""
    if not raw_archive.exists():
        return []
    changes: list[FileChange] = []
    for source in sorted(raw_archive.rglob("*.md")):
        original = source.read_text(encoding="utf-8")
        updated = archive_markdown_with_no_delete_property(original, overwrite_existing=False)
        if updated != original:
            changes.append(FileChange.write(source, updated))
    return changes


def line_hashes(text: str) -> list[str]:
    return [
        hashlib.sha256(line.strip().encode("utf-8")).hexdigest()
        for line in text.splitlines()
        if line.strip()
    ]


def build_manifest(
    *,
    bundle: InputBundle,
    archive: Path,
    source_destinations: list[tuple[InboxItem, Path]],
    formal_note: str,
    remote_urls: list[str],
    local_attachments: list[str],
    archived_at: datetime,
    source_type: str,
    edit_mode: str,
    preserved_body: str = "",
) -> dict:
    return {
        "version": 2,
        "bundle_id": bundle.bundle_id,
        "archive_name": archive.name,
        "archived_at": archived_at.isoformat(),
        "formal_note": formal_note,
        "source_type": source_type,
        "edit_mode": edit_mode,
        "source_files": [
            {
                "name": destination.relative_to(archive if len(bundle.items) == 1 else archive.parent).as_posix(),
                "original_name": item.relative_path.name,
                "sha256": item.sha256,
            }
            for item, destination in source_destinations
        ],
        "remote_urls": remote_urls,
        "local_attachments": local_attachments,
        "required_line_hashes": line_hashes(preserved_body) if edit_mode == "preserve" else [],
    }


def _unique_migration_target(parent: Path, base: str, reserved: set[Path]) -> Path:
    safe = _safe_component(base)
    component = Path(safe)
    candidate = parent / safe
    counter = 2
    while candidate.exists() or candidate in reserved:
        candidate = parent / f"{component.stem} ({counter}){component.suffix}"
        counter += 1
    reserved.add(candidate)
    return candidate


def compile_archive_migration_changes(raw_archive: Path, *, now: datetime | None = None) -> list[FileChange]:
    if not raw_archive.exists():
        return []
    now = now or datetime.now().astimezone()
    changes: list[FileChange] = []
    reserved: set[Path] = set()
    archives = sorted(path for path in raw_archive.glob("*/*/*/*") if path.is_dir() and path.name != ".preservation")
    for archive in archives:
        sources = sorted(path for path in archive.iterdir() if path.name != ".preservation.json" and not path.name.startswith("."))
        if not sources:
            continue
        manifest_path = archive / ".preservation.json"
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        except Exception:
            data = {}
        bundle_id = str(data.get("bundle_id") or archive.name)
        archived_at = data.get("archived_at") or datetime.fromtimestamp(archive.stat().st_mtime, tz=now.tzinfo).isoformat()
        hidden_manifest = archive.parent / ".preservation" / f"{_safe_component(bundle_id)}.json"
        if len(sources) == 1 and sources[0].is_file():
            source = sources[0]
            target = _unique_migration_target(archive.parent, source.name, reserved)
            # _unique_migration_target treats the entire filename as a component and keeps its suffix.
            data.update({
                "version": 3,
                "bundle_id": bundle_id,
                "archive_name": archive.parent.name,
                "archived_at": archived_at,
                "source_type": data.get("source_type", "legacy"),
                "edit_mode": data.get("edit_mode", "legacy"),
                "source_files": [{"name": target.name, "original_name": source.name, "sha256": sha256_file(source)}],
                "required_line_hashes": data.get("required_line_hashes", []),
            })
            changes.append(FileChange.move(source, target))
            if manifest_path.is_file():
                changes.append(FileChange.write(hidden_manifest, json.dumps(data, ensure_ascii=False, indent=2) + "\n"))
            changes.append(FileChange.delete(archive))
            continue
        if manifest_path.is_file():
            data.update({
                "version": 3,
                "bundle_id": bundle_id,
                "archive_name": archive.name,
                "archived_at": archived_at,
                "source_type": data.get("source_type", "legacy"),
                "edit_mode": data.get("edit_mode", "legacy"),
                "source_files": [
                    {"name": f"{archive.name}/{path.name}", "original_name": path.name, "sha256": sha256_file(path)}
                    for path in sources if path.is_file()
                ],
                "required_line_hashes": data.get("required_line_hashes", []),
            })
            changes.append(FileChange.write(hidden_manifest, json.dumps(data, ensure_ascii=False, indent=2) + "\n"))
            changes.append(FileChange.delete(manifest_path))
    return changes
