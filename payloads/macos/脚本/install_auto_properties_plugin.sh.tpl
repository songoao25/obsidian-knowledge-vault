#!/bin/bash
# 由 deploy.py 从模板渲染生成：把本地 Auto Properties 插件安装进目标 Vault。
# 注意：所有变量都加 ${} 花括号，避免旧版 bash 把紧邻的多字节字符并入变量名。
set -euo pipefail

PROJECT_ROOT="{{PROJECT}}"
VAULT="{{VAULT_PATH}}"
SOURCE="${PROJECT_ROOT}/插件/auto-properties"
LOCAL_ID="knowledge-vault-auto-properties"
LEGACY_ID="auto-properties"
TARGET="${VAULT}/.obsidian/plugins/${LOCAL_ID}"
LEGACY_TARGET="${VAULT}/.obsidian/plugins/${LEGACY_ID}"

[[ -d "${VAULT}/.obsidian" ]] || { echo "找不到目标 Obsidian Vault：${VAULT}" >&2; exit 1; }
mkdir -p "${TARGET}"
cp "${SOURCE}/main.js" "${SOURCE}/rules.js" "${SOURCE}/manifest.json" "${TARGET}/"
"{{PYTHON}}" "${PROJECT_ROOT}/脚本/render_auto_properties_config.py" \
  --tag-policy "${PROJECT_ROOT}/配置/tag_policy.json" \
  --output "${TARGET}/data.json"

"{{PYTHON}}" - "${VAULT}/.obsidian/community-plugins.json" "${LEGACY_ID}" "${LOCAL_ID}" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
legacy_id = sys.argv[2]
local_id = sys.argv[3]
plugins = json.loads(path.read_text(encoding="utf-8"))
plugins = [plugin for plugin in plugins if plugin != legacy_id]
if local_id not in plugins:
    plugins.append(local_id)
temporary = path.with_suffix(path.suffix + ".tmp")
temporary.write_text(json.dumps(plugins, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
temporary.replace(path)
PY

if [[ -d "${LEGACY_TARGET}" ]]; then
  trash_target="${HOME}/.Trash/${LEGACY_ID}-$(date +%Y%m%d-%H%M%S)"
  mv "${LEGACY_TARGET}" "${trash_target}"
  echo "已将冲突的旧插件目录移入废纸篓：${trash_target}"
fi

echo "已安装本地 Auto Properties（${LOCAL_ID}）；重启或重新加载 Obsidian 后生效。"
