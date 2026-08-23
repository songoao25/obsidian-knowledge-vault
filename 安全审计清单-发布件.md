# Public-release privacy checklist

This repository may contain code, templates, documentation, verified release
archives, and checksums only. It must never contain a real Vault, notes,
credentials, logs, machine paths, or provider responses.

## Required before every release

1. Run `PYTHONDONTWRITEBYTECODE=1 python3 scripts/build_release.py`. It builds
   the general and legal profiles for macOS and Windows, scans the source,
   payloads, archives, and repository, and writes checksums.
2. Run `gitleaks detect --source . --log-opts='--all' --redact` to scan the
   complete Git history for secrets.
3. Confirm that the release is made from a clean public-history repository;
   never expose an earlier private repository whose history contains old
   archives or user-specific material.
4. Confirm the automation checks pass on both GitHub-hosted macOS and Windows
   runners. Those checks use disposable folders only.
5. Review the GitHub release description and assets immediately before publish.

## Never publish

- API keys, tokens, passwords, keychain exports, DPAPI files, `.env` files, or
  environment dumps.
- Personal notes, Obsidian exports, screenshots, run logs, recovery data, or
  computer-specific paths.
- Marketing drafts, launch scripts, or internal planning material. Keep public
  repository documentation limited to product use and contribution.
