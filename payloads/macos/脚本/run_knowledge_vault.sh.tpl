#!/bin/bash
# 由 deploy.py 从模板渲染生成：知识库维护定时任务的固定启动入口。
set -euo pipefail

PROJECT="{{PROJECT}}"
export PYTHONPATH="$PROJECT/程序"
export PATH="{{HOMEBREW_PREFIX}}/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export HOME="{{HOME}}"
export USER="$USER"
export CODEX_HOME="{{CODEX_HOME}}"
export TERM="dumb"
cd "$PROJECT"
exec "{{PYTHON}}" -m knowledge_vault.cli --settings "$PROJECT/配置/settings.json" run
