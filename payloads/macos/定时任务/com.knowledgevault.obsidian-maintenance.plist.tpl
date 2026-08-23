<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.knowledgevault.obsidian-maintenance</string>
  <key>ProgramArguments</key>
  <array>
    <string>{{PROJECT}}/脚本/run_knowledge_vault.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
{{START_CALENDAR_INTERVAL}}
  </array>
  <key>ProcessType</key>
  <string>Background</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>{{HOME}}</string>
    <key>CODEX_HOME</key>
    <string>{{CODEX_HOME}}</string>
    <key>PATH</key>
    <string>{{HOMEBREW_PREFIX}}/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>StandardOutPath</key>
  <string>{{PROJECT}}/.state/launchagent.out.log</string>
  <key>StandardErrorPath</key>
  <string>{{PROJECT}}/.state/launchagent.err.log</string>
</dict>
</plist>
