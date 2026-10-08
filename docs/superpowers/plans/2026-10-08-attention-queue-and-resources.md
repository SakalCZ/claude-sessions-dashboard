# Attention Queue and Resource Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add to the Claude Sessions Dashboard:
- a "Waiting for you" queue, fed by Claude Code hooks with a fallback estimate;
- a resource monitor (per-session process trees, system memory/CPU, top apps, Docker) with warnings and macOS notifications;
- Switch, which focuses a session's existing iTerm2 tab.

**Architecture:**
- **Hook:** a stdlib hook script, registered as an async Claude Code hook next to the user's existing `cc-status` hooks, writes one state file per session.
- **Server:** the server reads those files (`agents.py`) and samples processes, system and Docker in a background thread (`monitor.py`). The same thread drives notification rules (`notifier.py`).
- **Registration:** `settings_merge.py` adds and removes the hook entries in `~/.claude/settings.json` safely.

**Tech Stack:** Python 3 stdlib, unittest, vanilla JS (+ node test), osascript/AppleScript (notifications, iTerm2), macOS `ps`/`sysctl`/`memory_pressure`, Docker CLI.

**Spec:** `docs/superpowers/specs/2026-10-08-attention-queue-and-resources-design.md` (extends `docs/superpowers/specs/2026-10-07-claude-sessions-dashboard-design.md`)

## Global Constraints

- Python standard library only; Python ≥ 3.10; `from __future__ import annotations` in every module. The hook script must also run standalone (no imports from the repo).
- Everything in the repository is English: code, comments, UI text, messages, tests, docs, commit messages. Dates in the UI use `en-GB`.
- The hook never writes to stdout/stderr and always exits 0.
- **`~/.claude` is read-only.**
  - The only exception is `~/.claude/settings.json`, which is changed only through `settings_merge.py`. That script writes a backup first, preserves all foreign hooks and is idempotent.
  - The spike in Task 2 also changes `settings.json`, but only temporarily and through the same script.
- No secrets, real employer/client names or real local paths in the repository. Fixtures are anonymized: `/Users/me/...`, `acme/shop...`, `PROJ-…`.
- Server binds `127.0.0.1` only. Every POST endpoint keeps the existing Host/Origin/Content-Type checks. Clients send only a session id; the server resolves ttys, paths and commands.
- Frontend: DOM APIs only (no `innerHTML` & co.), no external libraries.
- Tests: `python3 -m unittest discover -s tests -t . -v` from the repo root (includes `node tests/js/test_filter.js` when node is available).
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Working directory: `/Users/me/Documents/Development/agent-orgestrator`, branch `feature/attention-queue-and-resources`.

## Review Focus

1. **Hook payload shapes differ from the docs** (field names, missing fields, extra events). The hook must ignore what it does not understand and never crash or print. Task 2 captures real payloads; Task 3's `test_captured_fixtures` replays them through the hook.
2. **Concurrent async hooks of one session** (`PostToolUse` racing `Stop`) must not regress the state, e.g. `waiting` → `working`. Task 3 guards this with a per-session `flock` and with `PostToolUse` only acting on `permission`/`question` (`test_post_tool_use_does_not_touch_other_states`).
3. **`settings.json` with the user's own hooks, unusual shapes or invalid JSON** must never be damaged. Task 1 covers it with `test_install_keeps_others`, `test_invalid_json_aborts_unchanged`, `test_non_object_hooks_aborts` and `test_uninstall_removes_only_ours`.
4. **Docker or ps being slow, missing or failing** must not freeze the dashboard or break notifications. Task 5 covers it with `test_docker_unavailable`, `test_missing_commands_degrade` and timeouts on every command.
5. **A resource alert that stays raised for hours** must not spam: notify only on a rise, then remind at most hourly. Task 6 covers it with `test_resource_alert_rise_reminder_clear`.

---

## File Structure

| File | Responsibility |
|---|---|
| `config.py` | `load_config(path)`: optional `config.json` with typed defaults |
| `settings_merge.py` | add/remove our hook groups in `settings.json`; CLI for install/uninstall |
| `hook/claude_hook.py` | standalone hook: payload → per-session state file (`agents/<id>.json`) |
| `agents.py` | read hook states, `SeenStore`, `attention_for`, `build_queue` |
| `monitor.py` | pure parsers (`ps`, `sysctl`, `memory_pressure`, `docker`), process trees, top apps, Docker attribution, `Monitor` sampler threads |
| `notifier.py` | `send_notification`, `Notifier` rules (queue episodes, resource alert rise/reminder) |
| `iterm.py` | + `focus_tty(tty)` |
| `live.py` | + `status_updated_at` in live entries |
| `server.py` | wire it all: row `attention`/`resources`/`tty`, payload `queue`/`system`, `/api/system`, `/api/seen`, `/api/focus`, background `tick` |
| `static/*` | system bar, alerts, queue panel, row dot/resources, Switch |
| `launchd/*.sh` | deploy hook + new modules, merge/unmerge hooks |
| `tests/…` | tests per module; `tests/fixtures/hooks/*.json` anonymized captured payloads |

---

### Task 1: `config.py` and `settings_merge.py`

**Files:**
- Create: `config.py`, `settings_merge.py`
- Test: `tests/test_config.py`, `tests/test_settings_merge.py`

**Interfaces:**
- Produces (used by later tasks):
  - `config.DEFAULTS: dict`
  - `config.load_config(path: Path) -> dict`
  - `settings_merge.MARKER = "claude-dashboard/app/hook/claude_hook.py"`
  - `settings_merge.EVENTS: tuple[str, ...]` (8 events)
  - `settings_merge.MergeError`
  - `settings_merge.install(path: Path, command: str) -> None`
  - `settings_merge.uninstall(path: Path) -> None`
  - `settings_merge.main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write the failing tests**

`tests/test_config.py`:

```python
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import config


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name, "config.json")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, obj):
        self.path.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")

    def test_missing_file_gives_defaults(self):
        self.assertEqual(config.load_config(self.path), config.DEFAULTS)

    def test_overrides_are_type_checked(self):
        self.write({
            "waiting_notify_after_s": 300, "cpu_load_factor": 2, "notifications_enabled": False,
            "permission_notify_after_s": "soon", "process_sample_interval_s": True, "cpu_sustain_s": -5,
            "unknown_key": 1, "docker_project_dirs": {"shared-stack": "acme/shop_local", "bad": 5},
        })
        cfg = config.load_config(self.path)
        self.assertEqual(cfg["waiting_notify_after_s"], 300)
        self.assertEqual(cfg["cpu_load_factor"], 2)
        self.assertFalse(cfg["notifications_enabled"])
        self.assertEqual(cfg["permission_notify_after_s"], 60)
        self.assertEqual(cfg["process_sample_interval_s"], 10)
        self.assertEqual(cfg["cpu_sustain_s"], 120)
        self.assertNotIn("unknown_key", cfg)
        self.assertEqual(cfg["docker_project_dirs"], {"shared-stack": "acme/shop_local"})

    def test_invalid_file_gives_defaults_and_logs(self):
        for content in ("{nope", "[1, 2]"):
            with self.subTest(content=content):
                self.write(content)
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(config.load_config(self.path), config.DEFAULTS)
                self.assertIn("config:", err.getvalue())

    def test_defaults_are_not_shared(self):
        cfg = config.load_config(self.path)
        cfg["docker_project_dirs"]["x"] = "y"
        self.assertEqual(config.DEFAULTS["docker_project_dirs"], {})


if __name__ == "__main__":
    unittest.main()
```

`tests/test_settings_merge.py`:

```python
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

import settings_merge as sm

CMD = "\"/usr/local/bin/python3\" '/Users/me/Library/Application Support/claude-dashboard/app/hook/claude_hook.py'"
OURS = {"hooks": [{"type": "command", "command": CMD, "async": True, "timeout": 5}]}
CC = {"hooks": [{"type": "command", "command": "/Users/me/.config/iterm2/cc-status"}]}


class SettingsMergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name, "settings.json")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data):
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def backups(self):
        return sorted(p.name for p in self.path.parent.glob("settings.json.bak-claude-dashboard-*"))

    def test_install_keeps_others(self):
        self.write({"model": "opus", "hooks": {"Stop": [CC], "PreToolUse": [CC]}, "theme": "dark"})
        sm.install(self.path, CMD)
        data = self.read()
        self.assertEqual(list(data), ["model", "hooks", "theme"])
        self.assertEqual(data["hooks"]["Stop"], [CC, OURS])
        self.assertEqual(data["hooks"]["PreToolUse"], [CC])
        self.assertEqual(set(data["hooks"]), {"PreToolUse", *sm.EVENTS})
        for event in sm.EVENTS:
            self.assertEqual(data["hooks"][event][-1], OURS)
        self.assertEqual(len(self.backups()), 1)

    def test_install_is_idempotent(self):
        self.write({"hooks": {"Stop": [CC]}})
        sm.install(self.path, CMD)
        first = self.path.read_text(encoding="utf-8")
        sm.install(self.path, CMD)
        self.assertEqual(self.path.read_text(encoding="utf-8"), first)
        self.assertEqual(len(self.backups()), 1)

    def test_install_replaces_our_old_command(self):
        self.write({"hooks": {"Stop": [CC]}})
        sm.install(self.path, CMD)
        newer = CMD.replace("python3", "python3.14")
        sm.install(self.path, newer)
        stop = self.read()["hooks"]["Stop"]
        self.assertEqual(len(stop), 2)
        self.assertEqual(stop[1]["hooks"][0]["command"], newer)

    def test_invalid_json_aborts_unchanged(self):
        self.path.write_text("{nope", encoding="utf-8")
        with self.assertRaises(sm.MergeError):
            sm.install(self.path, CMD)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{nope")
        self.assertEqual(self.backups(), [])

    def test_non_object_hooks_aborts(self):
        for data in ({"hooks": []}, {"hooks": {"Stop": {"x": 1}}}, [1, 2]):
            with self.subTest(data=data):
                self.write(data)
                before = self.path.read_text(encoding="utf-8")
                with self.assertRaises(sm.MergeError):
                    sm.install(self.path, CMD)
                self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_missing_file_is_created(self):
        sm.install(self.path, CMD)
        self.assertEqual(set(self.read()["hooks"]), set(sm.EVENTS))
        self.assertEqual(self.backups(), [])

    def test_uninstall_removes_only_ours(self):
        self.write({"hooks": {"Stop": [CC]}, "permissions": {"allow": []}})
        sm.install(self.path, CMD)
        sm.uninstall(self.path)
        self.assertEqual(self.read(), {"hooks": {"Stop": [CC]}, "permissions": {"allow": []}})
        self.assertEqual(len(self.backups()), 2)

    def test_uninstall_without_our_hooks_is_noop(self):
        self.write({"hooks": {"Stop": [CC], "Empty": []}})
        before = self.path.read_text(encoding="utf-8")
        sm.uninstall(self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertEqual(self.backups(), [])

    def test_uninstall_missing_file(self):
        sm.uninstall(self.path)
        self.assertFalse(self.path.exists())

    def test_keeps_file_mode(self):
        self.write({"hooks": {}})
        os.chmod(self.path, 0o600)
        sm.install(self.path, CMD)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_main_cli(self):
        self.assertEqual(sm.main(["install", str(self.path), CMD]), 0)
        self.assertEqual(sm.main(["uninstall", str(self.path)]), 0)
        self.assertEqual(sm.main(["bogus"]), 2)
        self.path.write_text("{nope", encoding="utf-8")
        self.assertEqual(sm.main(["install", str(self.path), CMD]), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests, they must fail**

Run: `python3 -m unittest tests.test_config tests.test_settings_merge 2>&1 | tail -3`
Expected: `ModuleNotFoundError: No module named 'config'` / `'settings_merge'` (FAILED, errors=2)

- [ ] **Step 3: Implement**

`config.py`:

```python
"""Optional <data-dir>/config.json with typed defaults for thresholds and intervals."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

DEFAULTS = {
    "permission_notify_after_s": 60,
    "waiting_notify_after_s": 600,
    "resource_reminder_after_s": 3600,
    "cpu_load_factor": 1.0,
    "cpu_sustain_s": 120,
    "process_sample_interval_s": 10,
    "docker_sample_interval_s": 30,
    "notifications_enabled": True,
    "docker_project_dirs": {},
}


def _valid(default, value) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
    return isinstance(value, type(default))


def load_config(path: Path) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return cfg
    except (OSError, ValueError) as e:
        print(f"config: ignoring {path}: {e}", file=sys.stderr, flush=True)
        return cfg
    if not isinstance(raw, dict):
        print(f"config: ignoring {path}: not a JSON object", file=sys.stderr, flush=True)
        return cfg
    for key, default in DEFAULTS.items():
        if key in raw and _valid(default, raw[key]):
            cfg[key] = copy.deepcopy(raw[key])
    cfg["docker_project_dirs"] = {
        k: v for k, v in cfg["docker_project_dirs"].items() if isinstance(k, str) and isinstance(v, str)
    }
    return cfg
```

`settings_merge.py`:

```python
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


def add_hooks(settings: dict, command: str) -> dict:
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise MergeError("settings.hooks is not an object")
    for event in EVENTS:
        if not isinstance(hooks.get(event, []), list):
            raise MergeError(f"settings.hooks.{event} is not a list")
    ours = {"hooks": [{"type": "command", "command": command, "async": True, "timeout": 5}]}
    for event in EVENTS:
        hooks[event] = [g for g in hooks.get(event, []) if not _is_ours(g)] + [ours]
    return settings


def remove_hooks(settings: dict) -> dict:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept = [g for g in groups if not _is_ours(g)]
        if len(kept) == len(groups):
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
    path = Path(path)
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
    path = Path(path)
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
```

- [ ] **Step 4: Run the tests, they must pass**

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3`
Expected: `OK` (all existing tests plus the new ones)

- [ ] **Step 5: Commit**

```bash
git add config.py settings_merge.py tests/test_config.py tests/test_settings_merge.py
git commit -m "Add config loader and safe settings.json hook merge

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Spike: capture real hook payloads and verify assumptions

This task produces facts and fixtures, not product code. Its findings go into the ledger. Later tasks apply them as rulings when they differ from the spec.

**Files:**
- Create (repo): `tests/fixtures/hooks/<Event>.json` (anonymized payloads, one per captured event type)
- Create (scratch only, never committed): the capture script and the pty driver

**Interfaces:**
- Consumes: `settings_merge.install/uninstall` (Task 1). The capture script is temporarily installed at the hook's real path, so `MARKER` matches and uninstall removes it.
- Produces: ledger lines that start with `Task 2: Fact:` for each item in spec §2 "To verify", plus fixtures for Task 3.

- [ ] **Step 1: Prepare the capture script at the hook path**

```bash
SCRATCH=$(mktemp -d)
APP="$HOME/Library/Application Support/claude-dashboard/app"
mkdir -p "$APP/hook"
cat > "$APP/hook/claude_hook.py" <<'EOF'
import json, os, subprocess, sys, time
sys.stdout = sys.stderr = open(os.devnull, "w")   # never print, even if async is not honored
try:
  out = os.path.expanduser("~/Library/Application Support/claude-dashboard/hook-capture")
  os.makedirs(out, exist_ok=True)
  t0 = time.time()
  raw = sys.stdin.read()
  chain, pid = [], os.getpid()
  for _ in range(8):
      r = subprocess.run(["ps", "-o", "ppid=,tty=,comm=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
      if not r:
          break
      ppid, tty, comm = r.split(None, 2)
      chain.append({"pid": pid, "ppid": int(ppid), "tty": tty, "comm": comm,
                    "has_pid_file": os.path.exists(os.path.expanduser(f"~/.claude/sessions/{pid}.json"))})
      pid = int(ppid)
      if pid <= 1:
          break
  try:
      payload = json.loads(raw)
  except ValueError:
      payload = {"_raw": raw[:2000]}
  event = payload.get("hook_event_name", "unknown") if isinstance(payload, dict) else "unknown"
  env = {k: v for k, v in os.environ.items() if k.startswith(("CLAUDE", "TERM", "ITERM"))}
  with open(os.path.join(out, f"{time.time():.6f}-{event}.json"), "w") as fh:
      json.dump({"payload": payload, "chain": chain, "env": env, "started": t0}, fh, indent=1)
except Exception:
  pass
EOF
cp ~/.claude/settings.json "$SCRATCH/settings.before.json"
python3 settings_merge.py install ~/.claude/settings.json "\"$(command -v python3)\" '$APP/hook/claude_hook.py'"
diff <(python3 -m json.tool "$SCRATCH/settings.before.json") <(python3 -m json.tool ~/.claude/settings.json) | head -40
```

Expected:
- The diff shows only added groups, eight `{"hooks": [{"type": "command", "command": "…claude_hook.py'", "async": true, "timeout": 5}]}` entries.
- No `cc-status` line is removed.

- [ ] **Step 2: Hot reload and the current session**

Run any harmless tool call in this session (e.g. `ls "$HOME/Library/Application Support/claude-dashboard/hook-capture"`), wait 5 s, then:

```bash
ls "$HOME/Library/Application Support/claude-dashboard/hook-capture" | tail -5
```

Record the result in the ledger: `Task 2: Fact: running sessions <do|do not> pick up new hooks without restart`. The test is whether a `PostToolUse` file for this session's id appeared.

- [ ] **Step 3: Non-interactive run (SessionStart/UserPromptSubmit/Stop/SessionEnd)**

```bash
(cd /Users/me/Documents/Development/agent-orgestrator && claude -p "Reply with the single word OK." > "$SCRATCH/p.out" 2>&1); cat "$SCRATCH/p.out"
ls "$HOME/Library/Application Support/claude-dashboard/hook-capture"
```

Expected:
- `OK`, with no settings or hook validation error in `p.out`. An error means Claude Code rejects `async`/`timeout`: stop, remove the hook (Step 7) and record it.
- Capture files for at least `SessionStart`, `UserPromptSubmit`, `Stop` and `SessionEnd`. If print mode runs no hooks, record that and rely on Step 4.
- Check also that the existing `cc-status` hooks still run: the iTerm2 tab status of this session keeps updating.
- Record whether `async` is honored. If hooks were synchronous, every tool call would wait for the ~60–100 ms Python start. If it is not honored, drop `PostToolUse` from `EVENTS` and let `UserPromptSubmit`/`Stop` clear the `permission` state (a ruling).

- [ ] **Step 4: Interactive permission prompt (PermissionRequest/Notification/PostToolUse) through a pty**

```bash
cat > "$SCRATCH/drive.py" <<'EOF'
import os, pty, select, signal, sys, time
cwd = sys.argv[1]
argv = ["claude", "--permission-mode", "default",
        "Run exactly this shell command and nothing else: touch /tmp/claude-hook-spike-probe"]
pid, fd = pty.fork()
if pid == 0:
    os.chdir(cwd)
    os.execvp(argv[0], argv)
import glob, json
def pid_status():
    for f in glob.glob(os.path.expanduser("~/.claude/sessions/*.json")):
        try:
            d = json.load(open(f))
        except (OSError, ValueError):
            continue
        if d.get("pid") == pid:
            return d.get("status"), d.get("statusUpdatedAt")
    return None
buf, start, prompt_at, esc_at = b"", time.time(), None, None
while time.time() - start < 150:
    r, _, _ = select.select([fd], [], [], 1)
    if r:
        try:
            buf += os.read(fd, 65536)
        except OSError:
            break
    now = time.time()
    if prompt_at is None and (b"Do you want" in buf or b"ermission" in buf):
        prompt_at = now
        print(f"{now - start:5.1f}s permission prompt visible, pid status {pid_status()}")
    if prompt_at and esc_at is None and now - prompt_at > 8:
        os.write(fd, b"\x1b")
        esc_at = now
        print(f"{now - start:5.1f}s sent Esc, pid status {pid_status()}")
    if esc_at and int(now - esc_at) % 10 == 0:
        print(f"{now - start:5.1f}s after Esc, pid status {pid_status()}")
    if esc_at and now - esc_at > 75:
        break
os.kill(pid, signal.SIGTERM)
time.sleep(2)
print("bytes read:", len(buf), "| prompt seen:", prompt_at is not None, "| esc sent:", esc_at is not None)
EOF
python3 "$SCRATCH/drive.py" /Users/me/Documents/Development/agent-orgestrator
ls "$HOME/Library/Application Support/claude-dashboard/hook-capture" | sed 's/^[0-9.]*-//' | sort | uniq -c
```

Expected: capture files for `PermissionRequest` and a `Notification` with `notification_type` `permission_prompt` (it fires after ~6 s of waiting).

Then record what happened after Esc:
- which events fired;
- whether a `Notification` `idle_prompt` arrived about 60 s later;
- the pid file status timeline during the prompt and after Esc.

These facts decide the interrupt rules:
- Task 3 maps `idle_prompt` during `working` to `idle`. Extend it to `permission`/`question` if the spike shows `idle_prompt` after an Esc on a prompt.
- Task 4 overrides hook `working` with live `idle`. Extend it to `permission` only if the pid status during a real permission prompt is **not** `idle`.

Record each decision as a ruling. If the pty run never reaches a prompt, record what was captured; missing events are then handled by tolerant parsing.

- [ ] **Step 5: Inspect the captures and record the facts**

```bash
for f in "$HOME/Library/Application Support/claude-dashboard/hook-capture"/*.json; do
  python3 -c "
import json, sys; d = json.load(open(sys.argv[1])); p = d['payload']
print(p.get('hook_event_name'), sorted(p) if isinstance(p, dict) else p)
print('   chain:', [(c['pid'], c['tty'], c['comm'][-30:], c['has_pid_file']) for c in d['chain']])
print('   env:', sorted(d['env']))" "$f"; done
```

Write one `Task 2: Fact:` line per item in spec §2:
- the field names per event (Stop message field, Notification type field and values, the PermissionRequest tool field, the StopFailure error field, the SessionEnd reason field, SessionStart `source`);
- which ancestor has the pid file and its tty (and so how many `ps` steps the hook needs);
- whether `async` is honored (the `started` time vs Claude continuing);
- hot reload, from Step 2.

- [ ] **Step 6: Verify iTerm2 focus by tty**

```bash
python3 - "$HOME/Library/Application Support/claude-dashboard/hook-capture" <<'EOF'
import json, os, sys
seen = set()
for name in sorted(os.listdir(sys.argv[1])):
    d = json.load(open(os.path.join(sys.argv[1], name)))
    for c in d["chain"]:
        if c["has_pid_file"] and (c["pid"], c["tty"]) not in seen:
            seen.add((c["pid"], c["tty"]))
            print("claude pid", c["pid"], "tty", c["tty"], "session", str(d["payload"].get("session_id"))[:8])
EOF
osascript -e 'on run argv
  set out to {}
  tell application "iTerm2"
    repeat with w in windows
      repeat with t in tabs of w
        repeat with s in sessions of t
          set end of out to (tty of s)
        end repeat
      end repeat
    end repeat
  end tell
  return out
end run'
```

Expected: the claude pids from the captures with their ttys, and an iTerm2 list of `/dev/ttysNNN` that contains this session's tty. Record `Task 2: Fact: iTerm2 lists session ttys: <yes/no>` and whether macOS asked for Automation permission. Selecting a tab is verified in Task 9 with the user.

- [ ] **Step 7: Remove the capture hook and verify settings.json is back to the original**

```bash
python3 settings_merge.py uninstall ~/.claude/settings.json
diff <(python3 -m json.tool "$SCRATCH/settings.before.json") <(python3 -m json.tool ~/.claude/settings.json) && echo "settings.json restored"
rm -f "$APP/hook/claude_hook.py" /tmp/claude-hook-spike-probe
```

Expected: `settings.json restored` (the content is equal after normalization).

- [ ] **Step 8: Save anonymized fixtures**

For each event type captured, write `tests/fixtures/hooks/<Event>.json` containing **only the payload**, anonymized:
- `session_id` → `00000000-0000-4000-8000-0000000000aa`;
- paths → `/Users/me/Documents/Development/acme/shop` (or `/tmp/...`);
- `transcript_path` → `/Users/me/.claude/projects/-Users-me-Documents-Development-acme-shop/00000000-0000-4000-8000-0000000000aa.jsonl`;
- message texts → short neutral English, keeping their shape (multi-line stays multi-line);
- keep every key and type.

Use this helper (adjust the mapping if a payload has other real values):

```bash
python3 - "$HOME/Library/Application Support/claude-dashboard/hook-capture" tests/fixtures/hooks <<'EOF'
import json, os, re, sys
src, dst = sys.argv[1], sys.argv[2]
os.makedirs(dst, exist_ok=True)
SID = "00000000-0000-4000-8000-0000000000aa"
def scrub(v):
    if isinstance(v, dict):
        return {k: scrub(x) for k, x in v.items()}
    if isinstance(v, list):
        return [scrub(x) for x in v]
    if isinstance(v, str):
        v = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", SID, v)
        v = re.sub(r"/Users/[^/\s\"']+", "/Users/me", v)
        v = re.sub(r"-Users-[A-Za-z0-9-]+?-Documents-Development-[A-Za-z0-9-]+", "-Users-me-Documents-Development-acme-shop", v)
        v = re.sub(r"/Documents/Development/[^\s\"']+", "/Documents/Development/acme/shop", v)
    return v
KEEP = {"session_id", "hook_event_name", "cwd", "notification_type", "tool_name", "source", "reason", "end_reason",
        "error_type", "permission_mode", "transcript_path", "stop_hook_active"}
TEXT = {"last_assistant_message": "Example text line one.\nExample text line two.", "message": "Example message"}
def blank(v):
    if isinstance(v, str):
        return "x"
    if isinstance(v, dict):
        return {k: blank(x) for k, x in v.items()}
    if isinstance(v, list):
        return [blank(x) for x in v]
    return v
def allowlist(p):
    # Keep only known-safe fields verbatim; every other string becomes "x" (types and keys preserved).
    return {k: (v if k in KEEP else TEXT.get(k, blank(v))) for k, v in p.items()}
seen = set()
for name in sorted(os.listdir(src)):
    payload = json.load(open(os.path.join(src, name)))["payload"]
    event = payload.get("hook_event_name")
    kind = payload.get("notification_type")
    key = f"{event}-{kind}" if kind else event
    if not event or key in seen:
        continue
    seen.add(key)
    payload = allowlist(scrub(payload))
    json.dump(payload, open(os.path.join(dst, f"{key}.json"), "w"), indent=2, sort_keys=True)
    print("wrote", key)
EOF
leaks=0
for w in "$(whoami)" $(ls "$HOME/Documents/Development"); do
  if grep -rqiF -- "$w" tests/fixtures/hooks; then echo "possible leak: $w"; leaks=1; fi
done
[ "$leaks" = 0 ] && echo "fixtures clean"
```

Then run the patterns from the repository-wide anonymization (the scratch `anonymize.py` used when the repo was published) over `tests/fixtures/hooks` as a second check. It must change nothing.

Expected: one fixture per captured event type, and `fixtures clean`. Only allowlisted fields keep their values; `session_title`, `tool_input`, `tool_response` and prompts become `"x"`. The check compares the fixtures with the user name and every directory name under `~/Documents/Development`, so it needs no hard-coded names. Generic hits (e.g. a folder called `tests`) are judged by hand. Inspect every fixture by eye before committing.

- [ ] **Step 9: Commit fixtures and clean up captures**

```bash
git add tests/fixtures/hooks
git commit -m "Add anonymized Claude Code hook payload fixtures from a capture spike

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
rm -rf "$HOME/Library/Application Support/claude-dashboard/hook-capture" "$SCRATCH"
```

---

### Task 3: The hook script

**Files:**
- Create: `hook/claude_hook.py`
- Test: `tests/test_hook.py`

**Interfaces:**
- Consumes: the Task 2 facts. If a field name differs, change only `next_state()` and the payloads in the tests below, and add a ruling.
- Produces: the state file `<data-dir>/agents/<session_id>.json`:
  - fields `version, session_id, state, since, event, note, cwd, claude_pid, tty, updated_at`;
  - `state` is one of `idle|working|permission|question|waiting|failed|ended`;
  - times are ISO `YYYY-mm-ddTHH:MM:SSZ`.
- Python API (tests): `handle(payload, *, ddir, cdir, parent_pid, ps_info, now)`, `next_state(event, payload, current)`, `find_claude(pid, sessions_dir, ps_info)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_hook.py`:

```python
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = ROOT / "hook" / "claude_hook.py"
FIXTURES = ROOT / "tests" / "fixtures" / "hooks"
SID = "11111111-0000-4000-8000-000000000000"


def load_hook():
    spec = importlib.util.spec_from_file_location("claude_hook", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HookTest(unittest.TestCase):
    def setUp(self):
        self.hook = load_hook()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data, self.claude = root / "data", root / "claude"
        (self.claude / "sessions").mkdir(parents=True)
        (self.claude / "sessions" / "400.json").write_text("{}")
        self.table = {500: (400, "??"), 400: (300, "ttys003"), 300: (1, "ttys003")}
        self.ps_calls = []
        self.ticks = iter(f"2026-10-08T10:00:{i:02d}Z" for i in range(60))

    def tearDown(self):
        self.tmp.cleanup()

    def ps(self, pid):
        self.ps_calls.append(pid)
        return self.table.get(pid)

    def fire(self, event, **fields):
        payload = {"session_id": SID, "hook_event_name": event, "cwd": "/w/acme/shop", **fields}
        self.hook.handle(payload, ddir=str(self.data), cdir=str(self.claude), parent_pid=500,
                         ps_info=self.ps, now=lambda: next(self.ticks))
        return self.state()

    def state(self):
        path = self.data / "agents" / f"{SID}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def test_turn_cycle(self):
        self.assertEqual(self.fire("SessionStart", source="startup")["state"], "idle")
        self.assertEqual(self.fire("UserPromptSubmit")["state"], "working")
        s = self.fire("Stop", last_assistant_message="\n\nAll tests pass.\nDetails follow.")
        self.assertEqual((s["state"], s["note"], s["event"]), ("waiting", "All tests pass.", "Stop"))
        self.assertEqual((s["claude_pid"], s["tty"], s["cwd"]), (400, "ttys003", "/w/acme/shop"))
        self.assertEqual(s["version"], 1)

    def test_permission_since_is_kept_and_cleared_by_post_tool_use(self):
        self.fire("UserPromptSubmit")
        first = self.fire("PermissionRequest", tool_name="Bash")
        self.assertEqual((first["state"], first["note"]), ("permission", "Bash"))
        again = self.fire("Notification", notification_type="permission_prompt",
                          message="Claude needs your permission to use Bash")
        self.assertEqual(again["since"], first["since"])
        self.assertEqual(again["note"], "Claude needs your permission to use Bash")
        self.assertEqual(self.fire("PostToolUse", tool_name="Bash")["state"], "working")

    def test_post_tool_use_does_not_touch_other_states(self):
        self.fire("Stop", last_assistant_message="done")
        before = self.state()
        self.fire("PostToolUse", tool_name="Bash")
        self.assertEqual(self.state(), before)

    def test_question_failure_end_and_ignored_notifications(self):
        self.assertEqual(self.fire("Notification", notification_type="elicitation_dialog", message="Pick one")["state"], "question")
        before = self.state()
        self.fire("Notification", notification_type="idle_prompt", message="Claude is waiting for your input")
        self.assertEqual(self.state(), before)
        failed = self.fire("StopFailure", error_type="rate_limit")
        self.assertEqual((failed["state"], failed["note"]), ("failed", "rate_limit"))
        self.assertEqual(self.fire("SessionEnd", reason="prompt_input_exit")["state"], "ended")

    def test_idle_prompt_after_interrupt_goes_idle(self):
        self.fire("UserPromptSubmit")
        self.assertEqual(self.fire("Notification", notification_type="idle_prompt", message="waiting")["state"], "idle")

    def test_claude_pid_resolved_once_and_again_on_session_start(self):
        self.fire("UserPromptSubmit")
        calls = len(self.ps_calls)
        self.assertGreater(calls, 0)
        self.fire("Stop", last_assistant_message="x")
        self.assertEqual(len(self.ps_calls), calls)
        self.fire("SessionStart", source="resume")
        self.assertGreater(len(self.ps_calls), calls)

    def test_unknown_ancestry_gives_nulls(self):
        self.table = {}
        s = self.fire("UserPromptSubmit")
        self.assertIsNone(s["claude_pid"])
        self.assertIsNone(s["tty"])

    def test_invalid_input_does_nothing(self):
        for payload in (None, [], {"session_id": "../../etc/x", "hook_event_name": "Stop"},
                        {"session_id": SID}, {"session_id": SID, "hook_event_name": "Bogus"}):
            self.hook.handle(payload, ddir=str(self.data), cdir=str(self.claude), parent_pid=500,
                             ps_info=self.ps, now=lambda: "2026-10-08T10:00:00Z")
        self.assertFalse((self.data / "agents").exists())

    def test_long_note_is_truncated(self):
        s = self.fire("Stop", last_assistant_message="x" * 500)
        self.assertEqual(len(s["note"]), 200)
        self.assertTrue(s["note"].endswith("…"))

    def test_script_is_silent_and_exits_zero(self):
        env = dict(os.environ, CLAUDE_DASHBOARD_DATA_DIR=str(self.data), CLAUDE_DASHBOARD_CLAUDE_DIR=str(self.claude))
        for stdin in ("not json", json.dumps({"session_id": SID, "hook_event_name": "Stop", "last_assistant_message": "hi"})):
            r = subprocess.run([sys.executable, str(HOOK_PATH)], input=stdin, capture_output=True, text=True,
                               env=env, timeout=20)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        self.assertEqual(self.state()["state"], "waiting")

    def test_captured_fixtures(self):
        expected = {"SessionStart": "idle", "UserPromptSubmit": "working", "PermissionRequest": "permission",
                    "Notification-permission_prompt": "permission", "Stop": "waiting", "StopFailure": "failed",
                    "SessionEnd": "ended"}
        files = sorted(FIXTURES.glob("*.json"))
        if not files:
            self.skipTest("no captured fixtures")
        for f in files:
            with self.subTest(fixture=f.name):
                payload = json.loads(f.read_text())
                payload["session_id"] = SID
                self.hook.handle(payload, ddir=str(self.data), cdir=str(self.claude), parent_pid=500,
                                 ps_info=self.ps, now=lambda: "2026-10-08T10:00:00Z")
                if f.stem in expected:
                    self.assertEqual(self.state()["state"], expected[f.stem])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests, they must fail**

Run: `python3 -m unittest tests.test_hook 2>&1 | tail -3`
Expected: errors such as `FileNotFoundError` (no `hook/claude_hook.py`).

- [ ] **Step 3: Implement**

`hook/claude_hook.py`:

```python
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
```

- [ ] **Step 4: Run the tests, they must pass**

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3`
Expected: `OK`. `test_captured_fixtures` runs over the Task 2 fixtures. If an expected state fails because a field name differs, fix `next_state()` per the Task 2 facts and record a ruling.

- [ ] **Step 5: Commit**

```bash
git add hook/claude_hook.py tests/test_hook.py
git commit -m "Add Claude Code hook that records per-session attention state

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `agents.py` (attention, seen marks, queue) and `live.py` status time

**Files:**
- Create: `agents.py`
- Modify: `live.py` (add `status_updated_at`)
- Test: `tests/test_agents.py`, `tests/test_live.py` (update the expected dict, add one test)

**Interfaces:**
- Consumes: state files from Task 3.
- Produces:
  - `agents.read_hook_states(agents_dir: Path) -> dict[str, dict]`
  - `agents.SeenStore(path)` with `.all() -> dict[str, str]` and `.mark(session_id: str, when: str) -> str`
  - `agents.attention_for(hook: dict | None, live: dict | None, seen_at: str | None) -> dict`, which returns `{state, since, note, source, in_queue, group}`
  - `agents.build_queue(rows: list[dict]) -> list[str]`
  - `agents.ms_to_iso(ms) -> str | None`
  - live entries gain `"status_updated_at": int | None`

- [ ] **Step 1: Write the failing tests**

`tests/test_agents.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

import agents

T0, T1, T2 = "2026-10-08T10:00:00Z", "2026-10-08T10:05:00Z", "2026-10-08T10:10:00Z"
LIVE_IDLE = {"status": "idle", "pid": 42, "status_updated_at": 1791453600000}
LIVE_BUSY = {"status": "busy", "pid": 42, "status_updated_at": 1791453900000}


class AttentionTest(unittest.TestCase):
    def test_hook_state_wins(self):
        a = agents.attention_for({"state": "permission", "since": T1, "note": "Bash"}, LIVE_BUSY, None)
        self.assertEqual(a, {"state": "permission", "since": T1, "note": "Bash", "source": "hook",
                             "in_queue": True, "group": "blocking"})

    def test_interrupted_turn_becomes_idle(self):
        hook = {"state": "working", "since": T0}
        self.assertEqual(agents.attention_for(hook, dict(LIVE_IDLE, status_updated_at=1791453900000), None)["state"], "idle")
        self.assertEqual(agents.attention_for(hook, dict(LIVE_IDLE, status_updated_at=1791453602000), None)["state"], "working")
        self.assertEqual(agents.attention_for(hook, LIVE_BUSY, None)["state"], "working")
        perm = {"state": "permission", "since": T0}
        self.assertEqual(agents.attention_for(perm, dict(LIVE_IDLE, status_updated_at=1791453900000), None)["state"], "permission")
        self.assertEqual(agents.iso_to_ms(T0), 1791453600000)
        self.assertIsNone(agents.iso_to_ms("x"))

    def test_estimate_from_live_status(self):
        a = agents.attention_for(None, LIVE_IDLE, None)
        self.assertEqual((a["state"], a["since"], a["source"], a["in_queue"], a["group"]),
                         ("waiting", T0, "estimate", True, "done"))
        b = agents.attention_for(None, LIVE_BUSY, None)
        self.assertEqual((b["state"], b["since"], b["in_queue"], b["group"]), ("working", T1, False, None))

    def test_not_live_is_ended(self):
        a = agents.attention_for({"state": "waiting", "since": T1, "note": "x"}, None, None)
        self.assertEqual((a["state"], a["in_queue"], a["group"], a["source"]), ("ended", False, None, "hook"))

    def test_seen_hides_until_next_since(self):
        hook = {"state": "waiting", "since": T1}
        self.assertFalse(agents.attention_for(hook, LIVE_IDLE, T2)["in_queue"])
        self.assertTrue(agents.attention_for(hook, LIVE_IDLE, T0)["in_queue"])

    def test_non_queue_states_and_missing_since(self):
        for state in ("working", "idle", "ended"):
            self.assertFalse(agents.attention_for({"state": state, "since": T1}, LIVE_IDLE, None)["in_queue"])
        no_time = dict(LIVE_IDLE, status_updated_at=None)
        self.assertFalse(agents.attention_for(None, no_time, None)["in_queue"])

    def test_build_queue_order(self):
        def row(sid, group, since, in_queue=True):
            return {"session_id": sid, "attention": {"in_queue": in_queue, "group": group, "since": since}}
        rows = [row("a", "done", T0), row("b", "blocking", T2), row("c", "blocking", T1),
                row("d", None, T0, in_queue=False), row("e", "done", T1)]
        self.assertEqual(agents.build_queue(rows), ["c", "b", "a", "e"])

    def test_ms_to_iso(self):
        self.assertEqual(agents.ms_to_iso(1791453600000), T0)
        self.assertIsNone(agents.ms_to_iso(None))
        self.assertIsNone(agents.ms_to_iso("x"))


class StoresTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_hook_states(self):
        d = self.root / "agents"
        d.mkdir()
        (d / "aaaa.json").write_text(json.dumps({"state": "waiting", "since": T0}))
        (d / "bbbb.json").write_text("{broken")
        (d / "cccc.json").write_text(json.dumps({"state": "waiting"}))
        (d / "dddd.lock").write_text("")
        self.assertEqual(list(agents.read_hook_states(d)), ["aaaa"])
        self.assertEqual(agents.read_hook_states(self.root / "missing"), {})

    def test_seen_store(self):
        store = agents.SeenStore(self.root / "sub" / "seen.json")
        self.assertEqual(store.all(), {})
        self.assertEqual(store.mark("s1", T1), T1)
        self.assertEqual(agents.SeenStore(self.root / "sub" / "seen.json").all(), {"s1": T1})
        (self.root / "sub" / "seen.json").write_text("{broken")
        self.assertEqual(store.all(), {})


if __name__ == "__main__":
    unittest.main()
```

In `tests/test_live.py`, replace the expected value in `test_matching_process_is_live`:

```python
        self.assertEqual(result, {sid(1): {"status": "busy", "pid": 100, "name": "x", "updated_at": 1791371445884,
                                           "status_updated_at": None}})
```

Then add this test to `GetLiveTest`:

```python
    def test_status_updated_at_is_passed_through(self):
        self.fake.write_pid(100, {"pid": 100, "sessionId": sid(1), "procStart": START, "status": "idle",
                                  "statusUpdatedAt": 1791453600000})
        result = live.get_live(self.fake.root, ps=lambda pids: {100: START})
        self.assertEqual(result[sid(1)]["status_updated_at"], 1791453600000)
```

- [ ] **Step 2: Run the tests, they must fail**

Run: `python3 -m unittest tests.test_agents tests.test_live 2>&1 | tail -3`
Expected: `No module named 'agents'`, and `test_live` fails on the missing `status_updated_at`.

- [ ] **Step 3: Implement**

In `live.py`, add `status_updated_at` to the result entry:

```python
            result[e["sessionId"]] = {
                "status": e.get("status") or "unknown",
                "pid": e["pid"],
                "name": e.get("name"),
                "updated_at": e.get("updatedAt"),
                "status_updated_at": e.get("statusUpdatedAt"),
            }
```

`agents.py`:

```python
"""Attention state per session (hook data, a fallback estimate, seen marks) and the "waiting for you" queue."""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

QUEUE_STATES = {"permission", "question", "failed", "waiting"}
BLOCKING_STATES = {"permission", "question", "failed"}
INTERRUPT_GRACE_MS = 5000


def ms_to_iso(ms) -> str | None:
    if not isinstance(ms, (int, float)) or isinstance(ms, bool):
        return None
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_to_ms(ts) -> int | None:
    try:
        return int(datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def read_hook_states(agents_dir: Path) -> dict[str, dict]:
    result: dict[str, dict] = {}
    try:
        files = sorted(Path(agents_dir).glob("*.json"))
    except OSError:
        return result
    for f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and isinstance(data.get("state"), str) and isinstance(data.get("since"), str):
            result[f.stem] = data
    return result


def attention_for(hook: dict | None, live: dict | None, seen_at: str | None) -> dict:
    if live is None:
        state, since = "ended", (hook or {}).get("since")
        note, source = (hook or {}).get("note"), "hook" if hook else "estimate"
    elif hook:
        state, since, note, source = hook["state"], hook["since"], hook.get("note"), "hook"
        hook_ms, live_ms = iso_to_ms(since), live.get("status_updated_at")
        if (state == "working" and live.get("status") == "idle" and hook_ms is not None
                and isinstance(live_ms, (int, float)) and live_ms > hook_ms + INTERRUPT_GRACE_MS):
            state = "idle"  # interrupted turn: Stop does not fire on Esc
    else:
        state = "waiting" if live.get("status") == "idle" else "working"
        since, note, source = ms_to_iso(live.get("status_updated_at")), None, "estimate"
    in_queue = state in QUEUE_STATES and since is not None and (seen_at is None or seen_at < since)
    group = ("blocking" if state in BLOCKING_STATES else "done") if in_queue else None
    return {"state": state, "since": since, "note": note, "source": source, "in_queue": in_queue, "group": group}


def build_queue(rows: list[dict]) -> list[str]:
    queued = [r for r in rows if (r.get("attention") or {}).get("in_queue")]
    queued.sort(key=lambda r: (0 if r["attention"]["group"] == "blocking" else 1, r["attention"]["since"]))
    return [r["session_id"] for r in queued]


class SeenStore:
    """{session_id: seen_at} in a small JSON file, written atomically."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def all(self) -> dict[str, str]:
        with self._lock:
            return self._read()

    def mark(self, session_id: str, when: str) -> str:
        with self._lock:
            data = self._read()
            data[session_id] = when
            self._write(data)
        return when

    def _read(self) -> dict[str, str]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        seen = data.get("seen") if isinstance(data, dict) else None
        return {k: v for k, v in seen.items() if isinstance(v, str)} if isinstance(seen, dict) else {}

    def _write(self, data: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".seen-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "seen": data}, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise
```

- [ ] **Step 4: Run the tests, they must pass**

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add agents.py live.py tests/test_agents.py tests/test_live.py
git commit -m "Compute per-session attention state and the waiting-for-you queue

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `monitor.py`

**Files:**
- Create: `monitor.py`
- Test: `tests/test_monitor.py`

**Interfaces:**
- Consumes: `config` dict (Task 1).
- Produces:
  - Parsers: `parse_ps(out) -> list[Proc]`, `app_name(comm) -> str`, `top_apps(procs, n=5)`, `subtree(procs, root)`, `tree_usage(procs, root) -> dict | None` (`{rss_mb, cpu, processes, top, tty}`), `parse_pressure_level`, `parse_memory_free`, `parse_swap`, `parse_mem_mb`, `parse_docker_ps`, `parse_docker_stats`, `docker_projects`.
  - `stack_for(row, projects, mapping, dev_root) -> str | None`.
  - `Monitor(config, runner=subprocess.run, clock=time.time, loadavg=os.getloadavg, ncpu=None)` with `.sample()`, `.sample_docker()`, `.snapshot() -> dict` (the spec §10 `system` object), `.tree_usage(pid) -> dict | None`, `.start(on_sample=None)` and `.stop()`.

- [ ] **Step 1: Write the failing tests**

`tests/test_monitor.py`:

```python
import json
import subprocess
import unittest

import config
import monitor

PS_OUT = """    1     0   12000   0,5 ??       /sbin/launchd
  400   300  220000   3.0 ttys003  claude
  500   400   10000   1.0 ttys003  /bin/zsh
  501   500  150000  50.0 ttys003  /usr/local/bin/php
  600     1  900000  20.0 ??       /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
  601   600 1200000  30.0 ??       /Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/Versions/1/Helpers/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer)
garbage line
"""
DOCKER_PS = "\n".join(json.dumps(d) for d in [
    {"Names": "budget_mysql8", "Labels": "com.docker.compose.project=budget,com.docker.compose.project.working_dir=/w/budget/Docker,com.docker.compose.service=mysql"},
    {"Names": "budget_php", "Labels": "com.docker.compose.project=budget,com.docker.compose.project.working_dir=/w/budget/Docker"},
    {"Names": "mailpit", "Labels": ""},
])
DOCKER_STATS = "\n".join(json.dumps(d) for d in [
    {"Name": "budget_mysql8", "CPUPerc": "0.90%", "MemUsage": "558.9MiB / 7.748GiB"},
    {"Name": "budget_php", "CPUPerc": "1.10%", "MemUsage": "88.62MiB / 7.748GiB"},
    {"Name": "mailpit", "CPUPerc": "0.00%", "MemUsage": "14.84MiB / 7.748GiB"},
])
PS_ARGS = ("ps", "-axo", "pid=,ppid=,rss=,%cpu=,tty=,comm=")


class FakeRunner:
    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((tuple(args), kwargs))
        key = tuple(args)
        if key not in self.outputs:
            raise FileNotFoundError(args[0])
        out = self.outputs[key]
        if isinstance(out, Exception):
            raise out
        return subprocess.CompletedProcess(args, 0 if out is not None else 1, out or "", "")


class ParserTest(unittest.TestCase):
    def test_parse_ps(self):
        procs = monitor.parse_ps(PS_OUT)
        self.assertEqual(len(procs), 6)
        self.assertEqual(procs[0].cpu, 0.5)
        self.assertTrue(procs[5].comm.endswith("Google Chrome Helper (Renderer)"))

    def test_app_name(self):
        procs = monitor.parse_ps(PS_OUT)
        self.assertEqual([monitor.app_name(p.comm) for p in procs],
                         ["launchd", "claude", "zsh", "php", "Google Chrome", "Google Chrome"])

    def test_top_apps(self):
        top = monitor.top_apps(monitor.parse_ps(PS_OUT), n=2)
        self.assertEqual(top, [{"name": "Google Chrome", "rss_mb": 2051, "cpu": 50.0},
                               {"name": "claude", "rss_mb": 215, "cpu": 3.0}])

    def test_tree_usage(self):
        usage = monitor.tree_usage(monitor.parse_ps(PS_OUT), 400)
        self.assertEqual((usage["rss_mb"], usage["cpu"], usage["processes"], usage["tty"]), (371, 54.0, 3, "ttys003"))
        self.assertEqual([p["pid"] for p in usage["top"]], [400, 501, 500])
        self.assertIsNone(monitor.tree_usage(monitor.parse_ps(PS_OUT), 999))
        self.assertIsNone(monitor.tree_usage(monitor.parse_ps(PS_OUT), 600)["tty"])

    def test_system_parsers(self):
        self.assertEqual([monitor.parse_pressure_level(x) for x in ("1\n", "2", "4", "x")],
                         ["normal", "warn", "critical", None])
        self.assertEqual(monitor.parse_memory_free("blah\nSystem-wide memory free percentage: 38%\n"), 38)
        self.assertIsNone(monitor.parse_memory_free("nothing"))
        self.assertEqual(monitor.parse_swap("total = 10240.00M  used = 9297.25M  free = 942.75M  (encrypted)"), (9297, 10240))
        self.assertEqual(monitor.parse_swap("total = 10240,00M  used = 9297,25M  free = 942,75M"), (9297, 10240))
        self.assertEqual(monitor.parse_swap("total = 2.00G  used = 1.50G  free = 0.50G"), (1536, 2048))
        self.assertEqual(monitor.parse_swap("garbage"), (None, None))

    def test_docker_parsers(self):
        self.assertEqual(monitor.parse_mem_mb("1.5GiB"), 1536)
        self.assertEqual(monitor.parse_mem_mb("512KiB"), 0.5)
        self.assertIsNone(monitor.parse_mem_mb("--"))
        projects = monitor.docker_projects(monitor.parse_docker_ps(DOCKER_PS), monitor.parse_docker_stats(DOCKER_STATS))
        self.assertEqual(projects, [
            {"project": "budget", "working_dir": "/w/budget/Docker", "rss_mb": 648, "cpu": 2.0, "containers": 2},
            {"project": "mailpit", "working_dir": None, "rss_mb": 15, "cpu": 0.0, "containers": 1},
        ])

    def test_stack_for(self):
        projects = [{"project": "budget", "working_dir": "/w/budget/Docker"},
                    {"project": "shared-stack", "working_dir": "/w/acme/shop_local/application/local-app"}]
        mapping = {"shared-stack": "acme/shop_local"}
        self.assertEqual(monitor.stack_for({"repo": "/w/budget", "cwd": "/w/budget"}, projects, mapping, "/w"), "budget")
        wt = {"repo": "/w/acme/shop_local/shop", "cwd": "/w/acme/shop_local/shop/.claude/worktrees/PROJ-1"}
        self.assertEqual(monitor.stack_for(wt, projects, mapping, "/w"), "shared-stack")
        self.assertIsNone(monitor.stack_for({"repo": "/w/acme/shop", "cwd": "/w/acme/shop"}, projects, mapping, "/w"))
        self.assertIsNone(monitor.stack_for({"repo": "/w/budget", "cwd": "/w/budget"}, projects[1:], {}, "/w"))


class MonitorTest(unittest.TestCase):
    def outputs(self, **over):
        base = {
            PS_ARGS: PS_OUT,
            ("sysctl", "-n", "kern.memorystatus_vm_pressure_level"): "2\n",
            ("memory_pressure", "-Q"): "System-wide memory free percentage: 38%\n",
            ("sysctl", "-n", "vm.swapusage"): "total = 10240.00M  used = 9297.25M  free = 942.75M",
            ("docker", "ps", "--format", "{{json .}}"): DOCKER_PS,
            ("docker", "stats", "--no-stream", "--format", "{{json .}}"): DOCKER_STATS,
        }
        base.update(over)
        return base

    def make(self, outputs, load=1.0):
        self.now = 1000.0
        self.load = load
        cfg = config.load_config("/nonexistent/config.json")
        self.runner = FakeRunner(outputs)
        return monitor.Monitor(cfg, runner=self.runner, clock=lambda: self.now,
                               loadavg=lambda: (self.load, 0, 0), ncpu=8)

    def test_sample_builds_system_snapshot(self):
        m = self.make(self.outputs())
        self.assertIsNone(m.snapshot()["sampled_at"])
        m.sample()
        s = m.snapshot()
        self.assertEqual(s["memory"], {"pressure": "warn", "free_pct": 38, "swap_used_mb": 9297, "swap_total_mb": 10240})
        self.assertEqual(s["cpu"], {"load1": 1.0, "ncpu": 8})
        self.assertEqual(s["top_apps"][0]["name"], "Google Chrome")
        self.assertEqual([(a["kind"], a["level"]) for a in s["alerts"]], [("memory", "warn")])
        self.assertEqual(m.tree_usage(400)["rss_mb"], 371)
        for args, kwargs in self.runner.calls:
            self.assertEqual(kwargs["env"]["LC_ALL"], "C")
            self.assertIn("timeout", kwargs)

    def test_cpu_alert_needs_sustain(self):
        m = self.make(self.outputs(**{("sysctl", "-n", "kern.memorystatus_vm_pressure_level"): "1"}), load=9.0)
        m.sample()
        self.assertEqual(m.snapshot()["alerts"], [])
        self.now += 130
        m.sample()
        [alert] = m.snapshot()["alerts"]
        self.assertEqual((alert["kind"], alert["level"]), ("cpu", "warn"))
        self.assertEqual(alert["since"], "1970-01-01T00:18:50Z")  # first sample with the alert raised (now=1130)
        self.load = 2.0
        m.sample()
        self.assertEqual(m.snapshot()["alerts"], [])

    def test_alert_since_is_stable(self):
        m = self.make(self.outputs())
        m.sample()
        first = m.snapshot()["alerts"][0]["since"]
        self.now += 60
        m.sample()
        self.assertEqual(m.snapshot()["alerts"][0]["since"], first)

    def test_docker_sample(self):
        m = self.make(self.outputs())
        m.sample_docker()
        d = m.snapshot()["docker"]
        self.assertTrue(d["running"])
        self.assertEqual(d["projects"][0]["project"], "budget")

    def test_docker_unavailable(self):
        for failure in (None, subprocess.TimeoutExpired("docker", 10), FileNotFoundError("docker")):
            with self.subTest(failure=repr(failure)):
                m = self.make(self.outputs(**{("docker", "ps", "--format", "{{json .}}"): failure}))
                m.sample_docker()
                self.assertEqual(m.snapshot()["docker"]["running"], False)
                self.assertEqual(m.snapshot()["docker"]["projects"], [])

    def test_missing_commands_degrade(self):
        outputs = self.outputs()
        del outputs[("memory_pressure", "-Q")]
        outputs[PS_ARGS] = None
        m = self.make(outputs)
        m.sample()
        s = m.snapshot()
        self.assertIsNone(s["memory"]["free_pct"])
        self.assertIsNone(s["top_apps"])
        self.assertFalse(s["processes_ok"])
        self.assertIsNone(m.tree_usage(400))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests, they must fail**

Run: `python3 -m unittest tests.test_monitor 2>&1 | tail -3`
Expected: `No module named 'monitor'`

- [ ] **Step 3: Implement**

`monitor.py`:

```python
"""Background sampling of Claude process trees, system memory/CPU, top apps and Docker usage (macOS)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PRESSURE_LEVELS = {"1": "normal", "2": "warn", "4": "critical"}
MEM_UNITS = {"b": 1 / 1024 / 1024, "kib": 1 / 1024, "kb": 1 / 1000, "mib": 1, "mb": 1,
             "gib": 1024, "gb": 1000, "tib": 1024 * 1024}
SWAP_UNITS = {"K": 1 / 1024, "M": 1, "G": 1024}
PS_ARGS = ["ps", "-axo", "pid=,ppid=,rss=,%cpu=,tty=,comm="]


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    rss_kb: int
    cpu: float
    tty: str
    comm: str


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ps(out: str) -> list[Proc]:
    procs = []
    for line in out.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        try:
            procs.append(Proc(int(parts[0]), int(parts[1]), int(parts[2]), float(parts[3].replace(",", ".")),
                              parts[4], parts[5].strip()))
        except ValueError:
            continue
    return procs


def app_name(comm: str) -> str:
    i = comm.find(".app/")
    if i != -1:
        return os.path.basename(comm[:i])
    if comm.endswith(".app"):
        return os.path.basename(comm[:-4])
    return os.path.basename(comm) or comm


def top_apps(procs: list[Proc], n: int = 5) -> list[dict]:
    rss: dict[str, int] = defaultdict(int)
    cpu: dict[str, float] = defaultdict(float)
    for p in procs:
        name = app_name(p.comm)
        rss[name] += p.rss_kb
        cpu[name] += p.cpu
    names = sorted(rss, key=lambda k: rss[k], reverse=True)[:n]
    return [{"name": k, "rss_mb": round(rss[k] / 1024), "cpu": round(cpu[k], 1)} for k in names]


def subtree(procs: list[Proc], root: int) -> list[Proc]:
    by_pid = {p.pid: p for p in procs}
    if root not in by_pid:
        return []
    children: dict[int, list[Proc]] = defaultdict(list)
    for p in procs:
        if p.pid != p.ppid:
            children[p.ppid].append(p)
    out, stack, seen = [], [by_pid[root]], set()
    while stack:
        p = stack.pop()
        if p.pid in seen:
            continue
        seen.add(p.pid)
        out.append(p)
        stack.extend(children.get(p.pid, []))
    return out


def tree_usage(procs: list[Proc], root: int) -> dict | None:
    tree = subtree(procs, root)
    if not tree:
        return None
    top = sorted(tree, key=lambda p: p.rss_kb, reverse=True)[:5]
    return {
        "rss_mb": round(sum(p.rss_kb for p in tree) / 1024),
        "cpu": round(sum(p.cpu for p in tree), 1),
        "processes": len(tree),
        "top": [{"pid": p.pid, "name": app_name(p.comm), "rss_mb": round(p.rss_kb / 1024), "cpu": round(p.cpu, 1)}
                for p in top],
        "tty": tree[0].tty if tree[0].tty not in ("??", "-", "") else None,
    }


def parse_pressure_level(out: str) -> str | None:
    return PRESSURE_LEVELS.get(out.strip())


def parse_memory_free(out: str) -> int | None:
    m = re.search(r"free percentage:\s*(\d+)%", out)
    return int(m.group(1)) if m else None


def parse_swap(out: str) -> tuple[int | None, int | None]:
    def value(name: str) -> int | None:
        m = re.search(name + r"\s*=\s*([\d.,]+)([KMG])", out)
        return round(float(m.group(1).replace(",", ".")) * SWAP_UNITS[m.group(2)]) if m else None
    return value("used"), value("total")


def parse_mem_mb(text: str) -> float | None:
    m = re.match(r"\s*([\d.]+)\s*([A-Za-z]+)", text or "")
    if not m or m.group(2).lower() not in MEM_UNITS:
        return None
    return float(m.group(1)) * MEM_UNITS[m.group(2).lower()]


def _labels(text: str) -> dict[str, str]:
    return dict(item.split("=", 1) for item in (text or "").split(",") if "=" in item)


def parse_docker_ps(out: str) -> list[dict]:
    result = []
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        labels = _labels(d.get("Labels", ""))
        result.append({"name": d.get("Names"), "project": labels.get("com.docker.compose.project"),
                       "working_dir": labels.get("com.docker.compose.project.working_dir")})
    return result


def parse_docker_stats(out: str) -> dict[str, tuple[float, float]]:
    result = {}
    for line in out.splitlines():
        try:
            d = json.loads(line)
            mem = parse_mem_mb(str(d.get("MemUsage", "")).split("/")[0])
            cpu = float(str(d.get("CPUPerc", "0")).rstrip("%") or 0)
        except (ValueError, AttributeError):
            continue
        result[d.get("Name")] = (mem or 0.0, cpu)
    return result


def docker_projects(containers: list[dict], stats: dict[str, tuple[float, float]]) -> list[dict]:
    groups: dict[str, dict] = {}
    for c in containers:
        key = c["project"] or c["name"]
        g = groups.setdefault(key, {"project": key, "working_dir": c["working_dir"], "rss": 0.0, "cpu": 0.0,
                                    "containers": 0})
        mem, cpu = stats.get(c["name"], (0.0, 0.0))
        g["rss"] += mem
        g["cpu"] += cpu
        g["containers"] += 1
    projects = [{"project": g["project"], "working_dir": g["working_dir"], "rss_mb": round(g["rss"]),
                 "cpu": round(g["cpu"], 1), "containers": g["containers"]} for g in groups.values()]
    return sorted(projects, key=lambda p: p["rss_mb"], reverse=True)


def _inside(path: str, base: str) -> bool:
    base = base.rstrip("/")
    return path == base or path.startswith(base + "/")


def stack_for(row: dict, projects: list[dict], mapping: dict[str, str], dev_root) -> str | None:
    repo, cwd = row.get("repo") or row.get("cwd"), row.get("cwd")
    for p in projects:
        wd = p.get("working_dir")
        if wd and repo and (_inside(wd, repo) or _inside(repo, wd)):
            return p["project"]
        target = mapping.get(p["project"])
        if target and cwd:
            absolute = target if target.startswith("/") else str(Path(dev_root) / target)
            if _inside(cwd, absolute):
                return p["project"]
    return None


class _Sustain:
    def __init__(self) -> None:
        self.since: float | None = None

    def update(self, over: bool, now: float) -> float:
        if not over:
            self.since = None
            return 0.0
        if self.since is None:
            self.since = now
        return now - self.since


class Monitor:
    def __init__(self, config: dict, runner=subprocess.run, clock=time.time, loadavg=os.getloadavg, ncpu=None):
        self.config = config
        self._run, self._clock, self._loadavg = runner, clock, loadavg
        self._ncpu = ncpu or os.cpu_count() or 1
        self._env = dict(os.environ, LC_ALL="C")
        self._lock = threading.Lock()
        self._procs: list[Proc] = []
        self._system: dict | None = None
        self._docker = {"running": False, "sampled_at": None, "projects": []}
        self._cpu = _Sustain()
        self._alert_since: dict[tuple[str, str], float] = {}
        self._stop = threading.Event()

    def _cmd(self, args: list[str], timeout: float = 10) -> str | None:
        try:
            r = self._run(args, capture_output=True, text=True, timeout=timeout, env=self._env)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout if r.returncode == 0 else None

    def sample(self) -> None:
        now = self._clock()
        out = self._cmd(PS_ARGS)
        procs = parse_ps(out) if out else None
        level = self._cmd(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"])
        free = self._cmd(["memory_pressure", "-Q"])
        swap = self._cmd(["sysctl", "-n", "vm.swapusage"])
        used, total = parse_swap(swap) if swap else (None, None)
        memory = {"pressure": parse_pressure_level(level) if level else None,
                  "free_pct": parse_memory_free(free) if free else None,
                  "swap_used_mb": used, "swap_total_mb": total}
        load1 = self._loadavg()[0]
        over_for = self._cpu.update(load1 > self._ncpu * self.config["cpu_load_factor"], now)
        alerts = []
        if memory["pressure"] in ("warn", "critical"):
            free_text = f", {memory['free_pct']}% free" if memory["free_pct"] is not None else ""
            alerts.append({"kind": "memory", "level": memory["pressure"],
                           "message": f"Memory pressure is {memory['pressure']}{free_text}."})
        if over_for >= self.config["cpu_sustain_s"]:
            alerts.append({"kind": "cpu", "level": "warn",
                           "message": f"CPU overloaded: load {load1:.1f} on {self._ncpu} cores for {int(over_for // 60)} min."})
        active = {(a["kind"], a["level"]) for a in alerts}
        self._alert_since = {k: v for k, v in self._alert_since.items() if k in active}
        for a in alerts:
            a["since"] = iso(self._alert_since.setdefault((a["kind"], a["level"]), now))
        system = {"sampled_at": iso(now), "memory": memory, "cpu": {"load1": round(load1, 2), "ncpu": self._ncpu},
                  "top_apps": top_apps(procs) if procs is not None else None, "processes_ok": procs is not None,
                  "alerts": alerts}
        with self._lock:
            self._procs = procs or []
            self._system = system

    def sample_docker(self) -> None:
        now = self._clock()
        ps_out = self._cmd(["docker", "ps", "--format", "{{json .}}"])
        if ps_out is None:
            docker = {"running": False, "sampled_at": iso(now), "projects": []}
        else:
            stats_out = self._cmd(["docker", "stats", "--no-stream", "--format", "{{json .}}"]) or ""
            docker = {"running": True, "sampled_at": iso(now),
                      "projects": docker_projects(parse_docker_ps(ps_out), parse_docker_stats(stats_out))}
        with self._lock:
            self._docker = docker

    def snapshot(self) -> dict:
        with self._lock:
            system = dict(self._system) if self._system else {
                "sampled_at": None, "memory": None, "cpu": None, "top_apps": None, "processes_ok": False, "alerts": []}
            system["alerts"] = [dict(a) for a in system["alerts"]]
            system["docker"] = json.loads(json.dumps(self._docker))
        return system

    def tree_usage(self, pid: int) -> dict | None:
        with self._lock:
            procs = self._procs
        return tree_usage(procs, pid) if procs else None

    def start(self, on_sample=None) -> None:
        def loop(fn, interval, after=None):
            while not self._stop.is_set():
                try:
                    fn()
                    if after is not None:
                        after()
                except Exception as e:  # the background loop must survive anything
                    print(f"monitor: {e!r}", file=sys.stderr, flush=True)
                self._stop.wait(max(1, interval))
        threading.Thread(target=loop, args=(self.sample, self.config["process_sample_interval_s"], on_sample),
                         name="monitor", daemon=True).start()
        threading.Thread(target=loop, args=(self.sample_docker, self.config["docker_sample_interval_s"]),
                         name="docker", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
```

- [ ] **Step 4: Run the tests, they must pass**

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 5: Smoke test on the real machine (read-only)**

Run:
```bash
python3 -c "
import monitor, config, json
m = monitor.Monitor(config.load_config('/nonexistent'))
m.sample(); m.sample_docker()
s = m.snapshot(); print(json.dumps({k: s[k] for k in ('memory','cpu','alerts')}, indent=1)); print(s['top_apps'][:3]); print(s['docker'])"
```
Expected: the real pressure level, swap, load, top apps (e.g. Google Chrome first) and Docker projects, all matching what `sysctl`, `ps` and `docker ps` show.

- [ ] **Step 6: Commit**

```bash
git add monitor.py tests/test_monitor.py
git commit -m "Sample process trees, system memory/CPU, top apps and Docker usage

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `notifier.py`

**Files:**
- Create: `notifier.py`
- Test: `tests/test_notifier.py`

**Interfaces:**
- Consumes: rows with `attention` (Task 4), `system.alerts` (Task 5) and `config`.
- Produces:
  - `NOTIFY_SCRIPT`;
  - `send_notification(title, message, runner=subprocess.run) -> bool`;
  - `Notifier(config, send=send_notification, clock=time.time)` with `.check_queue(rows)` and `.check_alerts(alerts)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_notifier.py`:

```python
import subprocess
import unittest

import config
import notifier

SINCE = "2026-10-08T10:00:00Z"
T_SINCE = 1791453600.0


def row(sid="s1", state="permission", group="blocking", since=SINCE, in_queue=True, source="hook"):
    return {"session_id": sid, "jira_key": "PROJ-1", "topic": "t", "display_dir": "acme/shop",
            "attention": {"state": state, "group": group, "since": since, "in_queue": in_queue, "source": source}}


class NotifierTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load_config("/nonexistent")
        self.sent = []
        self.now = T_SINCE
        self.n = notifier.Notifier(self.cfg, send=lambda t, m: self.sent.append((t, m)), clock=lambda: self.now)
        self.n.check_queue([])  # the first check primes the notifier (start-up rule)

    def test_blocking_after_threshold_once_per_episode(self):
        self.now = T_SINCE + 30
        self.n.check_queue([row()])
        self.assertEqual(self.sent, [])
        self.now = T_SINCE + 70
        self.n.check_queue([row()])
        self.n.check_queue([row()])
        self.assertEqual(self.sent, [("Claude is waiting", "PROJ-1 · acme/shop · permission 1 min")])
        self.now = T_SINCE + 400
        self.n.check_queue([row(since="2026-10-08T10:05:00Z")])
        self.assertEqual(len(self.sent), 2)

    def test_waiting_uses_longer_threshold(self):
        self.now = T_SINCE + 300
        self.n.check_queue([row(state="waiting", group="done")])
        self.assertEqual(self.sent, [])
        self.now = T_SINCE + 601
        self.n.check_queue([row(state="waiting", group="done")])
        self.assertEqual(len(self.sent), 1)

    def test_seen_or_not_queued_never_notifies(self):
        self.now = T_SINCE + 3600
        self.n.check_queue([row(in_queue=False, group=None), row(sid="s2", since=None)])
        self.assertEqual(self.sent, [])

    def test_estimates_never_notify(self):
        self.now = T_SINCE + 3600
        self.n.check_queue([row(source="estimate")])
        self.assertEqual(self.sent, [])

    def test_no_burst_on_first_check(self):
        fresh = notifier.Notifier(self.cfg, send=lambda t, m: self.sent.append((t, m)), clock=lambda: self.now)
        self.now = T_SINCE + 3600
        fresh.check_queue([row(sid="old")])
        self.assertEqual(self.sent, [])
        fresh.check_queue([row(sid="old")])
        self.assertEqual(self.sent, [])
        fresh.check_queue([row(sid="new", since="2026-10-08T10:59:30Z")])
        self.assertEqual(self.sent, [])
        self.now += 120
        fresh.check_queue([row(sid="new", since="2026-10-08T10:59:30Z")])
        self.assertEqual(len(self.sent), 1)

    def test_disabled_sends_nothing(self):
        self.cfg["notifications_enabled"] = False
        self.now = T_SINCE + 3600
        self.n.check_queue([row()])
        self.n.check_alerts([{"kind": "memory", "level": "warn", "message": "m"}])
        self.assertEqual(self.sent, [])

    def test_resource_alert_rise_reminder_clear(self):
        warn = [{"kind": "memory", "level": "warn", "message": "Memory pressure is warn."}]
        crit = [{"kind": "memory", "level": "critical", "message": "Memory pressure is critical."}]
        self.n.check_alerts(warn)
        self.assertEqual(self.sent, [("Mac resources: warn", "Memory pressure is warn.")])
        self.now += 1800
        self.n.check_alerts(warn)
        self.assertEqual(len(self.sent), 1)
        self.now += 1801
        self.n.check_alerts(warn)
        self.assertEqual(len(self.sent), 2)
        self.n.check_alerts(crit)
        self.assertEqual(self.sent[-1], ("Mac resources: critical", "Memory pressure is critical."))
        self.n.check_alerts([])
        self.n.check_alerts(warn)
        self.assertEqual(len(self.sent), 4)

    def test_send_notification_argv_and_failure(self):
        calls = []

        def ok(args, **kw):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")

        self.assertTrue(notifier.send_notification("Title", "Message", runner=ok))
        self.assertEqual(calls[0], ["osascript", "-e", notifier.NOTIFY_SCRIPT, "Message", "Title"])
        failing = lambda args, **kw: subprocess.CompletedProcess(args, 1, "", "nope")
        self.assertFalse(notifier.send_notification("T", "M", runner=failing))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests, they must fail**

Run: `python3 -m unittest tests.test_notifier 2>&1 | tail -3`
Expected: `No module named 'notifier'`

- [ ] **Step 3: Implement**

`notifier.py`:

```python
"""macOS notifications for the attention queue and resource alerts, rate limited."""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime, timezone

NOTIFY_SCRIPT = """on run argv
  display notification (item 1 of argv) with title (item 2 of argv)
end run"""
LEVEL_RANK = {"warn": 1, "critical": 2}


def send_notification(title: str, message: str, runner=subprocess.run) -> bool:
    try:
        r = runner(["osascript", "-e", NOTIFY_SCRIPT, message, title], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"notifier: {e!r}", file=sys.stderr, flush=True)
        return False
    if r.returncode != 0:
        print(f"notifier: osascript failed: {(r.stderr or '').strip()}", file=sys.stderr, flush=True)
        return False
    return True


def _parse_iso(ts: str) -> float | None:
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


class Notifier:
    def __init__(self, config: dict, send=send_notification, clock=time.time) -> None:
        self.config, self._send, self._clock = config, send, clock
        self._episodes: set[tuple[str, str]] = set()
        self._primed = False
        self._levels: dict[str, int] = {}
        self._last_sent: dict[str, float] = {}

    def _deliver(self, title: str, message: str) -> None:
        if self.config["notifications_enabled"]:
            self._send(title, message)

    def check_queue(self, rows: list[dict]) -> None:
        now = self._clock()
        active: set[tuple[str, str]] = set()
        for r in rows:
            a = r.get("attention") or {}
            since = _parse_iso(a.get("since"))
            if not a.get("in_queue") or since is None or a.get("source") == "estimate":
                continue
            key = (r["session_id"], a["since"])
            active.add(key)
            limit = (self.config["permission_notify_after_s"] if a.get("group") == "blocking"
                     else self.config["waiting_notify_after_s"])
            age = now - since
            if not self._primed and age >= limit:
                self._episodes.add(key)  # start-up: already overdue → treat as notified, no burst
            if age >= limit and key not in self._episodes:
                self._episodes.add(key)
                label = r.get("jira_key") or r.get("topic") or r["session_id"][:8]
                self._deliver("Claude is waiting", f"{label} · {r.get('display_dir')} · {a['state']} {int(age // 60)} min")
        self._episodes &= active
        self._primed = True

    def check_alerts(self, alerts: list[dict]) -> None:
        now = self._clock()
        by_kind = {a["kind"]: a for a in alerts}
        for kind in set(self._levels) | set(by_kind):
            alert = by_kind.get(kind)
            current = LEVEL_RANK.get(alert["level"], 0) if alert else 0
            previous = self._levels.get(kind, 0)
            due = current and current == previous and now - self._last_sent.get(kind, now) >= self.config["resource_reminder_after_s"]
            if current > previous or due:
                self._deliver(f"Mac resources: {alert['level']}", alert["message"])
                self._last_sent[kind] = now
            if not current:
                self._last_sent.pop(kind, None)
            self._levels[kind] = current
```

- [ ] **Step 4: Run the tests, they must pass**

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3`
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add notifier.py tests/test_notifier.py
git commit -m "Add rate-limited macOS notifications for the queue and resource alerts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `iterm.focus_tty` and server wiring

**Files:**
- Modify: `iterm.py`, `server.py`
- Test: `tests/test_iterm.py` (add tests), `tests/test_server.py` (update setUp, add tests)

**Interfaces:**
- Consumes: Tasks 1, 4, 5, 6.
- Produces:
  - `iterm.focus_tty(tty, runner=subprocess.run) -> tuple[str, str | None]`, where the first element is `"ok"`, `"notfound"` or `"error"`.
  - `App(..., config=None, monitor=None, notifier=None, focuser=iterm.focus_tty)`.
  - `App.mark_seen(id)`, `App.focus_session(row) -> (status, payload)`, `App.tick()`, `App.start_background()`.
  - Rows gain `attention`, `resources` and `tty`. The payload gains `queue` and `system`.
  - Endpoints `GET /api/system`, `POST /api/seen/<id>` and `POST /api/focus/<id>`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_iterm.py`:

```python
class FocusTest(unittest.TestCase):
    def run_with(self, stdout="", returncode=0, stderr=""):
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, returncode, stdout, stderr)
        return calls, runner

    def test_ok_and_argv(self):
        calls, runner = self.run_with("ok\n")
        self.assertEqual(iterm.focus_tty("ttys003", runner=runner), ("ok", None))
        self.assertEqual(calls[0], ["osascript", "-e", iterm.FOCUS_SCRIPT, "/dev/ttys003"])
        self.assertNotIn("ttys003", iterm.FOCUS_SCRIPT)

    def test_notfound_and_errors(self):
        self.assertEqual(iterm.focus_tty("ttys003", runner=self.run_with("notfound")[1]), ("notfound", None))
        status, error = iterm.focus_tty("ttys003", runner=self.run_with("", 1, "boom")[1])
        self.assertEqual((status, error), ("error", "boom"))
        status, error = iterm.focus_tty("ttys003", runner=self.run_with("", 1, "Not authorized (-1743)")[1])
        self.assertEqual(status, "error")
        self.assertIn("Automation", error)

    def test_invalid_tty_never_runs_osascript(self):
        calls, runner = self.run_with("ok")
        for tty in (None, "", "/dev/ttys003", "ttys003; rm -rf /", "console"):
            self.assertEqual(iterm.focus_tty(tty, runner=runner)[0], "error")
        self.assertEqual(calls, [])
```

In `tests/test_server.py`, add near the top (after imports):

```python
class FakeMonitor:
    def __init__(self):
        self.usage = {42: {"rss_mb": 300, "cpu": 4.0, "processes": 3, "top": [], "tty": "ttys003"}}
        self.system = {
            "sampled_at": "2026-10-08T10:00:00Z", "memory": {"pressure": "warn", "free_pct": 38,
            "swap_used_mb": 1, "swap_total_mb": 2}, "cpu": {"load1": 1.0, "ncpu": 8}, "top_apps": [],
            "processes_ok": True, "alerts": [{"kind": "memory", "level": "warn", "message": "m", "since": "x"}],
            "docker": {"running": True, "sampled_at": "x", "projects": [
                {"project": "shared-stack", "working_dir": "/elsewhere/app", "rss_mb": 900, "cpu": 1.0, "containers": 3}]},
        }
        self.started = None

    def snapshot(self):
        return json.loads(json.dumps(self.system))

    def tree_usage(self, pid):
        return dict(self.usage[pid]) if pid in self.usage else None

    def start(self, on_sample=None):
        self.started = on_sample


class FakeNotifier:
    def __init__(self):
        self.rows, self.alerts = None, None

    def check_queue(self, rows):
        self.rows = rows

    def check_alerts(self, alerts):
        self.alerts = alerts
```

In `ServerTest.setUp`, replace the `self.app = server.App(...)` call with:

```python
        self.focus_result = ("ok", None)
        self.focused = []

        def focuser(tty):
            self.focused.append(tty)
            return self.focus_result

        cfg = config.load_config(Path(self.data.name) / "config.json")
        cfg["docker_project_dirs"] = {"shared-stack": "acme/shop"}
        self.monitor = FakeMonitor()
        self.notifier = FakeNotifier()
        self.app = server.App(
            self.fake.root, self.data.name, static_dir=self.static.name, opener=opener,
            live_fn=lambda d: {sid(1): {"status": "idle", "pid": 42, "name": "x", "updated_at": 1,
                                        "status_updated_at": 1790000000000}},
            dev_root=Path("/w"), is_missing=lambda c: self.missing,
            config=cfg, monitor=self.monitor, notifier=self.notifier, focuser=focuser,
        )
```

Add `import config` to the imports. In the module, `CWD` must be under `/w/acme/shop` for the mapping to apply. It already is after the anonymization: `CWD = "/w/acme/shop"`.

Add these tests to `ServerTest`:

```python
    def test_payload_has_attention_resources_queue_system(self):
        payload = self.request("GET", "/api/sessions")[1]
        [row] = payload["rows"]
        self.assertEqual(row["attention"]["state"], "waiting")
        self.assertEqual(row["attention"]["source"], "estimate")
        self.assertEqual(payload["queue"], [sid(1)])
        self.assertEqual(row["resources"]["rss_mb"], 300)
        self.assertEqual(row["resources"]["stack"], "shared-stack")
        self.assertNotIn("tty", row["resources"])
        self.assertEqual(row["tty"], "ttys003")
        self.assertEqual(payload["system"]["memory"]["pressure"], "warn")

    def test_hook_state_overrides_estimate(self):
        agents_dir = Path(self.data.name) / "agents"
        agents_dir.mkdir()
        (agents_dir / f"{sid(1)}.json").write_text(json.dumps(
            {"state": "permission", "since": "2026-10-08T10:01:00Z", "note": "Bash", "tty": "ttys009"}))
        row = self.rows()[sid(1)]
        self.assertEqual((row["attention"]["state"], row["attention"]["source"], row["tty"]),
                         ("permission", "hook", "ttys009"))

    def test_seen_endpoint(self):
        status, body = self.request("POST", f"/api/seen/{sid(1)}", {})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        payload = self.request("GET", "/api/sessions")[1]
        self.assertEqual(payload["queue"], [])
        self.assertFalse(payload["rows"][0]["attention"]["in_queue"])
        self.assertEqual(self.request("POST", f"/api/seen/{sid(99)}", {})[0], 404)
        self.assertEqual(self.request("POST", f"/api/seen/{sid(1)}", {}, {"Origin": "http://evil.example"})[0], 403)

    def test_focus_endpoint(self):
        self.assertEqual(self.request("POST", f"/api/focus/{sid(1)}", {}), (200, {"ok": True}))
        self.assertEqual(self.focused, ["ttys003"])
        self.focus_result = ("notfound", None)
        self.assertEqual(self.request("POST", f"/api/focus/{sid(1)}", {})[0], 404)
        self.focus_result = ("error", "boom")
        self.assertEqual(self.request("POST", f"/api/focus/{sid(1)}", {}), (502, {"ok": False, "error": "boom"}))
        self.app.live_fn = lambda d: {}
        self.assertEqual(self.request("POST", f"/api/focus/{sid(1)}", {})[0], 409)

    def test_system_endpoint(self):
        self.assertEqual(self.request("GET", "/api/system"), (200, self.monitor.system))

    def test_tick_feeds_notifier(self):
        self.app.tick()
        self.assertEqual([r["session_id"] for r in self.notifier.rows], [sid(1)])
        self.assertEqual(self.notifier.alerts, self.monitor.system["alerts"])

    def test_start_background_registers_tick(self):
        self.app.start_background()
        self.assertEqual(self.monitor.started, self.app.tick)
```

- [ ] **Step 2: Run the tests, they must fail**

Run: `python3 -m unittest tests.test_iterm tests.test_server 2>&1 | tail -3`
Expected: errors, e.g. `AttributeError: module 'iterm' has no attribute 'FOCUS_SCRIPT'` and `TypeError: App.__init__() got an unexpected keyword argument 'config'`.

- [ ] **Step 3: Implement `iterm.py`**

In `iterm.py`, add `import re`, then extract the error mapping and add `focus_tty`:

```python
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
```

In `open_in_iterm`, replace the body of `if completed.returncode != 0:` with `return False, _osascript_error(completed)`.

- [ ] **Step 4: Implement `server.py`**

Imports: add `import agents`, `import config as config_mod`, `import monitor as monitor_mod`, `import notifier as notifier_mod`.

Change `POST_PATH_RE` to `re.compile(r"/api/(notes|open|seen|focus)/([^/?#]+)")`.

Extend `App.__init__` with the new keyword arguments and their state:

```python
    def __init__(self, claude_dir, data_dir, *, static_dir=APP_DIR / "static", opener=iterm.open_in_iterm,
                 live_fn=live.get_live, dev_root=sessions.DEV_ROOT, is_missing=sessions.cwd_missing,
                 config=None, monitor=None, notifier=None, focuser=iterm.focus_tty) -> None:
        # ... existing assignments stay as they are ...
        data_dir = Path(data_dir)
        self.config = config if config is not None else config_mod.load_config(data_dir / "config.json")
        self.agents_dir = data_dir / "agents"
        self.seen = agents.SeenStore(data_dir / "seen.json")
        self.monitor = monitor if monitor is not None else monitor_mod.Monitor(self.config)
        self.notifier = notifier if notifier is not None else notifier_mod.Notifier(self.config)
        self.focuser = focuser
```

In `snapshot()`, after the loop that sets `row["live"]` and `row["note"]`, add the attention/resources step and extend the returned payload:

```python
        hook_states = agents.read_hook_states(self.agents_dir)
        seen = self.seen.all()
        system = self.monitor.snapshot()
        projects = (system.get("docker") or {}).get("projects") or []
        for row in rows:
            live_entry = row["live"]
            hook = hook_states.get(row["session_id"])
            row["attention"] = agents.attention_for(hook, live_entry, seen.get(row["session_id"]))
            usage = self.monitor.tree_usage(live_entry["pid"]) if live_entry else None
            row["tty"] = ((hook or {}).get("tty") or (usage or {}).get("tty")) if live_entry else None
            if usage is not None:
                usage = {k: v for k, v in usage.items() if k != "tty"}
                usage["stack"] = monitor_mod.stack_for(row, projects, self.config["docker_project_dirs"], self.dev_root)
            row["resources"] = usage
        with self._lock:
            self._rows = {r["session_id"]: r for r in rows}
        return {"generated_at": now_iso(), "jira_hosts": hosts, "rows": rows,
                "queue": agents.build_queue(rows), "system": system}
```

The existing `with self._lock: self._rows = …` and `return …` lines are replaced by these final lines.

Add methods to `App`:

```python
    def mark_seen(self, session_id: str) -> str:
        return self.seen.mark(session_id, now_iso())

    def focus_session(self, row: dict) -> tuple[int, dict]:
        if not row.get("live"):
            return 409, {"ok": False, "error": "Session is not running."}
        tty = row.get("tty")
        if not tty:
            return 409, {"ok": False, "error": "Session has no known terminal."}
        result, error = self.focuser(tty)
        if result == "ok":
            return 200, {"ok": True}
        if result == "notfound":
            return 404, {"ok": False, "error": f"No iTerm2 tab found for {tty}."}
        return 502, {"ok": False, "error": error or "osascript failed"}

    def tick(self) -> None:
        payload = self.snapshot()
        self.notifier.check_queue(payload["rows"])
        self.notifier.check_alerts(payload["system"].get("alerts") or [])

    def start_background(self) -> None:
        self.monitor.start(on_sample=self.tick)
```

`focus_session` must use the current live state, so the handler refreshes the row first. In `_handle_post`, after `row = self.app.find_row(session_id)`:

```python
        if action == "seen":
            return self._json({"ok": True, "seen_at": self.app.mark_seen(session_id)})
        if action == "focus":
            self.app.snapshot()
            status, payload = self.app.focus_session(self.app.find_row(session_id) or row)
            return self._json(payload, status)
```

These lines go before the existing `if action == "notes":` branch.

In `_handle_get`, add before the 404:

```python
        if path == "/api/system":
            return self._json(self.app.monitor.snapshot())
```

In `main()`, replace the warmup thread line with `app.start_background()`. The monitor's first tick runs `snapshot()`, which warms the cache.

- [ ] **Step 5: Run the tests, they must pass**

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3`
Expected: `OK`. The existing server tests still pass, because `FakeMonitor` and `FakeNotifier` are injected in `setUp`.

- [ ] **Step 6: Commit**

```bash
git add iterm.py server.py tests/test_iterm.py tests/test_server.py
git commit -m "Serve attention, resources and system state; add seen and focus endpoints

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: UI

**Files:**
- Modify: `static/filter.js`, `static/app.js`, `static/index.html`
- Test: `tests/js/test_filter.js` (add tests)

**Interfaces:**
- Consumes: payload fields from Task 7 (`queue`, `system`, row `attention`/`resources`/`tty`), plus `POST /api/seen/<id>` and `POST /api/focus/<id>`.
- Produces: `DashFilter.formatMB(mb)`, `DashFilter.minutesSince(ts, now?)`, `DashFilter.splitQueue(rows, queue)` and `DashFilter.worstLevel(alerts)`.

- [ ] **Step 1: Write the failing JS tests**

Add to the `tests` object in `tests/js/test_filter.js`:

```js
  "formatMB"() {
    assert.strictEqual(F.formatMB(312), "312 MB");
    assert.strictEqual(F.formatMB(4403), "4.3 GB");
    assert.strictEqual(F.formatMB(null), "");
  },
  "minutesSince"() {
    const now = Date.parse("2026-10-07T12:00:00Z");
    assert.strictEqual(F.minutesSince("2026-10-07T11:48:00Z", now), 12);
    assert.strictEqual(F.minutesSince("2026-10-07T12:30:00Z", now), 0);
    assert.strictEqual(F.minutesSince("nonsense", now), null);
  },
  "splitQueue keeps server order and splits groups"() {
    const rows = [
      row({ session_id: "a", attention: { group: "done" } }),
      row({ session_id: "b", attention: { group: "blocking" } }),
      row({ session_id: "c", attention: { group: "done" } }),
    ];
    const q = F.splitQueue(rows, ["b", "c", "a", "missing"]);
    assert.deepStrictEqual(q.blocking.map((r) => r.session_id), ["b"]);
    assert.deepStrictEqual(q.done.map((r) => r.session_id), ["c", "a"]);
    assert.deepStrictEqual(F.splitQueue(rows, undefined), { blocking: [], done: [] });
  },
  "worstLevel"() {
    assert.strictEqual(F.worstLevel([]), "ok");
    assert.strictEqual(F.worstLevel(undefined), "ok");
    assert.strictEqual(F.worstLevel([{ level: "warn" }]), "warn");
    assert.strictEqual(F.worstLevel([{ level: "warn" }, { level: "critical" }]), "critical");
  },
```

- [ ] **Step 2: Run, it must fail**

Run: `node tests/js/test_filter.js 2>&1 | tail -3`
Expected: `FAIL - formatMB … F.formatMB is not a function` and similar failures.

- [ ] **Step 3: Implement `filter.js` helpers**

Add inside the factory, before `return`:

```js
  function formatMB(mb) {
    if (mb === null || mb === undefined) return "";
    return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
  }

  function minutesSince(ts, now) {
    const t = Date.parse(ts);
    if (Number.isNaN(t)) return null;
    return Math.max(0, Math.floor(((now === undefined ? Date.now() : now) - t) / 60000));
  }

  function splitQueue(rows, queue) {
    const byId = new Map(rows.map((r) => [r.session_id, r]));
    const out = { blocking: [], done: [] };
    for (const id of queue || []) {
      const r = byId.get(id);
      if (r) (r.attention && r.attention.group === "blocking" ? out.blocking : out.done).push(r);
    }
    return out;
  }

  function worstLevel(alerts) {
    const list = alerts || [];
    if (list.some((a) => a.level === "critical")) return "critical";
    return list.length ? "warn" : "ok";
  }
```

Then change the return to `return { NO_ISSUE, matches, groupRows, relTime, dirCounts, formatMB, minutesSince, splitQueue, worstLevel };`.

- [ ] **Step 4: Implement `index.html` additions**

Inside `<header>`, after `<div class="chips" id="statuses"></div>`, add:

```html
  <div id="alerts"></div>
  <div id="system" class="system"></div>
```

Between `<div id="banner" hidden></div>` and `<main id="list">`, add:

```html
<section id="queue"></section>
```

Append to the `<style>` block (before `@media`):

```css
  .system { font: 12px var(--mono); color: var(--muted); cursor: pointer; }
  .system.warn { color: var(--yellow); }
  .system.critical { color: var(--red); }
  .system-details { display: flex; gap: 24px; margin-top: 6px; cursor: auto; }
  .system-details table { border-collapse: collapse; }
  .system-details th, .system-details td { padding: 1px 10px 1px 0; text-align: left; font-weight: normal; }
  .system-details th { color: var(--faint); }
  .alert { padding: 4px 10px; border-radius: 5px; font-size: 12px; }
  .alert.warn { background: #3a3110; color: var(--yellow); }
  .alert.critical { background: #3b1d1d; color: var(--red); }
  #queue { padding: 8px 16px 0; }
  #queue h2 { font: 600 13px var(--sans); color: var(--text); margin: 8px 0 4px; }
  #queue h3 { font-size: 12px; color: var(--faint); margin: 8px 0 4px; font-weight: 600; }
  .queue-empty { color: var(--faint); font-size: 12px; padding: 4px 0; }
  .queue-item { display: grid; grid-template-columns: 22px minmax(0, 1fr) auto; gap: 4px 10px; padding: 6px 10px;
                border: 1px solid var(--border); border-radius: 6px; background: var(--row); margin-bottom: 4px; }
  .queue-item.blocking { border-color: #6b4a1a; }
  .qicon { font-size: 14px; }
  .dot.working { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .dot.waiting { background: var(--accent); }
  .dot.permission { background: #fb923c; box-shadow: 0 0 6px #fb923c; }
  .dot.failed { background: var(--red); }
  .dot.estimate { background: transparent !important; box-shadow: none !important; border: 2px solid var(--muted); }
  .res { font: 11px var(--mono); color: var(--faint); margin-top: 2px; }
  .badge { display: inline-block; margin-left: 4px; padding: 0 5px; border: 1px solid var(--border); border-radius: 4px; }
```

- [ ] **Step 5: Implement `app.js` changes**

Add constants after `WARN_LABELS`:

```js
  const STATE_ICONS = { permission: "🔐", question: "❓", failed: "⚠", waiting: "✅" };
  const STATE_LABELS = { working: "working", waiting: "waiting", permission: "needs permission",
    question: "has a question", failed: "failed", idle: "idle", ended: "ended" };
  const DOT_CLASS = { working: "working", waiting: "waiting", permission: "permission", question: "permission",
    failed: "failed", idle: "idle" };
  let systemOpen = false;
```

Add functions, e.g. after `saveNote`:

```js
  async function focusSession(r) {
    const res = await post(`/api/focus/${encodeURIComponent(r.session_id)}`, {});
    if (res.ok) toast("Switched to the session's iTerm2 tab.");
    else toast(`Could not switch: ${res.error} Use Copy instead.`, true);
  }

  async function markSeen(r) {
    const res = await post(`/api/seen/${encodeURIComponent(r.session_id)}`, {});
    if (!res.ok) {
      toast(`Could not mark as seen: ${res.error}`, true);
      return;
    }
    const current = data.rows.find((x) => x.session_id === r.session_id);
    for (const target of current && current !== r ? [r, current] : [r]) {
      target.attention = Object.assign({}, target.attention, { in_queue: false, group: null });
    }
    data.queue = (data.queue || []).filter((id) => id !== r.session_id);
    render();
  }

  function renderSystemDetails(s) {
    const apps = el("table", {}, el("tr", {}, el("th", {}, "App"), el("th", {}, "RAM"), el("th", {}, "CPU")),
      (s.top_apps || []).map((a) => el("tr", {}, el("td", {}, a.name), el("td", {}, F.formatMB(a.rss_mb)), el("td", {}, `${a.cpu}%`))));
    const projects = el("table", {},
      el("tr", {}, el("th", {}, "Docker project"), el("th", {}, "RAM"), el("th", {}, "CPU"), el("th", {}, "Containers")),
      ((s.docker && s.docker.projects) || []).map((p) => el("tr", { title: p.working_dir || "" },
        el("td", {}, p.project), el("td", {}, F.formatMB(p.rss_mb)), el("td", {}, `${p.cpu}%`), el("td", {}, p.containers))));
    return el("div", { class: "system-details", onclick: (e) => e.stopPropagation() }, apps, projects);
  }

  function renderSystem() {
    const s = data.system;
    const box = $("#system");
    if (!s || !s.sampled_at) {
      box.className = "system";
      box.replaceChildren("System: sampling…");
      $("#alerts").replaceChildren();
      return;
    }
    const m = s.memory || {};
    const c = s.cpu || {};
    const swap = m.swap_total_mb ? ` · swap ${F.formatMB(m.swap_used_mb)}/${F.formatMB(m.swap_total_mb)}` : "";
    const parts = [
      `RAM ${m.pressure || "?"}${m.free_pct !== null && m.free_pct !== undefined ? ` · ${m.free_pct}% free` : ""}${swap}`,
      c.load1 !== undefined ? `CPU ${c.load1}/${c.ncpu}` : "CPU ?",
      s.top_apps ? `Top: ${s.top_apps.slice(0, 3).map((a) => `${a.name} ${F.formatMB(a.rss_mb)}`).join(", ")}` : "Processes unavailable",
      s.docker && s.docker.running
        ? `Docker: ${s.docker.projects.slice(0, 3).map((p) => `${p.project} ${F.formatMB(p.rss_mb)}`).join(", ") || "no containers"}`
        : "Docker: not running",
    ];
    box.className = `system ${F.worstLevel(s.alerts)}`;
    const summary = el("div", { onclick: () => { systemOpen = !systemOpen; render(); } }, parts.join("  │  "));
    box.replaceChildren(...[summary, systemOpen ? renderSystemDetails(s) : null].filter(Boolean));
    $("#alerts").replaceChildren(...(s.alerts || []).map((a) =>
      el("div", { class: `alert ${a.level}` }, `${a.level === "critical" ? "⛔" : "⚠"} ${a.message}`)));
  }

  function renderQueueItem(r) {
    const a = r.attention;
    const mins = F.minutesSince(a.since);
    return el("div", { class: `queue-item ${a.group}` },
      el("span", { class: "qicon" }, STATE_ICONS[a.state] || "•"),
      el("div", { class: "body" },
        el("div", { class: "main-line" }, r.jira_key ? jiraLink(r.jira_key, "key") : null, el("span", { class: "topic" }, r.topic)),
        el("div", { class: "sub" }, `${r.display_dir} · ${STATE_LABELS[a.state] || a.state} · waiting ${mins === null ? "?" : mins} min${a.source === "estimate" ? " (estimate)" : ""}`),
        a.note ? el("div", { class: "prompts" }, a.note) : null),
      el("div", { class: "actions" },
        el("button", { type: "button", disabled: !r.tty, title: r.tty ? `Focus the iTerm2 tab on ${r.tty}` : "Terminal unknown", onclick: () => focusSession(r) }, "⇥ Switch"),
        el("button", { type: "button", onclick: () => markSeen(r) }, "✓ Seen"),
        el("button", { type: "button", disabled: !r.resume_cmd, onclick: () => copyText(r.resume_cmd) }, "⧉ Copy")));
  }

  function renderQueue() {
    const q = F.splitQueue(data.rows, data.queue);
    const total = q.blocking.length + q.done.length;
    document.title = total ? `(${total}) Claude Sessions` : "Claude Sessions";
    const box = $("#queue");
    if (!loaded) { box.replaceChildren(); return; }
    if (!total) { box.replaceChildren(el("div", { class: "queue-empty" }, "Nobody is waiting for you")); return; }
    const parts = [el("h2", {}, `Waiting for you (${total})`)];
    if (q.blocking.length) parts.push(el("h3", {}, "Blocking"), ...q.blocking.map(renderQueueItem));
    if (q.done.length) parts.push(el("h3", {}, "Done, waiting"), ...q.done.map(renderQueueItem));
    box.replaceChildren(...parts);
  }
```

In `renderRow`, make three changes.

1. Replace the dot element:

```js
      el("div", {
        class: `dot ${r.live ? (DOT_CLASS[r.attention && r.attention.state] || "idle") : ""}${r.live && r.attention && r.attention.source === "estimate" ? " estimate" : ""}`,
        title: r.live ? `${STATE_LABELS[r.attention && r.attention.state] || "running"}${r.attention && r.attention.source === "estimate" ? " (estimate)" : ""}, pid ${r.live.pid}` : "not running",
      }),
```

2. Replace the `when` cell:

```js
      el("div", { class: "when", title: fmt(r.last_ts) }, F.relTime(r.last_ts),
        r.resources ? el("div", { class: "res", title: r.resources.top.map((p) => `${p.name} ${F.formatMB(p.rss_mb)} ${p.cpu}%`).join("\n") },
          `${F.formatMB(r.resources.rss_mb)} · ${r.resources.cpu}%`,
          r.resources.stack ? el("span", { class: "badge" }, `stack ${r.resources.stack}`) : null) : null),
```

3. In `renderActions`, replace the `▶ Open` button line:

```js
      r.live
        ? el("button", { type: "button", disabled: !r.tty, title: r.tty ? `Focus the iTerm2 tab on ${r.tty}` : "Terminal unknown", onclick: () => focusSession(r) }, "⇥ Switch")
        : el("button", { type: "button", title: "Open in a new iTerm2 tab", disabled: !r.resume_cmd, onclick: () => openSession(r) }, "▶ Open"),
```

In `render()`, call `renderSystem();` and `renderQueue();` right after `renderChips();`.

- [ ] **Step 6: Run all tests**

Run: `node --check static/app.js && node tests/js/test_filter.js | tail -1 && python3 -m unittest discover -s tests -t . 2>&1 | tail -3`
Expected: `all filter.js tests passed` and `OK`, including `test_static` (no `innerHTML`).

- [ ] **Step 7: Verify in headless Chrome against real data**

1. Start a dev server with a temporary data dir. In that dir, add a hook state file that makes the live session `<THIS_SESSION_ID>` show as `waiting` since 15 minutes ago.
2. Use the CDP check script from the base plan's verification (scratch, not committed), extended with these checks:
   - `#system` text contains `RAM` and `CPU`;
   - the queue shows `Waiting for you (≥1)`, including this session's row;
   - clicking `✓ Seen` removes it from the queue;
   - live rows show `⇥ Switch` instead of `▶ Open`;
   - `document.title` starts with `(`.
3. Do **not** click Switch in automation, because it would move the user's terminal.
4. Take a screenshot and look at it.

Expected: all checks PASS, and the screenshot shows the system bar, the queue panel and row resources.

- [ ] **Step 8: Commit**

```bash
git add static tests/js/test_filter.js
git commit -m "Add system bar, alerts, waiting-for-you queue and Switch to the UI

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: Install, uninstall, README, and real-data verification

**Files:**
- Modify: `launchd/install.sh`, `launchd/uninstall.sh`, `README.md`

**Interfaces:**
- Consumes: all modules; `settings_merge.py` CLI (Task 1).
- Produces: deployed hook registered in `~/.claude/settings.json`; running dashboard with background monitor.

- [ ] **Step 1: Update `launchd/install.sh`**

Replace the two `cp` lines with:

```bash
mkdir -p "$APP_DIR/hook"
cp "$REPO_DIR"/{server,sessions,live,notes,iterm,agents,monitor,notifier,settings_merge,config}.py "$APP_DIR/"
cp "$REPO_DIR"/hook/claude_hook.py "$APP_DIR/hook/"
cp "$REPO_DIR"/static/index.html "$REPO_DIR"/static/app.js "$REPO_DIR"/static/filter.js "$APP_DIR/static/"

HOOK_CMD="\"$PYTHON\" '$APP_DIR/hook/claude_hook.py'"
if ! "$PYTHON" "$APP_DIR/settings_merge.py" install "$HOME/.claude/settings.json" "$HOOK_CMD"; then
  echo "Could not register the Claude Code hooks in ~/.claude/settings.json (see above). Nothing was changed there." >&2
  exit 1
fi
```

- [ ] **Step 2: Update `launchd/uninstall.sh`**

Before `launchctl bootout …`, add:

```bash
if [ -f "$SUPPORT_DIR/app/settings_merge.py" ]; then
  python3 "$SUPPORT_DIR/app/settings_merge.py" uninstall "$HOME/.claude/settings.json" \
    || echo "Could not remove the Claude Code hooks from ~/.claude/settings.json." >&2
fi
```

Change the final echo to: `echo "Uninstalled. Notes, seen marks and config were kept in: $SUPPORT_DIR"`.

- [ ] **Step 3: Update `README.md`**

Add a section after "Features":

````markdown
## Waiting for you, and resources

- **Attention queue:** sessions where Claude finished a turn or is blocked on you (a permission prompt, a question, an
  error). Blocking ones are listed first, oldest first. **Switch** focuses the session's existing iTerm2 tab;
  **Seen** hides it until the next turn.
- **How it knows:** `install.sh` registers a small async hook (`hook/claude_hook.py`) for eight Claude Code events in
  `~/.claude/settings.json`. It writes a backup first and keeps all your other hooks. `uninstall.sh` removes only the
  dashboard's entries.
- **Before the hook has seen a session**, its state is estimated from Claude Code's own status and marked
  "(estimate)".
- **Resources:**
  - per session: RAM and CPU of the Claude process and everything it started;
  - for the whole Mac: memory pressure, swap, CPU load, the top apps, and Docker usage per compose project.
- **Warnings:** a banner and a macOS notification when the kernel reports memory pressure, or when the CPU stays
  overloaded.
- **Queue notifications:** a session blocked for more than 60 s, or done and waiting for more than 10 min, notifies
  once.

### Configuration

Optional `~/Library/Application Support/claude-dashboard/config.json`; missing keys use the defaults:

```json
{
  "permission_notify_after_s": 60,
  "waiting_notify_after_s": 600,
  "resource_reminder_after_s": 3600,
  "cpu_load_factor": 1.0,
  "cpu_sustain_s": 120,
  "process_sample_interval_s": 10,
  "docker_sample_interval_s": 30,
  "notifications_enabled": true,
  "docker_project_dirs": {"my-shared-stack": "acme/shop_local"}
}
```

`docker_project_dirs` attributes a compose project whose `working_dir` lies outside the session's repository (e.g.
a stack shared by several worktrees) to a directory below `~/Documents/Development`.
````

- [ ] **Step 4: Run all tests**

Run: `python3 -m unittest discover -s tests -t . 2>&1 | tail -3 && bash -n launchd/install.sh && bash -n launchd/uninstall.sh && echo scripts-ok`
Expected: `OK` and `scripts-ok`

- [ ] **Step 5: Write the user's local config (not in the repo)**

Set `LOCAL_STACK` to the name of the shared compose project, and `LOCAL_DEV_DIR` to the user's local-development directory relative to `~/Documents/Development`. Both are known from the brainstorming and deliberately not written in this public plan.

```bash
LOCAL_STACK="…" LOCAL_DEV_DIR="…" python3 - <<'EOF'
import json, os
local, stack = os.environ["LOCAL_DEV_DIR"], os.environ["LOCAL_STACK"]
assert "…" not in local + stack, "set LOCAL_STACK and LOCAL_DEV_DIR"
p = os.path.expanduser("~/Library/Application Support/claude-dashboard/config.json")
cfg = json.load(open(p)) if os.path.exists(p) else {}
cfg.setdefault("docker_project_dirs", {})[stack] = local
json.dump(cfg, open(p, "w"), indent=2)
print(open(p).read())
EOF
```

Expected: `config.json` contains the user's real mapping (from the brainstorming: the shared `shared-stack` stack ↔ the local-development directory). This file lives only in the data dir.

- [ ] **Step 6: Install and verify settings.json**

```bash
cp ~/.claude/settings.json "$TMPDIR/settings.pre-install.json"
./launchd/install.sh
diff <(python3 -m json.tool "$TMPDIR/settings.pre-install.json") <(python3 -m json.tool ~/.claude/settings.json) | grep '^[<>]' | grep -c '^<'
python3 -c "
import json, os; d = json.load(open(os.path.expanduser('~/.claude/settings.json')))
ours = sum(1 for ev, gs in d['hooks'].items() for g in gs for h in g.get('hooks', []) if 'claude-dashboard/app/hook/claude_hook.py' in h.get('command', ''))
cc = sum(1 for ev, gs in d['hooks'].items() for g in gs for h in g.get('hooks', []) if 'cc-status' in h.get('command', ''))
print('ours', ours, 'cc-status', cc)"
ls ~/.claude/settings.json.bak-claude-dashboard-*
```

Expected:
- `OK: dashboard is running…`;
- `0` removed lines in the diff;
- `ours 8`, and `cc-status` with the same count as before;
- one backup file.

- [ ] **Step 7: Real-data verification**

1. Trigger events by running a command in this session (and, per the Task 2 facts, in a new session if running sessions need a restart).
2. Check `/api/sessions`:

   ```bash
   curl -s http://127.0.0.1:7333/api/sessions | python3 -c "
   import json, sys; d = json.load(sys.stdin)
   print('queue', d['queue'][:5]); print('system', {k: d['system'][k] for k in ('memory','cpu','alerts')})
   for r in d['rows']:
       if r['live']: print(r['session_id'][:8], r['attention']['state'], r['attention']['source'], r['tty'], r['resources'] and (r['resources']['rss_mb'], r['resources']['stack']))"
   ```

   Expected:
   - live sessions with plausible states, and `source: hook` for sessions that have had an event since install;
   - ttys present;
   - RAM per session in the 100–400 MB range;
   - the system memory pressure matching `sysctl -n kern.memorystatus_vm_pressure_level`.
3. Notification check **from the LaunchAgent context**. The terminal has other permissions than launchd, so the check must run under launchd:
   ```bash
   APP="$HOME/Library/Application Support/claude-dashboard/app"
   launchctl submit -l local.claude-sessions-dashboard.notify-probe -- "$(command -v python3)" -c \
     "import sys; sys.path.insert(0, '$APP'); import notifier; notifier.send_notification('Claude Sessions', 'Test notification from the LaunchAgent context')"
   sleep 5; launchctl remove local.claude-sessions-dashboard.notify-probe
   ```
   **The user confirms** the notification appeared. If macOS asks for permission, allow it.
4. Switch check, **with the user**: in the dashboard, click **Switch** on a live session in another iTerm2 tab. Expected: that tab comes to the front. If macOS asks for Automation permission, allow it.

- [ ] **Step 8: Commit and push**

```bash
git add launchd README.md
git commit -m "Install the Claude Code hook and document the queue and resource monitor

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
git push -u origin feature/attention-queue-and-resources
```
