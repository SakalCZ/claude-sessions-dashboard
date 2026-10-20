"""Claude Code status line wrapper for the Claude Sessions Dashboard.

Records each session's usage limits (`rate_limits` of the status line input), then runs the user's own status line
command with the same input. Standalone: standard library only. Its output is exactly the original command's output.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

SESSION_ID_RE = re.compile(r"[0-9a-f-]{36}")
WINDOWS = ("five_hour", "seven_day")
REFRESH_S = 60


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(ts) -> float | None:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: str, record: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".limits-", suffix=".tmp")
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


def record_limits(raw: bytes, ddir: str, now=time.time) -> None:
    """Store this session's `rate_limits` when they changed or the stored copy is older than REFRESH_S."""
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        return
    sid, limits = payload.get("session_id"), payload.get("rate_limits")
    if not isinstance(sid, str) or not SESSION_ID_RE.fullmatch(sid):
        return
    if not isinstance(limits, dict) or not any(isinstance(limits.get(w), dict) for w in WINDOWS):
        return
    folder = os.path.join(ddir, "rate_limits")
    path = os.path.join(folder, sid + ".json")
    ts = now()
    stored = _read(path)
    captured = parse_iso(stored.get("captured_at"))
    if stored.get("rate_limits") == limits and captured is not None and 0 <= ts - captured < REFRESH_S:
        return
    os.makedirs(folder, exist_ok=True)
    _write(path, {"captured_at": iso(ts), "session_id": sid, "rate_limits": limits})


def original_command(ddir: str) -> str | None:
    command = _read(os.path.join(ddir, "statusline.json")).get("command")
    if not isinstance(command, str) or not command.strip() or "statusline_tap.py" in command:
        return None
    return command


def main() -> int:
    try:
        raw = sys.stdin.buffer.read()
    except (OSError, ValueError, AttributeError):
        raw = b""
    ddir = os.environ.get("CLAUDE_DASHBOARD_DATA_DIR") or os.path.expanduser(
        "~/Library/Application Support/claude-dashboard")
    try:
        record_limits(raw, ddir)
    except Exception:
        pass
    command = original_command(ddir)
    if command is None:
        return 0
    try:
        code = subprocess.run(command, shell=True, input=raw).returncode
    except OSError:
        return 1
    return code if code >= 0 else 128 - code


if __name__ == "__main__":
    sys.exit(main())
