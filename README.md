# Obsidian Knowledge Vault

**English** | [**中文**](README.zh-CN.md)

[![Release](https://img.shields.io/github/v/release/songoao25/obsidian-knowledge-vault)](https://github.com/songoao25/obsidian-knowledge-vault/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/songoao25/obsidian-knowledge-vault/test.yml?branch=main)](https://github.com/songoao25/obsidian-knowledge-vault/actions)
[![CodeQL](https://img.shields.io/github/actions/workflow/status/songoao25/obsidian-knowledge-vault/codeql.yml?label=CodeQL)](https://github.com/songoao25/obsidian-knowledge-vault/security/code-scanning)
[![License](https://img.shields.io/github/license/songoao25/obsidian-knowledge-vault)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/songoao25/obsidian-knowledge-vault)](https://github.com/songoao25/obsidian-knowledge-vault/commits/main)

Create a safe, automated starting point for a **new Obsidian vault**. Choose a
General or Legal template, choose when organization runs, connect an AI provider
you trust, and send material to one inbox for structured processing.

## What it is

Obsidian Knowledge Vault is an installer and maintenance toolkit for a fresh,
empty Obsidian vault. Its two templates share the same organizing system; only
the work-directory taxonomy differs:

- **General** — for ordinary work, life, learning, records, and system rules.
- **Legal** — for legal-profession work, with a legal work taxonomy.

The local maintenance program accepts constrained AI organization plans, checks
them locally, and writes only permitted files. It does not publish your notes or
use a cloud service run by this project.

## Install

Create a **new, empty folder** for the vault. Version 1.2 deliberately refuses
to install into an existing vault, so it cannot alter existing notes or Obsidian
settings.

```bash
git clone https://github.com/songoao25/obsidian-knowledge-vault.git
cd obsidian-knowledge-vault
python3 deploy.py
```

The installer asks you to choose a template, schedule, AI provider, and one
final local approval. You can also download the macOS or Windows package from
the [latest Release](https://github.com/songoao25/obsidian-knowledge-vault/releases/latest).

## Platforms and AI choices

| Platform | AI choices |
| --- | --- |
| macOS | ChatGPT Desktop, DeepSeek, or a user-supplied OpenAI-compatible provider |
| Windows | DeepSeek or a user-supplied OpenAI-compatible provider |

API keys are requested only through hidden local input and stay in the user's
local credential store. Never paste a key into an Issue or AI chat.

## Daily use

Put material that needs organization into the chosen inbox, then let the
schedule you selected run. You can change schedules, AI providers, and local
rules later without replacing your vault.

For the full workflow, see [Daily use](docs/en/DAILY-USE.md).

## Documentation

| English | 中文 |
| --- | --- |
| [Installation](docs/en/INSTALL.md) | [安装](docs/zh-CN/INSTALL.md) |
| [Daily use](docs/en/DAILY-USE.md) | [日常使用](docs/zh-CN/DAILY-USE.md) |
| [Customization](docs/en/CUSTOMIZATION.md) | [自定义](docs/zh-CN/CUSTOMIZATION.md) |
| [Troubleshooting](docs/en/TROUBLESHOOTING.md) | [排错](docs/zh-CN/TROUBLESHOOTING.md) |

## Privacy and safety

- This repository and its release packages contain templates and code only—no
  personal notes, credentials, logs, recovery data, or computer-specific paths.
- Content is sent only to the provider you actively choose during setup.
- Existing-vault migration is deliberately outside v1.2. Do not use this
  installer on an existing vault.
- For ordinary problems, use
  [Issues](https://github.com/songoao25/obsidian-knowledge-vault/issues), and
  never include credentials or real notes.

## Release files

The [v1.2.1 Release](https://github.com/songoao25/obsidian-knowledge-vault/releases/tag/v1.2.1)
contains macOS and Windows packages plus a SHA-256 checksum file.

## License

[MIT](LICENSE) © 2026 songoao25
