from __future__ import annotations

from pathlib import Path
import re

from .models import InputBundle, OrganizePlan


PRESERVED_SOURCE_TYPES = {"web_article", "personal_analysis", "reference"}
FRAGMENT_CONFIDENCE_THRESHOLD = 0.85
_FRONTMATTER = re.compile(r"\A---\r?\n(?P<body>.*?)\r?\n---(?:\r?\n)?", re.DOTALL)
_FENCE = re.compile(r"^[ ]{0,3}(?P<marker>`{3,}|~{3,})(?P<rest>.*)$")
_ATX_HEADING = re.compile(r"^(#{1,6})(\s+.*)$")


def markdown_lines_outside_fences(text: str) -> tuple[str, ...]:
    """Return Markdown prose lines while excluding fenced code and its markers."""
    visible: list[str] = []
    fence_char = ""
    fence_length = 0
    for line in text.splitlines():
        match = _FENCE.match(line)
        if fence_char:
            if match:
                marker = match.group("marker")
                if marker[0] == fence_char and len(marker) >= fence_length and not match.group("rest").strip():
                    fence_char = ""
                    fence_length = 0
            continue
        if match:
            marker = match.group("marker")
            fence_char = marker[0]
            fence_length = len(marker)
            continue
        visible.append(line)
    return tuple(visible)


def canonicalize_heading_levels(text: str) -> str:
    """Map prose headings to the vault's H2/H3 body hierarchy.

    Heading markers are presentational structure, so a model-generated H1 or
    overly deep heading can be repaired without changing its text. Fenced code
    is kept byte-for-byte because ``#`` has language-specific meaning there.
    """
    lines = text.splitlines()
    prose = markdown_lines_outside_fences(text)
    levels = [len(match.group(1)) for line in prose if (match := _ATX_HEADING.match(line))]
    if not levels:
        return text

    shallowest = min(levels)
    rendered: list[str] = []
    fence_char = ""
    fence_length = 0
    for line in lines:
        fence = _FENCE.match(line)
        if fence_char:
            rendered.append(line)
            if fence:
                marker = fence.group("marker")
                if marker[0] == fence_char and len(marker) >= fence_length and not fence.group("rest").strip():
                    fence_char = ""
                    fence_length = 0
            continue
        if fence:
            marker = fence.group("marker")
            fence_char = marker[0]
            fence_length = len(marker)
            rendered.append(line)
            continue
        match = _ATX_HEADING.match(line)
        if match:
            depth = len(match.group(1)) - shallowest
            line = "#" * min(3, 2 + depth) + match.group(2)
        rendered.append(line)
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(rendered) + suffix


def _markdown_source(bundle: InputBundle) -> Path | None:
    return next((item.path for item in bundle.items if item.path.suffix.lower() == ".md"), None)


def _frontmatter_tags(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = _FRONTMATTER.match(text)
    if match is None:
        return set()
    return {
        tag.strip().strip("\"'")
        for tag in re.findall(r"^\s+-\s+(.+?)\s*$", match.group("body"), re.MULTILINE)
    }


def resolve_edit_mode(bundle: InputBundle, plan: OrganizePlan) -> str:
    """Choose between loss-minimizing preservation and AI reorganization.

    Explicit source metadata wins. Model-classified fragments are reorganized
    only at high confidence; uncertainty always falls back to preservation.
    """
    source = _markdown_source(bundle)
    if source is None:
        if plan.source_type in PRESERVED_SOURCE_TYPES:
            return "preserve"
        return "organize"
    tags = _frontmatter_tags(source)
    if "kind/web-clip" in tags or "clippings" in tags:
        return "preserve"
    if "kind/analysis" in tags:
        return "preserve"
    if plan.source_type in PRESERVED_SOURCE_TYPES:
        return "preserve"
    if plan.source_type == "fragment" and plan.confidence >= FRAGMENT_CONFIDENCE_THRESHOLD:
        return "organize"
    return "preserve"


def critical_tokens(text: str) -> tuple[str, ...]:
    patterns = (
        r"https?://[^\s<>)\]]+",
        r"\b\d{1,4}(?:[./:-]\d{1,4})+(?:\b|(?=\s))",
        r"\b\d+(?:\.\d+)?\s*(?:%|元|万元|公里|米|分钟|小时|天|次|GB|MB|kg|g)\b",
        r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])",
    )
    found: list[str] = []
    for pattern in patterns:
        found.extend(re.findall(pattern, text, flags=re.IGNORECASE))
    return tuple(dict.fromkeys(token.strip() for token in found if token.strip()))


def missing_critical_tokens(source: str, rendered: str) -> tuple[str, ...]:
    return tuple(token for token in critical_tokens(source) if token not in rendered)


def tags_for_source_type(source_type: str) -> tuple[str, ...]:
    kind = {
        "web_article": "kind/web-clip",
        "personal_analysis": "kind/analysis",
        "reference": "kind/reference",
        "fragment": "kind/note",
    }.get(source_type, "kind/note")
    return (kind,)
