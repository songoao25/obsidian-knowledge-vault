from __future__ import annotations

from pathlib import Path
import logging
import platform
import subprocess


logger = logging.getLogger(__name__)


def list_files(root: Path, pattern: str = "*") -> list[Path]:
    """List files without recursively opening every iCloud File Provider directory."""
    if not root.exists():
        return []
    is_icloud = platform.system() == "Darwin" and "Library/Mobile Documents/" in root.as_posix()
    if not is_icloud:
        return sorted(path for path in root.rglob(pattern) if path.is_file())
    query = (
        'kMDItemContentTypeTree == "public.item"'
        if pattern == "*"
        else f'kMDItemFSName == "{pattern}"c'
    )
    paths = []
    try:
        result = subprocess.run(
            ["mdfind", "-onlyin", str(root), query],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        logger.warning("Spotlight 读取 iCloud 目录超时，改用根层文件扫描：%s", root)
    except OSError as exc:
        logger.warning("Spotlight 无法读取 iCloud 目录，改用根层文件扫描：%s (%s)", root, exc)
    else:
        if result.returncode != 0:
            logger.warning(
                "Spotlight 无法读取 iCloud 目录，改用根层文件扫描：%s (%s)",
                root,
                result.stderr.strip(),
            )
        else:
            for line in result.stdout.splitlines():
                path = Path(line)
                try:
                    path.relative_to(root)
                except ValueError:
                    continue
                if path.is_file():
                    paths.append(path)
    try:
        for path in root.iterdir():
            if not path.is_file():
                continue
            if pattern != "*" and not path.match(pattern):
                continue
            paths.append(path)
    except OSError as exc:
        raise RuntimeError(f"无法读取 iCloud 目录根层文件：{exc}") from exc
    return sorted(set(paths))
