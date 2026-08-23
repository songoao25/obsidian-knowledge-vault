from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS = PROJECT_ROOT / "配置" / "settings.json"


@dataclass(frozen=True)
class VaultConfig:
    vault_path: Path
    timezone: str
    run_hours: tuple[int, ...]
    source_retention_days: int = 7
    stable_file_minutes: int = 10
    manual_only_paths: tuple[str, ...] = ()
    provider_order: tuple[str, ...] = ("codex", "deepseek")
    codex_binary: Path = Path("/Applications/ChatGPT.app/Contents/Resources/codex")
    codex_min_version: str = "0.144.5"
    codex_model: str = "gpt-5.6-luna"
    codex_reasoning_effort: str = "medium"
    codex_timeout_seconds: int = 180
    codex_batch_max_items: int = 10
    codex_batch_max_chars: int = 80_000
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_timeout_seconds: int = 120
    deepseek_thinking: bool = False
    keychain_service: str = "knowledge-vault-deepseek"
    api_providers: dict[str, dict] = field(default_factory=dict)
    expected_community_plugins: tuple[str, ...] = (
        "realclaudian", "frontmatter-modified-date", "obsidian-auto-organizer"
    )
    strict_local_audit: bool = True
    whisper_binary: Path = Path("/opt/homebrew/bin/whisper-cli")
    whisper_model: Path = Path("~/.local/share/knowledge-vault/models/ggml-small.bin")

    @classmethod
    def load(cls, path: Path = DEFAULT_SETTINGS) -> "VaultConfig":
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            vault_path=Path(raw["vault_path"]).expanduser(),
            timezone=raw["timezone"],
            run_hours=tuple(raw["run_hours"]),
            source_retention_days=raw.get("source_retention_days", 7),
            stable_file_minutes=raw.get("stable_file_minutes", 10),
            manual_only_paths=tuple(raw.get("manual_only_paths", [])),
            provider_order=tuple(raw.get("provider_order", ["codex", "deepseek"])),
            codex_binary=Path(raw.get("codex_binary", "/Applications/ChatGPT.app/Contents/Resources/codex")).expanduser(),
            codex_min_version=raw.get("codex_min_version", "0.144.5"),
            codex_model=raw.get("codex_model", "gpt-5.6-luna"),
            codex_reasoning_effort=raw.get("codex_reasoning_effort", "medium"),
            codex_timeout_seconds=raw.get("codex_timeout_seconds", 180),
            codex_batch_max_items=raw.get("codex_batch_max_items", 10),
            codex_batch_max_chars=raw.get("codex_batch_max_chars", 80_000),
            deepseek_model=raw.get("deepseek_model", "deepseek-v4-flash"),
            deepseek_timeout_seconds=raw.get("deepseek_timeout_seconds", 120),
            deepseek_thinking=raw.get("deepseek_thinking", False),
            keychain_service=raw.get("keychain_service", "knowledge-vault-deepseek"),
            api_providers=dict(raw.get("api_providers", {})),
            expected_community_plugins=tuple(raw.get("expected_community_plugins", [
                "realclaudian", "frontmatter-modified-date", "obsidian-auto-organizer"
            ])),
            strict_local_audit=bool(raw.get("strict_local_audit", True)),
            whisper_binary=Path(raw.get("whisper_binary", "/opt/homebrew/bin/whisper-cli")).expanduser(),
            whisper_model=Path(raw.get("whisper_model", "~/.local/share/knowledge-vault/models/ggml-small.bin")).expanduser(),
        )

    @property
    def known_providers(self) -> set[str]:
        return {"codex", "deepseek"} | set(self.api_providers)

    def provider_label(self, name: str) -> str:
        """Human-readable provider label (for logs and records)."""
        if name == "codex":
            return "Codex"
        if name == "deepseek":
            return "DeepSeek"
        return name

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.timezone:
            errors.append("timezone must not be empty")
        hours = self.run_hours
        if not hours or len(hours) > 24:
            errors.append("run_hours must contain 1-24 entries")
        elif hours != tuple(sorted(set(hours))):
            errors.append("run_hours must be unique and ascending")
        elif any(not isinstance(h, int) or h < 0 or h > 23 for h in hours):
            errors.append("run_hours entries must be integers in 0..23")
        if self.source_retention_days != 7:
            errors.append("source retention must be 7")
        if self.stable_file_minutes < 1:
            errors.append("stable_file_minutes must be positive")
        if len(self.manual_only_paths) != len(set(self.manual_only_paths)):
            errors.append("manual_only_paths must not contain duplicates")
        for path in self.manual_only_paths:
            relative = Path(path)
            if relative.is_absolute() or ".." in relative.parts or not path.startswith(("10 生活/", "20 工作/", "30 学习/")):
                errors.append(f"manual_only_path is unsafe: {path}")
        if not self.provider_order:
            errors.append("provider_order must not be empty")
        elif len(self.provider_order) != len(set(self.provider_order)):
            errors.append("provider_order must not contain duplicates")
        else:
            unknown = [p for p in self.provider_order if p not in self.known_providers]
            if unknown:
                errors.append(f"provider_order contains unknown providers: {unknown}")
        for name, spec in self.api_providers.items():
            if not isinstance(spec, dict) or not spec.get("base_url") or not spec.get("model"):
                errors.append(f"api_providers[{name}] must include base_url and model")
        if len(self.expected_community_plugins) != len(set(self.expected_community_plugins)):
            errors.append("expected_community_plugins must not contain duplicates")
        if self.codex_reasoning_effort not in {"low", "medium", "high"}:
            errors.append("codex_reasoning_effort must be low, medium, or high")
        if self.codex_batch_max_items < 1 or self.codex_batch_max_chars < 1:
            errors.append("codex batch limits must be positive")
        return errors


def is_manual_only_path(relative_path: str | Path, manual_only_paths: tuple[str, ...]) -> bool:
    path = Path(relative_path)
    return any(path == Path(root) or Path(root) in path.parents for root in manual_only_paths)
