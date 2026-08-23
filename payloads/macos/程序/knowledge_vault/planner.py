from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from urllib.parse import urlparse

from .deepseek import DeepSeekClient
from .config import is_manual_only_path
from .content_policy import canonicalize_heading_levels, markdown_lines_outside_fences
from .model_router import ModelRequest, ModelRouter
from .models import InputBundle, OrganizePlan
from .prompts import BATCH_ORGANIZE_SYSTEM, ORGANIZE_SYSTEM, organize_batch_prompt, organize_user_prompt


URL_RE = re.compile(r"https?://[^\s<>)\]]+")


PLAN_PROPERTIES = {
    "input_id": {"type": "string"},
    "title": {"type": "string"},
    "target_dir": {"type": "string"},
    "action": {"type": "string", "enum": ["create", "classify"]},
    "body": {"type": "string"},
    "source_type": {"type": "string", "enum": ["web_article", "personal_analysis", "fragment", "reference"]},
    "source_urls": {"type": "array", "items": {"type": "string"}},
    "attachment_names": {"type": "array", "items": {"type": "string"}},
    "existing_note": {"type": "string"},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "rationale": {"type": "string"},
    "continuous_maintenance": {"type": "boolean"},
}

BATCH_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "properties": {
        "plans": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": PLAN_PROPERTIES,
                "required": list(PLAN_PROPERTIES),
                "additionalProperties": False,
            },
        },
        "relationships": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "file_ids": {"type": "array", "items": {"type": "string"}, "minItems": 2},
                    "relationship_type": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "strong_evidence": {"type": "array", "items": {"type": "string"}},
                    "weak_evidence": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["file_ids", "relationship_type", "confidence", "strong_evidence", "weak_evidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["plans", "relationships"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class RelationshipSuggestion:
    file_ids: tuple[str, ...]
    relationship_type: str
    confidence: float
    strong_evidence: tuple[str, ...]
    weak_evidence: tuple[str, ...]


@dataclass(frozen=True)
class PlannedBundle:
    bundle: InputBundle
    plan: OrganizePlan
    provider: str
    model: str
    fallback_reason: str = ""
    duration: float = 0.0


def load_catalog(path: Path, excluded_paths: tuple[str, ...] = ()) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        p for p in data["paths"]
        if p.startswith(("10 生活/", "20 工作/", "30 学习/"))
        and not is_manual_only_path(p, excluded_paths)
    ]


def _bounded_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    marker = "\n\n[中间内容因模型批次上限省略；原文仍在本地完整保全]\n\n"
    available = max(0, limit - len(marker))
    head = available // 2
    return value[:head] + marker + value[-(available - head):]


def _files_payload(bundle: InputBundle, text_limit: int | None = None) -> list[dict]:
    by_path = {content.path: content for content in bundle.contents}
    result = []
    for item in bundle.items:
        content = by_path.get(item.path)
        result.append({
            "name": item.relative_path.as_posix(),
            "kind": item.kind,
            "text": _bounded_text(content.text, text_limit) if content and text_limit is not None else (content.text if content else ""),
            "metadata": content.metadata if content else {},
            "extraction_status": content.status if content else "missing",
            "extraction_reason": content.reason if content else "",
            "media_count": len(content.media_paths) if content else 0,
        })
    return result


def validate_plan(raw: dict, catalog: list[str], bundle: InputBundle, allowed_existing: set[str] | None = None) -> OrganizePlan:
    required = {"title", "target_dir", "action", "body", "source_type", "source_urls", "attachment_names", "existing_note", "confidence", "rationale", "continuous_maintenance"}
    missing = required - raw.keys()
    if missing:
        raise ValueError(f"整理方案缺少字段：{', '.join(sorted(missing))}")
    unknown = raw.keys() - required
    if unknown:
        raise ValueError(f"整理方案含未知字段：{', '.join(sorted(unknown))}")
    title = str(raw["title"]).strip()
    if not title or any(char in title for char in "/\\:\n\r"):
        raise ValueError("标题为空或含非法字符")
    target = str(raw["target_dir"]).strip().strip("/")
    if target not in catalog:
        raise ValueError(f"目标目录不在既有分类中：{target}")
    action = str(raw["action"])
    if action not in {"create", "classify"}:
        raise ValueError("action 必须是 create 或 classify")
    body = canonicalize_heading_levels(str(raw["body"])).strip()
    source_type = str(raw["source_type"]).strip()
    allowed_source_types = {"web_article", "personal_analysis", "fragment", "reference"}
    if source_type not in allowed_source_types:
        raise ValueError(f"source_type 必须是 {', '.join(sorted(allowed_source_types))} 之一")
    if action == "create":
        if not body and source_type == "fragment":
            raise ValueError("碎片整理结果为空")
        if re.search(r"^#\s", "\n".join(markdown_lines_outside_fences(body)), re.MULTILINE):
            raise ValueError("正文不得包含一级标题")
    # 非 Markdown 资料只能直接分类（classify），不得整理成笔记。
    has_markdown = any(item.path.suffix.lower() == ".md" for item in bundle.items)
    if action == "create" and not has_markdown:
        raise ValueError("非 Markdown 资料只能直接分类（classify），不得整理成笔记")
    urls = tuple(str(url).strip() for url in raw["source_urls"] if str(url).strip())
    if any(urlparse(url).scheme not in {"http", "https"} for url in urls):
        raise ValueError("来源 URL 格式错误")
    input_urls = set(URL_RE.findall("\n".join(c.text for c in bundle.contents)))
    if not set(urls).issubset(input_urls):
        raise ValueError("方案含有输入中不存在的来源 URL")
    confidence = float(raw["confidence"])
    if not 0 <= confidence <= 1:
        raise ValueError("confidence 必须在 0 到 1 之间")
    existing = str(raw["existing_note"]).strip()
    if existing:
        raise ValueError("新建方案不得指定 existing_note")
    return OrganizePlan(
        title=title,
        target_dir=target,
        action=action,
        body=body,
        source_urls=urls,
        attachment_names=tuple(map(str, raw["attachment_names"])),
        existing_note=existing,
        confidence=confidence,
        rationale=str(raw["rationale"]).strip(),
        continuous_maintenance=bool(raw["continuous_maintenance"]),
        source_type=source_type,
    )


def plan_bundle(client: DeepSeekClient, bundle: InputBundle, catalog: list[str], existing_notes: list[dict] | None = None) -> OrganizePlan:
    raw = client.json_completion(ORGANIZE_SYSTEM, organize_user_prompt(catalog, _files_payload(bundle), existing_notes))
    allowed = {str(note["path"]) for note in existing_notes or []}
    return validate_plan(raw, catalog, bundle, allowed)


def _request_for_bundles(
    bundles: list[InputBundle],
    catalog: list[str],
    existing_by_id: dict[str, list[dict]],
    max_chars: int = 80_000,
) -> ModelRequest:
    content_count = max(1, sum(len(bundle.contents) for bundle in bundles))
    per_content_limit = max(1, max_chars // content_count)
    inputs = [
        {
            "input_id": bundle.bundle_id,
            "allowed_directories": catalog,
            "input_files": _files_payload(bundle, per_content_limit),
            "possible_existing_notes": existing_by_id.get(bundle.bundle_id, []),
        }
        for bundle in bundles
    ]
    images = tuple(dict.fromkeys(
        path
        for bundle in bundles
        for content in bundle.contents
        for path in content.media_paths
    ))
    has_text_evidence = all(any(content.text.strip() for content in bundle.contents) for bundle in bundles)
    return ModelRequest(BATCH_ORGANIZE_SYSTEM, organize_batch_prompt(inputs), BATCH_SCHEMA, images, allow_text_only_fallback=has_text_evidence)


def _valid_relationships(raw: object, known_ids: set[str]) -> list[RelationshipSuggestion]:
    suggestions: list[RelationshipSuggestion] = []
    if not isinstance(raw, list):
        return suggestions
    for item in raw:
        if not isinstance(item, dict):
            continue
        ids = tuple(dict.fromkeys(str(value) for value in item.get("file_ids", [])))
        strong = tuple(str(value).strip() for value in item.get("strong_evidence", []) if str(value).strip())
        try:
            confidence = float(item.get("confidence", 0))
        except (TypeError, ValueError):
            continue
        if len(ids) < 2 or not set(ids).issubset(known_ids) or confidence < 0.92 or len(strong) < 2:
            continue
        suggestions.append(RelationshipSuggestion(
            ids,
            str(item.get("relationship_type", "资料包")).strip() or "资料包",
            confidence,
            strong,
            tuple(str(value).strip() for value in item.get("weak_evidence", []) if str(value).strip()),
        ))
    return suggestions


def plan_bundles(
    router: ModelRouter,
    bundles: list[InputBundle],
    catalog: list[str],
    existing_by_id: dict[str, list[dict]] | None = None,
) -> tuple[list[PlannedBundle], list[RelationshipSuggestion], list[tuple[InputBundle, Exception]]]:
    existing_by_id = existing_by_id or {}
    request = _request_for_bundles(bundles, catalog, existing_by_id, router.config.codex_batch_max_chars)
    result = router.complete(request)
    raw_plans = result.payload.get("plans", [])
    by_id = {str(raw.get("input_id")): raw for raw in raw_plans if isinstance(raw, dict)}
    planned: list[PlannedBundle] = []
    failed: list[tuple[InputBundle, Exception]] = []
    for bundle in bundles:
        raw = by_id.get(bundle.bundle_id)
        try:
            if raw is None:
                raise ValueError("批量结果缺少对应 input_id")
            single = dict(raw); single.pop("input_id", None)
            allowed = {str(note["path"]) for note in existing_by_id.get(bundle.bundle_id, [])}
            plan = validate_plan(single, catalog, bundle, allowed)
            planned.append(PlannedBundle(bundle, plan, result.provider, result.model, result.fallback_reason, result.duration))
        except Exception as error:
            if result.degraded:
                failed.append((bundle, error))
                continue
            try:
                fallback = router.complete_fallback(
                    _request_for_bundles([bundle], catalog, existing_by_id, router.config.codex_batch_max_chars),
                    "invalid_response",
                )
                fallback_raw = fallback.payload.get("plans", [])
                if len(fallback_raw) != 1 or fallback_raw[0].get("input_id") != bundle.bundle_id:
                    raise ValueError("备用模型结果缺少对应 input_id")
                single = dict(fallback_raw[0]); single.pop("input_id", None)
                allowed = {str(note["path"]) for note in existing_by_id.get(bundle.bundle_id, [])}
                plan = validate_plan(single, catalog, bundle, allowed)
                planned.append(PlannedBundle(bundle, plan, fallback.provider, fallback.model, fallback.fallback_reason, fallback.duration))
            except Exception as fallback_error:
                failed.append((bundle, fallback_error))
    relationships = _valid_relationships(result.payload.get("relationships"), {bundle.bundle_id for bundle in bundles})
    return planned, relationships, failed
