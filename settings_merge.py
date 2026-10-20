"""Add or remove the dashboard's hook entries and status line tap in Claude Code's settings.json, leaving every other
hook and setting untouched.

Usage: settings_merge.py install <settings.json> <hook-command> <tap-command> <statusline-store>
       settings_merge.py uninstall <settings.json> [<statusline-store>]
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

MARKER = "claude-dashboard/app/hook/claude_hook.py"
TAP_MARKER = "claude-dashboard/app/hook/statusline_tap.py"
KEEP = object()
EVENTS = (
    "SessionStart", "UserPromptSubmit", "PermissionRequest", "Notification",
    "PostToolUse", "Stop", "StopFailure", "SessionEnd",
)


class MergeError(Exception):
    pass


def _is_ours(group) -> bool:
    if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
        return False
    return any(isinstance(h, dict) and MARKER in str(h.get("command", "")) for h in group["hooks"])


def _is_our_hook(hook) -> bool:
    return isinstance(hook, dict) and MARKER in str(hook.get("command", ""))


def _without_ours(group):
    """The group with our hook entries removed; None when nothing else is left in it."""
    if not _is_ours(group):
        return group
    kept = [h for h in group["hooks"] if not _is_our_hook(h)]
    return {**group, "hooks": kept} if kept else None


def _strip(groups: list) -> list:
    return [g for g in (_without_ours(g) for g in groups) if g is not None]


def add_hooks(settings: dict, command: str) -> dict:
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise MergeError("settings.hooks is not an object")
    for event in EVENTS:
        if not isinstance(hooks.get(event, []), list):
            raise MergeError(f"settings.hooks.{event} is not a list")
    ours = {"hooks": [{"type": "command", "command": command, "async": True, "timeout": 5}]}
    for event in EVENTS:
        hooks[event] = _strip(hooks.get(event, [])) + [ours]
    return settings


def remove_hooks(settings: dict) -> dict:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept = _strip(groups)
        if kept == groups:
            continue
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    return settings


def _is_tap(command) -> bool:
    return isinstance(command, str) and TAP_MARKER in command


def wrap_statusline(settings: dict, tap_command: str) -> object:
    """Point statusLine at the tap. Returns the user's command to store (None when there was no status line), or KEEP
    when the tap was already in place."""
    line = settings.get("statusLine")
    if line is None:
        settings["statusLine"] = {"type": "command", "command": tap_command}
        return None
    if not isinstance(line, dict) or line.get("type") != "command" or not isinstance(line.get("command"), str):
        raise MergeError("settings.statusLine is not a command status line")
    original = KEEP if _is_tap(line["command"]) else line["command"]
    line["command"] = tap_command
    return original


def unwrap_statusline(settings: dict, original: str | None) -> None:
    line = settings.get("statusLine")
    if not isinstance(line, dict) or not _is_tap(line.get("command")):
        return
    if original:
        line["command"] = original
    else:
        del settings["statusLine"]


def _stored_command(store: Path) -> str | None:
    try:
        data = json.loads(store.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    command = data.get("command") if isinstance(data, dict) else None
    return command if isinstance(command, str) and command else None


def _load(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    try:
        data = json.loads(text)
    except ValueError as e:
        raise MergeError(f"{path} is not valid JSON: {e}") from None
    if not isinstance(data, dict):
        raise MergeError(f"{path} does not contain a JSON object")
    return data


def _dump(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _backup(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.name}.bak-claude-dashboard-{stamp}")
    n = 1
    while target.exists():
        target = path.with_name(f"{path.name}.bak-claude-dashboard-{stamp}-{n}")
        n += 1
    shutil.copy2(path, target)
    return target


def _write(path: Path, text: str) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def install(path: Path, command: str, tap_command: str, store: Path) -> None:
    path = Path(os.path.realpath(path))  # write through a symlinked settings.json (dotfiles), never replace the link
    data = _load(path)
    before = None if data is None else _dump(data)
    data = add_hooks({} if data is None else data, command)
    original = wrap_statusline(data, tap_command)
    after = _dump(data)
    if original is not KEEP:
        _write(Path(store), _dump({"command": original}))  # before settings.json: the tap must never run without it
    if before is None:
        _write(path, after)
    elif after != before:
        _backup(path)
        _write(path, after)


def default_store() -> Path:
    return Path.home() / "Library" / "Application Support" / "claude-dashboard" / "statusline.json"


def uninstall(path: Path, store: Path) -> None:
    path = Path(os.path.realpath(path))
    data = _load(path)
    if data is None:
        return
    before = _dump(data)
    remove_hooks(data)
    unwrap_statusline(data, _stored_command(Path(store)))
    after = _dump(data)
    if after != before:
        _backup(path)
        _write(path, after)
    with contextlib.suppress(FileNotFoundError):
        Path(store).unlink()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) == 5 and args[0] == "install":
            install(Path(args[1]), args[2], args[3], Path(args[4]))
        elif len(args) in (2, 3) and args[0] == "uninstall":
            uninstall(Path(args[1]), Path(args[2]) if len(args) == 3 else default_store())
        else:
            print(__doc__.split("\n\n", 1)[1].strip(), file=sys.stderr)
            return 2
    except MergeError as e:
        print(f"settings_merge: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
