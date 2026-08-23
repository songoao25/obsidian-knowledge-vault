#!/usr/bin/env python3
"""Render the cross-platform Obsidian plugin settings from the canonical tag policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag-policy", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    policy = json.loads(args.tag_policy.read_text(encoding="utf-8"))
    settings = {
        "delayMs": 500,
        "fallbackTags": ["kind/note"],
        "pathDefaults": policy["path_defaults"],
    }
    args.output.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
