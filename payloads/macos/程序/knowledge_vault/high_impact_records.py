from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re

from .models import FileChange


def _safe_component(value: str) -> str:
    cleaned = re.sub(r'[/\\\n\r\t<>:"|?*]', " ", value)
    return re.sub(r"\s+", " ", cleaned).strip().strip(".")[:80] or "高影响操作"


def build_unexecuted_record(vault: Path, now: datetime, summary: str, reasons: list[str]) -> FileChange:
    """Create one audit record for an automatic action that must stay unexecuted."""
    parent = vault / "90 系统/94 维护记录/94.4 高影响操作记录" / f"{now:%Y}"
    stem = f"{now:%Y-%m-%d %H%M} 未执行 - {_safe_component(summary)}-AI"
    target = parent / f"{stem}.md"
    counter = 2
    while target.exists():
        target = parent / f"{stem} ({counter}).md"
        counter += 1
    rendered_reasons = "\n".join(f"- {reason}" for reason in reasons) or "- 触发高影响操作保护规则。"
    content = f"""---
created: {now:%Y-%m-%dT%H:%M:%S}
modified: {now:%Y-%m-%dT%H:%M:%S}
tags:
  - kind/maintenance-log
  - topic/automation
  - project/knowledge-vault
---
操作：未执行

{summary}

原因：

{rendered_reasons}

后续：需要本人在 Codex 对话中明确指令后，才可重新核对并执行。
"""
    return FileChange.write(target, content)
