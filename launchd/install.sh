#!/bin/bash
# Nasadí dashboard do ~/Library/Application Support/claude-dashboard/app a zaregistruje LaunchAgent.
# Spouštěj znovu po každé změně kódu (zkopíruje soubory a restartuje agenta).
set -euo pipefail

LABEL="local.claude-sessions-dashboard"
PORT="${CLAUDE_DASHBOARD_PORT:-7333}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUPPORT_DIR="$HOME/Library/Application Support/claude-dashboard"
APP_DIR="$SUPPORT_DIR/app"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/claude-dashboard.log"
PYTHON="$(command -v python3)"
DOMAIN="gui/$(id -u)"

mkdir -p "$APP_DIR/static" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cp "$REPO_DIR"/server.py "$REPO_DIR"/sessions.py "$REPO_DIR"/live.py "$REPO_DIR"/notes.py "$REPO_DIR"/iterm.py "$APP_DIR/"
cp "$REPO_DIR"/static/index.html "$REPO_DIR"/static/app.js "$REPO_DIR"/static/filter.js "$APP_DIR/static/"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$APP_DIR/server.py</string>
    <string>--port</string>
    <string>$PORT</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF

if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  launchctl bootout "$DOMAIN/$LABEL" || true
  for _ in $(seq 1 20); do
    launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 || break
    sleep 0.25
  done
fi
launchctl bootstrap "$DOMAIN" "$PLIST"

for _ in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    echo "OK: dashboard běží na http://127.0.0.1:$PORT/"
    exit 0
  fi
  sleep 0.25
done
echo "Server neodpovídá na portu $PORT, viz log: $LOG" >&2
tail -n 20 "$LOG" >&2 || true
exit 1
