# Obsidian Knowledge Vault

[**English**](README.md) | **中文**

[![Release](https://img.shields.io/github/v/release/songoao25/obsidian-knowledge-vault)](https://github.com/songoao25/obsidian-knowledge-vault/releases)
[![CI](https://img.shields.io/github/actions/workflow/status/songoao25/obsidian-knowledge-vault/test.yml?branch=main)](https://github.com/songoao25/obsidian-knowledge-vault/actions)
[![CodeQL](https://img.shields.io/github/actions/workflow/status/songoao25/obsidian-knowledge-vault/codeql.yml?label=CodeQL)](https://github.com/songoao25/obsidian-knowledge-vault/security/code-scanning)
[![License](https://img.shields.io/github/license/songoao25/obsidian-knowledge-vault)](LICENSE)
[![Last commit](https://img.shields.io/github/last-commit/songoao25/obsidian-knowledge-vault)](https://github.com/songoao25/obsidian-knowledge-vault/commits/main)

为一个**全新的 Obsidian 知识库**建立安全、可自动化整理的起点。选择通用模板或法律模板，选择整理时间，接入你信任的 AI 服务，再把待整理材料放入同一个收件箱即可。

## 它是什么

Obsidian Knowledge Vault 是面向全新空白 Obsidian Vault 的安装与维护工具包。两个模板共用同一套整理机制，只有工作目录分类不同：

- **通用模板**：适用于日常工作、生活、学习、资料与系统规则。
- **法律模板**：适用于法律专业工作，提供法律业务目录分类。

本地维护程序只接受受约束的 AI 整理计划，先在本地校验，再写入允许的文件。本项目不会公开你的笔记，也不提供由本项目托管的云端服务。

## 安装

请先创建一个**全新的空文件夹**作为 Vault。v1.2 会明确拒绝非空 Vault，因此不会改动已有笔记或既有 Obsidian 设置。

```bash
git clone https://github.com/songoao25/obsidian-knowledge-vault.git
cd obsidian-knowledge-vault
python3 deploy.py
```

安装器会依次让你选择模板、执行时间、AI 服务，并在本机进行一次最终确认。也可以直接在[最新发布页](https://github.com/songoao25/obsidian-knowledge-vault/releases/latest)下载 macOS 或 Windows 安装包。

## 支持的平台与 AI 选择

| 平台 | AI 选择 |
| --- | --- |
| macOS | ChatGPT Desktop、DeepSeek，或用户自填的 OpenAI 兼容服务 |
| Windows | DeepSeek，或用户自填的 OpenAI 兼容服务 |

API 密钥只会通过本机隐藏输入请求，并保存在用户自己的本机凭据存储中。请不要把密钥粘贴到 Issue 或 AI 对话中。

## 日常使用

把需要整理的材料放进你选定的收件箱，之后按你设置的时间自动运行即可。以后可以调整执行时间、AI 服务和本地规则，不需要重新安装或替换 Vault。

完整流程见[日常使用说明](docs/zh-CN/DAILY-USE.md)。

## 使用文档

| English | 中文 |
| --- | --- |
| [Installation](docs/en/INSTALL.md) | [安装](docs/zh-CN/INSTALL.md) |
| [Daily use](docs/en/DAILY-USE.md) | [日常使用](docs/zh-CN/DAILY-USE.md) |
| [Customization](docs/en/CUSTOMIZATION.md) | [自定义](docs/zh-CN/CUSTOMIZATION.md) |
| [Troubleshooting](docs/en/TROUBLESHOOTING.md) | [排错](docs/zh-CN/TROUBLESHOOTING.md) |

## 隐私与安全边界

- 本仓库和发布包只包含模板与代码，不包含个人笔记、凭据、日志、恢复数据或电脑专属路径。
- 只有你在安装时主动选择的 AI 服务才会接收内容。
- v1.2 故意不支持已有 Vault 迁移；请不要把安装器用于已有 Vault。
- 普通问题请通过 [Issues](https://github.com/songoao25/obsidian-knowledge-vault/issues) 反馈，且不要附上密钥或真实笔记。

## 发布文件

[v1.2.1 发布页](https://github.com/songoao25/obsidian-knowledge-vault/releases/tag/v1.2.1)提供 macOS、Windows 安装包及 SHA-256 校验清单。

## 许可证

[MIT](LICENSE) © 2026 songoao25
