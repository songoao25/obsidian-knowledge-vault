from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from .models import FileChange, TransactionResult
from .metadata import stamp_modified


class VaultTransaction:
    def __init__(self, rollback_root: Path):
        self.rollback_root = rollback_root

    def apply(self, changes: list[FileChange], *, now: datetime | None = None) -> TransactionResult:
        write_time = now or datetime.now()
        run_id = write_time.strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8]
        rollback_dir = self.rollback_root / run_id
        backups = rollback_dir / "files"
        manifest: list[dict] = []
        applied: list[Path] = []
        try:
            backups.mkdir(parents=True, exist_ok=False)
            for index, change in enumerate(changes):
                original = change.target
                backup = backups / f"{index:04d}"
                entry = {"operation": change.operation, "target": str(change.target), "source": str(change.source or ""), "existed": original.exists()}
                if original.exists():
                    if original.is_dir():
                        shutil.copytree(original, backup)
                    else:
                        shutil.copy2(original, backup)
                    entry["backup"] = str(backup)
                manifest.append(entry)
                if change.operation == "write":
                    change.target.parent.mkdir(parents=True, exist_ok=True)
                    fd, tmp_name = tempfile.mkstemp(prefix=".kv-", dir=change.target.parent)
                    try:
                        with os.fdopen(fd, "wb") as handle:
                            content = change.content or b""
                            if change.update_modified:
                                content = stamp_modified(content.decode("utf-8"), write_time).encode("utf-8")
                            handle.write(content)
                            handle.flush()
                            os.fsync(handle.fileno())
                        os.replace(tmp_name, change.target)
                    finally:
                        Path(tmp_name).unlink(missing_ok=True)
                elif change.operation == "move":
                    if change.source is None or not change.source.exists():
                        raise FileNotFoundError(change.source)
                    change.target.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(change.source, change.target)
                elif change.operation == "delete":
                    if not change.target.exists():
                        raise FileNotFoundError(change.target)
                    if change.target.is_dir():
                        shutil.rmtree(change.target)
                    else:
                        change.target.unlink()
                elif change.operation == "fail":
                    raise RuntimeError("forced test failure")
                else:
                    raise ValueError(f"unsupported operation: {change.operation}")
                applied.append(change.target)
            (rollback_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return TransactionResult(True, applied=applied, rollback_dir=rollback_dir)
        except Exception as exc:
            for entry in reversed(manifest):
                target = Path(entry["target"])
                source = Path(entry["source"]) if entry.get("source") else None
                if entry["operation"] == "move" and target.exists() and source is not None:
                    source.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(target, source)
                if entry.get("existed"):
                    backup = Path(entry["backup"])
                    if target.exists():
                        if target.is_dir(): shutil.rmtree(target)
                        else: target.unlink()
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if backup.is_dir(): shutil.copytree(backup, target)
                    else: shutil.copy2(backup, target)
                elif target.exists():
                    if target.is_dir(): shutil.rmtree(target)
                    else: target.unlink()
            return TransactionResult(False, applied=[], rollback_dir=rollback_dir, error=str(exc))
