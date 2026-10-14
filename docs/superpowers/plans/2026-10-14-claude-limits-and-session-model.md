# Claude Usage Limits and Per-Session Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add to the Claude Sessions Dashboard:
- a usage limits block (5-hour, weekly, per-model weekly, extra usage) with a banner and macOS notifications at 80 % / 95 %;
- the model and effort of every session.

**Architecture:**
- **Status line tap:** a stdlib script, `hook/statusline_tap.py`, replaces the user's status line command. It records each session's `rate_limits` into `<data>/rate_limits/<session_id>.json` and then runs the original command unchanged. `settings_merge.py` wraps and unwraps it.
- **`limits.py`:** merges the session files with Claude Code's own usage cache in `~/.claude.json` into one window per kind. Usage within a window only goes up, so the merge is monotonic. The module also computes levels and alerts.
- **Server:** serves the merged result as `limits` and feeds its alerts to the existing notifier together with the resource alerts.
- **Model and effort:** read from the transcripts that `sessions.py` already parses.

**Tech Stack:** Python 3 stdlib, unittest, vanilla JS (+ node test), Claude Code status line command protocol.

**Spec:** `docs/superpowers/specs/2026-10-14-claude-limits-and-session-model-design.md` (extends `docs/superpowers/specs/2026-10-08-attention-queue-and-resources-design.md`)

## Global Constraints

- Python standard library only; Python ≥ 3.10; `from __future__ import annotations` in every module. The tap must also run standalone (no imports from the repo).
- Everything in the repository is English: code, comments, UI text, messages, tests, docs, commit messages. Dates in the UI use `en-GB`.
- Code comments are as short as possible, state facts only, and contain no issue IDs and no temporary notes ("for now", "later").
- The tap never prints anything of its own. Its stdout, stderr and exit code are exactly those of the user's original command, and recording errors are swallowed.
- **`~/.claude` is read-only.** The only exception is `~/.claude/settings.json`, which is changed only through `settings_merge.py`. That script writes a backup first, preserves all foreign settings and is idempotent. `~/.claude.json` is read-only for the dashboard.
- No network calls, no OAuth token, no use of `/api/oauth/usage`.
- No secrets, real employer/client names or real local paths in the repository. Fixtures are anonymized: `/Users/me/...`, `acme/shop`, UUIDs like `aaaaaaaa-0000-4000-8000-000000000000`.
- Server binds `127.0.0.1` only; the existing Host/Origin/Content-Type checks stay.
- Frontend: DOM APIs only (no `innerHTML` & co.), no external libraries.
- Tests: `python3 -m unittest discover -s tests -t . -v` from the repo root (includes `node tests/js/test_filter.js` when node is available).
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Working directory: the repository root; branch `feature/claude-limits-and-model` (already created, contains the spec).

## Review Focus

1. **An idle session redraws its status line with older limits than a working session.** The block must never go back to the lower value. Task 4 covers it with `test_idle_session_never_lowers_the_value`.
2. **`~/.claude.json` caught mid-write or rewritten by Claude Code.** The per-model and extra-usage parts must not flicker away. Task 4 covers it with `test_unparsable_claude_json_keeps_last_good` and `test_reads_files_only_when_changed`.
3. **The user's status line must behave exactly as before.** That covers its output, stderr, exit code and working directory. It must also hold for a command that ignores stdin, for input larger than a pipe buffer, and for small overhead. Task 2 covers it with `test_original_command_gets_stdin_and_passes_output_and_exit_code`, `test_command_that_ignores_stdin`, `test_broken_input_or_unwritable_data_still_runs_the_command` and `test_overhead_is_small`.
4. **Unusual `statusLine` shapes, a missing status line, or one the user replaced after install** must never be damaged or lost. Task 3 covers it with `test_invalid_status_line_aborts_unchanged`, `test_wrap_without_status_line`, `test_rewrap_after_user_replaced_status_line` and `test_unwrap_leaves_foreign_status_line`.
5. **Reset times across midnight, on another weekday and across a DST change** must read correctly. Task 4 covers it with `test_reset_text`, Task 7 with `formatReset`.

---

## File Structure

| File | Responsibility |
|---|---|
| `hook/statusline_tap.py` | standalone status line wrapper: record `rate_limits` per session, run the original command |
| `settings_merge.py` | + wrap/unwrap `statusLine`, store of the original command, new CLI arguments |
| `config.py` | + `limit_warn_pct`, `limit_critical_pct` |
| `limits.py` | parse session files and `~/.claude.json`, monotonic merge, levels, alerts, `LimitsReader` |
| `notifier.py` | alert title from the alert (`title`), fallback `Mac resources: <level>` |
| `server.py` | `App.limits`, payload `limits`, one `check_alerts` call for system + limit alerts |
| `sessions.py` | + `model`, `effort` per session, `model_label()`, row fields |
| `static/filter.js` | + `formatCountdown`, `formatReset`, `formatMoney`, model/effort in search |
| `static/app.js`, `static/index.html` | limits line and details, combined alerts, model/effort in rows and details |
| `launchd/*.sh`, `README.md` | deploy the tap and `limits.py`, pass the new arguments, document |
| `tests/…` | tests per module; `tests/fixtures/limits/` anonymized status line input and `~/.claude.json` excerpt |

---

### Task 1: Spike: capture the status line input and verify assumptions

This task produces facts and one fixture, not product code. Its findings go into the **Ledger** at the end of this plan as lines starting with `Task 1: Fact:`. Later tasks apply them as rulings when they differ from the spec.

**Files:**
- Create (repo): `tests/fixtures/limits/statusline-input.json` (one anonymized captured input)
- Create (scratch only, never committed): the capture script

**Interfaces:**
- Consumes: nothing.
- Produces: ledger facts for each item in spec §2 "To verify"; the fixture for Task 2.

- [ ] **Step 1: Prepare the capture script**

```bash
SCRATCH=$(mktemp -d); echo "$SCRATCH"
cp ~/.claude/settings.json "$SCRATCH/settings.before.json"
python3 -c 'import json,os; print(json.load(open(os.path.expanduser("~/.claude/settings.json")))["statusLine"]["command"])' > "$SCRATCH/original"
cat "$SCRATCH/original"
cat > "$SCRATCH/capture.sh" <<'EOF'
#!/bin/bash
here="$(cd "$(dirname "$0")" && pwd)"
input=$(cat)
mkdir -p "$here/captures"
stamp=$(/usr/bin/python3 -c 'import time; print(f"{time.time():.3f}")')
sid=$(printf '%s' "$input" | /usr/bin/python3 -c 'import json,sys; print(json.load(sys.stdin).get("session_id","x"))' 2>/dev/null)
printf '%s' "$input" > "$here/captures/$stamp-$sid.json"
{ ps -o pid=,ppid=,command= -p $PPID; echo "SHELL=$SHELL"; } > "$here/captures/$stamp-$sid.parent"
printf '%s' "$input" | /bin/sh -c "$(cat "$here/original")"
EOF
chmod +x "$SCRATCH/capture.sh"
```

Expected: `original` holds the user's current status line command.

- [ ] **Step 2: Point the status line at the capture script (temporarily)**

```bash
python3 - "$SCRATCH" <<'EOF'
import json, os, sys
p = os.path.realpath(os.path.expanduser("~/.claude/settings.json"))
d = json.load(open(p))
d["statusLine"]["command"] = f"bash '{sys.argv[1]}/capture.sh'"
open(p, "w").write(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
EOF
```

The status line must look exactly as before, because the capture script runs the original command.

- [ ] **Step 3: Hot reload, frequency, shell**

Work normally in this session for ~1 minute: run 3–4 harmless tool calls. Then:

```bash
ls "$SCRATCH/captures" | wc -l
ls "$SCRATCH/captures" | sed 's/^[0-9.]*-//; s/\..*//' | sort | uniq -c
cat "$(ls -t "$SCRATCH"/captures/*.parent | head -1)"
python3 -m json.tool "$(ls -t "$SCRATCH"/captures/*.json | head -1)" | head -80
```

Record in the ledger:
- `Task 1: Fact: running sessions <do|do not> pick up a changed statusLine.command`. Captures with this session's id mean they do.
- `Task 1: Fact: rate_limits <present|absent>; keys: …`. If absent in every capture, **stop and report to the user**: the status line source does not work for this account. The cache-only fallback (spec §1, variant 3) is then the user's decision.
- `Task 1: Fact: status line runs ~N times per minute while working`, from the capture timestamps.
- `Task 1: Fact: status line command runs under <parent command>`, from the `.parent` files. If the parent is not `/bin/sh` or `sh -c`, Task 2 runs the original command with that shell instead of `shell=True` (a ruling).

- [ ] **Step 4: An idle session next to a working one**

Ask the user to open a new iTerm2 tab in any directory, start `claude`, send `Reply with OK.`, and leave that session idle. Meanwhile keep working in this session for ~3 minutes. Then:

```bash
python3 - "$SCRATCH/captures" <<'EOF'
import json, os, sys
rows = []
for name in sorted(os.listdir(sys.argv[1])):
    if name.endswith(".json"):
        d = json.load(open(os.path.join(sys.argv[1], name)))
        r = d.get("rate_limits") or {}
        rows.append((name.split("-", 1)[0], d.get("session_id", "?")[:8],
                     (r.get("five_hour") or {}).get("used_percentage"), (r.get("five_hour") or {}).get("resets_at"),
                     (r.get("seven_day") or {}).get("used_percentage")))
for row in rows[-40:]:
    print(*row)
EOF
```

Record `Task 1: Fact: an idle session <does|does not> redraw (every ~N s), with <its old|current> values`.

- [ ] **Step 5: Same-window distance and cache cadence**

```bash
python3 - "$SCRATCH/captures" <<'EOF'
import json, os, sys
from datetime import datetime
cache = json.load(open(os.path.expanduser("~/.claude.json")))["cachedUsageUtilization"]
u = cache["utilization"]
last = sorted(n for n in os.listdir(sys.argv[1]) if n.endswith(".json"))[-1]
tap = json.load(open(os.path.join(sys.argv[1], last)))["rate_limits"]
for k in ("five_hour", "seven_day"):
    c = datetime.fromisoformat(u[k]["resets_at"]).timestamp()
    print(k, "status line", tap[k]["resets_at"], "cache", c, "distance", abs(tap[k]["resets_at"] - c))
print("fetchedAtMs", cache["fetchedAtMs"], datetime.fromtimestamp(cache["fetchedAtMs"] / 1000))
EOF
```

Record:
- `Task 1: Fact: status line vs cache resets_at distance: N s`. If N > 300, raise `SAME_WINDOW_S` in Task 4 to at least 2 × N (a ruling).
- `Task 1: Fact: cache fetchedAtMs at <time>`. Task 8 Step 7 records it again to measure the refresh cadence.

- [ ] **Step 6: Empty status line output**

Ask the user to run this in a new iTerm2 tab and say whether an empty line appears where the status line would be:

```bash
claude --settings '{"statusLine":{"type":"command","command":"true"}}'
```

Record `Task 1: Fact: an empty status line output <takes no|takes a> line`. If it takes a line, add this ruling: when the user has no status line, `settings_merge.wrap_statusline` leaves `statusLine` missing and returns `KEEP`, and the README says that limits then come from the cache only. Task 3's `test_wrap_without_status_line` changes accordingly.

- [ ] **Step 7: Startup overhead**

```bash
for i in 1 2 3 4 5; do /usr/bin/time -p python3 -c 'import json, os, re, subprocess, sys, tempfile, time, datetime' 2>&1 | awk '/real/ {print $2}'; done
```

Record `Task 1: Fact: python3 startup with the tap's imports ≈ N ms`. The target is < 100 ms.

- [ ] **Step 8: Restore the user's settings and write the fixture**

```bash
cp "$SCRATCH/settings.before.json" "$(python3 -c 'import os; print(os.path.realpath(os.path.expanduser("~/.claude/settings.json")))')"
diff "$SCRATCH/settings.before.json" ~/.claude/settings.json && echo restored
```

Expected: `restored`.

Create `tests/fixtures/limits/statusline-input.json` from the newest capture that has `rate_limits`. Keep its keys, but anonymize:
- `session_id` → `aaaaaaaa-0000-4000-8000-000000000000` (also inside `transcript_path`);
- every path → below `/Users/me/Documents/Development/acme/shop`;
- any name, e-mail or organization → `me` / `acme`.

If a capture is not available, use this documented shape:

```json
{
  "hook_event_name": "Status",
  "session_id": "aaaaaaaa-0000-4000-8000-000000000000",
  "transcript_path": "/Users/me/.claude/projects/-Users-me-Documents-Development-acme-shop/aaaaaaaa-0000-4000-8000-000000000000.jsonl",
  "cwd": "/Users/me/Documents/Development/acme/shop",
  "model": {"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
  "workspace": {"current_dir": "/Users/me/Documents/Development/acme/shop", "project_dir": "/Users/me/Documents/Development/acme/shop"},
  "version": "2.1.299",
  "effort": {"level": "xhigh", "enabled": true},
  "rate_limits": {
    "five_hour": {"used_percentage": 32, "resets_at": 1791996599},
    "seven_day": {"used_percentage": 27, "resets_at": 1792385999}
  }
}
```

```bash
F=tests/fixtures/limits/statusline-input.json
ORG=$(python3 -c 'import json,os; print(json.load(open(os.path.expanduser("~/.claude.json")))["oauthAccount"]["emailAddress"].split("@")[1].split(".")[0])')
grep -n -i -e "$(whoami)" -e "$ORG" "$F"; grep -n '/Users/' "$F" | grep -v '/Users/me/'; echo "check done"
```

Expected: only `check done`. Remove `$SCRATCH` afterwards.

- [ ] **Step 9: Commit**

```bash
git add tests/fixtures/limits/statusline-input.json docs/superpowers/plans/2026-10-14-claude-limits-and-session-model.md
git commit -m "Add an anonymized status line input fixture and spike findings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The status line tap

**Files:**
- Create: `hook/statusline_tap.py`
- Test: `tests/test_statusline_tap.py`

**Interfaces:**
- Consumes: `tests/fixtures/limits/statusline-input.json` (Task 1).
- Produces:
  - file format `<data>/rate_limits/<session_id>.json` = `{"captured_at": "YYYY-MM-DDTHH:MM:SSZ", "session_id": str, "rate_limits": dict}` (read by Task 4);
  - store format `<data>/statusline.json` = `{"command": str | null}` (written by Task 3);
  - `statusline_tap.record_limits(raw: bytes, ddir: str, now=time.time) -> None`;
  - `statusline_tap.original_command(ddir: str) -> str | None`;
  - `statusline_tap.main() -> int`.

- [ ] **Step 1: Write the failing tests**

`tests/test_statusline_tap.py`:

```python
import importlib.util
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAP_PATH = ROOT / "hook" / "statusline_tap.py"
INPUT = (ROOT / "tests" / "fixtures" / "limits" / "statusline-input.json").read_bytes()
SID = json.loads(INPUT)["session_id"]
T = 1791985200.0  # 2026-10-14T13:40:00Z


def load_tap():
    spec = importlib.util.spec_from_file_location("statusline_tap", TAP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordLimitsTest(unittest.TestCase):
    def setUp(self):
        self.tap = load_tap()
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.file = self.data / "rate_limits" / f"{SID}.json"

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, payload, at):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.tap.record_limits(raw, str(self.data), now=lambda: at)

    def stored(self):
        return json.loads(self.file.read_text(encoding="utf-8"))

    def test_records_rate_limits_per_session(self):
        self.record(INPUT, T)
        self.assertEqual(self.stored(), {"captured_at": "2026-10-14T13:40:00Z", "session_id": SID,
                                         "rate_limits": json.loads(INPUT)["rate_limits"]})

    def test_unchanged_values_are_rewritten_only_after_60_s(self):
        self.record(INPUT, T)
        self.record(INPUT, T + 59)
        self.assertEqual(self.stored()["captured_at"], "2026-10-14T13:40:00Z")
        self.record(INPUT, T + 61)
        self.assertEqual(self.stored()["captured_at"], "2026-10-14T13:41:01Z")

    def test_changed_values_are_written_at_once(self):
        self.record(INPUT, T)
        payload = json.loads(INPUT)
        payload["rate_limits"]["five_hour"]["used_percentage"] += 1
        self.record(payload, T + 5)
        self.assertEqual(self.stored()["captured_at"], "2026-10-14T13:40:05Z")
        self.assertEqual(self.stored()["rate_limits"], payload["rate_limits"])

    def test_nothing_recorded_without_limits_or_valid_session(self):
        payload = json.loads(INPUT)
        cases = [
            {**payload, "rate_limits": None}, {**payload, "rate_limits": {}},
            {**payload, "rate_limits": {"spend_limit": {"used_percentage": 5}}},
            {**payload, "session_id": "../../../../tmp/x"}, {**payload, "session_id": SID + "\n"},
            {**payload, "session_id": None}, [1, 2],
        ]
        for case in cases:
            with self.subTest(case=str(case)[:60]):
                self.record(case, T)
                self.assertFalse((self.data / "rate_limits").exists())


class TapScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name, "data")
        self.data.mkdir()
        self.env = {**os.environ, "CLAUDE_DASHBOARD_DATA_DIR": str(self.data)}

    def tearDown(self):
        self.tmp.cleanup()

    def set_command(self, command):
        (self.data / "statusline.json").write_text(json.dumps({"command": command}), encoding="utf-8")

    def run_tap(self, stdin=INPUT, cwd=None):
        return subprocess.run([sys.executable, str(TAP_PATH)], input=stdin, capture_output=True, env=self.env,
                              cwd=cwd, timeout=20)

    def test_original_command_gets_stdin_and_passes_output_and_exit_code(self):
        self.set_command("cat; printf ' cwd=%s' \"$(/bin/pwd -P)\"; echo oops >&2; exit 3")
        r = self.run_tap(cwd=self.tmp.name)
        self.assertEqual(r.stdout, INPUT + f" cwd={os.path.realpath(self.tmp.name)}".encode())
        self.assertEqual(r.stderr, b"oops\n")
        self.assertEqual(r.returncode, 3)
        self.assertTrue((self.data / "rate_limits" / f"{SID}.json").exists())

    def test_command_that_ignores_stdin(self):
        self.set_command("echo static line")
        r = self.run_tap(stdin=INPUT * 2000)  # far more than a pipe buffer
        self.assertEqual((r.stdout, r.stderr, r.returncode), (b"static line\n", b"", 0))

    def test_no_command_prints_nothing(self):
        path = self.data / "statusline.json"
        for store in (None, {"command": None}, {"command": "  "}, "{nope"):
            with self.subTest(store=store):
                if store is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(store if isinstance(store, str) else json.dumps(store), encoding="utf-8")
                r = self.run_tap()
                self.assertEqual((r.stdout, r.stderr, r.returncode), (b"", b"", 0))

    def test_broken_input_or_unwritable_data_still_runs_the_command(self):
        self.set_command("cat")
        (self.data / "rate_limits" / f"{SID}.json").mkdir(parents=True)  # a directory where the file should go
        for stdin in (INPUT, b"{nope", b"\xff\xfe", b""):
            with self.subTest(stdin=stdin[:10]):
                r = self.run_tap(stdin=stdin)
                self.assertEqual((r.stdout, r.stderr, r.returncode), (stdin, b"", 0))
        self.assertEqual([p.name for p in (self.data / "rate_limits").iterdir()], [f"{SID}.json"])  # no temp files left

    def test_overhead_is_small(self):
        self.set_command("true")
        times = []
        for _ in range(5):
            start = time.perf_counter()
            self.run_tap()
            times.append(time.perf_counter() - start)
        self.assertLess(statistics.median(times), 0.5)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_statusline_tap -v`
Expected: errors, because `hook/statusline_tap.py` does not exist (`FileNotFoundError`).

- [ ] **Step 3: Write the tap**

`hook/statusline_tap.py`:

```python
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
    return command if isinstance(command, str) and command.strip() else None


def main() -> int:
    raw = sys.stdin.buffer.read()
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
```

If the Task 1 ledger names a shell other than `/bin/sh` for the status line, replace `subprocess.run(command, shell=True, input=raw)` with `subprocess.run([<that shell>, "-c", command], input=raw)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_statusline_tap -v`
Expected: all 9 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add hook/statusline_tap.py tests/test_statusline_tap.py
git commit -m "Add a status line tap that records usage limits per session

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Wrap and unwrap the status line in `settings_merge.py`

**Files:**
- Modify: `settings_merge.py`
- Test: `tests/test_settings_merge.py`

**Interfaces:**
- Consumes: store format `{"command": str | null}` (Task 2).
- Produces:
  - `settings_merge.TAP_MARKER = "claude-dashboard/app/hook/statusline_tap.py"`;
  - `settings_merge.KEEP` (sentinel);
  - `settings_merge.wrap_statusline(settings: dict, tap_command: str) -> str | None | KEEP`;
  - `settings_merge.unwrap_statusline(settings: dict, original: str | None) -> None`;
  - `settings_merge.install(path: Path, command: str, tap_command: str, store: Path) -> None`;
  - `settings_merge.uninstall(path: Path, store: Path) -> None`;
  - CLI `install <settings.json> <hook-command> <tap-command> <statusline-store>`, `uninstall <settings.json> <statusline-store>` (used by Task 8).

- [ ] **Step 1: Update the existing tests to the new signatures**

In `tests/test_settings_merge.py`:

1. Below `CC = …` add:

```python
TAP = "\"/usr/local/bin/python3\" '/Users/me/Library/Application Support/claude-dashboard/app/hook/statusline_tap.py'"
USER_LINE = {"type": "command", "command": "bash ~/.claude/statusline-command.sh", "padding": 0}
```

2. In `setUp` add `self.store = Path(self.tmp.name, "statusline.json")`. Below `backups()` add:

```python
    def install(self, cmd=CMD, tap=TAP):
        sm.install(self.path, cmd, tap, self.store)

    def uninstall(self):
        sm.uninstall(self.path, self.store)

    def stored(self):
        return json.loads(self.store.read_text(encoding="utf-8"))
```

3. Replace calls in every existing test:
   - `sm.install(self.path, CMD)` → `self.install()`;
   - `sm.install(self.path, newer)` → `self.install(newer)`;
   - `sm.uninstall(self.path)` → `self.uninstall()`.
4. In `test_install_keeps_others`, change `self.assertEqual(list(data), ["model", "hooks", "theme"])` to `self.assertEqual(list(data), ["model", "hooks", "theme", "statusLine"])`.
5. In `test_non_object_hooks_aborts`, add `self.assertFalse(self.store.exists())` after the `before` comparison.
6. Replace `test_main_cli` with:

```python
    def test_main_cli(self):
        self.assertEqual(sm.main(["install", str(self.path), CMD, TAP, str(self.store)]), 0)
        self.assertEqual(self.read()["statusLine"]["command"], TAP)
        self.assertEqual(sm.main(["uninstall", str(self.path), str(self.store)]), 0)
        self.assertNotIn("statusLine", self.read())
        self.assertEqual(sm.main(["install", str(self.path), CMD]), 2)
        self.assertEqual(sm.main(["uninstall", str(self.path)]), 2)
        self.assertEqual(sm.main(["bogus"]), 2)
        self.path.write_text("{nope", encoding="utf-8")
        self.assertEqual(sm.main(["install", str(self.path), CMD, TAP, str(self.store)]), 1)
```

- [ ] **Step 2: Add the new tests**

Add to `SettingsMergeTest`:

```python
    def test_wrap_existing_status_line(self):
        self.write({"statusLine": USER_LINE, "hooks": {"Stop": [CC]}})
        self.install()
        self.assertEqual(self.read()["statusLine"], {**USER_LINE, "command": TAP})
        self.assertEqual(self.stored(), {"command": USER_LINE["command"]})
        self.assertEqual(len(self.backups()), 1)

    def test_wrap_without_status_line(self):
        self.write({"hooks": {"Stop": [CC]}})
        self.install()
        self.assertEqual(self.read()["statusLine"], {"type": "command", "command": TAP})
        self.assertEqual(self.stored(), {"command": None})

    def test_reinstall_keeps_stored_original(self):
        self.write({"statusLine": USER_LINE})
        self.install()
        newer = TAP.replace("python3", "python3.14")
        self.install(tap=newer)
        self.assertEqual(self.read()["statusLine"]["command"], newer)
        self.assertEqual(self.stored(), {"command": USER_LINE["command"]})
        self.install(tap=newer)
        self.assertEqual(len(self.backups()), 2)

    def test_rewrap_after_user_replaced_status_line(self):
        self.write({"statusLine": USER_LINE})
        self.install()
        data = self.read()
        data["statusLine"] = {"type": "command", "command": "~/new-line.sh"}
        self.write(data)
        self.install()
        self.assertEqual(self.read()["statusLine"]["command"], TAP)
        self.assertEqual(self.stored(), {"command": "~/new-line.sh"})

    def test_unwrap_restores_original(self):
        self.write({"hooks": {"Stop": [CC]}, "statusLine": USER_LINE})
        before = self.read()
        self.install()
        self.uninstall()
        self.assertEqual(self.read(), before)
        self.assertFalse(self.store.exists())

    def test_unwrap_without_stored_command_removes_status_line(self):
        self.write({"hooks": {"Stop": [CC]}})
        self.install()
        self.uninstall()
        self.assertEqual(self.read(), {"hooks": {"Stop": [CC]}})
        self.install()
        self.store.unlink()
        self.uninstall()
        self.assertEqual(self.read(), {"hooks": {"Stop": [CC]}})

    def test_unwrap_leaves_foreign_status_line(self):
        self.write({"statusLine": USER_LINE})
        self.store.write_text(json.dumps({"command": "something else"}), encoding="utf-8")
        before = self.path.read_text(encoding="utf-8")
        self.uninstall()
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertEqual(self.backups(), [])
        self.assertFalse(self.store.exists())

    def test_invalid_status_line_aborts_unchanged(self):
        for line in ([1], "bash x.sh", {"type": "static", "command": "x"}, {"command": "x"},
                     {"type": "command", "command": 5}):
            with self.subTest(line=line):
                self.write({"statusLine": line})
                before = self.path.read_text(encoding="utf-8")
                with self.assertRaises(sm.MergeError):
                    self.install()
                self.assertEqual(self.path.read_text(encoding="utf-8"), before)
                self.assertFalse(self.store.exists())
        self.assertEqual(self.backups(), [])

    def test_wrap_and_unwrap_through_symlink(self):
        target = Path(self.tmp.name, "dotfiles-settings.json")
        target.write_text(json.dumps({"statusLine": USER_LINE}), encoding="utf-8")
        os.symlink(target, self.path)
        self.install()
        self.uninstall()
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"statusLine": USER_LINE, "hooks": {}})
```

The last assertion includes `"hooks": {}`: `add_hooks` creates the `hooks` object and `remove_hooks` keeps it empty. That is existing behavior.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_settings_merge -v`
Expected: FAIL/ERROR, e.g. `TypeError: install() takes 2 positional arguments but 4 were given`.

- [ ] **Step 4: Implement**

In `settings_merge.py`:

1. Replace the module docstring:

```python
"""Add or remove the dashboard's hook entries and status line tap in Claude Code's settings.json, leaving every other
hook and setting untouched.

Usage: settings_merge.py install <settings.json> <hook-command> <tap-command> <statusline-store>
       settings_merge.py uninstall <settings.json> <statusline-store>
"""
```

2. Below `MARKER = …` add:

```python
TAP_MARKER = "claude-dashboard/app/hook/statusline_tap.py"
KEEP = object()
```

3. Below `remove_hooks` add:

```python
def _is_tap(command) -> bool:
    return isinstance(command, str) and TAP_MARKER in command


def wrap_statusline(settings: dict, tap_command: str):
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
```

4. Replace `install` and `uninstall`:

```python
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


def uninstall(path: Path, store: Path) -> None:
    path = Path(os.path.realpath(path))
    data = _load(path)
    if data is not None:
        before = _dump(data)
        remove_hooks(data)
        unwrap_statusline(data, _stored_command(Path(store)))
        after = _dump(data)
        if after != before:
            _backup(path)
            _write(path, after)
    with contextlib.suppress(FileNotFoundError):
        Path(store).unlink()
```

5. In `main`, replace the two argument branches:

```python
        if len(args) == 5 and args[0] == "install":
            install(Path(args[1]), args[2], args[3], Path(args[4]))
        elif len(args) == 3 and args[0] == "uninstall":
            uninstall(Path(args[1]), Path(args[2]))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_settings_merge -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add settings_merge.py tests/test_settings_merge.py
git commit -m "Wrap and unwrap the status line with the limits tap in settings.json

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `limits.py` and the threshold config

**Files:**
- Create: `limits.py`, `tests/fixtures/limits/claude.json`
- Modify: `config.py`
- Test: `tests/test_limits.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: session file format (Task 2).
- Produces (used by Tasks 5 and 7):
  - `config.DEFAULTS["limit_warn_pct"] = 80`, `config.DEFAULTS["limit_critical_pct"] = 95`;
  - `limits.LimitsReader(data_dir: Path, claude_json: Path, config: dict, clock=time.time, tz=None)` with attributes `sessions_dir`, `claude_json` and `snapshot() -> dict`;
  - snapshot shape: `{"windows": [{"kind", "label", "used_pct": float, "resets_at": iso, "as_of": iso, "source": "statusline"|"cache", "level": "normal"|"warn"|"critical"}], "extra_usage": {"enabled": bool, "used": float|None, "limit": float|None, "currency": str|None, "limit_reached": bool, "as_of": iso} | None, "as_of": iso | None, "alerts": [{"kind": "limit:<kind>", "level", "title", "message"}]}`;
  - `limits.iso`, `limits.parse_ts`, `limits.level`, `limits.reset_text`, `limits.merge`, `limits.SAME_WINDOW_S = 600`.

- [ ] **Step 1: Create the anonymized `~/.claude.json` fixture**

`tests/fixtures/limits/claude.json` (the shape observed on 2026-10-14; the account id is anonymized):

```json
{
  "numStartups": 12,
  "oauthAccount": {
    "accountUuid": "00000000-0000-4000-8000-0000000000aa",
    "emailAddress": "me@example.com",
    "billingType": "stripe_subscription"
  },
  "cachedUsageUtilization": {
    "fetchedAtMs": 1791984333901,
    "accountUuid": "00000000-0000-4000-8000-0000000000aa",
    "utilization": {
      "five_hour": {"utilization": 32, "resets_at": "2026-10-14T16:49:59.701245+00:00", "limit_dollars": null,
                    "used_dollars": null, "remaining_dollars": null, "locked_reason": null},
      "seven_day": {"utilization": 27, "resets_at": "2026-10-19T04:59:59.701269+00:00", "limit_dollars": null,
                    "used_dollars": null, "remaining_dollars": null, "locked_reason": null},
      "seven_day_opus": null,
      "seven_day_sonnet": null,
      "extra_usage": {"is_enabled": true, "monthly_limit": 1000, "used_credits": 0, "utilization": null,
                      "currency": "EUR", "decimal_places": 2, "disabled_reason": null, "user_disabled": false,
                      "spend_limit_reached": false, "credits_ever_enabled": true, "daily": null, "weekly": null},
      "limits": [
        {"kind": "session", "group": "session", "percent": 32, "severity": "normal",
         "resets_at": "2026-10-14T16:49:59.701245+00:00", "scope": null, "is_active": true},
        {"kind": "weekly_all", "group": "weekly", "percent": 27, "severity": "normal",
         "resets_at": "2026-10-19T04:59:59.701269+00:00", "scope": null, "is_active": false},
        {"kind": "weekly_scoped", "group": "weekly", "percent": 3, "severity": "normal",
         "resets_at": "2026-10-19T04:59:59.701572+00:00",
         "scope": {"model": {"id": null, "display_name": "Fable"}, "surface": null}, "is_active": false}
      ]
    }
  }
}
```

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_config.py` (class `ConfigTest`):

```python
    def test_limit_thresholds(self):
        self.assertEqual((config.DEFAULTS["limit_warn_pct"], config.DEFAULTS["limit_critical_pct"]), (80, 95))
        self.write({"limit_warn_pct": 70, "limit_critical_pct": "high"})
        cfg = config.load_config(self.path)
        self.assertEqual((cfg["limit_warn_pct"], cfg["limit_critical_pct"]), (70, 95))
```

`tests/test_limits.py`:

```python
import json
import os
import tempfile
import unittest
import zoneinfo
from datetime import timezone
from pathlib import Path

import config
import limits

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "limits" / "claude.json"
NOW = 1791985200.0          # 2026-10-14T13:40:00Z
FIVE_RESET = 1791996599     # 2026-10-14T16:49:59Z
WEEK_RESET = 1792385999     # 2026-10-19T04:59:59Z (a Monday)
SID_A = "aaaaaaaa-0000-4000-8000-000000000000"
SID_B = "bbbbbbbb-0000-4000-8000-000000000000"


class LimitsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data = root / "data"
        self.claude_json = root / ".claude.json"
        self.cfg = config.load_config(root / "none.json")
        self.now = NOW
        self.reader = limits.LimitsReader(self.data, self.claude_json, self.cfg, clock=lambda: self.now,
                                          tz=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def write_cache(self, mutate=None):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        if mutate:
            mutate(data)
        self.claude_json.write_text(json.dumps(data), encoding="utf-8")

    def write_tap(self, session_id, captured_at, five=None, week=None):
        rate = {}
        if five:
            rate["five_hour"] = {"used_percentage": five[0], "resets_at": five[1]}
        if week:
            rate["seven_day"] = {"used_percentage": week[0], "resets_at": week[1]}
        folder = self.data / "rate_limits"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{session_id}.json"
        path.write_text(json.dumps({"captured_at": limits.iso(captured_at), "session_id": session_id,
                                    "rate_limits": rate}), encoding="utf-8")
        os.utime(path, (captured_at, captured_at))
        return path

    def windows(self):
        return {w["kind"]: w for w in self.reader.snapshot()["windows"]}

    def test_nothing_available(self):
        self.assertEqual(self.reader.snapshot(), {"windows": [], "extra_usage": None, "as_of": None, "alerts": []})

    def test_cache_only(self):
        self.write_cache()
        snap = self.reader.snapshot()
        self.assertEqual([w["kind"] for w in snap["windows"]], ["five_hour", "seven_day", "weekly:Fable"])
        self.assertEqual(snap["windows"][0], {
            "kind": "five_hour", "label": "5h", "used_pct": 32.0, "resets_at": "2026-10-14T16:49:59Z",
            "as_of": "2026-10-14T13:25:33Z", "source": "cache", "level": "normal"})
        self.assertEqual((snap["windows"][2]["label"], snap["windows"][2]["used_pct"]), ("Week · Fable", 3.0))
        self.assertEqual(snap["extra_usage"], {"enabled": True, "used": 0.0, "limit": 10.0, "currency": "EUR",
                                               "limit_reached": False, "as_of": "2026-10-14T13:25:33Z"})
        self.assertEqual((snap["as_of"], snap["alerts"]), ("2026-10-14T13:25:33Z", []))

    def test_session_value_beats_lower_cache_and_keeps_newest_as_of(self):
        self.write_cache()
        self.write_tap(SID_A, NOW - 60, five=(40, FIVE_RESET), week=(27, WEEK_RESET))
        w = self.windows()
        self.assertEqual((w["five_hour"]["used_pct"], w["five_hour"]["source"], w["five_hour"]["as_of"]),
                         (40.0, "statusline", "2026-10-14T13:39:00Z"))
        self.assertEqual((w["seven_day"]["source"], w["seven_day"]["as_of"]), ("statusline", "2026-10-14T13:39:00Z"))

    def test_idle_session_never_lowers_the_value(self):
        self.write_tap(SID_A, NOW - 300, five=(55, FIVE_RESET))  # working session, last change 5 min ago
        self.write_tap(SID_B, NOW - 5, five=(41, FIVE_RESET))    # idle session redrew with its old response
        w = self.windows()["five_hour"]
        self.assertEqual((w["used_pct"], w["as_of"]), (55.0, "2026-10-14T13:35:00Z"))

    def test_later_window_beats_earlier(self):
        self.write_cache()
        self.write_tap(SID_A, NOW, five=(3, FIVE_RESET + 5 * 3600))
        self.assertEqual(self.windows()["five_hour"]["used_pct"], 3.0)

    def test_same_window_tolerance(self):
        self.write_tap(SID_A, NOW - 60, five=(50, FIVE_RESET))
        self.write_tap(SID_B, NOW - 30, five=(20, FIVE_RESET + limits.SAME_WINDOW_S - 1))
        self.assertEqual(self.windows()["five_hour"]["used_pct"], 50.0)
        self.write_tap(SID_B, NOW - 29, five=(20, FIVE_RESET + limits.SAME_WINDOW_S + 1))
        self.assertEqual(self.windows()["five_hour"]["used_pct"], 20.0)

    def test_expired_windows_are_dropped(self):
        self.write_cache()
        self.write_tap(SID_A, NOW, five=(90, FIVE_RESET))
        self.now = FIVE_RESET + 1
        w = self.windows()
        self.assertNotIn("five_hour", w)
        self.assertIn("seven_day", w)

    def test_cache_of_another_account_is_ignored(self):
        for mutate in (lambda d: d["oauthAccount"].update(accountUuid="00000000-0000-4000-8000-0000000000bb"),
                       lambda d: d.pop("oauthAccount"),
                       lambda d: d["cachedUsageUtilization"].pop("accountUuid")):
            with self.subTest(mutate=mutate):
                self.write_cache(mutate)
                snap = self.reader.snapshot()
                self.assertEqual((snap["windows"], snap["extra_usage"]), ([], None))

    def test_unparsable_claude_json_keeps_last_good(self):
        self.write_cache()
        self.assertEqual(len(self.reader.snapshot()["windows"]), 3)
        self.claude_json.write_text('{"cachedUsageUtilization": {', encoding="utf-8")
        self.assertEqual(len(self.reader.snapshot()["windows"]), 3)
        self.claude_json.unlink()
        self.assertEqual(self.reader.snapshot()["windows"], [])

    def test_reads_files_only_when_changed(self):
        self.write_cache()
        self.reader.snapshot()
        st = self.claude_json.stat()
        text = self.claude_json.read_text(encoding="utf-8").replace('"utilization": 32', '"utilization": 77')
        self.claude_json.write_text(text, encoding="utf-8")
        os.utime(self.claude_json, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(self.windows()["five_hour"]["used_pct"], 32.0)  # same mtime and size: not re-read
        os.utime(self.claude_json, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        self.assertEqual(self.windows()["five_hour"]["used_pct"], 77.0)

    def test_old_session_files_are_deleted(self):
        old = self.write_tap(SID_A, NOW - 9 * 86400, five=(90, FIVE_RESET))
        fresh = self.write_tap(SID_B, NOW - 60, five=(10, FIVE_RESET))
        self.assertEqual(self.windows()["five_hour"]["used_pct"], 10.0)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_levels_and_alerts(self):
        self.write_tap(SID_A, NOW, five=(85, FIVE_RESET), week=(100, WEEK_RESET))
        snap = self.reader.snapshot()
        self.assertEqual([w["level"] for w in snap["windows"]], ["warn", "critical"])
        self.assertEqual(snap["alerts"], [
            {"kind": "limit:five_hour", "level": "warn", "title": "Claude limits: warn",
             "message": "Claude 5h limit at 85% · resets 16:49"},
            {"kind": "limit:seven_day", "level": "critical", "title": "Claude limits: critical",
             "message": "Claude Week limit reached · resets Mon 04:59"},
        ])

    def test_per_model_alert(self):
        self.write_cache(lambda d: d["cachedUsageUtilization"]["utilization"]["limits"][2].update(percent=81))
        [alert] = self.reader.snapshot()["alerts"]
        self.assertEqual(alert["message"], "Claude Week · Fable limit at 81% · resets Mon 04:59")

    def test_threshold_edges_and_config(self):
        for pct, expected in ((79.9, "normal"), (80, "warn"), (94.9, "warn"), (95, "critical"), (130, "critical")):
            with self.subTest(pct=pct):
                self.assertEqual(limits.level(pct, self.cfg), expected)
        self.cfg["limit_warn_pct"] = 30
        self.write_cache()
        self.assertEqual(self.windows()["five_hour"]["level"], "warn")

    def test_reset_text(self):
        utc = timezone.utc
        self.assertEqual(limits.reset_text(FIVE_RESET, NOW, utc), "16:49")
        self.assertEqual(limits.reset_text(WEEK_RESET, NOW, utc), "Mon 04:59")
        late = 1792020600  # 2026-10-14T23:30:00Z
        self.assertEqual(limits.reset_text(late + 2400, late, utc), "Thu 00:10")
        prague = zoneinfo.ZoneInfo("Europe/Prague")
        before = 1792886400  # 2026-10-25T00:00:00Z = 02:00 CEST; clocks go back at 03:00 CEST
        self.assertEqual(limits.reset_text(before + 3 * 3600, before, prague), "04:00")

    def test_parse_ts(self):
        self.assertEqual(limits.parse_ts(1791996599), 1791996599.0)
        self.assertEqual(limits.parse_ts("2026-10-14T16:49:59Z"), 1791996599.0)
        self.assertAlmostEqual(limits.parse_ts("2026-10-14T16:49:59.701245+00:00"), 1791996599.701245, places=5)
        for bad in ("2026-10-14T16:49:59", "soon", None, True, [1]):
            with self.subTest(bad=bad):
                self.assertIsNone(limits.parse_ts(bad))

    def test_malformed_inputs(self):
        def mutate(d):
            d["cachedUsageUtilization"]["utilization"].update(
                five_hour={"utilization": "high", "resets_at": "2026-10-14T16:49:59Z"}, seven_day=None,
                limits={"x": 1}, extra_usage=[])
        self.write_cache(mutate)
        folder = self.data / "rate_limits"
        folder.mkdir(parents=True)
        (folder / f"{SID_A}.json").write_text("[1, 2]", encoding="utf-8")
        (folder / f"{SID_B}.json").write_text("{nope", encoding="utf-8")
        (folder / "notes.txt").write_text("x", encoding="utf-8")
        snap = self.reader.snapshot()
        self.assertEqual((snap["windows"], snap["extra_usage"], snap["alerts"]), ([], None, []))
        self.claude_json.write_text("[]", encoding="utf-8")
        self.assertEqual(self.reader.snapshot()["windows"], [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_limits tests.test_config -v`
Expected: `ModuleNotFoundError: No module named 'limits'` and a `KeyError: 'limit_warn_pct'` in `test_limit_thresholds`.

- [ ] **Step 4: Add the config defaults**

In `config.py`, add to `DEFAULTS` after `"docker_sample_interval_s": 30,`:

```python
    "limit_warn_pct": 80,
    "limit_critical_pct": 95,
```

- [ ] **Step 5: Write `limits.py`**

```python
"""Claude subscription usage limits from the status line tap's session files and Claude Code's own usage cache."""
from __future__ import annotations

import contextlib
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

WINDOWS = {"five_hour": "5h", "seven_day": "Week"}
SAME_WINDOW_S = 600
SESSION_FILE_TTL_S = 8 * 86400


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _num(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def parse_ts(value) -> float | None:
    """Epoch seconds from epoch seconds or an ISO 8601 string with a UTC offset."""
    number = _num(value)
    if number is not None:
        return number
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.timestamp() if dt.tzinfo else None


def _dig(obj, *keys):
    for key in keys:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _window(kind: str, label: str, used, resets, as_of: float, source: str) -> dict | None:
    used, resets = _num(used), parse_ts(resets)
    if used is None or resets is None:
        return None
    return {"kind": kind, "label": label, "used_pct": used, "resets_at": resets, "as_of": as_of, "source": source}


def tap_windows(data) -> list[dict]:
    """Windows from one session file of hook/statusline_tap.py."""
    as_of = parse_ts(_dig(data, "captured_at"))
    rate = _dig(data, "rate_limits")
    if as_of is None or not isinstance(rate, dict):
        return []
    found = (_window(kind, label, _dig(rate, kind, "used_percentage"), _dig(rate, kind, "resets_at"), as_of,
                     "statusline") for kind, label in WINDOWS.items())
    return [w for w in found if w]


def _extra_usage(extra, as_of: float) -> dict | None:
    if not isinstance(extra, dict) or not isinstance(extra.get("is_enabled"), bool):
        return None
    places = extra.get("decimal_places")
    scale = 10 ** places if isinstance(places, int) and not isinstance(places, bool) and 0 <= places <= 6 else 100
    used, limit, currency = _num(extra.get("used_credits")), _num(extra.get("monthly_limit")), extra.get("currency")
    return {
        "enabled": extra["is_enabled"],
        "used": None if used is None else used / scale,
        "limit": None if limit is None else limit / scale,
        "currency": currency if isinstance(currency, str) else None,
        "limit_reached": extra.get("spend_limit_reached") is True,
        "as_of": iso(as_of),
    }


def cache_usage(data) -> tuple[list[dict], dict | None]:
    """Windows and extra usage from ~/.claude.json; nothing unless the cache belongs to the signed-in account."""
    cache = _dig(data, "cachedUsageUtilization")
    account = _dig(data, "oauthAccount", "accountUuid")
    fetched = _num(_dig(cache, "fetchedAtMs"))
    usage = _dig(cache, "utilization")
    if not account or _dig(cache, "accountUuid") != account or fetched is None or not isinstance(usage, dict):
        return [], None
    as_of = fetched / 1000
    windows = [_window(kind, label, _dig(usage, kind, "utilization"), _dig(usage, kind, "resets_at"), as_of, "cache")
               for kind, label in WINDOWS.items()]
    entries = usage.get("limits")
    for entry in entries if isinstance(entries, list) else []:
        model = _dig(entry, "scope", "model", "display_name")
        if _dig(entry, "kind") == "weekly_scoped" and isinstance(model, str) and model:
            windows.append(_window(f"weekly:{model}", f"Week · {model}", entry.get("percent"), entry.get("resets_at"),
                                   as_of, "cache"))
    return [w for w in windows if w], _extra_usage(usage.get("extra_usage"), as_of)


def merge(candidates: list[dict], now: float) -> list[dict]:
    """One window per kind: the latest window, its highest value, and the newest observation of that value.

    Usage within a window only goes up, so a lower value is an older one, whichever session reported it.
    """
    by_kind: dict[str, list[dict]] = {}
    for c in candidates:
        if c["resets_at"] > now:
            by_kind.setdefault(c["kind"], []).append(c)
    out = []
    for group in by_kind.values():
        latest = max(c["resets_at"] for c in group)
        current = [c for c in group if latest - c["resets_at"] <= SAME_WINDOW_S]
        top = max(c["used_pct"] for c in current)
        out.append(max((c for c in current if c["used_pct"] == top), key=lambda c: c["as_of"]))
    order = list(WINDOWS)
    return sorted(out, key=lambda w: (order.index(w["kind"]) if w["kind"] in WINDOWS else len(order), w["label"]))


def level(used_pct: float, config: dict) -> str:
    if used_pct >= config["limit_critical_pct"]:
        return "critical"
    if used_pct >= config["limit_warn_pct"]:
        return "warn"
    return "normal"


def reset_text(resets_at: float, now: float, tz=None) -> str:
    """Local HH:MM, with the weekday when the reset is not today."""
    reset = datetime.fromtimestamp(resets_at, tz)
    if reset.date() == datetime.fromtimestamp(now, tz).date():
        return reset.strftime("%H:%M")
    return reset.strftime("%a %H:%M")


def alert_for(window: dict, now: float, tz=None) -> dict | None:
    if window["level"] == "normal":
        return None
    used = window["used_pct"]
    head = f"Claude {window['label']} limit " + ("reached" if used >= 100 else f"at {round(used)}%")
    return {"kind": f"limit:{window['kind']}", "level": window["level"], "title": f"Claude limits: {window['level']}",
            "message": f"{head} · resets {reset_text(window['resets_at'], now, tz)}"}


class LimitsReader:
    def __init__(self, data_dir: Path, claude_json: Path, config: dict, clock=time.time, tz=None) -> None:
        self.sessions_dir = Path(data_dir) / "rate_limits"
        self.claude_json = Path(claude_json)
        self.config, self.clock, self.tz = config, clock, tz
        self._files: dict[Path, tuple[tuple[int, int], object]] = {}

    def _load(self, path: Path, st: os.stat_result | None = None):
        """Parsed JSON, re-read only when (mtime, size) changes; an unparsable file keeps its last good parse."""
        try:
            st = st or path.stat()
        except OSError:
            self._files.pop(path, None)
            return None
        key = (st.st_mtime_ns, st.st_size)
        cached = self._files.get(path)
        if cached and cached[0] == key:
            return cached[1]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cached[1] if cached else None
        self._files[path] = (key, data)
        return data

    def _session_windows(self, now: float) -> list[dict]:
        try:
            entries = [e for e in os.scandir(self.sessions_dir) if e.name.endswith(".json")]
        except OSError:
            entries = []
        seen, out = set(), []
        for entry in entries:
            path = Path(entry.path)
            try:
                st = entry.stat()
            except OSError:
                continue
            if now - st.st_mtime > SESSION_FILE_TTL_S:
                with contextlib.suppress(OSError):
                    path.unlink()
                continue
            seen.add(path)
            out.extend(tap_windows(self._load(path, st)))
        for path in [p for p in self._files if p.parent == self.sessions_dir and p not in seen]:
            del self._files[path]
        return out

    def snapshot(self) -> dict:
        now = self.clock()
        cache_windows, extra = cache_usage(self._load(self.claude_json))
        windows, alerts = [], []
        for w in merge(self._session_windows(now) + cache_windows, now):
            w = {**w, "level": level(w["used_pct"], self.config)}
            alert = alert_for(w, now, self.tz)
            if alert:
                alerts.append(alert)
            windows.append({**w, "resets_at": iso(w["resets_at"]), "as_of": iso(w["as_of"])})
        as_of = max((w["as_of"] for w in windows), default=None)
        return {"windows": windows, "extra_usage": extra, "as_of": as_of, "alerts": alerts}
```

If the Task 1 ledger raised the same-window distance, set `SAME_WINDOW_S` accordingly.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_limits tests.test_config -v`
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add limits.py config.py tests/test_limits.py tests/test_config.py tests/fixtures/limits/claude.json
git commit -m "Merge usage limits from session files and Claude Code's usage cache

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Notifier titles and server wiring

**Files:**
- Modify: `notifier.py`, `server.py`
- Test: `tests/test_notifier.py`, `tests/test_server.py`

**Interfaces:**
- Consumes: `limits.LimitsReader` and its snapshot shape (Task 4).
- Produces:
  - `App(..., limits=None)` with `App.limits`;
  - `/api/sessions` payload key `limits` (snapshot or `null`), used by Task 7;
  - alert dicts may carry `title`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_notifier.py` (class `NotifierTest`):

```python
    def test_alert_titles_and_mixed_kinds_in_one_call(self):
        mem = {"kind": "memory", "level": "warn", "message": "m"}
        lim = {"kind": "limit:five_hour", "level": "warn", "title": "Claude limits: warn",
               "message": "Claude 5h limit at 85% · resets 16:49"}
        self.n.check_alerts([mem, lim])
        self.assertCountEqual(self.sent, [("Mac resources: warn", "m"),
                                          ("Claude limits: warn", "Claude 5h limit at 85% · resets 16:49")])
        self.now += 60
        self.n.check_alerts([mem, lim])
        self.assertEqual(len(self.sent), 2)
        self.n.check_alerts([mem, {**lim, "level": "critical", "title": "Claude limits: critical", "message": "c"}])
        self.assertEqual(self.sent[-1], ("Claude limits: critical", "c"))
        self.assertEqual(len(self.sent), 3)
```

In `tests/test_server.py`:

1. Below `class FakeNotifier` add:

```python
class FakeLimits:
    def __init__(self):
        self.value = {
            "windows": [{"kind": "five_hour", "label": "5h", "used_pct": 85.0, "resets_at": "2026-10-14T16:49:59Z",
                         "as_of": "2026-10-14T13:40:00Z", "source": "statusline", "level": "warn"}],
            "extra_usage": None, "as_of": "2026-10-14T13:40:00Z",
            "alerts": [{"kind": "limit:five_hour", "level": "warn", "title": "Claude limits: warn",
                        "message": "Claude 5h limit at 85% · resets 16:49"}],
        }

    def snapshot(self):
        if isinstance(self.value, Exception):
            raise self.value
        return json.loads(json.dumps(self.value))
```

2. In `setUp`, add `self.limits = FakeLimits()` next to `self.notifier = FakeNotifier()`, and pass `limits=self.limits` to `server.App(...)`.
3. Replace `test_tick_feeds_notifier` with:

```python
    def test_tick_feeds_notifier(self):
        self.app.tick()
        self.assertEqual([r["session_id"] for r in self.notifier.rows], [sid(1)])
        self.assertEqual(self.notifier.alerts, self.monitor.system["alerts"] + self.limits.value["alerts"])
```

4. Add:

```python
    def test_payload_has_limits(self):
        self.assertEqual(self.request("GET", "/api/sessions")[1]["limits"], self.limits.value)

    def test_failing_limits_give_null(self):
        self.limits.value = ValueError("boom")
        status, payload = self.request("GET", "/api/sessions")
        self.assertEqual((status, payload["limits"]), (200, None))
        self.app.tick()
        self.assertEqual(self.notifier.alerts, self.monitor.system["alerts"])

    def test_default_limits_reader_paths(self):
        cfg = config.load_config(Path(self.data.name) / "config.json")
        app = server.App(self.fake.root, self.data.name, config=cfg, monitor=self.monitor, notifier=self.notifier)
        self.assertEqual(app.limits.claude_json, Path(self.fake.root).parent / ".claude.json")
        self.assertEqual(app.limits.sessions_dir, Path(self.data.name) / "rate_limits")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_notifier tests.test_server -v`
Expected:
- `test_alert_titles_and_mixed_kinds_in_one_call` fails, because the title is still `Mac resources: warn`;
- the server tests fail with `TypeError: App.__init__() got an unexpected keyword argument 'limits'`.

- [ ] **Step 3: Implement**

`notifier.py`, in `check_alerts`, replace `self._deliver(f"Mac resources: {alert['level']}", alert["message"])` with:

```python
                self._deliver(alert.get("title") or f"Mac resources: {alert['level']}", alert["message"])
```

`server.py`:

1. Add `import limits as limits_mod` to the imports (alphabetically after `import iterm`).
2. Extend the `App.__init__` signature with `limits=None` after `focuser=iterm.focus_tty`. At the end of `__init__` add:

```python
        self.limits = limits if limits is not None else limits_mod.LimitsReader(
            data_dir, self.claude_dir.parent / ".claude.json", self.config)
```

3. In `snapshot()`, replace the `return` statement with:

```python
        try:
            limits = self.limits.snapshot()
        except Exception as e:  # the overview must work even without the limits block
            print(f"limits: {e!r}", file=sys.stderr, flush=True)
            limits = None
        return {"generated_at": now_iso(), "jira_hosts": hosts, "rows": rows,
                "queue": agents.build_queue(rows), "system": system, "limits": limits}
```

4. Replace `tick()`:

```python
    def tick(self) -> None:
        payload = self.snapshot()
        self.notifier.check_queue(payload["rows"])
        # One call: check_alerts treats every kind missing from a call as cleared.
        self.notifier.check_alerts((payload["system"].get("alerts") or [])
                                   + ((payload["limits"] or {}).get("alerts") or []))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_notifier tests.test_server -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add notifier.py server.py tests/test_notifier.py tests/test_server.py
git commit -m "Serve usage limits and notify about them together with resource alerts

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Model and effort per session

**Files:**
- Modify: `sessions.py`, `tests/helpers.py`
- Test: `tests/test_sessions_parse.py`, `tests/test_sessions_rows.py`, `tests/test_sessions_helpers.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `Session.model: str | None`, `Session.effort: str | None`;
  - `sessions.model_label(model_id: str | None) -> str | None`;
  - row keys `model`, `model_label`, `effort` (used by Task 7);
  - `tests.helpers.assistant(..., model=None, **extra)`.

- [ ] **Step 1: Extend the test helper**

In `tests/helpers.py`, replace `assistant`:

```python
def assistant(*, ts: str, uuid: str, cwd: str = "/w/proj", branch: str = "master", model: str | None = None,
              **extra) -> dict:
    message = {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}
    if model is not None:
        message["model"] = model
    return {
        "type": "assistant", "message": message, "timestamp": ts, "uuid": uuid, "parentUuid": None, "cwd": cwd,
        "gitBranch": branch, "isSidechain": False, **extra,
    }
```

- [ ] **Step 2: Write the failing tests**

Add to `tests/test_sessions_parse.py`, class `ParseSessionTest`:

```python
    def test_model_and_effort_from_last_main_chain_response(self):
        s = self.parse([
            user("hi", ts="2026-10-14T10:00:00Z", uuid="u1", cwd=CWD),
            assistant(ts="2026-10-14T10:00:01Z", uuid="a1", cwd=CWD, model="claude-sonnet-4-5-20250929", effort="high"),
            assistant(ts="2026-10-14T10:00:02Z", uuid="a2", cwd=CWD, model="claude-opus-5-5", effort="xhigh"),
            assistant(ts="2026-10-14T10:00:03Z", uuid="a3", cwd=CWD, model="claude-haiku-4-5", effort="low",
                      isSidechain=True),
            assistant(ts="2026-10-14T10:00:04Z", uuid="a4", cwd=CWD, model="<synthetic>"),
            assistant(ts="2026-10-14T10:00:05Z", uuid="a5", cwd=CWD),
        ])
        self.assertEqual((s.model, s.effort), ("claude-opus-5-5", "xhigh"))

    def test_model_without_effort_clears_it(self):
        s = self.parse([
            assistant(ts="2026-10-14T10:00:01Z", uuid="a1", cwd=CWD, model="claude-opus-5-5", effort="xhigh"),
            assistant(ts="2026-10-14T10:00:02Z", uuid="a2", cwd=CWD, model="claude-haiku-4-5"),
        ])
        self.assertEqual((s.model, s.effort), ("claude-haiku-4-5", None))

    def test_no_model_yet(self):
        s = self.parse([user("hi", ts="2026-10-14T10:00:00Z", uuid="u1", cwd=CWD)])
        self.assertEqual((s.model, s.effort), (None, None))
```

Add to `SessionCacheTest` in `tests/test_sessions_parse.py` (it has `self.cache`):

```python
    def test_cache_picks_up_model_switch_in_appended_lines(self):
        path = self.fake.write_session(CWD, sid(1), [
            assistant(ts="2026-10-14T10:00:01Z", uuid="a1", cwd=CWD, model="claude-opus-5-5", effort="xhigh")])
        self.cache.load()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(assistant(ts="2026-10-14T10:00:02Z", uuid="a2", cwd=CWD, model="claude-fable-5-1",
                                          effort="high")) + "\n")
        [s] = self.cache.load()
        self.assertEqual((s.model, s.effort), ("claude-fable-5-1", "high"))
```

In `tests/test_sessions_rows.py`, change the helpers import to `from tests.helpers import FakeClaude, assistant, custom_title, last_prompt, sid, user`. Add to `BuildRowsTest`:

```python
    def test_row_model_and_effort(self):
        s = self.session(1, [user("hi", ts="2026-10-14T10:00:00Z", uuid="u1", cwd=SHOP),
                             assistant(ts="2026-10-14T10:00:05Z", uuid="a1", cwd=SHOP, model="claude-opus-5-5",
                                       effort="xhigh")])
        r = self.rows([s])[0][sid(1)]
        self.assertEqual((r["model"], r["model_label"], r["effort"]), ("claude-opus-5-5", "Opus 5.5", "xhigh"))
        s = self.session(2, [user("hi", ts="2026-10-14T10:00:00Z", uuid="u2", cwd=SHOP)])
        r = self.rows([s])[0][sid(2)]
        self.assertEqual((r["model"], r["model_label"], r["effort"]), (None, None, None))
```

Add to `tests/test_sessions_helpers.py`:

```python
class ModelLabelTest(unittest.TestCase):
    def test_model_label(self):
        cases = {
            "claude-opus-5-5": "Opus 5.5",
            "claude-fable-5-1": "Fable 5.1",
            "claude-sonnet-4-5-20250929": "Sonnet 4.5",
            "claude-3-5-haiku-20241022": "Haiku 3.5",
            "claude-opus-4-6[1m]": "Opus 4.6",
            "claude-opus": "Opus",
            "claude-5-5": "claude-5-5",
            "us.anthropic.claude-sonnet-4-5-20250929-v1:0": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        }
        for model_id, label in cases.items():
            with self.subTest(model_id=model_id):
                self.assertEqual(sessions.model_label(model_id), label)
        self.assertIsNone(sessions.model_label(None))
        self.assertIsNone(sessions.model_label(""))
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_sessions_parse tests.test_sessions_rows tests.test_sessions_helpers -v`
Expected: `AttributeError: 'Session' object has no attribute 'model'`, `KeyError: 'model'` and `AttributeError: module 'sessions' has no attribute 'model_label'`.

- [ ] **Step 4: Implement**

In `sessions.py`:

1. Below the other module constants (next to `SKIP_FLAGS`) add `SYNTHETIC_MODEL = "<synthetic>"`.
2. In `@dataclass class Session`, add before `warnings`:

```python
    model: str | None = None
    effort: str | None = None
```

3. In `_SessionParser.__init__`, add:

```python
        self.model: str | None = None
        self.effort: str | None = None
```

4. In `_SessionParser._line`, right after the `first_ts`/`last_ts` block (before `if self.root_uuid is None …`), add:

```python
        if rtype == "assistant" and not rec.get("isSidechain"):
            message = rec.get("message")
            model = message.get("model") if isinstance(message, dict) else None
            if isinstance(model, str) and model and model != SYNTHETIC_MODEL:
                self.model = model
                effort = rec.get("effort")
                self.effort = effort if isinstance(effort, str) and effort else None
```

5. In `_SessionParser.session`, after `s.root_uuid = self.root_uuid`, add `s.model, s.effort = self.model, self.effort`.
6. Below `resume_command`, add:

```python
def model_label(model_id: str | None) -> str | None:
    """`claude-opus-5-5` → `Opus 5.5`; ids of other shapes stay as they are."""
    if not model_id or not model_id.startswith("claude-"):
        return model_id or None
    name = re.sub(r"-\d{8}$", "", re.sub(r"\[[^\]]*\]$", "", model_id)[len("claude-"):])
    parts = name.split("-")
    family = next((p for p in parts if p.isalpha()), None)
    if family is None:
        return model_id
    version = ".".join(p for p in parts if p.isdigit())
    return f"{family.capitalize()} {version}".rstrip()
```

7. In `_row`, after `"prompt_count": len(s.prompts),`, add:

```python
        "model": s.model,
        "model_label": model_label(s.model),
        "effort": s.effort,
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_sessions_parse tests.test_sessions_rows tests.test_sessions_helpers -v`
Expected: all tests PASS.

- [ ] **Step 6: Commit**

```bash
git add sessions.py tests/helpers.py tests/test_sessions_parse.py tests/test_sessions_rows.py tests/test_sessions_helpers.py
git commit -m "Read the model and effort of every session from its transcript

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: UI: limits line, combined alerts, model and effort

**Files:**
- Modify: `static/filter.js`, `static/app.js`, `static/index.html`
- Test: `tests/js/test_filter.js`, `tests/test_static.py`

**Interfaces:**
- Consumes: payload `limits` (Task 5); row keys `model`, `model_label`, `effort` (Task 6).
- Produces: `DashFilter.formatCountdown(ts, now)`, `DashFilter.formatReset(ts, now)`, `DashFilter.formatMoney(amount, currency)`.

- [ ] **Step 1: Write the failing JS tests**

Add these entries to the `tests` object in `tests/js/test_filter.js`:

```js
  "search finds model and effort"() {
    const r = row({ model_label: "Fable 5.1", effort: "xhigh" });
    assert.strictEqual(F.matches(r, prefs({ q: "fable" })), true);
    assert.strictEqual(F.matches(r, prefs({ q: "xhigh" })), true);
    assert.strictEqual(F.matches(row(), prefs({ q: "fable" })), false);
  },
  "formatCountdown"() {
    const now = Date.parse("2026-10-14T13:40:00Z");
    assert.strictEqual(F.formatCountdown("2026-10-14T14:49:00Z", now), "in 1 h 9 min");
    assert.strictEqual(F.formatCountdown("2026-10-19T04:59:59Z", now), "in 4 d 15 h");
    assert.strictEqual(F.formatCountdown("2026-10-14T13:43:30Z", now), "in 3 min");
    assert.strictEqual(F.formatCountdown("2026-10-14T13:40:20Z", now), "in <1 min");
    assert.strictEqual(F.formatCountdown("2026-10-14T13:00:00Z", now), "in <1 min");
    assert.strictEqual(F.formatCountdown("nonsense", now), "");
  },
  "formatReset"() {
    const now = new Date(2026, 9, 14, 15, 0).getTime();
    assert.strictEqual(F.formatReset(new Date(2026, 9, 14, 18, 50).toISOString(), now), "18:50");
    assert.strictEqual(F.formatReset(new Date(2026, 9, 19, 6, 59).toISOString(), now), "Mon 06:59");
    assert.strictEqual(F.formatReset(new Date(2026, 9, 15, 0, 10).toISOString(), new Date(2026, 9, 14, 23, 30).getTime()), "Thu 00:10");
    assert.strictEqual(F.formatReset("nonsense", now), "");
  },
  "formatMoney"() {
    assert.strictEqual(F.formatMoney(0, "EUR"), "€0");
    assert.strictEqual(F.formatMoney(10, "EUR"), "€10");
    assert.strictEqual(F.formatMoney(12.5, "EUR"), "€12.5");
    assert.strictEqual(F.formatMoney(3.5, null), "3.50");
    assert.strictEqual(F.formatMoney(3.5, "EURO"), "3.50 EURO");
    assert.strictEqual(F.formatMoney(null, "EUR"), "?");
  },
```

`formatReset` builds its inputs with local-time `Date` constructors, so the test passes in any time zone.

In `tests/test_static.py`, add to `test_index_loads_filter_before_app_and_no_external_resources`:

```python
        self.assertIn('id="limits"', html)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `node tests/js/test_filter.js; python3 -m unittest tests.test_static -v`
Expected: `FAIL - search finds model and effort`, `F.formatCountdown is not a function` and similar errors; `test_index_loads_filter_before_app_and_no_external_resources` fails.

- [ ] **Step 3: Implement `filter.js`**

1. In `haystack`, change the line `r.display_dir, r.worktree || "", r.cwd || "", r.search_text || "", (r.note && r.note.note) || "",` to:

```js
      r.display_dir, r.worktree || "", r.cwd || "", r.search_text || "", (r.note && r.note.note) || "",
      r.model_label || "", r.effort || "",
```

2. Before the `return { … }` add:

```js
  function formatCountdown(ts, now) {
    const t = Date.parse(ts);
    if (Number.isNaN(t)) return "";
    const mins = Math.floor((t - (now === undefined ? Date.now() : now)) / 60000);
    if (mins < 1) return "in <1 min";
    if (mins < 60) return `in ${mins} min`;
    const hours = Math.floor(mins / 60);
    if (hours < 24) return `in ${hours} h ${mins % 60} min`;
    return `in ${Math.floor(hours / 24)} d ${hours % 24} h`;
  }

  function formatReset(ts, now) {
    const t = Date.parse(ts);
    if (Number.isNaN(t)) return "";
    const d = new Date(t);
    const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    const today = new Date(now === undefined ? Date.now() : now);
    return d.toDateString() === today.toDateString() ? time : `${d.toLocaleDateString("en-GB", { weekday: "short" })} ${time}`;
  }

  function formatMoney(amount, currency) {
    if (amount === null || amount === undefined) return "?";
    if (!currency) return amount.toFixed(2);
    try {
      return new Intl.NumberFormat("en-GB", { style: "currency", currency, minimumFractionDigits: 0, maximumFractionDigits: 2 }).format(amount);
    } catch (e) {
      return `${amount.toFixed(2)} ${currency}`;
    }
  }
```

3. Extend the returned object:

```js
  return { NO_ISSUE, matches, groupRows, relTime, dirCounts, formatMB, minutesSince, formatWait, splitQueue, worstLevel,
    formatCountdown, formatReset, formatMoney };
```

- [ ] **Step 4: Implement `index.html`**

1. After `<div id="system" class="system"></div>` add `<div id="limits" class="system"></div>`.
2. In `<style>`, after `.system.critical { color: var(--red); }` add:

```css
  .system.stale { color: var(--faint); }
  .meter { display: inline-block; width: 60px; height: 7px; border-radius: 4px; background: var(--chip); overflow: hidden;
           vertical-align: middle; }
  .meter > span { display: block; height: 100%; background: var(--green); }
  .meter.warn > span { background: var(--yellow); }
  .meter.critical > span { background: var(--red); }
```

- [ ] **Step 5: Implement `app.js`**

1. Below `let systemOpen = false;` add:

```js
  let limitsOpen = false;
  const STALE_MIN = 30;
  const METER_KINDS = new Set(["five_hour", "seven_day"]);
```

2. In `renderSystem`, delete the line `$("#alerts").replaceChildren();` in the `if (!s || !s.sampled_at)` block. Also delete the last statement, the one starting `$("#alerts").replaceChildren(...(s.alerts || []).map((a) =>` and ending two lines later.
3. After `renderSystem` add:

```js
  function renderAlerts() {
    const alerts = [...((data.system && data.system.alerts) || []), ...((data.limits && data.limits.alerts) || [])];
    $("#alerts").replaceChildren(...alerts.map((a) =>
      el("div", { class: `alert ${a.level}` }, `${a.level === "critical" ? "⛔" : "⚠"} ${a.message}`)));
  }

  function moneyPair(x) {
    return `${F.formatMoney(x.used, x.currency)} / ${F.formatMoney(x.limit, x.currency)}`;
  }

  function extraUsageText(x) {
    if (!x.enabled) return "Extra usage off";
    return `Extra usage ${moneyPair(x)}${x.limit_reached ? " · limit reached" : ""}`;
  }

  function renderLimitWindow(w, now) {
    const pct = `${Math.round(w.used_pct)}%`;
    if (!METER_KINDS.has(w.kind)) return el("span", {}, `${w.label} ${pct}`);
    return el("span", {},
      `${w.label} `,
      el("span", { class: `meter ${w.level}` }, el("span", { style: `width: ${Math.min(100, Math.max(0, w.used_pct))}%` })),
      ` ${pct} · reset ${F.formatReset(w.resets_at, now)} (${F.formatCountdown(w.resets_at, now)})`);
  }

  function renderLimitsDetails(l) {
    const head = el("tr", {}, ["Limit", "Used", "Resets", "Updated", "Source"].map((h) => el("th", {}, h)));
    const rows = l.windows.map((w) => el("tr", {}, el("td", {}, w.label), el("td", {}, `${Math.round(w.used_pct)}%`),
      el("td", {}, fmt(w.resets_at)), el("td", {}, fmt(w.as_of)), el("td", {}, w.source)));
    const x = l.extra_usage;
    const extra = x ? el("tr", {}, el("td", {}, "Extra usage"), el("td", {}, x.enabled ? moneyPair(x) : "off"),
      el("td", {}, "—"), el("td", {}, fmt(x.as_of)), el("td", {}, "cache")) : null;
    return el("div", { class: "system-details", onclick: (e) => e.stopPropagation() }, el("table", {}, head, rows, extra));
  }

  function renderLimits() {
    const box = $("#limits");
    const l = data.limits;
    box.className = "system";
    if (!loaded) { box.replaceChildren(); return; }
    if (!l) { box.replaceChildren("Claude limits: unavailable"); return; }
    if (!l.windows.length && !l.extra_usage) { box.replaceChildren("Claude limits: no data yet"); return; }
    const now = Date.now();
    const parts = l.windows.map((w) => renderLimitWindow(w, now));
    if (l.extra_usage) parts.push(extraUsageText(l.extra_usage));
    const age = l.as_of ? F.minutesSince(l.as_of, now) : null;
    const stale = age !== null && age > STALE_MIN;
    box.className = `system ${F.worstLevel(l.alerts)}${stale ? " stale" : ""}`;
    const items = ["Claude limits  "];
    parts.forEach((p, i) => items.push(i ? "  │  " : null, p));
    if (l.as_of) items.push("  ·  ", `updated ${F.relTime(l.as_of, now)}${stale ? " (stale)" : ""}`);
    const summary = el("div", { onclick: () => { limitsOpen = !limitsOpen; render(); } }, items);
    box.replaceChildren(...[summary, limitsOpen ? renderLimitsDetails(l) : null].filter(Boolean));
  }
```

4. In `render()`, after `renderSystem();` add:

```js
    renderLimits();
    renderAlerts();
```

5. In `renderRow`, replace the `const sub = …` statement with:

```js
    const model = [r.model_label, r.effort].filter(Boolean).join(" · ");
    const sub = [r.branch || "—", r.worktree ? `worktree ${r.worktree}` : null, `${r.prompt_count} ${plural(r.prompt_count)}`,
      model || null].filter(Boolean).join(" · ");
```

6. In `renderDetails`, after `items.push(dt("Started"), …, dt("Last activity"), …);` add:

```js
    if (r.model) items.push(dt("Model"), el("dd", {}, r.effort ? `${r.model} · effort ${r.effort}` : r.model));
```

- [ ] **Step 6: Run all tests**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: all tests PASS, including `test_filter_js_under_node` and `test_no_html_injection_apis`.

- [ ] **Step 7: Look at it in a browser**

```bash
python3 server.py --port 7334 --data-dir "$TMPDIR/cd-dev"
```

Open http://127.0.0.1:7334/ and check:
- the limits line renders from the real `~/.claude.json` with the cache values (5h, Week, Week · Fable, Extra usage);
- clicking it toggles the detail table;
- session rows show e.g. `Opus 5.5 · xhigh`, and the detail shows `Model: claude-opus-5-5 · effort xhigh`;
- searching `opus` filters to those sessions;
- at 980 px width nothing overflows.

Stop the server.

- [ ] **Step 8: Commit**

```bash
git add static/filter.js static/app.js static/index.html tests/js/test_filter.js tests/test_static.py
git commit -m "Show usage limits, limit alerts, and each session's model and effort

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Install, uninstall, README, and real-data verification

**Files:**
- Modify: `launchd/install.sh`, `launchd/uninstall.sh`, `README.md`

**Interfaces:**
- Consumes: the `settings_merge.py` CLI (Task 3), `hook/statusline_tap.py` (Task 2), `limits.py` (Task 4).
- Produces: a deployed dashboard with the tap active.

- [ ] **Step 1: `install.sh`**

1. In the `cp "$REPO_DIR"/{server,…,config}.py` line, add `limits` to the brace list: `{server,sessions,live,notes,iterm,agents,monitor,notifier,settings_merge,config,limits}.py`.
2. Replace `cp "$REPO_DIR"/hook/claude_hook.py "$APP_DIR/hook/"` with:

```bash
cp "$REPO_DIR"/hook/claude_hook.py "$REPO_DIR"/hook/statusline_tap.py "$APP_DIR/hook/"
```

3. Replace the `HOOK_CMD=…` line and the `if ! … settings_merge.py install …; then … fi` block with:

```bash
HOOK_CMD="\"$PYTHON\" '$APP_DIR/hook/claude_hook.py'"
TAP_CMD="\"$PYTHON\" '$APP_DIR/hook/statusline_tap.py'"
if ! "$PYTHON" "$APP_DIR/settings_merge.py" install "$HOME/.claude/settings.json" "$HOOK_CMD" "$TAP_CMD" \
     "$SUPPORT_DIR/statusline.json"; then
  echo "Could not register the Claude Code hooks and status line in ~/.claude/settings.json (see above). Nothing was changed there." >&2
  exit 1
fi
```

- [ ] **Step 2: `uninstall.sh`**

Replace the `settings_merge.py uninstall` call with:

```bash
  python3 "$SUPPORT_DIR/app/settings_merge.py" uninstall "$HOME/.claude/settings.json" "$SUPPORT_DIR/statusline.json" \
    || echo "Could not remove the Claude Code hooks and status line from ~/.claude/settings.json." >&2
```

- [ ] **Step 3: README**

1. In **Features**, add after the **Live status** bullet:

```markdown
- **Model and effort:** each session shows the model and reasoning effort of its last response (e.g. `Opus 5.5 · xhigh`); both are searchable.
```

2. After the section **Waiting for you, and resources** (before **### Configuration**), add:

```markdown
## Claude usage limits

- **The block** shows the 5-hour and the weekly limit (used %, reset time, countdown), the weekly limit of individual
  models (e.g. Fable) and the extra-usage credits, with the time of the newest data. Data older than 30 min is marked
  stale. Click it for exact reset times and sources.
- **Where the numbers come from:**
  - `install.sh` puts a small wrapper (`hook/statusline_tap.py`) in front of your status line command. It records the
    limits Claude Code passes to the status line after every response, then runs your own command unchanged. Your
    original command is kept in `statusline.json` in the data directory; `uninstall.sh` restores it.
  - Claude Code's own usage cache in `~/.claude.json` adds the per-model limits and extra usage. It is refreshed less
    often.
  - Usage only grows within a window, so the highest value reported for the current window wins, whichever session
    reported it.
- **To change your status line**, edit your own script (e.g. `~/.claude/statusline-command.sh`) directly; the wrapper
  keeps running it. If `/statusline` or a manual edit replaces the wrapper, run `install.sh` again.
- **Warnings:** a banner and a macOS notification at 80 % (warn) and 95 % (critical) of any window, repeated at most
  once per `resource_reminder_after_s`.
- Only Claude subscriptions report these limits. With an API key there is nothing to show.
```

If the Task 1 ledger says running sessions do not pick up the changed status line, add a bullet: "Sessions started before `install.sh` start reporting after a restart."

3. In the configuration JSON example, add after `"docker_sample_interval_s": 30,`:

```json
  "limit_warn_pct": 80,
  "limit_critical_pct": 95,
```

- [ ] **Step 4: Run all tests**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: all tests PASS.

- [ ] **Step 5: Deploy and check `settings.json`**

```bash
SNAP=$(mktemp -d)
cp ~/.claude/settings.json "$SNAP/before.json"
./launchd/install.sh
diff <(python3 -m json.tool "$SNAP/before.json") <(python3 -m json.tool ~/.claude/settings.json)
cat "$HOME/Library/Application Support/claude-dashboard/statusline.json"
```

Expected:
- `OK: dashboard is running at http://127.0.0.1:7333/`;
- the diff shows only the `statusLine.command` change to `…/hook/statusline_tap.py'`;
- `statusline.json` holds the user's original command.

**The user confirms** that the status line in their sessions looks exactly as before.

- [ ] **Step 6: Real-data verification**

1. After a response in this session (and, per the Task 1 facts, in a restarted session if needed):

   ```bash
   ls "$HOME/Library/Application Support/claude-dashboard/rate_limits"
   curl -s http://127.0.0.1:7333/api/sessions | python3 -c "
   import json, sys; d = json.load(sys.stdin); l = d['limits']
   for w in l['windows']: print(w['label'], w['used_pct'], w['resets_at'], w['source'], w['as_of'], w['level'])
   print('extra', l['extra_usage']); print('as_of', l['as_of'], 'alerts', l['alerts'])
   for r in d['rows'][:8]: print(r['session_id'][:8], r['model_label'], r['effort'])"
   ```

2. **With the user:** the user runs `/usage` in Claude Code and compares every number: the 5-hour %, the weekly %, the per-model weekly %, all reset times, and extra usage used/limit with currency. Differences beyond rounding are bugs. A wrong extra-usage limit means the minor-unit scaling (`decimal_places`) is wrong. Record the result: `Task 8: Fact: /usage matches the block: …`.
3. Notification check: write `{"limit_warn_pct": 1}` into `~/Library/Application Support/claude-dashboard/config.json`, keeping any existing keys. Then run `launchctl kickstart -k gui/$(id -u)/local.claude-sessions-dashboard`. Within ~10 s the dashboard shows a warn banner and macOS shows one "Claude limits: warn" notification. **The user confirms**, then remove the key again and kickstart once more.

- [ ] **Step 7: Cache cadence**

```bash
python3 -c 'import json,os,datetime; c=json.load(open(os.path.expanduser("~/.claude.json")))["cachedUsageUtilization"]; print(datetime.datetime.fromtimestamp(c["fetchedAtMs"]/1000))'
```

Record `Task 8: Fact: cache fetchedAtMs advanced from <Task 1 time> to <now> (N refreshes observed)`.

- [ ] **Step 8: Commit**

```bash
git add launchd README.md docs/superpowers/plans/2026-10-14-claude-limits-and-session-model.md
git commit -m "Install the status line tap and document usage limits and session model

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Ledger

Facts recorded during execution (`Task N: Fact: …`) and rulings applied when a fact differs from the spec (`Task N: Ruling: …`).

- (empty)
