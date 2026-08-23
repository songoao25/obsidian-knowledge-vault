from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import DEFAULT_SETTINGS, PROJECT_ROOT, VaultConfig
from .scaffold import install_vault_assets, load_taxonomy, merge_obsidian_settings, scaffold_directories
from .audit import audit
from .model_router import ModelRequest, ModelRouter
from .pipeline import run_maintenance
from .metadata import TagPolicy, compile_metadata_changes
from .prompts import SEARCH_SYSTEM
from .search import VaultSearch
from .transaction import VaultTransaction


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="knowledge-vault")
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS)
    sub = parser.add_subparsers(dest="command", required=True)
    scaffold = sub.add_parser("scaffold")
    mode = scaffold.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    sub.add_parser("run")
    audit_parser = sub.add_parser("audit")
    audit_parser.add_argument("--launch-agent", type=Path)
    metadata_parser = sub.add_parser("metadata-audit")
    metadata_parser.add_argument("--apply", action="store_true")
    metadata_parser.add_argument("--replace-created-if")
    metadata_parser.add_argument("--replace-modified-if")
    metadata_parser.add_argument("--repair-from-rollback", type=Path)
    sub.add_parser("index")
    query = sub.add_parser("query")
    query.add_argument("question")
    query.add_argument("--no-ai", action="store_true")
    smoke = sub.add_parser("provider-smoke")
    smoke.add_argument("--force-fallback", action="store_true")
    health = sub.add_parser("health")
    health.add_argument("--launch-agent", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    cfg = VaultConfig.load(args.settings)
    errors = cfg.validate()
    if errors:
        for error in errors:
            print(f"ERROR {error}")
        return 2
    if args.command == "scaffold":
        dry_run = args.dry_run
        paths = load_taxonomy(PROJECT_ROOT / "配置" / "taxonomy.json")
        created = scaffold_directories(cfg.vault_path, paths, dry_run=dry_run)
        report = install_vault_assets(PROJECT_ROOT / "资源与模板" / "初始知识库模板", cfg.vault_path, dry_run=dry_run)
        merge_obsidian_settings(cfg.vault_path, dry_run=dry_run)
        for path in created:
            print(f"CREATE_DIR {path.relative_to(cfg.vault_path)}")
        for path in report.created:
            print(f"CREATE_FILE {path.relative_to(cfg.vault_path)}")
        for path in report.conflicts:
            print(f"CONFLICT {path.relative_to(cfg.vault_path)}")
        print(f"SUMMARY dirs={len(created)} files={len(report.created)} conflicts={len(report.conflicts)} dry_run={dry_run}")
        return 1 if report.conflicts else 0
    if args.command == "run":
        result = run_maintenance(cfg)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result["failed"] else 0
    if args.command in {"audit", "health"}:
        report = audit(cfg, args.launch_agent)
        for item in report.passed:
            print(f"PASS {item}")
        for item in report.failed:
            print(f"FAIL {item}")
        return 0 if report.ok else 1
    if args.command == "metadata-audit":
        policy = TagPolicy.load(PROJECT_ROOT / "配置" / "tag_policy.json")
        created_overrides: dict[Path, datetime] = {}
        modified_overrides: dict[Path, datetime] = {}
        if args.repair_from_rollback:
            manifest_path = args.repair_from_rollback / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for entry in manifest:
                if not entry.get("existed") or not entry.get("backup"):
                    continue
                target = Path(entry["target"])
                try:
                    relative = target.relative_to(cfg.vault_path)
                except ValueError:
                    continue
                backup = Path(entry["backup"])
                preserved_time = datetime.fromtimestamp(
                    backup.stat().st_mtime, tz=ZoneInfo(cfg.timezone)
                )
                created_overrides[relative] = preserved_time
                modified_overrides[relative] = preserved_time
        changes, report = compile_metadata_changes(
            cfg.vault_path,
            policy,
            now=datetime.now(ZoneInfo(cfg.timezone)),
            replace_created_if=args.replace_created_if,
            replace_modified_if=args.replace_modified_if,
            created_overrides=created_overrides,
            modified_overrides=modified_overrides,
        )
        for path in report.changed_paths:
            print(f"METADATA_CHANGE {path.as_posix()}")
        for path, tags in report.unknown_tags.items():
            print(f"UNKNOWN_TAGS {path.as_posix()} {' '.join(tags)}")
        if args.apply and changes:
            result = VaultTransaction(PROJECT_ROOT / ".state" / "rollbacks").apply(changes)
            if not result.ok:
                print(f"ERROR {result.error}")
                return 1
            print(f"APPLIED {len(result.applied)} rollback={result.rollback_dir}")
        print(f"SUMMARY changes={len(changes)} unknown={len(report.unknown_tags)} apply={args.apply}")
        return 0
    if args.command == "index":
        search = VaultSearch(PROJECT_ROOT / ".state/search.sqlite3")
        try:
            print(f"INDEXED {search.rebuild(cfg.vault_path)}")
        finally:
            search.close()
        return 0
    if args.command == "query":
        search = VaultSearch(PROJECT_ROOT / ".state/search.sqlite3")
        try:
            hits = search.query(args.question)
        finally:
            search.close()
        if not hits:
            print("知识库里暂未找到可支持回答的笔记。")
            return 0
        if args.no_ai:
            for hit in hits:
                print(f"[[{Path(hit.relative_path).with_suffix('')}|{hit.title}]] — {hit.snippet}")
            return 0
        payload = [{"path": hit.relative_path, "title": hit.title, "snippet": hit.snippet} for hit in hits]
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}, "paths": {"type": "array", "items": {"type": "string"}}},
            "required": ["answer", "paths"], "additionalProperties": False,
        }
        response = ModelRouter(cfg).complete(ModelRequest(
            SEARCH_SYSTEM, f"问题：{args.question}\n检索片段：{json.dumps(payload, ensure_ascii=False)}", schema,
        )).payload
        print(response.get("answer", "知识库里暂未找到可支持回答的笔记。"))
        return 0
    if args.command == "provider-smoke":
        schema = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}, "message": {"type": "string"}},
            "required": ["ok", "message"], "additionalProperties": False,
        }
        router = ModelRouter(cfg)
        if args.force_fallback:
            from .model_router import ModelError
            class FailedCodex:
                def complete(self, request):
                    raise ModelError("quota", "smoke: simulated quota error")
            router.codex = FailedCodex()
        result = router.complete(ModelRequest("只返回符合 Schema 的 JSON。", "返回 ok=true，message=provider smoke。", schema))
        print(json.dumps({"provider": result.provider, "model": result.model, "fallback_reason": result.fallback_reason, "payload": result.payload}, ensure_ascii=False))
        return 0 if result.payload.get("ok") is True else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
