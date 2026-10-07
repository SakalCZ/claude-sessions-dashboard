"""Open a command in a new iTerm2 tab via osascript (the command is passed as argv, never into the script source)."""
from __future__ import annotations

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
        err = (completed.stderr or "").strip()
        if "-1743" in err:
            return False, ("macOS did not allow controlling iTerm2. Allow it in System Settings → "
                           "Privacy & Security → Automation, or copy the command instead.")
        return False, err or f"osascript exited with code {completed.returncode}"
    return True, None
