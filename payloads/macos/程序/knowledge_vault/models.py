from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FileState:
    path: Path
    sha256: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class InboxItem:
    path: Path
    relative_path: Path
    sha256: str
    size: int
    modified_at: datetime
    kind: str


@dataclass
class ExtractedContent:
    path: Path
    kind: str
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    media_paths: tuple[Path, ...] = ()
    status: str = "ok"
    reason: str = ""


@dataclass
class InputBundle:
    bundle_id: str
    items: list[InboxItem]
    contents: list[ExtractedContent] = field(default_factory=list)


@dataclass(frozen=True)
class FileChange:
    operation: str
    target: Path
    content: bytes | None = None
    source: Path | None = None
    update_modified: bool = False

    @classmethod
    def write(cls, target: Path, content: str | bytes, *, update_modified: bool = False) -> "FileChange":
        payload = content.encode("utf-8") if isinstance(content, str) else content
        return cls("write", target, content=payload, update_modified=update_modified)

    @classmethod
    def move(cls, source: Path, target: Path) -> "FileChange":
        return cls("move", target, source=source)

    @classmethod
    def delete(cls, target: Path) -> "FileChange":
        return cls("delete", target)

    @classmethod
    def fail_for_test(cls, target: Path) -> "FileChange":
        return cls("fail", target)


@dataclass
class TransactionResult:
    ok: bool
    applied: list[Path] = field(default_factory=list)
    rollback_dir: Path | None = None
    error: str = ""


@dataclass
class RetentionReport:
    trashed: list[Path] = field(default_factory=list)
    approvals_needed: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)


@dataclass(frozen=True)
class OrganizePlan:
    title: str
    target_dir: str
    action: str
    body: str
    source_urls: tuple[str, ...] = ()
    attachment_names: tuple[str, ...] = ()
    existing_note: str = ""
    confidence: float = 0.0
    rationale: str = ""
    continuous_maintenance: bool = False
    source_type: str = "fragment"


@dataclass(frozen=True)
class SearchHit:
    relative_path: str
    title: str
    snippet: str
    score: float
