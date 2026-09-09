#!/usr/bin/env python3
"""Build and privacy-check the two v1.2.1 public installation packages."""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERSION = "1.2.1"
RELEASE = ROOT / "release"
PAYLOADS = ROOT / "payloads"
SKIP = shutil.ignore_patterns("__pycache__", ".DS_Store", ".state", ".workbuddy")

sys.path.insert(0, str(ROOT / "tools"))
import scan_privacy  # noqa: E402


def copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=SKIP)


def run_payload_tests(payload: Path, platform: str) -> None:
    """Run the tests that will actually ship with a platform package.

    A release package is a smaller installation root than this repository, so
    tests must run after the payload has been assembled.  This catches tests
    that accidentally depend on release-factory-only files or paths.
    """
    test_root = payload / "测试"
    if not test_root.is_dir():
        return
    env = os.environ.copy()
    env["PYTHONPATH"] = str(payload / "程序")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", str(test_root), "-q"],
        check=True,
        env=env,
    )
    print(f"PASS package tests: {platform}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_clean(path: Path, label: str) -> None:
    report = scan_privacy.scan(path, scope="public")
    if report.clean:
        print(f"PASS privacy: {label}")
        return
    print(scan_privacy.render_md(report), file=sys.stderr)
    raise RuntimeError(f"privacy gate failed: {label}")


def package(platform: str, temporary: Path) -> Path:
    source = PAYLOADS / platform
    payload = temporary / platform
    copy_tree(source, payload)
    for name in ("deploy.py", "README.md", "AGENTS.md", "LICENSE"):
        shutil.copy2(ROOT / name, payload / name)
    run_payload_tests(payload, platform)
    assert_clean(payload, f"{platform} payload")
    filename = f"obsidian-knowledge-vault-{platform}-v{VERSION}.zip"
    result = temporary / filename
    with zipfile.ZipFile(result, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for item in sorted(payload.rglob("*")):
            relative = item.relative_to(payload)
            name = f"obsidian-knowledge-vault-{platform}-v{VERSION}/{relative.as_posix()}"
            if item.is_dir():
                archive.writestr(name + "/", "")
            else:
                archive.write(item, name)
    with tempfile.TemporaryDirectory(prefix="okv-verify-") as extract:
        with zipfile.ZipFile(result) as archive:
            archive.extractall(extract)
        assert_clean(Path(extract), f"{platform} package")
    return result


def main() -> int:
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_template_profiles.py")], check=True)
    with tempfile.TemporaryDirectory(prefix="okv-build-") as location:
        temporary = Path(location)
        artifacts = [package("macos", temporary), package("windows", temporary)]
        RELEASE.mkdir(parents=True, exist_ok=True)
        for artifact in artifacts:
            shutil.copy2(artifact, RELEASE / artifact.name)
        checksums = "\n".join(
            f"{sha256(artifact)}  {artifact.name}" for artifact in artifacts
        ) + "\n"
        (RELEASE / "checksums-sha256.txt").write_text(checksums, encoding="utf-8")
    assert_clean(ROOT, "repository")
    print(f"Built Obsidian Knowledge Vault v{VERSION} for macOS and Windows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
