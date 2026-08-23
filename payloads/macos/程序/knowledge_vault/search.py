from __future__ import annotations

from pathlib import Path
import re
import sqlite3

from .models import SearchHit
from .filesystem import list_files
from .state import sha256_file
from .config import is_manual_only_path


EXCLUDED_ROOTS = {".obsidian", "95 原始输入归档", "94.1 整理记录"}


def _title(path: Path, text: str) -> str:
    match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else path.stem


class VaultSearch:
    def __init__(self, database: Path):
        database.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database)
        existing = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='notes'"
        ).fetchone()
        if existing and "grams" not in existing[0]:
            self.connection.execute("DROP TABLE notes")
        self.connection.execute("CREATE VIRTUAL TABLE IF NOT EXISTS notes USING fts5(path UNINDEXED, title, body, grams, tokenize='unicode61')")

    def close(self) -> None:
        self.connection.close()

    def rebuild(self, vault: Path) -> int:
        self.connection.execute("DELETE FROM notes")
        count = 0
        for path in list_files(vault, "*.md"):
            relative = path.relative_to(vault)
            if any(part in EXCLUDED_ROOTS for part in relative.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            combined = f"{_title(path, text)}\n{text}"
            chinese_runs = re.findall(r"[\u4e00-\u9fff]+", combined)
            grams = " ".join(
                run[index:index + size]
                for run in chinese_runs
                for size in (1, 2)
                for index in range(len(run) - size + 1)
            )
            self.connection.execute(
                "INSERT INTO notes(path,title,body,grams) VALUES(?,?,?,?)",
                (relative.as_posix(), _title(path, text), text, grams),
            )
            count += 1
        self.connection.commit()
        return count

    def query(self, text: str, limit: int = 8) -> list[SearchHit]:
        tokens = re.findall(r"[\w\u4e00-\u9fff]+", text)
        if not tokens:
            return []
        expressions = []
        for token in tokens:
            if re.fullmatch(r"[\u4e00-\u9fff]+", token):
                pieces = [token[index:index + 2] for index in range(max(1, len(token) - 1))]
                expressions.append(" AND ".join(f'grams:"{piece}"' for piece in pieces))
            else:
                expressions.append(f'"{token}"')
        expression = " OR ".join(f"({part})" for part in expressions)
        rows = self.connection.execute(
            "SELECT path,title,snippet(notes,2,'','','…',24),bm25(notes) FROM notes WHERE notes MATCH ? ORDER BY bm25(notes) LIMIT ?",
            (expression, limit),
        ).fetchall()
        return [SearchHit(path, title, snippet, float(score)) for path, title, snippet, score in rows]


def related_candidates(
    vault: Path,
    text: str,
    limit: int = 6,
    excluded_paths: tuple[str, ...] = (),
) -> list[dict]:
    """Return actual topic notes only; candidates are suggestions, never authority to overwrite."""
    grams = {text[index:index + 2] for index in range(len(text) - 1) if re.fullmatch(r"[\u4e00-\u9fff]{2}", text[index:index + 2])}
    ranked: list[tuple[int, Path, str]] = []
    for path in list_files(vault, "*.md"):
        relative = path.relative_to(vault)
        if not relative.parts or relative.parts[0] not in {"10 生活", "20 工作", "30 学习"}:
            continue
        if is_manual_only_path(relative, excluded_paths):
            continue
        body = path.read_text(encoding="utf-8", errors="replace")
        note_grams = {body[index:index + 2] for index in range(len(body) - 1) if re.fullmatch(r"[\u4e00-\u9fff]{2}", body[index:index + 2])}
        score = len(grams & note_grams)
        if score:
            ranked.append((score, path, body))
    ranked.sort(key=lambda item: (-item[0], item[1].as_posix()))
    return [
        {"path": path.relative_to(vault).as_posix(), "title": _title(path, body), "snippet": body[:800], "sha256": sha256_file(path)}
        for _, path, body in ranked[:limit]
    ]
