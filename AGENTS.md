# Obsidian Knowledge Vault deployment instructions

This file is for an AI assistant helping a user install Obsidian Knowledge
Vault. Follow it exactly. Never treat a user's existing Vault as a test target.

## Safe deployment boundary

- v1.2 supports only a **new, empty directory** selected by the user. If it
  contains any item, stop without changing it.
- Ask the user to choose one profile: **general** for ordinary work and life,
  or **legal** for a legal-profession work taxonomy. The profiles differ only
  in their work-directory structure.
- Ask the user to choose a schedule and an AI provider. Supported choices are
  ChatGPT desktop on macOS, DeepSeek, or a user-supplied OpenAI-compatible API.
- Never request an API key in chat. The installer asks for it through hidden
  local terminal input and stores it only in the user's local credential store.
- Before changes, the installer displays its plan and asks for one approval.
  Do not bypass that approval unless the user explicitly chose automated mode.

## Installation

From a cloned repository, run `python3 deploy.py`. From a downloaded package,
unzip it and run `python3 deploy.py` from the extracted top-level folder. The
installer detects macOS or Windows, performs prerequisite checks, creates the
chosen empty Vault structure, installs only its own local components, and runs
verification.

## Validation and recovery

- Report checks that actually passed and any check that was not run.
- Do not claim the selected AI is ready unless the installer reports a successful
  provider check.
- If installation fails, read the exact failure message, leave the Vault alone,
  and rerun only after the user resolves the reported prerequisite.
- To stop automation, run `python3 deploy.py --uninstall`; this removes only
  the scheduled task, never the user's Vault or notes.
