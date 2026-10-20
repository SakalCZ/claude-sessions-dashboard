#!/bin/bash
# Stops and unregisters the LaunchAgent and removes the deployed copy of the app. Keeps the notes (notes.json).
set -euo pipefail

LABEL="local.claude-sessions-dashboard"
SUPPORT_DIR="$HOME/Library/Application Support/claude-dashboard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ -f "$SUPPORT_DIR/app/settings_merge.py" ]; then
  python3 "$SUPPORT_DIR/app/settings_merge.py" uninstall "$HOME/.claude/settings.json" "$SUPPORT_DIR/statusline.json" \
    || { echo "Could not remove the Claude Code hooks and status line from ~/.claude/settings.json. Nothing was removed." >&2; exit 1; }
fi
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
rm -rf "$SUPPORT_DIR/app"
echo "Uninstalled. Notes, seen marks and config were kept in: $SUPPORT_DIR"
