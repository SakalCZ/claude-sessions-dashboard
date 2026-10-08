"""Claude Code hook: record each session's attention state for the Claude Sessions Dashboard.

Registered (async) for SessionStart, UserPromptSubmit, PermissionRequest, Notification, PostToolUse, Stop,
StopFailure and SessionEnd. It must never print anything and never fail: the stdout of some hooks is injected
into Claude's context, and errors would be shown to the user. Standalone: standard library only.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

SESSION_ID_RE = re.compile(r"^[0-9a-f-]{36}$")
EVENTS = {"SessionStart", "UserPromptSubmit", "PermissionRequest", "Notification", "PostToolUse", "Stop",
          "StopFailure", "SessionEnd"}
QUESTION_TYPES = {"elicitation_dialog", "agent_needs_input"}
NOTE_LIMIT = 200


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def first_line(text, limit: int = NOTE_LIMIT):
    if not isinstance(text, str):
        return None
    for line in text.splitlines():
        line = " ".join(line.split())
        if line:
            return line if len(line) <= limit else line[: limit - 1] + "…"
    return None


def next_state(event: str, payload: dict, current):
    """(state, note) for this event, or None when the event does not change the state."""
    if event == "SessionStart":
        return "idle", None
    if event == "UserPromptSubmit":
        return "working", None
    if event == "PermissionRequest":
        return "permission", first_line(payload.get("tool_name"))
    if event == "Notification":
        kind = payload.get("notification_type")
        if kind == "permission_prompt":
            return "permission", first_line(payload.get("message"))
        if kind in QUESTION_TYPES:
            return "question", first_line(payload.get("message"))
        if kind == "idle_prompt" and current == "working":
            return "idle", None  # an interrupted turn: Stop does not run on Esc
        return None
    if event == "PostToolUse":
        return ("working", None) if current in ("permission", "question") else None
    if event == "Stop":
        return "waiting", first_line(payload.get("last_assistant_message"))
    if event == "StopFailure":
        return "failed", first_line(payload.get("error_message") or payload.get("error_type") or payload.get("error"))
    if event == "SessionEnd":
        return "ended", None
    return None


def ps_info(pid: int):
    try:
        out = subprocess.run(["ps", "-o", "ppid=,tty=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=2).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    if len(out) != 2 or not out[0].isdigit():
        return None
    return int(out[0]), out[1]


def find_claude(pid: int, sessions_dir: str, ps_info=ps_info):
    """Walk up from the hook's parent to the first process that has a ~/.claude/sessions/<pid>.json file."""
    for _ in range(6):
        if pid <= 1:
            break
        info = ps_info(pid)
        if info is None:
            break
        ppid, tty = info
        if os.path.exists(os.path.join(sessions_dir, f"{pid}.json")):
            return pid, (tty if tty not in ("??", "-", "") else None)
        pid = ppid
    return None, None


def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: str, record: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".agent-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def handle(payload, *, ddir: str, cdir: str, parent_pid: int, ps_info=ps_info, now=now_iso) -> None:
    if not isinstance(payload, dict):
        return
    sid, event = payload.get("session_id"), payload.get("hook_event_name")
    if not isinstance(sid, str) or not SESSION_ID_RE.match(sid) or event not in EVENTS:
        return
    agents = os.path.join(ddir, "agents")
    os.makedirs(agents, exist_ok=True)
    with open(os.path.join(agents, sid + ".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = os.path.join(agents, sid + ".json")
        current = _read(path)
        change = next_state(event, payload, current.get("state"))
        if change is None:
            return
        state, note = change
        ts = now()
        record = dict(current)
        if current.get("state") != state:
            record["since"] = ts
            record["note"] = note
        elif note is not None:
            record["note"] = note
        record.update({"version": 1, "session_id": sid, "state": state, "event": event, "updated_at": ts})
        if isinstance(payload.get("cwd"), str):
            record["cwd"] = payload["cwd"]
        sessions_dir = os.path.join(cdir, "sessions")
        known = record.get("claude_pid")
        if event == "SessionStart" or not known or not os.path.exists(os.path.join(sessions_dir, f"{known}.json")):
            record["claude_pid"], record["tty"] = find_claude(parent_pid, sessions_dir, ps_info)
        _write(path, record)


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "null")
        handle(
            payload,
            ddir=os.environ.get("CLAUDE_DASHBOARD_DATA_DIR")
            or os.path.expanduser("~/Library/Application Support/claude-dashboard"),
            cdir=os.environ.get("CLAUDE_DASHBOARD_CLAUDE_DIR") or os.path.expanduser("~/.claude"),
            parent_pid=os.getppid(),
        )
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    # Silence everything before doing any work: any accidental output could end up in Claude's context.
    sys.stdout = sys.stderr = open(os.devnull, "w")
    sys.exit(main())
