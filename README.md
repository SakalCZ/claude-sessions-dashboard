# Claude Sessions Dashboard

A local overview of all your Claude Code sessions from `~/.claude`. Each session gets one row, showing:

- the directory,
- the Jira issue or topic,
- the branch,
- the most recent prompts,
- the live status of a running instance (🟢 busy / 🟡 idle),
- your own status and note.

You can resume a session by copying `cd … && claude --resume …` or by opening it straight in a new iTerm2 tab.

**Address:** http://127.0.0.1:7333/ (localhost only)

## Features

- **Search:** full-text search over Jira keys, branches, directories, prompt text, notes and session ids. Searching ignores the status filter, so `563` also finds a session you marked as done.
- **Grouping:** by directory or by issue. Git worktrees are grouped under their repository.
- **Jira keys:** taken from branch names, `*.atlassian.net/browse/KEY` links and session titles. Free-text noise such as `P1-1` is ignored.
- **Forks:** forked or copied sessions are folded under the newest copy, and their keys stay searchable.
- **Live status:** read from `~/.claude/sessions/<pid>.json` and checked against the real process start time.
- **Missing directories:** a session whose directory no longer exists (for example a removed worktree) is flagged and is not opened.
- **Speed:** transcripts are append-only, so the server reads only new lines and refreshes are cheap even for large files.

## Requirements

- macOS (the LaunchAgent and the iTerm2 integration are macOS-specific)
- Python 3.10+ (standard library only, no dependencies)
- iTerm2, optional, for the "Open" action
- Node.js, optional, used only by the `filter.js` tests

## Install / update

```bash
./launchd/install.sh
```

- The script copies the app to `~/Library/Application Support/claude-dashboard/app/` and registers the LaunchAgent `local.claude-sessions-dashboard`. The agent starts at login and restarts after a crash.
- Run `install.sh` again after every code change.
- To use a different port: `CLAUDE_DASHBOARD_PORT=7400 ./launchd/install.sh`.

## Uninstall

```bash
./launchd/uninstall.sh
```

Your notes are kept in `~/Library/Application Support/claude-dashboard/notes.json`.

## Development

```bash
python3 server.py --port 7334 --data-dir "$TMPDIR/cd-dev"   # runs straight from the repo
python3 -m unittest discover -s tests -t . -v               # all tests (incl. the node test of filter.js)
```

- **Log:** the LaunchAgent writes to `~/Library/Logs/claude-dashboard.log`.
- **"Open in iTerm2":** the first time you use it, macOS asks for permission to control iTerm2. If you deny it, enable it later in System Settings → Privacy & Security → Automation.
- **Old sessions:** Claude Code deletes old transcripts (the `cleanupPeriodDays` setting). Those sessions disappear from the dashboard and can no longer be resumed.

## Security

- The server binds to `127.0.0.1` only. Every request must carry `Host: 127.0.0.1:<port>` or `Host: localhost:<port>`, which protects against DNS rebinding.
- POST requests also require `Content-Type: application/json` and a same-origin `Origin`.
- The resume command is built on the server and passed to `osascript` as an argument, never interpolated into the script.
- The UI renders transcript text through DOM APIs only, never as HTML.

Design: `docs/superpowers/specs/2026-10-07-claude-sessions-dashboard-design.md`
