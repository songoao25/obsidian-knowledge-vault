# Obsidian Knowledge Vault

[![Privacy gate](https://github.com/songoao25/obsidian-knowledge-vault/actions/workflows/privacy-gate.yml/badge.svg)](https://github.com/songoao25/obsidian-knowledge-vault/actions/workflows/privacy-gate.yml)
[![Release tests](https://github.com/songoao25/obsidian-knowledge-vault/actions/workflows/test.yml/badge.svg)](https://github.com/songoao25/obsidian-knowledge-vault/actions/workflows/test.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platforms: macOS and Windows](https://img.shields.io/badge/platform-macOS%20%7C%20Windows-555555)](#quick-start)

Obsidian Knowledge Vault creates a safe, automated starting point for a new
Obsidian vault. Choose a template, choose when it runs, connect an AI provider
you trust, and use one inbox for material you want organized.

中文说明见 [docs/zh-CN](docs/zh-CN/README.md)。

## What it includes

- **General template** for life, work, learning, records, and system rules.
- **Legal template** with the same core structure and a legal-profession work
  taxonomy.
- A macOS and Windows installer that asks for the template, schedule, and AI
  provider.
- DeepSeek, ChatGPT desktop on macOS, and OpenAI-compatible provider choices.
- Local, transactional organization rules: the AI returns constrained plans;
  local software validates and writes files.

## Quick start

Create a **new, empty** folder for the Vault. Version 1.2 intentionally refuses
to install into an existing Vault so it cannot alter existing notes or Obsidian
settings.

```sh
git clone https://github.com/songoao25/obsidian-knowledge-vault.git
cd obsidian-knowledge-vault
python3 deploy.py
```

The installer asks for the template, schedule, provider, and one final local
approval. It stores API keys only on the user's computer; never paste a key in
an issue or AI chat.

## Documentation

| English | 中文 |
| --- | --- |
| [Install](docs/en/INSTALL.md) | [安装](docs/zh-CN/INSTALL.md) |
| [Daily use](docs/en/DAILY-USE.md) | [日常使用](docs/zh-CN/DAILY-USE.md) |
| [Customize](docs/en/CUSTOMIZATION.md) | [自定义](docs/zh-CN/CUSTOMIZATION.md) |
| [Troubleshooting](docs/en/TROUBLESHOOTING.md) | [排错](docs/zh-CN/TROUBLESHOOTING.md) |

## Privacy and safety

- The repository and release packages contain templates and code only: no
  personal notes, credentials, logs, recovery data, or computer-specific paths.
- Content is sent only to the provider the user actively selects during setup.
- Do not use this installer on an existing Vault. Existing-Vault migration is
  deliberately outside v1.2.
- Report ordinary problems through [Issues](https://github.com/songoao25/obsidian-knowledge-vault/issues), without credentials or real notes.

## Release files

Versioned macOS and Windows packages and SHA-256 checksums are in
[`release/`](release/). The GitHub Release contains the same verified files.

## License

[MIT License](LICENSE)
