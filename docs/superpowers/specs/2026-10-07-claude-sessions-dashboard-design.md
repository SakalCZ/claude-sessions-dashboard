# Claude Sessions Dashboard — design

Date: 2026-10-07
Status: approved in brainstorming, awaiting spec review

## 1. Purpose

The user runs Claude Code in parallel in many directories under `~/Documents/Development`
(e.g. `acme/shop`, `acme/shop_2`, `acme/shop_local/shop`, worktrees, `budget`,
`client/website`) and loses track of which issue is being handled in which session.
They repeatedly ask Claude itself ("which directory was I working on PROJ-… in").

A browser dashboard shows **one row = one session** with its directory, issue, branch,
last activity and live status, and lets the user resume a session with one click (`claude --resume`).

### Success criteria

- I type "563" into the search and within a second see in which directory and which session PROJ-563 was handled.
- From a row I can copy or directly run the correct resume command (correct `cwd` + `sessionId`).
- I can see which sessions are running right now (busy / idle).
- For each session I can set a status and a short note. Done and archived sessions are hidden by default.

### Out of scope (YAGNI)

- Switching to an already open iTerm2 tab of a running session.
- Reading assistant response content, token and cost statistics.
- Access from anywhere other than localhost, authentication, multiple users.
- Jira API integration (issue status etc.). Jira keys are only links.

## 2. Data sources (verified on real data 2026-10-07)

| Source | Content |
|---|---|
| `~/.claude/projects/<enc-cwd>/<sessionId>.jsonl` | Session transcript, one JSON record per line. Currently 46 files, ~205 MB total, largest ~17 MB. |
| `~/.claude/projects/<enc-cwd>/<sessionId>/` | Subfolder with subagents etc. **Ignored.** |
| `~/.claude/sessions/<pid>.json` | Running instance: `pid`, `sessionId`, `cwd`, `status` (`busy`/`idle`), `procStart`, `name`, `updatedAt`. |

`<enc-cwd>` = `cwd` with every character outside `[A-Za-z0-9]` replaced by `-`
(`/Users/me/…/acme/shop_2` → `-Users-me-…-acme-shop-2`). The encoding is lossy, so the
real path is taken from the record contents, not from the folder name.

Relevant record types in the JSONL:

- `user` / `assistant`: have `uuid`, `parentUuid`, `timestamp`, `cwd`, `gitBranch`, `isSidechain`, `isMeta` and `message.content` (a string or a list of blocks).
- `custom-title` (`customTitle`) and `agent-name` (`agentName`): session title.
- `last-prompt` (`lastPrompt`).
- Other types (`attachment`, `mode`, `bridge-session`, `artifact-*`, …) are ignored by the parser.

## 3. Architecture

```
agent-orgestrator/
├── server.py                 # HTTP server (stdlib http.server, ThreadingHTTPServer), routing, API
├── sessions.py               # parsing ~/.claude → list of rows; pure logic with no HTTP I/O
├── live.py                   # detection of running instances from ~/.claude/sessions
├── notes.py                  # reading and atomic writing of notes
├── iterm.py                  # opening a new iTerm2 tab via osascript
├── static/
│   ├── index.html            # markup + CSS
│   ├── filter.js             # pure functions: filtering, grouping, relative time (tested in Node)
│   └── app.js                # rendering and actions (DOM API only, no innerHTML)
├── launchd/
│   ├── install.sh            # deployment + LaunchAgent registration
│   └── uninstall.sh
├── tests/
│   ├── helpers.py            # generates a fake ~/.claude (JSONL + pid files) in a temp directory
│   ├── js/test_filter.js     # filter.js tests (node + assert, no dependencies)
│   └── test_*.py             # unittest (stdlib)
└── docs/superpowers/specs/   # this document
```

- **Language:** Python 3 (on this machine `/usr/local/bin/python3`, 3.14), standard library only, no dependencies.
- **Server:** `ThreadingHTTPServer`, bound **exclusively to `127.0.0.1`**, default port **7333**. Can be overridden
  with `--port` or env `CLAUDE_DASHBOARD_PORT`.
- **Paths are configurable** (for tests): `--claude-dir` (default `~/.claude`) and `--data-dir` (default
  `~/Library/Application Support/claude-dashboard`).
- **Language of the UI and docs:** everything in the repository, including UI texts, is in English. UI texts are in English.

### Deployment via launchd (change from section 1 of the brainstorming)

macOS (TCC) protects `~/Documents`, and a process started from launchd may get `Operation not permitted` there.
Therefore:

- `install.sh` copies the application (`*.py`, `static/`) to `~/Library/Application Support/claude-dashboard/app/`
  and the LaunchAgent runs this copy.
- Notes are stored in `~/Library/Application Support/claude-dashboard/notes.json`, i.e. outside the repo and outside `~/Documents`.
- After a code change, `install.sh` is run again: it copies the files and restarts the agent.
- During development the server can be run directly from the repo (`python3 server.py --port 7334`).

LaunchAgent:

- Label `local.claude-sessions-dashboard`, plist in `~/Library/LaunchAgents/`.
- `RunAtLoad=true`, `KeepAlive=true`.
- Log to `~/Library/Logs/claude-dashboard.log`.
- Registration via `launchctl bootstrap gui/$UID`, uninstall via `launchctl bootout`.
- After starting, `install.sh` verifies that `curl -s http://127.0.0.1:7333/api/health` responds.

## 4. Session parsing (sessions.py)

Input: one `.jsonl` file. Output: a `Session` record. Parsed line by line. A line that is not valid
JSON (e.g. one still being written) is skipped.

### 4.1 Derived fields

| Field | Rule |
|---|---|
| `session_id` | File name without `.jsonl`. |
| `project_dir` | Name of the parent folder in `projects/`. |
| `cwd` | The first `cwd` from the records that satisfies `encode(cwd) == project_dir`. If none matches, `warnings += ["cwd-mismatch"]` is set and the first `cwd` found is used. With no `cwd` at all: `warnings += ["no-cwd"]` and `cwd = None` (resume is not possible). |
| `repo` / `worktree` | If `cwd` contains `/.claude/worktrees/<X>`: `repo` = the part before `/.claude/worktrees`, `worktree` = `<X>`. Otherwise `repo = cwd`, `worktree = None`. |
| `display_dir` | `repo` relative to `~/Documents/Development` (e.g. `acme/shop_2`); outside this root, the full path with `~`. |
| `prompts` | Real user prompts (rules in 4.2), chronologically. |
| `prompt_count` | `len(prompts)` |
| `first_ts` / `last_ts` | Min and max `timestamp` over `user`/`assistant` records, compared by value, not by order in the file. Fallback for `last_ts`: file mtime. |
| `branches` | Unique `gitBranch` values ordered by **last** occurrence: on each occurrence the value moves to the end, so the last element is the current branch. `branch` = the last element different from `HEAD`, otherwise `HEAD`. |
| `title` | The last `custom-title.customTitle`, otherwise the last `agent-name.agentName`, otherwise `None`. |
| `last_prompt` | `last-prompt.lastPrompt` (the last one). |
| `root_uuid` | `uuid` of the first `user`/`assistant` record with `isSidechain != true`. Used to detect forks. |
| `jira_keys` | See 4.3. |
| `jira_key` | Primary key, see 4.3. |
| `topic` | See 4.4. |
| `is_stub` | `prompt_count == 0` |

### 4.2 What is a "real prompt"

A record with `type == "user"`, none of the flags `isMeta`, `isSidechain`, `isCompactSummary`, `isVisibleInTranscriptOnly`, and additionally:

- `message.content` is a string, or a list that contains at least one block with `type == "text"` and no block with `type == "tool_result"`. For a list, the block texts are joined.
- The text (after `strip()`) does not start with a system tag `<name…` (regex `^<([a-z][a-z0-9_-]*)[\s>]`).
  The exception is `<pasted_content`, because that is real pasted content from the user. This skips
  `<command-name>`, `<local-command-*>`, `<task-notification>` (428× in the data), `<artifact-content-…>`,
  `<system-reminder>` etc.
- The text does not start with `Caveat:` or `[Request interrupted`. Prompts such as `[Image #3] …` or pasted terminal
  output `[me@db1 ~]$ …` are real and are kept.
- The text is not empty.

For display, a `<pasted_content …>X</pasted_content>` block is replaced with `[pasted: <first 60 characters of X>]`.
Whitespace is collapsed only at display time. At most 2000 characters of each prompt are stored. Jira URLs are searched
in the original text, including pasted content.

### 4.3 Jira keys

Key regex: `[A-Z][A-Z0-9]{1,9}-\d+`. Keys are taken **only from reliable sources**:

1. **Branches:** a key found in any element of `branches`.
2. **URLs in prompts:** `https?://([a-z0-9-]+)\.atlassian\.net/browse/(KEY)`. A prefix → host mapping is recorded at the same time (e.g. `PROJ` → `acme.atlassian.net`, `SD` → `example.atlassian.net`).
3. **Title:** a key in `title`. A space instead of a hyphen is also allowed (`PROJ 548` → `PROJ-548`).

Free text of prompts is **not searched**, because it would bring noise such as `P1-1` or `PSR-4`.

- `jira_keys`: all keys found, unique, in order of occurrence.
- `jira_key` (primary):
  1. the key from the last branch in `branches` that contains a key (after working on `me/feature/PROJ-512-…` the session may have switched back to `master`),
  2. otherwise the key from the last URL in prompts,
  3. otherwise the key from the title,
  4. otherwise `None`.

The `jira_hosts` map (prefix → host) is assembled across all sessions. The client builds the link
`https://<host>/browse/<KEY>` from it. A prefix without a known host is shown as text without a link.

### 4.4 Title, suspect title, topic

The title is carried over between sessions in the data. E.g. "country variables refactoring" appears on 6 sessions in
`shop_2` with different branches. A title is therefore **suspect** (`title_suspect = true`) when at least one
of these holds:

- (a) the title contains a Jira key that is not among the session's keys from branches or URLs, and the session has some such keys;
- (b) another session (a different chain, see 4.5) with the same `project_dir` and a different `jira_key` has the same title (after normalization: lowercase, strip), and both `jira_key` values are not `None`.

`topic` (a short description for the row):

1. `title`, if it is not suspect. A leading Jira URL or key is removed from it, because the key is displayed separately.
2. Otherwise the beginning of the first real prompt (one line, max. 140 characters).
3. Otherwise `last_prompt`.
4. Otherwise `"(no description)"`.

A suspect title is shown in the UI as secondary and grey, labeled "title (possibly inherited)".

### 4.5 Forks and copies

Files with the same `root_uuid` (excluding `None`) form a chain. Verified: `b0372375` is a copy of the beginning of
`cd6e5aad`, with the same first record. In a chain, the main row is the file with the newest `last_ts`;
the others go to `older_copies` (id, `last_ts`, `prompt_count`).

Notes are inherited: when the main row has no note of its own and some older copy does, the copy's note is shown
and on save it is written under the main row's id.

### 4.6 Cache

Module cache `{path: (mtime_ns, size, Session)}`. On every `GET /api/sessions`,
`projects/*/*.jsonl` is walked via `os.scandir` and only files with a changed `(mtime_ns, size)` are re-parsed.
Deleted files are evicted from the cache. Derived links between sessions (4.4 b, 4.5, `jira_hosts`) are
always recomputed, because they are cheap.

## 5. Live status (live.py)

For each `~/.claude/sessions/*.json`:

1. The JSON is loaded. An invalid file is skipped.
2. A check that the process is alive, by comparing `procStart` with the output of
   `LC_ALL=C TZ=UTC ps -o lstart= -p <pid>` (after `strip()`). A match means it is running. This filters out
   dead pid files and reused pids. Verified: the format matches only with `LC_ALL=C TZ=UTC`, because
   without them `ps` prints in Czech in local time.
3. The result is a map `sessionId → {status, pid, name, updated_at}`.

`ps` is called once for all pids (`ps -o pid=,lstart= -p 1,2,3`), not separately for each.

## 6. Notes (notes.py)

File `<data-dir>/notes.json`:

```json
{
  "version": 1,
  "notes": {
    "<sessionId>": {"status": "active", "note": "waiting for review from JH", "updated_at": "2026-10-07T12:00:00Z"}
  }
}
```

- `status`: one of `active`, `waiting`, `done`, `archived`. A missing entry means "no status".
- `note`: at most 500 characters, a single line (`\n` is replaced with a space).
- Writes are atomic (`tempfile` in the same directory, `fsync`, `os.replace`) and under a `threading.Lock`.
- A missing or invalid file means empty notes. An invalid file is renamed to `notes.json.corrupt-<ts>` before being overwritten.
- Notes for sessions that no longer exist (Claude Code deletes old transcripts, `cleanupPeriodDays`) remain in the file and are simply not displayed.

## 7. HTTP API (server.py)

| Method | Path | Description |
|---|---|---|
| GET | `/`, `/index.html`, `/app.js`, `/filter.js` | static files from `static/` (fixed whitelist) |
| GET | `/api/health` | `{"ok": true}` |
| GET | `/api/sessions` | `{generated_at, jira_hosts, rows: [Row…]}` |
| POST | `/api/notes/<sessionId>` | Body `{"status"?: str\|null, "note"?: str}` → the saved record. `status: null` clears the status. |
| POST | `/api/open/<sessionId>` | Opens resume in iTerm2 → 200 `{"ok": true}`. osascript failure → 502 `{"ok": false, "error": "…"}`. Unknown or nonexistent directory (`no-cwd`, `cwd-missing`) → 409 without launching. |

`Row` (JSON):

```
session_id, cwd, repo, worktree, display_dir, branch, branches[], jira_key, jira_keys[],
topic, title, title_suspect, first_ts, last_ts, prompt_count, is_stub,
recent_prompts[]   (last 5, newest last),
first_prompt, search_text (all prompts, each max. 300 characters, max. 60,000 in total),
older_copies[], warnings[],
live: {status, pid} | null,
note: {status, note, updated_at} | null,
resume_cmd         ("cd '<cwd>' && claude --resume <id>", quoted via shlex.quote)
```

Validation and security:

- `sessionId` in the path must match `^[0-9a-f-]{36}$` and exist in the index, otherwise 404.
- **All** requests (including GET) require a `Host` header of `127.0.0.1:<port>` or `localhost:<port>`,
  otherwise 403. This defends against DNS rebinding, where a foreign website could otherwise read `/api/sessions` including the contents
  of transcripts.
- POST endpoints additionally require `Content-Type: application/json`. `Origin`, if present, must be
  `http://127.0.0.1:<port>` or `http://localhost:<port>`. Otherwise 403.
- The POST body is limited to 4 KB.
- Static files are served only from the whitelist (`/` → `index.html`), no general file server.

## 8. Opening in iTerm2 (iterm.py)

- The command `cd <shlex.quote(cwd)> && claude --resume <session_id>` is assembled **on the server** from the index.
  The client sends only `sessionId`.
- It is run via `subprocess.run(["osascript", "-e", SCRIPT, cmd], timeout=10)`. The command is passed as
  `argv`, so nothing is interpolated into the AppleScript source:

```applescript
on run argv
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
end run
```

- On first use macOS asks for the permission "python3 wants to control iTerm2" (Automation). If the
  user denies it, osascript returns error -1743. The UI shows a message pointing to Settings → Privacy →
  Automation and offers to copy the command.
- When the session is running right now (`live != null`), the client asks via `confirm()` before opening, because
  a second instance would be created.

## 9. UI (static/index.html)

Dark, dense, table-like layout in the system monospace/sans font. A working tool, not a landing page.
Vanilla JS only, no external libraries.

**Top bar:**

- Full text: case-insensitive, multiple words = all must match. Searches in `jira_keys`, `topic`, `title`,
  `branches`, `display_dir`, `worktree`, `cwd`, prompts (`search_text`), the note and `session_id`.
  **With a non-empty search, status hiding and stub hiding do not apply**, so "563" finds
  even a session marked as done. The directory filter and "running only" still apply.
- Directory chips (`display_dir`, multi-select, none selected = all).
- Status filter: `done` and `archived` are hidden by default.
- Toggles: "running only", "show empty" (stubs are hidden by default).
- Grouping: by directory (default), by issue (`jira_key`, sessions without a key go into the group "no issue"), or none.
- Counter "showing X of Y".

**Row** (sorted within a group by `last_ts`, newest on top):

- Live status indicator: 🟢 busy / 🟡 idle / nothing.
- `jira_key` as a link (if the host is known) and `topic`. Below it `branch` (+ worktree), the prompt count and 1–2 recent prompts (truncated to ~120 characters).
- `display_dir`, relative time ("5 min ago", tooltip with the absolute time).
- Actions: **⧉ Copy** (`navigator.clipboard`, fallback `execCommand('copy')`), **▶ Open** (POST /api/open), a status dropdown and a note field (saved on `blur` and `Enter`).
- Clicking a row expands it: `session_id`, full `cwd`, all branches and Jira keys, the last 5 prompts, the suspect title (grey), `older_copies`, `warnings`.

**Refreshing:** `GET /api/sessions` is called every 10 s. Re-rendering preserves expanded rows and
does not overwrite a note field that has focus. Filters, grouping and the full-text query are saved to `localStorage`
(reads and writes in try/catch).

**Errors:** when the API does not respond, a "server is not responding" bar is shown and the last data stay
displayed. A failed note save or open shows a toast with the error.

## 10. Error handling (summary)

| Situation | Behavior |
|---|---|
| Invalid JSON line | skip |
| Unreadable file (OSError) | row with `warnings=["unreadable"]`, the others keep working |
| `cwd` does not match the folder encoding | `warnings=["cwd-mismatch"]`, see 4.1 |
| `ps` fails | all sessions without live status, the server keeps running |
| Corrupted `notes.json` | backup + empty notes |
| osascript fails or times out | `{"ok": false, "error": …}`, toast in the UI |
| Session directory no longer exists (deleted worktree) | `warnings=["cwd-missing"]`, ⚠ in the row, "Open" returns 409 with a message. When `stat` fails with something other than `FileNotFoundError` (e.g. TCC), the warning is not set. |
| Port in use | the server exits with a clear error in the log (launchd will restart it; resolved by changing the port) |

## 11. Testing

`python3 -m unittest discover tests`, stdlib only.

Fixtures are generated by `tests/helpers.py` into a temp directory (JSONL and pid files). The tests cover:

- **sessions.py:**
  - real prompts vs. meta, commands and tool_result,
  - list content with text,
  - `cwd` and encoding validation,
  - worktree → repo,
  - branches and `HEAD`,
  - Jira keys only from branches, URLs and title (the `P1-1` noise in text is ignored),
  - primary key, `jira_hosts`,
  - suspect title (a) and (b),
  - topic,
  - fork via `root_uuid`,
  - stub,
  - corrupted line,
  - cache (without an mtime change the file is not read again).
- **live.py:** `procStart` match and mismatch (`ps` output is mocked), invalid pid file.
- **notes.py:** atomic write, status and length validation, corrupted file → backup.
- **server.py:** API through a real server on a random port with fixtures:
  - `/api/sessions` shape,
  - POST notes OK,
  - 403 for a wrong `Origin` / `Host` / content-type,
  - 404 for an unknown id.
  - `iterm.py` is mocked.
- **iterm.py:** `argv` assembly and quoting of a `cwd` with a space and an apostrophe (osascript is mocked).
- **filter.js:** `node tests/js/test_filter.js` (also run from unittest if `node` is available).
  Covers search vs. hidden statuses, grouping and relative time. A static test ensures `app.js`
  does not use `innerHTML` and similar APIs, so text from transcripts cannot be injected as HTML.

**Verification on real data:**

- The server run against the real `~/.claude` returns sessions from shop, shop_2 and shop_local.
- PROJ-563 is under `acme/shop` with branch `me/bugfix/PROJ-563-duplicate-orders`.
- Running sessions have a live status.
- `b0372375` is collapsed under `cd6e5aad`.
- Opening in iTerm2 is tested manually on one non-running session.

## 12. Open points

None.
