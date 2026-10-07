"""Otevření příkazu v novém iTerm2 tabu přes osascript (příkaz jde jako argv, nikdy do zdrojáku skriptu)."""
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
        return False, "iTerm2 neodpověděl do 10 s."
    except OSError as e:
        return False, f"osascript nelze spustit: {e}"
    if completed.returncode != 0:
        err = (completed.stderr or "").strip()
        if "-1743" in err:
            return False, ("macOS nepovolil ovládání iTerm2. Povol ho v Nastavení systému → "
                           "Soukromí a zabezpečení → Automatizace, nebo příkaz zkopíruj.")
        return False, err or f"osascript skončil s kódem {completed.returncode}"
    return True, None
