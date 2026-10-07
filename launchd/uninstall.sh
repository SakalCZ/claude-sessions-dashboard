#!/bin/bash
# Stops and unregisters the LaunchAgent and removes the deployed copy of the app. Keeps the notes (notes.json).
set -euo pipefail

LABEL="local.claude-sessions-dashboard"
SUPPORT_DIR="$HOME/Library/Application Support/claude-dashboard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
rm -rf "$SUPPORT_DIR/app"
echo "Uninstalled. Notes were kept in: $SUPPORT_DIR/notes.json"
