from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re


_URL = re.compile(r"https?://[^\s<>\]\)\"']+")
_WIKI_EMBED = re.compile(r"!\[\[([^\]|#]+)")
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)")


@dataclass(frozen=True)
class MediaInventory:
    remote_urls: tuple[str, ...]
    local_references: tuple[str, ...]


def inventory_markdown(text: str) -> MediaInventory:
    """Collect every external media/source address and explicit local embed."""
    remote = tuple(dict.fromkeys(_URL.findall(text)))
    local: list[str] = []
    for value in _WIKI_EMBED.findall(text) + _MARKDOWN_IMAGE.findall(text):
        value = value.strip()
        if value and not value.startswith(("http://", "https://", "data:")):
            local.append(value)
    return MediaInventory(remote, tuple(dict.fromkeys(local)))


def rewrite_local_references(text: str, replacements: dict[str, str]) -> str:
    for source, target in replacements.items():
        escaped = re.escape(source)
        text = re.sub(rf"(!\[\[){escaped}(?=\]\])", lambda match: match.group(1) + target, text)
        text = re.sub(rf"(!\[[^\]]*\]\(){escaped}(?=[\s\)])", lambda match: match.group(1) + target, text)
    return text


def source_markdown_body(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return re.sub(r"\A---\r?\n.*?\r?\n---\r?\n?", "", text, count=1, flags=re.DOTALL).strip()
