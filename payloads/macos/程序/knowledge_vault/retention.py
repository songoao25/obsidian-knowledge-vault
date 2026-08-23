from __future__ import annotations

from datetime import datetime
from datetime import timedelta
import json
from pathlib import Path
import re
import shutil

from .models import RetentionReport
_NO_DELETE = re.compile(r"(?m)^no_delete:\s*true\s*$")


def _no_delete_checked(path: Path) -> bool:
    if path.suffix.lower() != ".md" or not path.is_file():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---\n"):
        return False
    closing = text.find("\n---", 4)
    return closing >= 0 and _NO_DELETE.search(text[4:closing]) is not None


def _archived_at(data: dict, archive: Path, now: datetime) -> datetime:
    value = data.get("archived_at")
    if value:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=now.tzinfo)
        return parsed
    return datetime.fromtimestamp(archive.stat().st_mtime, tz=now.tzinfo)


def apply_retention(
    now: datetime,
    raw_archive: Path,
    source_days: int = 7,
    trash_dir: Path | None = None,
) -> RetentionReport:
    """Move expired archive copies to Trash unless their visible checkbox is checked."""
    trash_dir = trash_dir or Path.home() / ".Trash"
    report = RetentionReport()
    cutoff = now - timedelta(days=source_days)
    if not raw_archive.exists():
        return report
    manifests = list(raw_archive.rglob(".preservation.json")) + list(raw_archive.glob("*/*/*/.preservation/*.json"))
    for manifest in manifests:
        legacy = manifest.name == ".preservation.json"
        day_root = manifest.parent if legacy else manifest.parent.parent
        archive = manifest.parent if legacy else day_root / str(json.loads(manifest.read_text(encoding="utf-8")).get("archive_name", ""))
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            tracked = [day_root / str(item.get("name", "")) for item in data.get("source_files", [])]
            report_path = archive if legacy or (archive != day_root and archive.exists()) else (tracked[0] if len(tracked) == 1 else day_root)
            if _archived_at(data, report_path, now) > cutoff:
                report.skipped.append(report_path)
                continue
            if any(_no_delete_checked(path) for path in tracked):
                report.skipped.append(report_path)
                continue
            trash_dir.mkdir(parents=True, exist_ok=True)
            targets = [archive] if legacy or (archive.exists() and archive != day_root) else tracked
            for source in targets:
                target = trash_dir / source.name
                counter = 1
                while target.exists():
                    target = trash_dir / f"{source.stem}-{counter}{source.suffix}"
                    counter += 1
                shutil.move(str(source), str(target))
            manifest.unlink(missing_ok=True)
            report.trashed.append(report_path)
        except Exception:
            report.approvals_needed.append(manifest.parent)
    return report
