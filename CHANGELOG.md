# 变更记录

## v1.2.1（2026-09-09）

- 修复 macOS 发布包携带发布工厂测试、导致安装验收失败的问题。
- 组包后执行实际载荷测试，并统一公开运行版本与发布版本。
- 清理载荷中的旧版重复部署入口和说明文件，避免用户看到过时的 v1.1.0 信息。

## v1.2.0（2026-08-23）

- 产品更名为 Obsidian Knowledge Vault，面向 macOS 与 Windows。
- 新增通用模板与法律模板；安装时选择，核心整理规则保持一致。
- 所有平台统一拒绝非空 Vault，保护已有笔记与 Obsidian 配置。
- 新增中英文安装、日常使用、自定义与排错说明。
- 发布包由隔离分发工程构建，并接受源码、载荷、压缩包和仓库隐私门禁。

## v1.1.0（历史私有构建）

此版本是公开项目之前的内部构建。其分发细节、旧版归档和使用者相关说明均不属于公开仓库；请使用 v1.2.0。

## Earlier private builds

Earlier private builds are superseded by v1.2.0. Their archive names, release
details, and repository settings are intentionally not part of the public
project.
