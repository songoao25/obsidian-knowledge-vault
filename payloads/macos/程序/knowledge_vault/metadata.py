from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re

from .models import FileChange


FRONTMATTER = re.compile(r"\A---\r?\n(?P<fields>.*?)\r?\n---(?:\r?\n)?", re.DOTALL)
FIELD = re.compile(r"^(created|modified):\s*.*$")
TAGS = re.compile(r"^tags:\s*$")
TAG_ITEM = re.compile(r"^\s+-\s+(.+?)\s*$")
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")


@dataclass(frozen=True)
class TagPolicy:
    allowed_tags: set[str]
    path_defaults: dict[str, tuple[str, ...]]
    overrides: dict[str, tuple[str, ...]]
    migrations: dict[str, tuple[str, ...]]

    @classmethod
    def load(cls, path: Path) -> "TagPolicy":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            allowed_tags=set(raw["allowed_tags"]),
            path_defaults={key: tuple(value) for key, value in raw["path_defaults"].items()},
            overrides={key: tuple(value) for key, value in raw.get("overrides", {}).items()},
            migrations={key: tuple(value) for key, value in raw.get("migrations", {}).items()},
        )

    def defaults_for(self, relative_path: Path) -> tuple[str, ...]:
        key = relative_path.as_posix()
        matches = [prefix for prefix in self.path_defaults if key == prefix or key.startswith(prefix + "/")]
        return self.path_defaults[max(matches, key=len)] if matches else ("kind/note", "workflow/inbox")


@dataclass(frozen=True)
class MetadataResult:
    changed: bool
    unknown_tags: tuple[str, ...]


@dataclass(frozen=True)
class MetadataReport:
    changed_paths: tuple[Path, ...]
    unknown_tags: dict[Path, tuple[str, ...]]


def format_timestamp(value: datetime) -> str:
    return value.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")


def stamp_modified(text: str, modified_at: datetime) -> str:
    """Return note text with its frontmatter ``modified`` set to the write time."""
    timestamp = format_timestamp(modified_at)
    match = FRONTMATTER.match(text)
    if match is None:
        return f"---\nmodified: {timestamp}\n---\n" + text
    lines = match.group("fields").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("modified:"):
            lines[index] = f"modified: {timestamp}"
            break
    else:
        lines.append(f"modified: {timestamp}")
    return "---\n" + "\n".join(lines) + "\n---\n" + text[match.end() :]


def _split_frontmatter(text: str) -> tuple[list[str], str]:
    match = FRONTMATTER.match(text)
    if not match:
        return [], text
    return match.group("fields").splitlines(), text[match.end() :]


def _extract_tags(lines: list[str]) -> list[str]:
    for index, line in enumerate(lines):
        if not TAGS.fullmatch(line):
            continue
        values: list[str] = []
        for candidate in lines[index + 1 :]:
            item = TAG_ITEM.fullmatch(candidate)
            if item is None:
                break
            values.append(item.group(1).strip().strip('"\''))
        return values
    return []


def _without_managed_fields(lines: list[str]) -> list[str]:
    result: list[str] = []
    skip_tags = False
    for line in lines:
        if FIELD.fullmatch(line):
            continue
        if TAGS.fullmatch(line):
            skip_tags = True
            continue
        if skip_tags and TAG_ITEM.fullmatch(line):
            continue
        skip_tags = False
        result.append(line)
    return result


def _render(lines: list[str], body: str, created: str, modified: str, tags: tuple[str, ...]) -> str:
    fields = [f"created: {created}", f"modified: {modified}", "tags:"]
    fields.extend(f"  - {tag}" for tag in tags)
    fields.extend(_without_managed_fields(lines))
    suffix = body if body.startswith("\n") or not body else "\n" + body
    return "---\n" + "\n".join(fields) + "\n---\n" + suffix


def normalize_note(
    text: str,
    relative_path: Path,
    policy: TagPolicy,
    *,
    created_at: datetime,
    modified_at: datetime | None = None,
    replace_created_if: str | None = None,
    replace_modified_if: str | None = None,
) -> tuple[str, MetadataResult]:
    lines, body = _split_frontmatter(text)
    created = next((line.split(":", 1)[1].strip() for line in lines if line.startswith("created:")), "")
    modified = next((line.split(":", 1)[1].strip() for line in lines if line.startswith("modified:")), "")
    tags = _extract_tags(lines)
    changed = False

    if not TIMESTAMP.fullmatch(created) or created == replace_created_if:
        created = format_timestamp(created_at)
        changed = True
    if not TIMESTAMP.fullmatch(modified):
        # A newly-created note has not been edited yet: its initial modified
        # timestamp is the creation timestamp, not a later filesystem sync time.
        modified = created
        changed = True
    elif modified == replace_modified_if:
        modified = format_timestamp(modified_at or created_at)
        changed = True

    if not tags:
        tags = list(policy.defaults_for(relative_path))
        changed = True
    elif "clippings" in tags:
        replacement = list(policy.migrations.get("clippings", policy.defaults_for(relative_path)))
        tags = [tag for tag in tags if tag != "clippings"] + [tag for tag in replacement if tag not in tags]
        changed = True

    normalized_tags = tuple(dict.fromkeys(tags))
    unknown = tuple(tag for tag in normalized_tags if tag not in policy.allowed_tags)
    if not changed:
        return text, MetadataResult(False, unknown)
    return _render(lines, body, created, modified, normalized_tags), MetadataResult(True, unknown)


FORMAL_ROOTS = ("00 收件箱", "10 生活", "20 工作", "30 学习", "40 记录", "90 系统")


def compile_metadata_changes(
    vault: Path,
    policy: TagPolicy,
    *,
    now: datetime,
    replace_created_if: str | None = None,
    replace_modified_if: str | None = None,
    created_overrides: dict[Path, datetime] | None = None,
    modified_overrides: dict[Path, datetime] | None = None,
) -> tuple[list[FileChange], MetadataReport]:
    changes: list[FileChange] = []
    changed_paths: list[Path] = []
    unknown: dict[Path, tuple[str, ...]] = {}
    for root_name in FORMAL_ROOTS:
        root = vault / root_name
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.md")):
            relative = path.relative_to(vault)
            text = path.read_text(encoding="utf-8", errors="replace")
            stat = path.stat()
            birth = getattr(stat, "st_birthtime", stat.st_ctime)
            created_at = datetime.fromtimestamp(birth, tz=now.tzinfo)
            created_at = (created_overrides or {}).get(relative, created_at)
            modified_at = (modified_overrides or {}).get(relative)
            if modified_at is None:
                modified_at = datetime.fromtimestamp(stat.st_mtime, tz=now.tzinfo)
            normalized, result = normalize_note(
                text,
                relative,
                policy,
                created_at=created_at,
                modified_at=modified_at,
                replace_created_if=replace_created_if,
                replace_modified_if=replace_modified_if,
            )
            if result.unknown_tags:
                unknown[relative] = result.unknown_tags
            if result.changed:
                changes.append(FileChange.write(path, normalized, update_modified=True))
                changed_paths.append(relative)
    return changes, MetadataReport(tuple(changed_paths), unknown)
