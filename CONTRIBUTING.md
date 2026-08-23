# Contributing

Thank you for helping improve Obsidian Knowledge Vault for macOS and Windows.

## Before opening a pull request

1. Do not add real notes, vault exports, logs, screenshots, account data, or credentials.
2. Keep deployment behaviour explicit: never overwrite a user's existing notes or silently send material to an external model.
3. Run the privacy gate:

   ```sh
   python3 tools/scan_privacy.py --root . --scope public
   ```

4. Validate the published package rather than only source files:

   ```sh
   PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
   PYTHONDONTWRITEBYTECODE=1 python3 scripts/build_release.py
   ```

5. Explain the user-facing impact and any privacy or compatibility change in the pull request.

## Pull-request rules

Automated privacy and release-package checks must pass before a pull request
merges. Changes merge automatically after those checks pass; no manual approval
is required by default.
