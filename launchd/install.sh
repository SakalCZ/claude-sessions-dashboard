#!/bin/bash
# Deploys the dashboard to ~/Library/Application Support/claude-dashboard/app and registers the LaunchAgent.
# Run it again after every code change (copies the files and restarts the agent).
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
mkdir -p "$APP_DIR/hook"
cp "$REPO_DIR"/{server,sessions,live,notes,iterm,agents,monitor,notifier,settings_merge,config}.py "$APP_DIR/"
cp "$REPO_DIR"/hook/claude_hook.py "$APP_DIR/hook/"
cp "$REPO_DIR"/static/index.html "$REPO_DIR"/static/app.js "$REPO_DIR"/static/filter.js "$APP_DIR/static/"

HOOK_CMD="\"$PYTHON\" '$APP_DIR/hook/claude_hook.py'"
if ! "$PYTHON" "$APP_DIR/settings_merge.py" install "$HOME/.claude/settings.json" "$HOOK_CMD"; then
  echo "Could not register the Claude Code hooks in ~/.claude/settings.json (see above). Nothing was changed there." >&2
  exit 1
fi

# launchd starts agents with a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin); docker usually lives elsewhere.
SERVICE_PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
DOCKER_BIN="$(command -v docker || true)"
if [ -n "$DOCKER_BIN" ]; then SERVICE_PATH="$(dirname "$DOCKER_BIN"):$SERVICE_PATH"; fi

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
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>$SERVICE_PATH</string>
  </dict>
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
    echo "OK: dashboard is running at http://127.0.0.1:$PORT/"
    exit 0
  fi
  sleep 0.25
done
echo "Server is not responding on port $PORT, see log: $LOG" >&2
tail -n 20 "$LOG" >&2 || true
exit 1
