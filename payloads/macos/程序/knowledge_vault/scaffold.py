from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import shutil
from typing import Iterable


REQUIRED_CORE_PLUGINS = {
    "file-explorer", "global-search", "switcher", "backlink", "outgoing-link",
    "properties", "page-preview", "daily-notes", "templates", "note-composer",
    "command-palette", "bookmarks", "file-recovery", "sync", "bases",
}


@dataclass
class AssetInstallReport:
    created: list[Path] = field(default_factory=list)
    unchanged: list[Path] = field(default_factory=list)
    conflicts: list[Path] = field(default_factory=list)


def _safe_relative(raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute() or ".." in path.parts or raw.endswith("/"):
        raise ValueError(f"unsafe taxonomy path: {raw}")
    return path


def load_taxonomy(path: Path) -> tuple[str, ...]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("version") != 1 or not isinstance(raw.get("paths"), list):
        raise ValueError("taxonomy version or paths is invalid")
    paths = tuple(raw["paths"])
    for item in paths:
        _safe_relative(item)
    if len(paths) != len(set(paths)):
        raise ValueError("taxonomy contains duplicate paths")
    return paths


def scaffold_directories(vault: Path, paths: Iterable[str], dry_run: bool = False) -> list[Path]:
    created: list[Path] = []
    vault = vault.resolve()
    for raw in paths:
        target = vault / _safe_relative(raw)
        if not target.exists():
            created.append(target)
            if not dry_run:
                target.mkdir(parents=True, exist_ok=True)
    return created


def install_vault_assets(source: Path, vault: Path, dry_run: bool = False) -> AssetInstallReport:
    report = AssetInstallReport()
    for src in sorted(p for p in source.rglob("*") if p.is_file()):
        rel = src.relative_to(source)
        target = vault / rel
        if not target.exists():
            report.created.append(target)
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, target)
        elif target.read_bytes() == src.read_bytes():
            report.unchanged.append(target)
        else:
            report.conflicts.append(target)
    return report


def _merge_json(path: Path, updates: dict) -> None:
    current = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    current.update(updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def merge_obsidian_settings(vault: Path, dry_run: bool = False) -> list[Path]:
    changed = [vault / ".obsidian" / "app.json", vault / ".obsidian" / "core-plugins.json"]
    if dry_run:
        return changed
    _merge_json(changed[0], {
        "newFileLocation": "folder",
        "newFileFolderPath": "00 收件箱",
        "attachmentFolderPath": "00 收件箱",
        "alwaysUpdateLinks": True,
        "useMarkdownLinks": False,
    })
    existing = json.loads(changed[1].read_text(encoding="utf-8")) if changed[1].exists() else {}
    for plugin in REQUIRED_CORE_PLUGINS:
        existing[plugin] = True
    changed[1].write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return changed

