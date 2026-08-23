from __future__ import annotations

from datetime import date, datetime, time
import json
from pathlib import Path
import re

from .archive import archive_markdown_with_no_delete_property, build_manifest, choose_archive_directory, choose_archive_file, manifest_path
from .content_policy import canonicalize_heading_levels, missing_critical_tokens, resolve_edit_mode, tags_for_source_type
from .models import FileChange, InputBundle, OrganizePlan
from .state import sha256_file
from .media import inventory_markdown, rewrite_local_references, source_markdown_body


_CREATED_PROPERTY = re.compile(r"^created:\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|Z)?)\s*$", re.MULTILINE)


def safe_title(title: str) -> str:
    cleaned = re.sub(r'[/:\\\n\r\t<>"|?*]', " ", title)
    return re.sub(r"\s+", " ", cleaned).strip().strip(".")[:100]


def note_filename(collected_on: date, title: str, ai_maintained: bool = False) -> str:
    suffix = "-AI" if ai_maintained else ""
    return f"{collected_on:%Y-%m-%d} {safe_title(title)}{suffix}.md"


def source_created_at(bundle: InputBundle, fallback: datetime) -> datetime:
    """Keep an Obsidian capture's original creation time when it is trustworthy."""
    for item in bundle.items:
        if item.path.suffix.lower() != ".md":
            continue
        match = _CREATED_PROPERTY.search(item.path.read_text(encoding="utf-8", errors="replace")[:4096])
        if match is None:
            continue
        try:
            parsed = datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None and fallback.tzinfo is not None:
            parsed = parsed.replace(tzinfo=fallback.tzinfo)
        return parsed
    return fallback


def _comparable_titles(title: str, ai_marked: bool = False) -> set[str]:
    stem = Path(title).stem.strip()
    candidates = {stem}
    date_match = re.match(r"^(\d{4}-\d{2}-\d{2})\s+", stem)
    without_date = re.sub(r"^\d{4}-\d{2}-\d{2}\s+", "", stem).strip()
    candidates.add(without_date)
    if ai_marked or stem.endswith("-AI"):
        candidates.update({value.removesuffix("-AI").strip() for value in tuple(candidates)})
        core = without_date.removesuffix("-AI").strip()
        candidates.add(f"AI {core}")
        if date_match:
            candidates.add(f"{date_match.group(1)} AI {core}")
    return {re.sub(r"\s+", " ", value).strip() for value in candidates if value.strip()}


def strip_redundant_opening_title(body: str, title: str, *, ai_marked: bool = False) -> str:
    """Remove consecutive opening lines whose only role is naming the document."""
    lines = body.splitlines(keepends=True)
    comparable = _comparable_titles(title, ai_marked)
    while True:
        first_content = next((index for index, line in enumerate(lines) if line.strip()), None)
        if first_content is None:
            return ""
        opening = lines[first_content].strip()
        opening = re.sub(r"^#{1,6}\s+", "", opening).strip()
        opening = re.sub(r"\s+", " ", opening)
        if opening not in comparable:
            return "".join(lines)
        lines = lines[first_content + 1 :]
        while lines and not lines[0].strip():
            lines.pop(0)


def normalize_preserved_body(body: str, title: str, *, ai_marked: bool = False) -> str:
    without_document_title = strip_redundant_opening_title(body, title, ai_marked=ai_marked).strip()
    return canonicalize_heading_levels(without_document_title).strip()


def extracted_source_body(bundle: InputBundle) -> str:
    """Build a readable preservation body from local non-Markdown extraction."""
    extracted = [(content.path.name, content.text.strip()) for content in bundle.contents if content.text.strip()]
    if len(extracted) == 1:
        return extracted[0][1]
    return "\n\n---\n\n".join(f"**来源文件：{name}**\n\n{text}" for name, text in extracted)


def render_note(
    plan: OrganizePlan,
    attachment_names: list[str] | None = None,
    *,
    created_at: datetime | None = None,
    tags: tuple[str, ...] = ("kind/note",),
    source_body: str | None = None,
) -> str:
    preserving_source = source_body is not None
    cleaned_plan_body = canonicalize_heading_levels(
        strip_redundant_opening_title(plan.body, plan.title, ai_marked=plan.continuous_maintenance),
    ).strip()
    body = source_body.strip() if preserving_source else cleaned_plan_body
    if not body:
        # A title-only capture still represents user input. Keep the title as
        # ordinary body text instead of leaving it stranded in the inbox.
        body = canonicalize_heading_levels(plan.body).strip()
    sections = [body]
    names = attachment_names or list(plan.attachment_names)
    missing_attachments = [name for name in names if name not in body]
    if missing_attachments:
        sections.append("## 附件\n\n" + "\n".join(f"- [[{name}]]" for name in missing_attachments))
    body_urls = set(re.findall(r"https?://[^\s<>)\]]+", body))
    remaining = [url for url in plan.source_urls if url not in body_urls]
    if remaining:
        sections.append("## 来源\n\n" + "\n".join(f"- {url}" for url in remaining))
    rendered = "\n\n".join(sections).rstrip() + "\n"
    if created_at is not None:
        timestamp = created_at.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")
        rendered = "---\n" + "\n".join([
            f"created: {timestamp}",
            f"modified: {timestamp}",
            "tags:",
            *(f"  - {tag}" for tag in tags),
            "---",
            "",
        ]) + rendered
    issues = validate_note(rendered, set(names))
    if issues:
        raise ValueError("笔记质量校验失败：" + "；".join(issues))
    return rendered


def validate_note(body: str, attachment_names: set[str] | None = None) -> list[str]:
    issues: list[str] = []
    if re.search(r"(?:希望对你有帮助|如果需要.*告诉我|作为(?:一个|一名)AI)", body):
        issues.append("存在对话式 AI 收尾")
    for linked in re.findall(r"\[\[([^\]|]+)", body):
        if "-附件/" in linked and attachment_names is not None and linked not in attachment_names:
            issues.append(f"附件引用无对应文件：{linked}")
    return issues


def compile_create_changes(
    vault: Path,
    bundle: InputBundle,
    plan: OrganizePlan,
    collected_on: date,
    *,
    created_at: datetime | None = None,
    archived_at: datetime | None = None,
) -> list[FileChange]:
    target_dir = vault / plan.target_dir
    filename = note_filename(collected_on, plan.title, plan.continuous_maintenance)
    note_path = target_dir / filename
    if note_path.exists():
        raise FileExistsError(note_path)
    markdown_source = next((item for item in bundle.items if item.path.suffix.lower() == ".md"), None)
    attachments = [item for item in bundle.items if item is not markdown_source and item.kind != "text"]
    attachment_names: list[str] = []
    changes: list[FileChange] = []
    replacements: dict[str, str] = {}
    if attachments:
        attachment_dir = target_dir / f"{filename[:-3]}-附件"
        for item in attachments:
            name = item.path.name
            destination = attachment_dir / name
            if destination.exists() and sha256_file(destination) != item.sha256:
                destination = attachment_dir / f"{item.path.stem}-{item.sha256[:8]}{item.path.suffix}"
            attachment_names.append(f"{attachment_dir.name}/{destination.name}")
            replacements[item.relative_path.name] = f"{attachment_dir.name}/{destination.name}"
            changes.append(FileChange.write(destination, item.path.read_bytes()))
    timestamp = created_at or datetime.combine(collected_on, time.min)
    archive_timestamp = archived_at or timestamp
    source_body = None
    edit_mode = resolve_edit_mode(bundle, plan)
    if markdown_source is not None:
        original_body = rewrite_local_references(source_markdown_body(markdown_source.path), replacements)
        if not original_body:
            raise ValueError("内容保全校验失败：原始 Markdown 正文为空")
        if edit_mode == "preserve":
            source_body = normalize_preserved_body(original_body, plan.title, ai_marked=plan.continuous_maintenance)
    elif edit_mode == "preserve":
        original_body = extracted_source_body(bundle)
        if not original_body:
            raise ValueError("内容保全校验失败：非 Markdown 资料没有可用的本地提取正文")
        source_body = normalize_preserved_body(original_body, plan.title, ai_marked=plan.continuous_maintenance)
    note_content = render_note(
        plan,
        attachment_names,
        created_at=timestamp,
        tags=tags_for_source_type(plan.source_type),
        source_body=source_body,
    )
    if markdown_source is not None and edit_mode == "organize":
        missing_tokens = missing_critical_tokens(source_markdown_body(markdown_source.path), note_content)
        if missing_tokens:
            raise ValueError("碎片整理丢失关键数据：" + "、".join(missing_tokens))
    inventory = inventory_markdown(source_body or plan.body)
    if source_body is not None:
        missing = [url for url in inventory.remote_urls if url not in note_content]
        if missing:
            raise ValueError("内容保全校验失败：正式笔记缺少原始媒体或链接")
    changes.insert(0, FileChange.write(note_path, note_content))
    archive = choose_archive_directory(vault, bundle, collected_on)
    used_archive_targets: set[Path] = set()
    source_destinations: list[tuple] = []
    for item in bundle.items:
        destination = choose_archive_file(archive, item, used_archive_targets)
        source_destinations.append((item, destination))
        changes.append(FileChange.move(item.path, destination))
        if item.path.suffix.lower() == ".md":
            changes.append(FileChange.write(destination, archive_markdown_with_no_delete_property(item.path.read_text(encoding="utf-8"))))
    manifest = build_manifest(
        bundle=bundle,
        archive=archive,
        source_destinations=source_destinations,
        formal_note=note_path.relative_to(vault).as_posix(),
        remote_urls=list(inventory.remote_urls),
        local_attachments=attachment_names,
        archived_at=archive_timestamp,
        source_type=plan.source_type,
        edit_mode=edit_mode,
        preserved_body=source_body or "",
    )
    changes.append(FileChange.write(manifest_path(vault, bundle, collected_on), json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"))
    return changes


def compile_classify_changes(
    vault: Path,
    bundle: InputBundle,
    plan: OrganizePlan,
    collected_on: date,
) -> list[FileChange]:
    """直接分类：原样把文件搬进分类目录，不建笔记、不归档、不写 manifest。

    已成稿 Markdown 重命名为 ``YYYY-MM-DD 标题.md``；其余文件（含被 Markdown
    显式引用的附件）保留其在收件箱内的相对子目录结构搬到 target_dir，使 wikilink
    与 markdown 相对链接都不断。
    """
    target_dir = vault / plan.target_dir
    changes: list[FileChange] = []
    markdown_source = next((item for item in bundle.items if item.path.suffix.lower() == ".md"), None)
    for item in bundle.items:
        if item is markdown_source:
            destination = target_dir / note_filename(collected_on, plan.title, plan.continuous_maintenance)
        else:
            destination = target_dir / item.relative_path
        if destination.exists():
            if sha256_file(destination) == item.sha256:
                continue
            destination = target_dir / f"{destination.stem}-{item.sha256[:8]}{destination.suffix}"
        changes.append(FileChange.move(item.path, destination))
    return changes


def compile_append_changes(
    vault: Path,
    bundle: InputBundle,
    plan: OrganizePlan,
    collected_on: date,
    *,
    archived_at: datetime | None = None,
) -> list[FileChange]:
    target = vault / plan.existing_note
    if not target.exists():
        raise FileNotFoundError(target)
    addition = render_note(plan)
    original = target.read_text(encoding="utf-8")
    changes = [FileChange.write(target, original.rstrip() + "\n\n" + addition, update_modified=True)]
    archive = choose_archive_directory(vault, bundle, collected_on)
    used_archive_targets: set[Path] = set()
    source_destinations: list[tuple] = []
    for item in bundle.items:
        destination = choose_archive_file(archive, item, used_archive_targets)
        source_destinations.append((item, destination))
        changes.append(FileChange.move(item.path, destination))
        if item.path.suffix.lower() == ".md":
            changes.append(FileChange.write(destination, archive_markdown_with_no_delete_property(item.path.read_text(encoding="utf-8"))))
    source_text = "\n".join(content.text for content in bundle.contents)
    inventory = inventory_markdown(source_text)
    timestamp = archived_at or datetime.combine(collected_on, time.min)
    manifest = build_manifest(
        bundle=bundle,
        archive=archive,
        source_destinations=source_destinations,
        formal_note=target.relative_to(vault).as_posix(),
        remote_urls=list(inventory.remote_urls),
        local_attachments=[],
        archived_at=timestamp,
        source_type=plan.source_type,
        edit_mode="organize",
    )
    changes.append(FileChange.write(manifest_path(vault, bundle, collected_on), json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"))
    return changes


def can_ai_update(note: Path, remembered_ai_hash: str | None) -> bool:
    return bool(note.exists() and remembered_ai_hash and sha256_file(note) == remembered_ai_hash)
