#!/bin/bash
# Zastaví a odregistruje LaunchAgent a smaže nasazenou kopii aplikace. Poznámky (notes.json) ponechá.
set -euo pipefail

LABEL="local.claude-sessions-dashboard"
SUPPORT_DIR="$HOME/Library/Application Support/claude-dashboard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
rm -rf "$SUPPORT_DIR/app"
echo "Odinstalováno. Poznámky zůstaly v: $SUPPORT_DIR/notes.json"
