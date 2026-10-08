"""Add or remove the dashboard's hook entries in Claude Code's settings.json, leaving every other hook untouched.

Usage: settings_merge.py install <settings.json> <hook-command>
       settings_merge.py uninstall <settings.json>
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


def install(path: Path, command: str) -> None:
    path = Path(os.path.realpath(path))  # write through a symlinked settings.json (dotfiles), never replace the link
    data = _load(path)
    if data is None:
        _write(path, _dump(add_hooks({}, command)))
        return
    before = _dump(data)
    after = _dump(add_hooks(data, command))
    if after != before:
        _backup(path)
        _write(path, after)


def uninstall(path: Path) -> None:
    path = Path(os.path.realpath(path))
    data = _load(path)
    if data is None:
        return
    before = _dump(data)
    after = _dump(remove_hooks(data))
    if after != before:
        _backup(path)
        _write(path, after)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        if len(args) == 3 and args[0] == "install":
            install(Path(args[1]), args[2])
        elif len(args) == 2 and args[0] == "uninstall":
            uninstall(Path(args[1]))
        else:
            print(__doc__.split("\n\n", 1)[1].strip(), file=sys.stderr)
            return 2
    except MergeError as e:
        print(f"settings_merge: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
