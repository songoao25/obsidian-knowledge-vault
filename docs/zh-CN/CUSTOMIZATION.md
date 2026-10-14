# 自定义

重新运行安装器可以修改整理时间或 AI 服务：解压后的发布包用 `python3 deploy.py`，克隆的仓库用 `python3 scripts/deploy.py`。`--template general` 和 `--template legal` 仅适用于创建新的空白 Vault。

自动整理运行期间，不要直接新增未登记的分类目录。应先同步修改 taxonomy 和对应模板规则，再在临时 Vault 验证。API 密钥不得写入配置文件。

