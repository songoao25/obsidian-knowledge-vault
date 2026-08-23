from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
from zoneinfo import ZoneInfo

from .config import PROJECT_ROOT, VaultConfig
from .deepseek import DeepSeekClient
from .extractors import extract
from .planner import PlannedBundle, load_catalog, plan_bundle, plan_bundles
from .archive import compile_archive_migration_changes, compile_archive_no_delete_property_changes
from .models import InputBundle
from .high_impact_records import build_unexecuted_record
from .retention import apply_retention
from .scanner import group_items, scan_inbox
from .state import RunLock, StateStore, inspect_file
from .transaction import VaultTransaction
from .metadata import TagPolicy, compile_metadata_changes
from .writer import (
    can_ai_update,
    compile_append_changes,
    compile_classify_changes,
    compile_create_changes,
    note_filename,
    source_created_at,
)
from .search import VaultSearch, related_candidates
from .organize_records import append_run_entry, append_cleanup_entry, archive_daily_record, daily_record_path, ensure_daily_record
from .model_router import ModelRouter


def _source_names(bundle) -> str:
    names = [f"《{item.relative_path.stem}》" for item in bundle.items]
    return "、".join(names)


def _note_link(relative_path: str, title: str) -> str:
    path = Path(relative_path)
    target = path.with_suffix("") if path.suffix.lower() == ".md" else path
    return f"[[{target}|{title}]]"


def _result_card(source: str, summary: str, location: str | None = None, reason: str | None = None) -> str:
    lines = [f"**{source}**", "", summary]
    if location:
        lines.append(f"位置：{location.replace('/', ' / ')}")
    if reason:
        lines.append(f"原因：{reason}")
    return "\n".join(lines)


def _render_run_record(organized: list[str], unprocessed: list[str], notices: list[str]) -> str:
    def numbered(items: list[str]) -> str:
        return "\n\n".join(f"{index}. {item.replace(chr(10), chr(10) + '   ')}" for index, item in enumerate(items, start=1))

    parts: list[str] = []
    if organized:
        parts.append("**已整理**\n\n" + numbered(organized))
    if unprocessed:
        parts.append("**暂未整理**\n\n" + numbered(unprocessed))
    if notices:
        parts.append("**其他情况**\n\n" + "\n".join(f"- {notice}" for notice in notices))
    return "\n\n".join(parts) if parts else "收件箱没有待处理内容。"


MAX_DEFER_AUDIT_RECORDS = 3


def _defer_high_impact(
    *,
    transaction: VaultTransaction,
    state: StateStore,
    vault: Path,
    bundle: InputBundle,
    summary: str,
    reasons: list[str],
    now: datetime,
    unprocessed: list[str] | None = None,
) -> None:
    """暂缓处理：源文件原样留在收件箱，但**不写入「已处理」指纹**（避免被扫描器永久跳过）。

    只有真正成功归档的文件才通过 ``state.remember`` 登记；此处仅登记为「已暂缓」，
    并在达到上限前写入一条不可执行审计记录。暂缓项会出现在当日整理记录的「暂未整理」，
    确保不再被隐藏。
    """
    for item in bundle.items:
        state.mark_deferred(item.relative_path)
    if state.deferral_count(bundle.items[0].relative_path) <= MAX_DEFER_AUDIT_RECORDS:
        applied = transaction.apply([build_unexecuted_record(vault, now, summary, reasons)], now=now)
        if not applied.ok:
            raise RuntimeError(applied.error)
    if unprocessed is not None:
        unprocessed.append(_result_card(
            _source_names(bundle),
            "已暂缓，等待你本人确认。",
            reason="；".join(reasons) + "（原文件仍留在收件箱，后续运行会再次评估。）",
        ))


def _batch_bundles(bundles: list, max_items: int, max_chars: int) -> list[list]:
    batches: list[list] = []
    current: list = []
    size = 0
    item_count = 0
    for bundle in bundles:
        item_size = sum(len(content.text) for content in bundle.contents)
        bundle_items = len(bundle.items)
        if current and (item_count + bundle_items > max_items or size + item_size > max_chars):
            batches.append(current); current = []; size = 0; item_count = 0
        current.append(bundle); size += item_size; item_count += bundle_items
    if current:
        batches.append(current)
    return batches


def _append_provider_status(state_root: Path, planned: PlannedBundle, now: datetime) -> None:
    path = state_root / "provider_runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    item_hash = hashlib.sha256("\n".join(sorted(item.sha256 for item in planned.bundle.items)).encode()).hexdigest()
    record = {
        "at": now.isoformat(), "provider": planned.provider, "model": planned.model,
        "duration_seconds": round(planned.duration, 3), "status": "success",
        "error_category": "", "fallback_reason": planned.fallback_reason, "item_hash": item_hash,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _provider_models(config: VaultConfig) -> str:
    models = []
    for name in config.provider_order:
        if name == "codex":
            models.append(config.codex_model)
        elif name == "deepseek":
            models.append(config.deepseek_model)
        else:
            models.append((config.api_providers.get(name) or {}).get("model", name))
    return "|".join(models)


def _append_provider_error(state_root: Path, bundle, error: Exception, now: datetime, config: VaultConfig) -> None:
    path = state_root / "provider_runs.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    item_hash = hashlib.sha256("\n".join(sorted(item.sha256 for item in bundle.items)).encode()).hexdigest()
    category = getattr(error, "category", "invalid_response")
    record = {
        "at": now.isoformat(), "provider": "+".join(config.provider_order), "model": _provider_models(config),
        "duration_seconds": 0, "status": "error", "error_category": category,
        "fallback_reason": "", "item_hash": item_hash,
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def run_maintenance(config: VaultConfig, *, now: datetime | None = None, client: DeepSeekClient | ModelRouter | None = None) -> dict:
    now = now or datetime.now(ZoneInfo(config.timezone))
    router = ModelRouter(config) if client is None else (client if isinstance(client, ModelRouter) else None)
    state = StateStore(PROJECT_ROOT / ".state/state.json")
    log: list[str] = []
    organized: list[str] = []
    unprocessed: list[str] = []
    result = {"processed": 0, "failed": 0, "deferred": 0}
    organize_record = daily_record_path(config.vault_path / "00 收件箱", now)
    with RunLock(PROJECT_ROOT / ".state/run.lock"):
        try:
            organize_record = ensure_daily_record(config.vault_path / "00 收件箱", now)
            print("STAGE scan", flush=True)
            items = scan_inbox(config.vault_path / "00 收件箱", now, config.stable_file_minutes, state)
            print(f"STAGE scan_done items={len(items)}", flush=True)
            bundles = group_items(items)
            catalog = load_catalog(PROJECT_ROOT / "配置/taxonomy.json", config.manual_only_paths)
            transaction = VaultTransaction(PROJECT_ROOT / ".state/rollback")
            # 设计约定（2026-08-05）：Markdown 通过 Wikilink / Markdown 链接明确引用的附件，
            # 应与引用它的笔记作为「同一输入」正常整理归档；只有模型在下方 relationships 分支
            # 建议的「独立多文件合并」才需要本人确认并暂缓。group_items 产出的组合恰好是前者，
            # 因此不再一刀切 defer，直接纳入后续处理流程。
            bundles = list(bundles)
            extraction_root = PROJECT_ROOT / ".state/extractions" / now.strftime("%Y%m%dT%H%M%S")
            ready: list = []
            for bundle in bundles:
                bundle.contents = [extract(item, PROJECT_ROOT, config=config, work_dir=extraction_root / bundle.bundle_id) for item in bundle.items]
                if any(content.status != "ok" for content in bundle.contents):
                    result["deferred"] += 1
                    unprocessed.append(_result_card(
                        _source_names(bundle), "还没有整理成功。", reason="内容还没有成功读取；原文件仍留在收件箱。"
                    ))
                    continue
                ready.append(bundle)
            if router is None:
                planned_entries: list[PlannedBundle] = []
                planning_failures: list[tuple] = []
                for bundle in ready:
                    try:
                        candidate_text = "\n".join(content.text for content in bundle.contents)
                        candidates = related_candidates(config.vault_path, candidate_text, excluded_paths=config.manual_only_paths)
                        planned_entries.append(PlannedBundle(bundle, plan_bundle(client, bundle, catalog, candidates), "legacy", config.deepseek_model))
                    except Exception as exc:
                        planning_failures.append((bundle, exc))
            else:
                planned_entries = []
                planning_failures = []
                for batch in _batch_bundles(ready, config.codex_batch_max_items, config.codex_batch_max_chars):
                    existing = {
                        bundle.bundle_id: related_candidates(
                            config.vault_path,
                            "\n".join(content.text for content in bundle.contents),
                            excluded_paths=config.manual_only_paths,
                        )
                        for bundle in batch
                    }
                    try:
                        batch_plans, relationships, failures = plan_bundles(router, batch, catalog, existing)
                    except Exception as batch_error:
                        planning_failures.extend((bundle, batch_error) for bundle in batch)
                        continue
                    deferred_ids: set[str] = set()
                    for suggestion in relationships:
                        related = [bundle for bundle in batch if bundle.bundle_id in suggestion.file_ids]
                        combined_items = [item for bundle in related for item in bundle.items]
                        combined_contents = [content for bundle in related for content in bundle.contents]
                        combined = InputBundle("-".join(sorted(bundle.bundle_id for bundle in related)), combined_items, combined_contents)
                        _defer_high_impact(
                            transaction=transaction,
                            state=state,
                            vault=config.vault_path,
                            bundle=combined,
                            summary=f"未执行：{_source_names(related[0])} 等资料的合并建议",
                            reasons=list(suggestion.strong_evidence),
                            now=now,
                            unprocessed=unprocessed,
                        )
                        deferred_ids.update(suggestion.file_ids)
                        result["deferred"] += len(combined.items)
                    planned_entries.extend(entry for entry in batch_plans if entry.bundle.bundle_id not in deferred_ids)
                    planning_failures.extend(failures)
            for bundle, exc in planning_failures:
                result["failed"] += 1
                if router is not None:
                    _append_provider_error(PROJECT_ROOT / ".state", bundle, exc, now, config)
                unprocessed.append(_result_card(_source_names(bundle), "还没有整理成功。", reason=f"{exc}；原文件仍留在收件箱。"))
            for planned in planned_entries:
                bundle, plan = planned.bundle, planned.plan
                if planned.provider != "legacy":
                    _append_provider_status(PROJECT_ROOT / ".state", planned, now)
                    if planned.fallback_reason:
                        log.append(f"{_source_names(bundle)}：主模型暂不可用，本项由{config.provider_label(planned.provider)}完成。")
                try:
                    if plan.action == "classify":
                        changes = compile_classify_changes(
                            config.vault_path,
                            bundle,
                            plan,
                            now.date(),
                        )
                        applied = transaction.apply(changes, now=now)
                        if not applied.ok:
                            raise RuntimeError(applied.error)
                        result["processed"] += 1
                        primary = next((item for item in bundle.items if item.path.suffix.lower() == ".md"), bundle.items[0])
                        destination = next(
                            (change.target for change in changes if change.operation == "move" and change.source == primary.path),
                            config.vault_path / plan.target_dir / (note_filename(now.date(), plan.title, plan.continuous_maintenance) if primary.path.suffix.lower() == ".md" else primary.relative_path),
                        )
                        relative_note = destination.relative_to(config.vault_path).as_posix()
                        organized.append(_result_card(
                            _source_names(bundle),
                            f"已分类为：{_note_link(relative_note, plan.title)}。",
                            plan.target_dir,
                        ))
                        continue
                    if plan.action != "create":
                        target = config.vault_path / plan.existing_note
                        automatic = (
                            target.name.endswith("-AI.md")
                            and can_ai_update(target, state.ai_written_hash(Path(plan.existing_note)))
                            and plan.confidence >= 0.9
                            and not plan.attachment_names
                            and all(item.kind == "text" for item in bundle.items)
                        )
                        if automatic:
                            changes = compile_append_changes(config.vault_path, bundle, plan, now.date(), archived_at=now)
                            applied = transaction.apply(changes, now=now)
                            if not applied.ok:
                                raise RuntimeError(applied.error)
                            state.remember(Path(plan.existing_note), inspect_file(target), inspect_file(target).sha256)
                            result["processed"] += 1
                            organized.append(_result_card(
                                _source_names(bundle),
                        f"已补充到：{_note_link(plan.existing_note, Path(plan.existing_note).stem)}。",
                                Path(plan.existing_note).parent.as_posix(),
                            ))
                            continue
                        _defer_high_impact(
                            transaction=transaction,
                            state=state,
                            vault=config.vault_path,
                            bundle=bundle,
                            summary=f"未执行：更新已有笔记《{Path(plan.existing_note or plan.title).stem}》",
                            reasons=["模型建议修改已有正式笔记，需由本人在对话中明确指令后重新核对内容与现状。"],
                            now=now,
                            unprocessed=unprocessed,
                        )
                        result["deferred"] += 1
                        continue
                    changes = compile_create_changes(
                        config.vault_path,
                        bundle,
                        plan,
                        now.date(),
                        created_at=source_created_at(bundle, now),
                        archived_at=now,
                    )
                    applied = transaction.apply(changes, now=now)
                    if not applied.ok:
                        raise RuntimeError(applied.error)
                    result["processed"] += 1
                    created = config.vault_path / plan.target_dir / note_filename(now.date(), plan.title, plan.continuous_maintenance)
                    if plan.continuous_maintenance and created.exists():
                        created_state = inspect_file(created)
                        state.remember(created.relative_to(config.vault_path), created_state, created_state.sha256)
                    relative_note = created.relative_to(config.vault_path).as_posix()
                    organized.append(_result_card(
                        _source_names(bundle),
                        f"整理为：{_note_link(relative_note, plan.title)}。",
                        plan.target_dir,
                    ))
                except Exception as exc:
                    result["failed"] += 1
                    unprocessed.append(_result_card(
                        _source_names(bundle), "还没有整理成功。", reason=f"{exc}；原文件仍留在收件箱。"
                    ))
            shutil.rmtree(extraction_root, ignore_errors=True)
            print("STAGE retention", flush=True)
            archive_migrations = compile_archive_migration_changes(
                config.vault_path / "90 系统/95 原始输入归档",
                now=now,
            )
            if archive_migrations:
                applied = transaction.apply(archive_migrations, now=now)
                if not applied.ok:
                    raise RuntimeError(f"原始输入归档命名迁移失败：{applied.error}")
                log.append(f"原始输入归档已改为可读名称：{len(archive_migrations) // 2} 项")
            archive_properties = compile_archive_no_delete_property_changes(
                config.vault_path / "90 系统/95 原始输入归档",
            )
            if archive_properties:
                applied = transaction.apply(archive_properties, now=now)
                if not applied.ok:
                    raise RuntimeError(f"原始输入归档 no_delete 属性迁移失败：{applied.error}")
                log.append(f"原始输入归档已补齐 no_delete 属性：{len(archive_properties)} 项")
            retention = apply_retention(
                now,
                config.vault_path / "90 系统/95 原始输入归档",
                config.source_retention_days,
            )
            if retention.trashed:
                lines = [f"已自动定期清理到期归档原件，并移入系统废纸篓：{len(retention.trashed)} 项"]
                for trashed in retention.trashed:
                    rel = trashed.relative_to(config.vault_path).as_posix()
                    lines.append(f"- {rel}")
                try:
                    append_cleanup_entry(config.vault_path, now, "\n".join(lines))
                except Exception as exc:
                    log.append(f"清理记录写入失败：{exc}")
            for expired in retention.approvals_needed:
                relative = expired.relative_to(config.vault_path).as_posix()
                log.append(f"到期原始输入仍需本人处理，未自动删除：{relative}")
            print("STAGE retention_done", flush=True)
            policy = TagPolicy.load(PROJECT_ROOT / "配置/tag_policy.json")
            metadata_changes, metadata_report = compile_metadata_changes(config.vault_path, policy, now=now)
            if metadata_changes:
                applied = transaction.apply(metadata_changes, now=now)
                if not applied.ok:
                    raise RuntimeError(f"属性标签校验写入失败：{applied.error}")
                pass
            for path, tags in metadata_report.unknown_tags.items():
                log.append(f"属性标签待确认：{path.as_posix()} 含未知标签 {', '.join(tags)}")
            if result["processed"] or metadata_changes:
                search = VaultSearch(PROJECT_ROOT / ".state/search.sqlite3")
                try:
                    indexed = search.rebuild(config.vault_path)
                finally:
                    search.close()
            state.append_run({"at": now.isoformat(), **result})
            state.save()
            print("STAGE state_saved", flush=True)
        except Exception as exc:
            result["failed"] += 1
            log.append(f"运行失败：{type(exc).__name__}: {exc}")
        final_body = _render_run_record(organized, unprocessed, log)
        try:
            append_run_entry(organize_record, now, final_body)
            archive_daily_record(config.vault_path, now)
        except Exception:
            # 记录失败不能覆盖原始错误；没有其他失败时仍要如实返回失败。
            if result["failed"] == 0:
                result["failed"] = 1
    return result
