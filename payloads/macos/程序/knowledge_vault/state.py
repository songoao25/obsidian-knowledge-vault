from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import time

from .models import FileState


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inspect_file(path: Path) -> FileState:
    stat = path.stat()
    return FileState(path=path, sha256=sha256_file(path), size=stat.st_size, mtime_ns=stat.st_mtime_ns)


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict = {"version": 1, "files": {}, "runs": []}
        if path.exists():
            self.data = json.loads(path.read_text(encoding="utf-8"))

    def known_hash(self, relative_path: Path) -> str | None:
        item = self.data.get("files", {}).get(relative_path.as_posix())
        return item.get("sha256") if item else None

    def ai_written_hash(self, relative_path: Path) -> str | None:
        item = self.data.get("files", {}).get(relative_path.as_posix())
        return item.get("ai_written_hash") if item else None

    def remember(self, relative_path: Path, state: FileState, ai_written_hash: str | None = None) -> None:
        self.data.setdefault("files", {})[relative_path.as_posix()] = {
            "sha256": state.sha256,
            "size": state.size,
            "mtime_ns": state.mtime_ns,
            "ai_written_hash": ai_written_hash,
        }

    def was_ai_written(self, relative_path: Path) -> bool:
        """True 仅当该路径的文件曾被 AI 成功写入/创建（ai_written_hash 已登记）。

        成功归档的收件箱源文件会被移出收件箱，因此只有「仍留在收件箱」的文件才可能
        命中此判断；用它替代旧的 known_hash 跳过逻辑，可避免「被暂缓（defer）的文件」
        被误判为已处理而永久跳过。
        """
        item = self.data.get("files", {}).get(relative_path.as_posix())
        return bool(item and item.get("ai_written_hash"))

    def mark_deferred(self, relative_path: Path) -> None:
        """登记一次「暂缓处理」，不写入「已处理」指纹。"""
        deferred = self.data.setdefault("deferred", {})
        key = relative_path.as_posix()
        deferred[key] = deferred.get(key, 0) + 1

    def deferral_count(self, relative_path: Path) -> int:
        return self.data.get("deferred", {}).get(relative_path.as_posix(), 0)

    def append_run(self, record: dict) -> None:
        self.data.setdefault("runs", []).append(record)
        self.data["runs"] = self.data["runs"][-100:]

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)


class RunLock(AbstractContextManager):
    def __init__(self, path: Path, stale_seconds: int = 6 * 3600):
        self.path = path
        self.stale_seconds = stale_seconds
        self.acquired = False

    @classmethod
    def acquire(cls, lock_path: Path) -> "RunLock":
        return cls(lock_path)

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and time.time() - self.path.stat().st_mtime > self.stale_seconds:
            self.path.unlink()
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as exc:
            raise RuntimeError(f"maintenance already running: {self.path}") from exc
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        self.acquired = True
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False
