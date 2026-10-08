# Attention Queue and Resource Monitor — design

Date: 2026-10-08
Status: approved in brainstorming, awaiting spec review
Builds on: `2026-10-07-claude-sessions-dashboard-design.md` (the dashboard this extends)

## 1. Purpose

The user runs Claude Code from fixed directories, because those directories are configured for work on remote
servers. Only one directory tree (`acme/shop_local`, with up to ~10 git worktrees) is used for local development.
With several sessions doing local work, macOS runs out of resources and becomes unusable.

Two additions to the dashboard:

1. **Attention queue ("Waiting for you").** A list of sessions in which Claude has finished a turn or is blocked on
   the user (a permission prompt, a question, an error) and the user has not reacted yet. Oldest first, one click to
   switch to the session's existing iTerm2 tab.
2. **Resource monitor.** RAM and CPU of every running session (Claude and everything it spawned), Docker usage per
   compose project, the overall memory/CPU state of the Mac, and the top apps. It warns in the dashboard and via a
   macOS notification when the Mac is under pressure.

### Success criteria

- Within ~10 s of Claude finishing a turn or asking for a permission, the session appears in the queue with the reason
  and how long it has been waiting.
- "Switch" brings the session's existing iTerm2 tab to the front. It never starts a second instance.
- A session marked "Seen" leaves the queue until its next finished turn.
- When the kernel reports memory pressure `warn` or `critical`, or the CPU stays overloaded, the dashboard shows a
  banner and macOS shows one notification (repeated at most once per hour while the state lasts).
- The system bar answers "what is eating my Mac right now" (e.g. Chrome 4.3 GB, claude 1.3 GB, a Docker stack).

### Out of scope (YAGNI)

- Killing processes, stopping containers or closing sessions from the dashboard (the user chose "see + warn").
- Starting new sessions or managing worktrees (that is the workflow of tools like viktomas/task; the user's
  directories are fixed).
- Remote server load (sessions working against remote servers cost almost nothing locally).
- Historical charts; only the current state is shown.

## 2. Verified facts (2026-10-07/08, on the user's machine)

- An idle Claude process uses 100–215 MB RSS and ~0 % CPU. The busy one in this session used 360 MB.
- Real memory state at design time: kernel pressure level `2` (warn), 38 % free, swap 9.3 GB used of 10.2 GB, load
  5.3 on 8 cores. Top consumers by RSS: Chrome renderers 4.3 GB, claude 1.3 GB, PhpStorm 0.26 GB.
- Docker Desktop: 8 CPUs, 7.75 GB memory limit. The VM's host RSS (~390 MB) does not reflect guest usage; per-container
  numbers must come from `docker stats`.
- Compose containers carry `com.docker.compose.project` and `com.docker.compose.project.working_dir` labels.
- The local-development tree uses one shared compose project (`shared-stack`) whose `working_dir` lives in a sibling
  repository, not in the worktrees. Path matching alone cannot attribute it, so a config mapping is needed.
- Commands available without extra tools: `ps -axo pid=,ppid=,rss=,%cpu=,tty=,comm=` (`comm` is the full executable
  path), `sysctl -n kern.memorystatus_vm_pressure_level` (1 normal, 2 warn, 4 critical), `sysctl -n vm.swapusage`,
  `memory_pressure -Q` ("System-wide memory free percentage: N%"), `os.getloadavg()`, `sysctl -n hw.ncpu`.
- The user's `~/.claude/settings.json` already has command hooks for `Notification`, `PermissionRequest`,
  `PostToolUse`, `PreToolUse`, `SessionEnd`, `SessionStart`, `Stop`, `StopFailure`, `SubagentStop` and
  `UserPromptSubmit` (all running iTerm2's `cc-status`, which reports to iTerm2 only and stores nothing readable).
- `~/.claude/sessions/<pid>.json` has `status` (`busy`/`idle`) and `statusUpdatedAt` (epoch ms). It is undocumented,
  so it is used only for the fallback estimate.

### To verify before building on it (plan Task 1, the spike)

Claude Code's hook documentation, as summarized by a docs agent, could not be fully confirmed. Before the hook parser
is written, a temporary capture hook records real payloads for all eight events and confirms:

- the exact stdin fields: `session_id`, `cwd`, `hook_event_name`, `last_assistant_message` (Stop),
  `notification_type`/`message` (Notification), `tool_name` (PermissionRequest), the error field (StopFailure), the
  end reason (SessionEnd), `source` (SessionStart);
- the process ancestry: whether walking `ppid` from the hook reaches the Claude process, and its tty;
- whether `"async": true` is honored, i.e. the hook never delays Claude, and whether `timeout` is in seconds
  (the design assumes seconds: `"timeout": 5`);
- whether already-running sessions pick up hooks added to `settings.json`, or only new ones;
- that iTerm2's AppleScript can find and select a session by `tty`, and what Automation permission it needs;
- that Claude Code accepts the settings with `async`/`timeout` (no settings error) and the existing `cc-status` hooks
  keep working;
- what fires when the user presses Esc during a turn and during a permission prompt (the docs say `Stop` does not run
  on an interrupt), whether `Notification` `idle_prompt` follows ~60 s later, and what the pid file `status` shows
  during and after a permission prompt.

The spike's findings are written into the plan's ledger. If a fact differs, the smallest change that keeps this
spec's behavior is made and recorded.

## 3. Architecture

New and changed files:

```
agent-orgestrator/
├── hook/claude_hook.py   NEW  run by Claude Code hooks; writes agents/<session_id>.json
├── agents.py             NEW  hook state + fallback estimate + seen marks → attention per session, the queue
├── monitor.py            NEW  background sampler: process trees, system memory/CPU, top apps, Docker
├── notifier.py           NEW  notification rules (queue + resources), rate limiting, osascript delivery
├── settings_merge.py     NEW  add/remove our hook entries in ~/.claude/settings.json (used by install/uninstall)
├── config.py             NEW  load <data-dir>/config.json with defaults
├── iterm.py              CHG  + focus_tty(tty)
├── server.py             CHG  starts the monitor thread; rows get attention/resources; payload gets queue/system;
│                              POST /api/seen/<id>, POST /api/focus/<id>, GET /api/system
├── static/{index.html,app.js,filter.js}  CHG  system bar, alert banner, queue panel, row dot/resources, Switch
└── launchd/{install.sh,uninstall.sh}     CHG  deploy hook/, merge/unmerge settings hooks
```

Data flow:

```
Claude Code ──hook──▶ <data-dir>/agents/<id>.json ┐
~/.claude/sessions/*.json + transcripts ─────────┼─▶ agents.py ─┐
<data-dir>/seen.json ────────────────────────────┘              ├─▶ /api/sessions (+queue, +system) ─▶ UI
ps / sysctl / memory_pressure / docker ─▶ monitor.py (thread) ───┤
                                                                 └─▶ notifier.py ─▶ macOS notification
```

The monitor and notifier run in a background thread started by `server.main()`, so notifications work with the
browser closed. Request handlers only read the latest snapshot; they never run `ps` or `docker` themselves.

## 4. Hook (`hook/claude_hook.py`)

- Invoked by Claude Code for eight events: `SessionStart`, `UserPromptSubmit`, `PermissionRequest`, `Notification`,
  `PostToolUse`, `Stop`, `StopFailure`, `SessionEnd`.
- Standard library only. **Never writes to stdout or stderr** (the stdout of some hooks is injected into Claude's
  context). Catches every exception and always exits with code 0. Target runtime under 50 ms.
- Reads the JSON payload from stdin and computes the next state from the event (table below). The new state is written
  atomically (temp file + `os.replace`) to `<data-dir>/agents/<session_id>.json`. The data dir defaults to
  `~/Library/Application Support/claude-dashboard`, overridable with env `CLAUDE_DASHBOARD_DATA_DIR` (used by tests).
- `session_id` must match `^[0-9a-f-]{36}$`; otherwise the hook does nothing (no path traversal via the file name).

| Event | New state | `note` |
|---|---|---|
| `SessionStart` | `idle` | — |
| `UserPromptSubmit` | `working` | — |
| `PermissionRequest` | `permission` | tool name |
| `Notification`, type `permission_prompt` | `permission` | message |
| `Notification`, type `elicitation_dialog` / `agent_needs_input` | `question` | message |
| `Notification`, type `idle_prompt`, current state `working` | `idle` (an interrupted turn: `Stop` does not run on Esc) | — |
| `Notification`, any other type, or `idle_prompt` in another state | unchanged (the spike may refine `idle_prompt` during `permission`/`question`) | — |
| `PostToolUse` | `working`, only if the current state is `permission` or `question`; otherwise unchanged | — |
| `Stop` | `waiting` | first non-empty line of the last assistant message, max 200 chars |
| `StopFailure` | `failed` | error type / message, max 200 chars |
| `SessionEnd` | `ended` | — |

State file:

```json
{
  "version": 1,
  "session_id": "<uuid>",
  "state": "waiting",
  "since": "2026-10-08T09:12:03Z",
  "event": "Stop",
  "note": "Tests pass; ready for review.",
  "cwd": "/Users/me/Documents/Development/acme/shop",
  "claude_pid": 55928,
  "tty": "ttys003",
  "updated_at": "2026-10-08T09:12:03Z"
}
```

- `since` changes only when the state changes. Repeating the same state (e.g. `PermissionRequest` followed by a
  `permission_prompt` notification) keeps the earlier `since`.
- `claude_pid` comes from walking the parent chain from the hook (up to 6 levels) to the first ancestor that has a
  `~/.claude/sessions/<pid>.json` file. `tty` is that process's tty from `ps`. Both are `null` when not found.
- `PostToolUse` fires for every tool call, so the hook process starts often. Because it runs `async`, this costs
  CPU time but never delays Claude; it is accepted.
- Registration in `settings.json` (by `settings_merge.py`, see §9): one new matcher group per event, containing
  `{"type": "command", "command": "<python3> '<app-dir>/hook/claude_hook.py'", "async": true, "timeout": 5}`.

## 5. Attention state and the queue (`agents.py`)

For every dashboard row (the head of a fork chain), `attention` is computed:

1. **Hook data:** if `agents/<session_id>.json` exists and is valid, its `state`, `since` and `note` are used
   (`source: "hook"`).
   Exception: if the hook state is `working` but the session is live with pid status `idle` and `statusUpdatedAt`
   more than 5 s after the hook's `since`, the state is `idle`. The turn was interrupted, and `Stop` does not fire on
   Esc.
2. **Fallback estimate:** otherwise, if the session is live (live detection, §5 of the base design), the pid file
   status maps `idle` → `waiting` and `busy` → `working`, with `since` from `statusUpdatedAt` (`source: "estimate"`).
   A permission prompt cannot be detected this way.
3. **Not live:** if the process is not live (dead pid, no pid file), the state is `ended`, regardless of hook data
   (covers crashes without `SessionEnd`).

Queue membership and order:

- A row is in the queue when its state is `permission`, `question`, `failed` or `waiting`, and it has not been marked
  seen since: `seen_at < since` (or never seen).
- Group `blocking` holds `permission`, `question` and `failed`. Group `done` holds `waiting`. Blocking comes first;
  within each group the oldest `since` comes first.
- "Seen" (`POST /api/seen/<id>`) stores `seen_at = now` in `<data-dir>/seen.json` (atomic write, same pattern as
  notes). The session re-enters the queue on its next state change into a queue state, because `since` moves past
  `seen_at`.

Row field:

```
attention: {state, since, note, source: "hook" | "estimate", in_queue: bool, group: "blocking" | "done" | null}
```

The payload gains `queue: [session_id, …]` in display order. The page title becomes `(N) Claude Sessions` when
N > 0.

## 6. Resource monitor (`monitor.py`)

A background thread samples on fixed intervals (configurable, §8) and keeps the latest snapshot in memory behind a
lock.

**Process sample (every 10 s):** one call to `ps -axo pid=,ppid=,rss=,%cpu=,tty=,comm=` (C locale).

- **Per live Claude pid:** the sum of RSS and %CPU over the pid and all its descendants, the number of processes, and
  the top 5 descendants by RSS (pid, short name, RSS, CPU). RSS sums can double-count shared pages; the UI labels the
  value as RSS.
- **Top apps:** processes grouped by app. A path containing `.app/` is named after its **outermost** bundle (all
  "Google Chrome Helper (Renderer)" processes count as "Google Chrome"); other processes use the executable's base
  name. The top 5 by RSS are kept, with the CPU sum.
- **tty of each live Claude pid**, used by Switch when the hook did not record it.

**System sample (every 10 s):**

- `sysctl -n kern.memorystatus_vm_pressure_level` maps 1/2/4 to `normal`/`warn`/`critical`.
- `memory_pressure -Q` gives the free %. If the command is missing, the value is `null`.
- `sysctl -n vm.swapusage` gives swap used and total in MB.
- `os.getloadavg()[0]` and `sysctl -n hw.ncpu` give load and core count.

**Docker sample (every 30 s, own thread, 10 s timeout per command):**

- `docker ps --format '{{json .}}'` gives container names and labels. `docker stats --no-stream --format
  '{{json .}}'` gives memory and CPU.
- Containers are aggregated per compose project: RSS sum, CPU sum, container count, `working_dir`.
- If the CLI is missing, the daemon is not running, or a command times out, the result is `docker: {running: false}`.
  The previous successful sample is not shown as current.

**Attributing a Docker project to rows:**

- A row gets the badge `stack <project>` when the project's `working_dir` is inside the row's repository, or the
  repository is inside `working_dir`.
- It also gets the badge when `config.docker_project_dirs[project]` is a prefix of the row's `display_dir` (relative
  to the dev root) or of its `cwd` (absolute).
- Stack memory is **not** added to row numbers, so a shared stack is not counted once per session. It appears once in
  the system bar.

**Alerts:**

| Alert | Raised when | Level |
|---|---|---|
| `memory` | pressure level `warn` | warn |
| `memory` | pressure level `critical` | critical |
| `cpu` | `load1 > ncpu × cpu_load_factor` continuously for ≥ `cpu_sustain_s` | warn |

Swap is shown but does not raise alerts: on macOS it grows and stays high for a long time, so an alert would never
clear.

## 7. Notifications (`notifier.py`)

Notifications are delivered with `osascript`. The message and title are passed as argv to a fixed script:

```applescript
on run argv
  display notification (item 1 of argv) with title (item 2 of argv)
end run
```

Rules, evaluated after each process sample (time is injected for tests):

- **Queue:** a `permission`, `question` or `failed` episode lasting ≥ `permission_notify_after_s` (60 s), or a
  `waiting` episode lasting ≥ `waiting_notify_after_s` (600 s), notifies **once per episode**. An episode is
  identified by `(session_id, since)`. A seen session never notifies. Title "Claude is waiting", message
  `<jira key or topic> · <dir> · <state> <minutes> min`.
- **Resources:** an alert notifies when its level rises (none → warn, warn → critical). If the level stays raised, it
  re-notifies after `resource_reminder_after_s` (3600 s). When the alert clears, nothing is sent and the next rise
  notifies again.
- Entries with `source: "estimate"` never notify; an estimate is too unreliable to interrupt the user.
- On the notifier's first check after the server starts, queue episodes that are already past their threshold are
  marked as notified without sending. Otherwise every install or login would send one notification per old idle
  session.
- `notifications_enabled: false` in config disables delivery; the rules still run, so the UI is unaffected.
- A delivery failure is logged to stderr (the LaunchAgent log) and otherwise ignored.

Notifier state lives in memory only; the start-up rule above prevents a burst after a restart.

## 8. Configuration (`config.py`)

`<data-dir>/config.json` is optional. Missing keys take defaults. Unknown keys are ignored. An invalid file is logged
and the defaults are used.

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
  "docker_project_dirs": {}
}
```

`docker_project_dirs` example (documented in README, not shipped): `{"shared-stack": "acme/shop_local"}`. The user's real
mapping goes into their local config file, never into the repository.

## 9. Settings merge and install (`settings_merge.py`, `launchd/*.sh`)

`python3 settings_merge.py install <settings.json> <hook-command>` and `… uninstall <settings.json>`:

- Our entries are recognized by the substring `claude-dashboard/app/hook/claude_hook.py` in `command`.
- **Install:**
  - If the file is not valid JSON, or `hooks` is not an object, abort with exit code 1 and change nothing.
  - Write a backup `settings.json.bak-claude-dashboard-<YYYYmmdd-HHMMSS>` next to it.
  - For each of the eight events, remove any existing group that contains our command, then append one new group.
    Other groups (e.g. `cc-status`) are untouched and keep their order.
  - Write atomically, with `indent=2` and `ensure_ascii=False`, preserving key order. Whitespace may differ from the
    original. Running install twice produces the same file.
- **Uninstall:** remove only groups containing our command; an event left empty is removed. A backup is written first.
- A missing `settings.json` on install creates `{"hooks": {…}}`. On uninstall it is a no-op.
- `install.sh` copies `hook/` and the new modules to the app dir, runs the merge with
  `"<python3>" '<app-dir>/hook/claude_hook.py'`, then restarts the agent as before. `uninstall.sh` runs the
  uninstall merge before removing the app dir.

## 10. HTTP API changes (`server.py`)

| Method | Path | Behavior |
|---|---|---|
| GET | `/api/sessions` | rows gain `attention`, `resources` (`{rss_mb, cpu, processes, top: [...], stack: str \| null}` or `null` when not live), `tty`; payload gains `queue` and `system` |
| GET | `/api/system` | the `system` object alone |
| POST | `/api/seen/<id>` | `{ok: true, seen_at}`; 404 for unknown id |
| POST | `/api/focus/<id>` | Focus the session's iTerm2 tab by tty. 200 `{ok: true}`. 409 if the session is not live or has no tty. 404 `{ok: false, error: "No iTerm2 tab found for <tty>"}` when no tab matches. 502 on an osascript error. |

`system` object:

```
{sampled_at, memory: {pressure: "normal"|"warn"|"critical"|null, free_pct, swap_used_mb, swap_total_mb},
 cpu: {load1, ncpu}, top_apps: [{name, rss_mb, cpu}], docker: {running, sampled_at, projects: [{project,
 working_dir, rss_mb, cpu, containers}]}, alerts: [{kind, level, message, since}]}
```

POST endpoints keep the existing Host/Origin/Content-Type checks. `focus` resolves the tty on the server (hook data,
then the monitor's ps sample); the client sends only the session id.

`iterm.focus_tty(tty)` runs a fixed AppleScript with `"/dev/" + tty` passed as argv. The script iterates windows,
tabs and sessions, selects the matching window, tab and session, and activates iTerm2. It returns `ok` or `notfound`.

## 11. UI (`static/*`)

- **System bar** (below the header, one line, colored by the worst alert level):
  `RAM warn · 38% free · swap 9.3/10.2 GB │ CPU 5.3/8 │ Top: Google Chrome 4.3 GB, claude 1.3 GB, … │ Docker: <project> 0.7 GB, …`.
  A click expands it to tables of top apps and Docker projects. With `docker.running == false` it shows
  "Docker: not running".
- **Alert banner** (yellow for warn, red for critical) above the system bar while an alert is active, with its message.
- **Queue panel** "Waiting for you (N)" above the session list.
  - Two groups, "Blocking" and "Done, waiting". Each item shows a state icon (🔐 permission, ❓ question,
    ⚠ failed, ✅ waiting), the Jira key and topic, the directory, "waiting N min", the note snippet, and the buttons
    Switch, Seen and Copy.
  - Estimated states are marked "(estimate)".
  - When empty, the panel collapses to "Nobody is waiting for you".
- **Rows:**
  - The live dot is colored by attention state: green working, blue waiting, orange permission/question, red failed.
    It is hollow when the state is an estimate.
  - A new resources cell shows `312 MB · 4%`; the tooltip lists the top processes. A `stack <project>` badge appears
    when attributed.
  - For a live session the primary button is **Switch** (focus the existing tab) instead of **Open**. Open stays for
    sessions that are not live.
- **Page title:** `(N) Claude Sessions`.
- All UI text is English. Dates use `en-GB`. Rendering uses DOM APIs only, as before.

## 12. Error handling

| Situation | Behavior |
|---|---|
| Hook: invalid JSON, unknown event, bad session id, unwritable dir | does nothing, exit 0, no output |
| Corrupt `agents/<id>.json` | ignored → fallback estimate |
| `ps` fails | per-row resources `null`, system bar shows "unavailable"; the server keeps running |
| `docker` missing / slow / daemon down | `docker.running = false`; 10 s timeout; never blocks requests |
| `memory_pressure` missing | `free_pct = null` |
| osascript notification fails | logged, ignored |
| iTerm2 not running / no matching tty | focus returns 404 with a message; the UI offers Copy |
| Invalid `config.json` | logged, defaults used |
| `settings.json` invalid on install | install aborts, nothing changed, clear message |

## 13. Testing

`python3 -m unittest discover -s tests -t .` (stdlib) plus the node test of `filter.js`.

- **hook:** state transitions per event over payload fixtures captured in the spike, anonymized before they are
  committed. Covered: `since` kept on a repeated state; `PostToolUse` only clears `permission`/`question`; a bad
  session id or invalid JSON does nothing; no output on stdout/stderr; atomic write; `claude_pid`/`tty` resolution
  with an injected process table.
- **agents:**
  - hook data vs estimate vs not-live → `ended`;
  - queue membership, groups and order;
  - seen hides an entry until the next `since`;
  - a corrupt state file falls back.
- **monitor:**
  - parsers over fixture outputs (`ps` with spaces in `comm`, `.app` bundle grouping, `sysctl` pressure/swap,
    `memory_pressure -Q`, `docker ps`/`docker stats` JSON incl. `MiB`/`GiB` units);
  - process-tree sums;
  - Docker attribution by path and by config mapping;
  - CPU sustain logic with injected time.
- **notifier:**
  - queue thresholds;
  - estimates never notify;
  - no burst on the first check after start;
  - once per episode;
  - seen suppresses;
  - resource rise/reminder/clear cycle;
  - disabled flag;
  - osascript argv shape.
- **settings_merge:**
  - install is idempotent;
  - `cc-status` groups preserved in order;
  - backup written;
  - invalid JSON aborts unchanged;
  - uninstall removes only ours and drops empty events;
  - missing file.
- **server:**
  - `queue`/`system`/`attention`/`resources` in the payload;
  - `seen` and `focus` endpoints (fake focuser), including 404/409 and the security checks.
- **filter.js:** queue grouping/sorting helpers if they live there.
- **Real-data verification:**
  - after install, this and other live sessions appear with hook data after their next event;
  - Switch focuses a real iTerm2 tab (manual, with the user);
  - the system bar matches `ps`/`sysctl`;
  - a notification is delivered once.

## 14. Open points

None. The spike (§2) may refine field names and process ancestry; changes are recorded as rulings.
