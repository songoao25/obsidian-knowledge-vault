#!/bin/bash
# 由 deploy.py 从模板渲染生成：安装并验收 LaunchAgent（先测试、后安装、失败回滚）。
set -euo pipefail

PROJECT="{{PROJECT}}"
LABEL="{{LABEL}}"
SOURCE="$PROJECT/定时任务/$LABEL.plist"
TARGET="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON="{{PYTHON}}"
EXPECTED_HOURS=({{EXPECTED_HOURS}})

mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT/.state"
plutil -lint "$SOURCE"
export PYTHONPATH="$PROJECT/程序"
"$PYTHON" -m unittest discover -s "$PROJECT/测试" -v
"$PYTHON" -m compileall -q "$PROJECT/程序" "$PROJECT/测试"

backup="$(mktemp "$PROJECT/.state/$LABEL.backup.XXXXXX")"
if [[ -f "$TARGET" ]]; then
  cp "$TARGET" "$backup"
else
  rm "$backup"
  backup=""
fi

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
cp "$SOURCE" "$TARGET"
if ! launchctl bootstrap "gui/$UID" "$TARGET"; then
  if [[ -n "$backup" ]]; then
    cp "$backup" "$TARGET"
    launchctl bootstrap "gui/$UID" "$TARGET" || true
  fi
  echo "ERROR failed to install $LABEL; restored the previous task when available" >&2
  exit 1
fi
launchctl enable "gui/$UID/$LABEL"

validate_installed_task() {
  local index actual count=0
  while /usr/libexec/PlistBuddy -c "Print :StartCalendarInterval:$count:Hour" "$TARGET" >/dev/null 2>&1; do
    count=$((count + 1))
  done
  [[ "$count" -eq "${#EXPECTED_HOURS[@]}" ]] || return 1
  for index in "${!EXPECTED_HOURS[@]}"; do
    actual="$(/usr/libexec/PlistBuddy -c "Print :StartCalendarInterval:$index:Hour" "$TARGET")" || return 1
    [[ "$actual" == "${EXPECTED_HOURS[$index]}" ]] || return 1
  done
  ! /usr/libexec/PlistBuddy -c "Print :RunAtLoad" "$TARGET" >/dev/null 2>&1 || return 1
  launchctl print "gui/$UID/$LABEL" >/dev/null
}

if ! validate_installed_task; then
  launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
  if [[ -n "$backup" ]]; then
    cp "$backup" "$TARGET"
    launchctl bootstrap "gui/$UID" "$TARGET" || true
    launchctl enable "gui/$UID/$LABEL" || true
  fi
  echo "ERROR $LABEL failed post-install checks; restored the previous task when available" >&2
  exit 1
fi
rm -f "$backup"
echo "INSTALLED $LABEL"
