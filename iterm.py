"""Open a command in a new iTerm2 tab via osascript (the command is passed as argv, never into the script source)."""
from __future__ import annotations

import re
import subprocess

APPLESCRIPT = """on run argv
  set cmd to item 1 of argv
  tell application "iTerm2"
    activate
    if (count of windows) = 0 then
      create window with default profile
    else
      tell current window to create tab with default profile
    end if
    tell current session of current window to write text cmd
  end tell
end run"""


def build_argv(cmd: str) -> list[str]:
    return ["osascript", "-e", APPLESCRIPT, cmd]


def open_in_iterm(cmd: str, runner=subprocess.run) -> tuple[bool, str | None]:
    try:
        completed = runner(build_argv(cmd), capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return False, "iTerm2 did not respond within 10 s."
    except OSError as e:
        return False, f"Cannot run osascript: {e}"
    if completed.returncode != 0:
        return False, _osascript_error(completed)
    return True, None


TTY_RE = re.compile(r"^ttys\d+$")
FOCUS_SCRIPT = """on run argv
  set target to item 1 of argv
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          if tty of s is target then
            select w
            select t
            select s
            activate
            return "ok"
          end if
        end repeat
      end repeat
    end repeat
  end tell
  return "notfound"
end run"""


def _osascript_error(completed) -> str:
    err = (completed.stderr or "").strip()
    if "-1743" in err:
        return ("macOS did not allow controlling iTerm2. Allow it in System Settings → "
                "Privacy & Security → Automation, or copy the command instead.")
    return err or f"osascript exited with code {completed.returncode}"


def focus_tty(tty, runner=subprocess.run) -> tuple[str, str | None]:
    if not isinstance(tty, str) or not TTY_RE.match(tty):
        return "error", f"invalid tty: {tty!r}"
    try:
        completed = runner(["osascript", "-e", FOCUS_SCRIPT, "/dev/" + tty], capture_output=True, text=True, timeout=10)
    except subprocess.TimeoutExpired:
        return "error", "iTerm2 did not respond within 10 s."
    except OSError as e:
        return "error", f"Cannot run osascript: {e}"
    if completed.returncode != 0:
        return "error", _osascript_error(completed)
    out = (completed.stdout or "").strip()
    if out in ("ok", "notfound"):
        return out, None
    return "error", out or "unexpected osascript output"
