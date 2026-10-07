# Claude Sessions Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local web dashboard (http://127.0.0.1:7333) that lists one row per Claude Code session from `~/.claude` (directory, Jira issue / topic, branch, recent prompts, live status, custom note) and can resume a session (copy `cd … && claude --resume …` or open it in iTerm2).

**Architecture:** Python stdlib HTTP server (`ThreadingHTTPServer`) with modules: `sessions.py` (JSONL parsing + mtime-based cache), `live.py` (running instances from `~/.claude/sessions`), `notes.py` (notes in JSON, atomic write), `iterm.py` (osascript). The frontend is static HTML + vanilla JS (`filter.js` pure functions, `app.js` DOM). Runs under launchd from a copy in `~/Library/Application Support/claude-dashboard/app/`.

**Tech Stack:** Python 3 (stdlib; 3.14 in `/usr/local/bin/python3` on this machine), unittest, vanilla JS, Node (only for the `filter.js` test, v16 on this machine), launchd, AppleScript (iTerm2).

**Spec:** `docs/superpowers/specs/2026-10-07-claude-sessions-dashboard-design.md`

## Global Constraints

- Python standard library only, no pip dependencies. The code must run on Python ≥ 3.10 (`from __future__ import annotations` in every module).
- The server binds **exclusively to `127.0.0.1`**. The default port is `7333`, overridable with `--port` or env `CLAUDE_DASHBOARD_PORT`.
- Default paths:
  - claude dir `~/.claude`,
  - data dir `~/Library/Application Support/claude-dashboard`,
  - log `~/Library/Logs/claude-dashboard.log`,
  - launchd label `local.claude-sessions-dashboard`,
  - deployed application `~/Library/Application Support/claude-dashboard/app/`.
- **Never** write to `~/.claude`, only read.
- All requests (including GET) check `Host` ∈ {`127.0.0.1:<port>`, `localhost:<port>`}. POST additionally checks `Content-Type: application/json` and `Origin` (if present).
- UI texts are in English. The frontend must not use `innerHTML`, `outerHTML`, `insertAdjacentHTML` or `document.write`.
- No external JS/CSS libraries or CDNs.
- Tests are run from the repo root: `python3 -m unittest discover -s tests -t . -v`. That also includes `node tests/js/test_filter.js` if `node` is on PATH.
- Every commit message ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Working directory: `/Users/me/Documents/Development/agent-orgestrator` (git repo, branch `master`, no remote).

## Review Focus

1. Searching for "563" when the session is marked "done", "archived" or is a stub: the user expects to find it. Search therefore overrides status and stub hiding. The tests are in Task 8 (`search finds done session by jira number`, `search finds stub`).
2. A foreign website using DNS rebinding (header `Host: evil.example:7333`) tries GET `/api/sessions`. It must get 403, and transcripts must not leak. The test is in Task 7 (`test_get_rejects_foreign_host`).
3. A prompt contains HTML (`<img onerror=…>`, `<pasted_content>`). The UI must show it as text. This is covered by the static `innerHTML` ban test in Task 8 and a manual check with an XSS fixture in Task 8, step 8.
4. The session directory no longer exists (deleted worktree). The row must show ⚠ and "Open" must return an understandable error, not an empty iTerm tab with a `cd` error. The tests are in Task 3 (`test_cwd_missing_warning`) and Task 7 (`test_open_refuses_missing_cwd`).
5. The session is being written right now: the last JSONL line is truncated and the file grows between refreshes. The line must be shown and the cache must be recomputed. The tests are in Task 2 (`test_truncated_last_line_is_skipped`, `test_cache_reparses_changed_file`).

---

## File Structure

| File | Responsibility |
|---|---|
| `sessions.py` | Pure helpers (prompt filter, Jira, worktree, display dir), `Session` + `parse_session`, `SessionCache`, `build_rows` (forks, suspect titles, topic, row). |
| `live.py` | `get_live(claude_dir, ps=...)`: map `sessionId → live status`. |
| `notes.py` | `NotesStore`: reading and atomic writing of `notes.json`, validation. |
| `iterm.py` | `open_in_iterm(cmd, runner=...)`: AppleScript via osascript with argv. |
| `server.py` | `App` (assembles data, actions), `Handler` (HTTP + security checks), `DashboardServer`, `main()`. |
| `static/index.html` | Markup and CSS. |
| `static/filter.js` | Pure functions `matches`, `groupRows`, `relTime`, `dirCounts` (UMD: browser and Node). |
| `static/app.js` | Rendering, events, API calls. |
| `launchd/install.sh`, `launchd/uninstall.sh` | Deploying the copy, plist, `launchctl`. |
| `tests/helpers.py` | `FakeClaude` + record builders. |
| `tests/test_*.py`, `tests/js/test_filter.js` | Tests. |
| `README.md` | Usage. |

---

### Task 1: Project skeleton + pure helpers in `sessions.py`

**Files:**
- Create: `tests/__init__.py` (empty)
- Create: `tests/test_sessions_helpers.py`
- Create: `sessions.py`

**Interfaces:**
- Produces (in `sessions.py`):
  - `DEV_ROOT: Path` (`~/Documents/Development`)
  - `JIRA_KEY_RE`, `JIRA_URL_RE` (compiled regexes)
  - `encode_cwd(cwd: str) -> str`
  - `prompt_text(rec: dict) -> str | None`
  - `clean_prompt(text: str) -> str`
  - `one_line(text: str, limit: int) -> str`
  - `title_keys(title: str) -> list[str]`
  - `split_worktree(cwd: str) -> tuple[str, str | None]`
  - `display_dir(path: str, dev_root: Path = DEV_ROOT) -> str`
  - `resume_command(cwd: str, session_id: str) -> str`

- [ ] **Step 1: Write failing tests**

`tests/__init__.py` — empty file.

`tests/test_sessions_helpers.py`:

```python
import shlex
import unittest
from pathlib import Path

import sessions


def rec_user(content, **extra):
    return {"type": "user", "message": {"role": "user", "content": content}, **extra}


class EncodeCwdTest(unittest.TestCase):
    def test_matches_claude_project_dir_naming(self):
        self.assertEqual(
            sessions.encode_cwd("/Users/me/Documents/Development/acme/shop_2"),
            "-Users-me-Documents-Development-acme-shop-2",
        )
        self.assertEqual(
            sessions.encode_cwd("/Users/me/Documents/Development/budget/.claude/worktrees/me-fix"),
            "-Users-me-Documents-Development-budget--claude-worktrees-me-fix",
        )


class PromptTextTest(unittest.TestCase):
    def test_plain_string_prompt(self):
        self.assertEqual(sessions.prompt_text(rec_user("  Analyze PROJ-563 \n")), "Analyze PROJ-563")

    def test_non_user_records_are_ignored(self):
        self.assertIsNone(sessions.prompt_text({"type": "assistant", "message": {"content": "x"}}))

    def test_flagged_records_are_ignored(self):
        for flag in ("isMeta", "isSidechain", "isCompactSummary", "isVisibleInTranscriptOnly"):
            with self.subTest(flag=flag):
                self.assertIsNone(sessions.prompt_text(rec_user("hello", **{flag: True})))

    def test_tool_results_are_ignored(self):
        content = [{"type": "tool_result", "tool_use_id": "t", "content": "ok"}]
        self.assertIsNone(sessions.prompt_text(rec_user(content)))

    def test_list_with_text_and_image_is_a_prompt(self):
        content = [{"type": "image", "source": {}}, {"type": "text", "text": "[Image #3] Still not working"}]
        self.assertEqual(sessions.prompt_text(rec_user(content)), "[Image #3] Still not working")

    def test_system_tags_are_ignored(self):
        for text in (
            "<command-name>/clear</command-name>",
            "<local-command-caveat>Caveat: The messages below…</local-command-caveat>",
            "<local-command-stdout>Set model</local-command-stdout>",
            "<task-notification>\n<task-id>x</task-id>",
            "<artifact-content-authored-by-claude>…",
            "<system-reminder>x</system-reminder>",
            "Caveat: something",
            "[Request interrupted by user]",
        ):
            with self.subTest(text=text):
                self.assertIsNone(sessions.prompt_text(rec_user(text)))

    def test_interrupted_text_block_is_ignored(self):
        self.assertIsNone(sessions.prompt_text(rec_user([{"type": "text", "text": "[Request interrupted by user for tool use]"}])))

    def test_real_prompts_starting_with_bracket_or_paste_are_kept(self):
        for text in ('<pasted_content id="b032">\nhttps://x</pasted_content>', "[me@db1 ~]$ mysql --defaults"):
            with self.subTest(text=text):
                self.assertEqual(sessions.prompt_text(rec_user(text)), text)

    def test_empty_prompt_is_ignored(self):
        self.assertIsNone(sessions.prompt_text(rec_user("   ")))
        self.assertIsNone(sessions.prompt_text(rec_user([])))


class CleanPromptTest(unittest.TestCase):
    def test_pasted_content_is_replaced_by_snippet(self):
        text = 'start on\n\n<pasted_content id="b0">\nhttps://acme.atlassian.net/browse/PROJ-563\n</pasted_content>'
        self.assertEqual(sessions.clean_prompt(text), "start on\n\n[pasted: https://acme.atlassian.net/browse/PROJ-563]")

    def test_long_paste_is_truncated(self):
        text = "<pasted_content id='x'>" + "a" * 100 + "</pasted_content>"
        self.assertEqual(sessions.clean_prompt(text), "[pasted: " + "a" * 60 + "…]")

    def test_empty_paste(self):
        self.assertEqual(sessions.clean_prompt("x <pasted_content id='x'> </pasted_content>"), "x [pasted]")


class OneLineTest(unittest.TestCase):
    def test_collapses_whitespace_and_truncates(self):
        self.assertEqual(sessions.one_line("a\n\n  b\tc", 100), "a b c")
        self.assertEqual(sessions.one_line("abcdefghij", 5), "abcd…")


class TitleKeysTest(unittest.TestCase):
    def test_keys_with_dash_and_leading_space_variant(self):
        self.assertEqual(sessions.title_keys("PROJ 548 Optimize DB queries"), ["PROJ-548"])
        self.assertEqual(sessions.title_keys("https://acme.atlassian.net/browse/PROJ-369 Remove"), ["PROJ-369"])
        self.assertEqual(sessions.title_keys("OPS-218, OPS-207, OPS-238 development"), ["OPS-218", "OPS-207", "OPS-238"])
        self.assertEqual(sessions.title_keys("Partner Audit"), [])


class PathHelpersTest(unittest.TestCase):
    def test_split_worktree(self):
        self.assertEqual(sessions.split_worktree("/d/budget/.claude/worktrees/me-fix/sub"), ("/d/budget", "me-fix"))
        self.assertEqual(sessions.split_worktree("/d/budget"), ("/d/budget", None))

    def test_display_dir(self):
        root = Path("/d")
        self.assertEqual(sessions.display_dir("/d/acme/shop_2", root), "acme/shop_2")
        self.assertEqual(sessions.display_dir("/d", root), "d")
        self.assertEqual(sessions.display_dir("/elsewhere/x", root), "/elsewhere/x")
        home = str(Path.home())
        self.assertEqual(sessions.display_dir(home + "/proj", root), "~/proj")

    def test_resume_command_quotes_cwd(self):
        cmd = sessions.resume_command("/Users/x/it's dir", "abc-123")
        self.assertEqual(shlex.split(cmd), ["cd", "/Users/x/it's dir", "&&", "claude", "--resume", "abc-123"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests, they must fail**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: FAIL / ERROR `ModuleNotFoundError: No module named 'sessions'`

- [ ] **Step 3: Implement the helpers**

`sessions.py`:

```python
"""Parsing of Claude Code transcripts (~/.claude/projects/*/*.jsonl) into dashboard rows."""
from __future__ import annotations

import re
import shlex
from pathlib import Path

DEV_ROOT = Path.home() / "Documents" / "Development"
WORKTREE_MARK = "/.claude/worktrees/"

JIRA_KEY_RE = re.compile(r"\b[A-Z][A-Z0-9]{1,9}-\d+\b")
JIRA_URL_RE = re.compile(r"https?://([a-z0-9-]+)\.atlassian\.net/browse/([A-Z][A-Z0-9]{1,9}-\d+)")
TITLE_LEADING_KEY_RE = re.compile(r"^\s*([A-Z][A-Z0-9]{1,9})[- ](\d+)\b")
PASTED_RE = re.compile(r"<pasted_content\b[^>]*>(.*?)</pasted_content>", re.S)
SYSTEM_TAG_RE = re.compile(r"^<([a-z][a-z0-9_-]*)[\s>]")
SKIP_PREFIXES = ("Caveat:", "[Request interrupted")
SKIP_FLAGS = ("isMeta", "isSidechain", "isCompactSummary", "isVisibleInTranscriptOnly")


def encode_cwd(cwd: str) -> str:
    """Same encoding Claude Code uses to name folders in ~/.claude/projects."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def prompt_text(rec: dict) -> str | None:
    """Text of a real user prompt, or None for meta/system records and tool results."""
    if rec.get("type") != "user" or any(rec.get(flag) for flag in SKIP_FLAGS):
        return None
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        blocks = [b for b in content if isinstance(b, dict)]
        if any(b.get("type") == "tool_result" for b in blocks):
            return None
        texts = [b.get("text") or "" for b in blocks if b.get("type") == "text"]
        if not texts:
            return None
        text = "\n".join(texts)
    else:
        return None
    text = text.strip()
    if not text or text.startswith(SKIP_PREFIXES):
        return None
    tag = SYSTEM_TAG_RE.match(text)
    if tag and tag.group(1) != "pasted_content":
        return None
    return text


def clean_prompt(text: str) -> str:
    """Replaces pasted content with a short snippet; keeps line breaks."""

    def replace(match: re.Match) -> str:
        flat = " ".join(match.group(1).split())
        if not flat:
            return "[pasted]"
        return f"[pasted: {flat[:60]}{'…' if len(flat) > 60 else ''}]"

    return PASTED_RE.sub(replace, text)


def one_line(text: str, limit: int) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def title_keys(title: str) -> list[str]:
    keys = JIRA_KEY_RE.findall(title)
    leading = TITLE_LEADING_KEY_RE.match(title)
    if leading:
        key = f"{leading.group(1)}-{leading.group(2)}"
        if key not in keys:
            keys.insert(0, key)
    return keys


def split_worktree(cwd: str) -> tuple[str, str | None]:
    """'/x/repo/.claude/worktrees/NAME/...' → ('/x/repo', 'NAME')."""
    i = cwd.find(WORKTREE_MARK)
    if i == -1:
        return cwd, None
    return cwd[:i], cwd[i + len(WORKTREE_MARK):].split("/")[0] or None


def display_dir(path: str, dev_root: Path = DEV_ROOT) -> str:
    try:
        rel = Path(path).relative_to(dev_root)
    except ValueError:
        home = str(Path.home())
        if path == home or path.startswith(home + "/"):
            return "~" + path[len(home):]
        return path
    return dev_root.name if str(rel) == "." else str(rel)


def resume_command(cwd: str, session_id: str) -> str:
    return f"cd {shlex.quote(cwd)} && claude --resume {shlex.quote(session_id)}"
```

- [ ] **Step 4: Run the tests, they must pass**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: all tests in `test_sessions_helpers` OK.

- [ ] **Step 5: Commit**

```bash
git add tests/__init__.py tests/test_sessions_helpers.py sessions.py
git commit -m "Add transcript parsing helpers (prompt filter, Jira keys, paths)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `parse_session` + `SessionCache` + test `FakeClaude`

**Files:**
- Create: `tests/helpers.py`
- Create: `tests/test_sessions_parse.py`
- Modify: `sessions.py` (add imports, `Session`, `parse_session`, `SessionCache`)

**Interfaces:**
- Consumes: helpers from Task 1.
- Produces (in `sessions.py`):
  - `@dataclass Session` with fields `session_id, path, project_dir, mtime, cwd, branches, title, last_prompt, prompts, first_ts, last_ts, root_uuid, url_keys, hosts, warnings`.
  - Properties `branch`, `branch_keys`, `title_keys`, `jira_key`, `jira_keys`.
  - `parse_session(path: Path) -> Session`
  - `class SessionCache(claude_dir: Path)` with `.load() -> list[Session]` and the counter `.parse_count: int`.
- Produces (in `tests/helpers.py`):
  - `sid(n) -> str` (UUID-like id),
  - `FakeClaude()` with `.root`, `.write_session(cwd, session_id, records, project_dir=None) -> Path`, `.write_pid(pid, data)`, `.cleanup()`,
  - builders `user(...)`, `assistant(...)`, `tool_result(...)`, `custom_title(...)`, `agent_name(...)`, `last_prompt(...)`.

- [ ] **Step 1: Write the test helpers**

`tests/helpers.py`:

```python
"""Test helpers: a fake ~/.claude directory with JSONL transcripts and pid files."""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path


def sid(n: int) -> str:
    """Deterministic id shaped like a UUID (36 characters, only [0-9a-f-])."""
    return f"{n:08x}-0000-4000-8000-000000000000"


class FakeClaude:
    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "projects").mkdir()
        (self.root / "sessions").mkdir()

    def cleanup(self) -> None:
        self._tmp.cleanup()

    def write_session(self, cwd: str, session_id: str, records: list, project_dir: str | None = None) -> Path:
        folder = self.root / "projects" / (project_dir or re.sub(r"[^A-Za-z0-9]", "-", cwd))
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{session_id}.jsonl"
        lines = [r if isinstance(r, str) else json.dumps(r, ensure_ascii=False) for r in records]
        path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        return path

    def write_pid(self, pid: int, data: dict) -> None:
        (self.root / "sessions" / f"{pid}.json").write_text(json.dumps(data), encoding="utf-8")


def user(content, *, ts: str, uuid: str, cwd: str = "/w/proj", branch: str = "master", **extra) -> dict:
    return {
        "type": "user", "message": {"role": "user", "content": content}, "timestamp": ts,
        "uuid": uuid, "parentUuid": None, "cwd": cwd, "gitBranch": branch, "isSidechain": False, **extra,
    }


def assistant(*, ts: str, uuid: str, cwd: str = "/w/proj", branch: str = "master") -> dict:
    return {
        "type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        "timestamp": ts, "uuid": uuid, "parentUuid": None, "cwd": cwd, "gitBranch": branch, "isSidechain": False,
    }


def tool_result(*, ts: str, uuid: str, cwd: str = "/w/proj", branch: str = "master") -> dict:
    return user([{"type": "tool_result", "tool_use_id": "t1", "content": "done"}], ts=ts, uuid=uuid, cwd=cwd, branch=branch)


def custom_title(title: str, session_id: str) -> dict:
    return {"type": "custom-title", "customTitle": title, "sessionId": session_id}


def agent_name(name: str, session_id: str) -> dict:
    return {"type": "agent-name", "agentName": name, "sessionId": session_id}


def last_prompt(text: str, session_id: str) -> dict:
    return {"type": "last-prompt", "lastPrompt": text, "sessionId": session_id}
```

- [ ] **Step 2: Write failing tests**

`tests/test_sessions_parse.py`:

```python
import os
import unittest
from pathlib import Path

import sessions
from tests.helpers import FakeClaude, agent_name, assistant, custom_title, last_prompt, sid, tool_result, user

CWD = "/w/acme/shop"


class ParseSessionTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeClaude()

    def tearDown(self):
        self.fake.cleanup()

    def parse(self, records, cwd=CWD, project_dir=None, n=1):
        return sessions.parse_session(self.fake.write_session(cwd, sid(n), records, project_dir))

    def test_full_session_fields(self):
        s = self.parse([
            agent_name("Agent title", sid(1)),
            user("Analyze https://acme.atlassian.net/browse/PROJ-369?x=1", ts="2026-09-18T10:00:00.000Z", uuid="u1", cwd=CWD),
            assistant(ts="2026-09-18T10:00:05.000Z", uuid="a1", cwd=CWD, branch="me/feature/PROJ-604-drop"),
            tool_result(ts="2026-09-18T10:00:06.000Z", uuid="t1", cwd=CWD, branch="me/feature/PROJ-604-drop"),
            user("done, switch back", ts="2026-09-18T09:59:00.000Z", uuid="u2", cwd=CWD, branch="master"),
            user("meta", ts="2026-09-18T10:00:07.000Z", uuid="m1", cwd=CWD, isMeta=True),
            custom_title("PROJ-369 Remove legacy column", sid(1)),
            last_prompt("done, switch back", sid(1)),
        ])
        self.assertEqual(s.session_id, sid(1))
        self.assertEqual(s.cwd, CWD)
        self.assertEqual(s.project_dir, sessions.encode_cwd(CWD))
        self.assertEqual(s.title, "PROJ-369 Remove legacy column")
        self.assertEqual(s.last_prompt, "done, switch back")
        self.assertEqual(s.prompts, ["Analyze https://acme.atlassian.net/browse/PROJ-369?x=1", "done, switch back"])
        self.assertEqual(s.first_ts, "2026-09-18T09:59:00.000Z")
        self.assertEqual(s.last_ts, "2026-09-18T10:00:07.000Z")
        self.assertEqual(s.root_uuid, "u1")
        self.assertEqual(s.branches, ["me/feature/PROJ-604-drop", "master"])
        self.assertEqual(s.branch, "master")
        self.assertEqual(s.jira_key, "PROJ-604")
        self.assertEqual(s.jira_keys, ["PROJ-604", "PROJ-369"])
        self.assertEqual(s.hosts, {"PROJ": "acme.atlassian.net"})
        self.assertEqual(s.warnings, [])

    def test_agent_name_is_title_fallback(self):
        s = self.parse([agent_name("DBO", sid(1)), user("x", ts="2026-01-01T00:00:00Z", uuid="u1", cwd=CWD)])
        self.assertEqual(s.title, "DBO")

    def test_branch_order_follows_last_occurrence(self):
        s = self.parse([
            user("a", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD, branch="A"),
            user("b", ts="2026-01-01T00:00:02Z", uuid="u2", cwd=CWD, branch="B"),
            user("c", ts="2026-01-01T00:00:03Z", uuid="u3", cwd=CWD, branch="A"),
        ])
        self.assertEqual(s.branches, ["B", "A"])
        self.assertEqual(s.branch, "A")

    def test_head_branch(self):
        only_head = self.parse([user("a", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD, branch="HEAD")], n=1)
        self.assertEqual(only_head.branch, "HEAD")
        detached_then_real = self.parse([
            user("a", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD, branch="x"),
            user("b", ts="2026-01-01T00:00:02Z", uuid="u2", cwd=CWD, branch="HEAD"),
        ], n=2)
        self.assertEqual(detached_then_real.branch, "x")

    def test_jira_key_fallbacks(self):
        from_url = self.parse([
            user("see https://acme.atlassian.net/browse/PROJ-1 and https://example.atlassian.net/browse/OPS-30", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD),
        ], n=1)
        self.assertEqual(from_url.jira_key, "OPS-30")
        self.assertEqual(from_url.hosts, {"PROJ": "acme.atlassian.net", "OPS": "example.atlassian.net"})
        from_title = self.parse([custom_title("PROJ 548 Optimize", sid(2)), user("x", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD)], n=2)
        self.assertEqual(from_title.jira_key, "PROJ-548")
        none = self.parse([user("x", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD)], n=3)
        self.assertIsNone(none.jira_key)
        self.assertEqual(none.jira_keys, [])

    def test_free_text_keys_are_not_jira(self):
        s = self.parse([user("see P1-1, PSR-4 and ARCH-1874", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD)])
        self.assertEqual(s.jira_keys, [])

    def test_url_inside_pasted_content_counts(self):
        s = self.parse([user('start\n<pasted_content id="b">\nhttps://acme.atlassian.net/browse/PROJ-563\n</pasted_content>',
                             ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD)])
        self.assertEqual(s.jira_key, "PROJ-563")
        self.assertEqual(s.prompts, ["start\n[pasted: https://acme.atlassian.net/browse/PROJ-563]"])

    def test_cwd_matching_project_dir_wins(self):
        s = self.parse([
            user("a", ts="2026-01-01T00:00:01Z", uuid="u1", cwd="/w/other"),
            user("b", ts="2026-01-01T00:00:02Z", uuid="u2", cwd=CWD),
        ], project_dir=sessions.encode_cwd(CWD))
        self.assertEqual(s.cwd, CWD)
        self.assertEqual(s.warnings, [])

    def test_cwd_mismatch_warning(self):
        s = self.parse([user("a", ts="2026-01-01T00:00:01Z", uuid="u1", cwd="/w/other")], project_dir="-w-somewhere")
        self.assertEqual(s.cwd, "/w/other")
        self.assertEqual(s.warnings, ["cwd-mismatch"])

    def test_stub_without_records(self):
        s = self.parse([custom_title("x", sid(1))])
        self.assertIsNone(s.cwd)
        self.assertEqual(s.warnings, ["no-cwd"])
        self.assertEqual(s.prompts, [])
        self.assertIsNotNone(s.last_ts)  # fallback from mtime
        self.assertTrue(s.last_ts.endswith("Z"))

    def test_truncated_last_line_is_skipped(self):
        s = self.parse([
            "not json at all",
            user("a", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD),
            '{"type": "user", "message": {"content": "truncat',
        ])
        self.assertEqual(s.prompts, ["a"])

    def test_unreadable_file(self):
        s = sessions.parse_session(Path(self.fake.root, "projects", "x", f"{sid(9)}.jsonl"))
        self.assertEqual(s.warnings, ["unreadable"])
        self.assertEqual(s.session_id, sid(9))


class SessionCacheTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeClaude()
        self.cache = sessions.SessionCache(self.fake.root)

    def tearDown(self):
        self.fake.cleanup()

    def test_cache_skips_unchanged_files(self):
        self.fake.write_session(CWD, sid(1), [user("a", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD)])
        self.assertEqual(len(self.cache.load()), 1)
        self.assertEqual(len(self.cache.load()), 1)
        self.assertEqual(self.cache.parse_count, 1)

    def test_cache_reparses_changed_file(self):
        path = self.fake.write_session(CWD, sid(1), [user("a", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD)])
        self.cache.load()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write('{"type": "user", "message": {"content": "b"}, "timestamp": "2026-01-01T00:00:02Z", "uuid": "u2", "cwd": "%s"}\n' % CWD)
        [s] = self.cache.load()
        self.assertEqual(s.prompts, ["a", "b"])
        self.assertEqual(self.cache.parse_count, 2)

    def test_cache_drops_deleted_files_and_ignores_subdirs(self):
        path = self.fake.write_session(CWD, sid(1), [user("a", ts="2026-01-01T00:00:01Z", uuid="u1", cwd=CWD)])
        (path.parent / sid(1)).mkdir()  # subfolder with subagents
        (path.parent / sid(1) / "agent.jsonl").write_text("{}\n")
        self.assertEqual([s.session_id for s in self.cache.load()], [sid(1)])
        os.remove(path)
        self.assertEqual(self.cache.load(), [])

    def test_missing_projects_dir(self):
        self.assertEqual(sessions.SessionCache(Path(self.fake.root, "nope")).load(), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests, they must fail**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: ERROR `AttributeError: module 'sessions' has no attribute 'parse_session'` (and `SessionCache`)

- [ ] **Step 4: Implement**

In `sessions.py`, extend the imports at the top of the file (replace the existing import block):

```python
import json
import os
import re
import shlex
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
```

Add below the constants:

```python
PROMPT_STORE_LIMIT = 2000
```

Add at the end of `sessions.py`:

```python
def _unique(items) -> list:
    return list(dict.fromkeys(items))


@dataclass
class Session:
    session_id: str
    path: str
    project_dir: str
    mtime: float = 0.0
    cwd: str | None = None
    branches: list[str] = field(default_factory=list)
    title: str | None = None
    last_prompt: str | None = None
    prompts: list[str] = field(default_factory=list)
    first_ts: str | None = None
    last_ts: str | None = None
    root_uuid: str | None = None
    url_keys: list[str] = field(default_factory=list)
    hosts: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def branch(self) -> str | None:
        for b in reversed(self.branches):
            if b != "HEAD":
                return b
        return self.branches[-1] if self.branches else None

    @property
    def branch_keys(self) -> list[str]:
        return _unique(k for b in self.branches for k in JIRA_KEY_RE.findall(b))

    @property
    def title_keys(self) -> list[str]:
        return title_keys(self.title) if self.title else []

    @property
    def jira_key(self) -> str | None:
        for b in reversed(self.branches):
            keys = JIRA_KEY_RE.findall(b)
            if keys:
                return keys[0]
        if self.url_keys:
            return self.url_keys[-1]
        keys = self.title_keys
        return keys[0] if keys else None

    @property
    def jira_keys(self) -> list[str]:
        return _unique([*self.branch_keys, *self.url_keys, *self.title_keys])


def _pick_cwd(cwds: list[str], project_dir: str, warnings: list[str]) -> str | None:
    for cwd in cwds:
        if encode_cwd(cwd) == project_dir:
            return cwd
    if cwds:
        warnings.append("cwd-mismatch")
        return cwds[0]
    warnings.append("no-cwd")
    return None


def parse_session(path: Path) -> Session:
    path = Path(path)
    s = Session(session_id=path.stem, path=str(path), project_dir=path.parent.name)
    try:
        s.mtime = path.stat().st_mtime
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        s.warnings.append("unreadable")
        return s
    cwds: list[str] = []
    branches: dict[str, None] = {}
    custom_title = agent_name = None
    with fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            rtype = rec.get("type")
            if rtype == "custom-title":
                custom_title = rec.get("customTitle") or custom_title
                continue
            if rtype == "agent-name":
                agent_name = rec.get("agentName") or agent_name
                continue
            if rtype == "last-prompt":
                s.last_prompt = rec.get("lastPrompt") or s.last_prompt
                continue
            cwd = rec.get("cwd")
            if isinstance(cwd, str) and cwd and cwd not in cwds:
                cwds.append(cwd)
            branch = rec.get("gitBranch")
            if isinstance(branch, str) and branch:
                branches.pop(branch, None)
                branches[branch] = None
            if rtype not in ("user", "assistant"):
                continue
            ts = rec.get("timestamp")
            if isinstance(ts, str):
                if s.first_ts is None or ts < s.first_ts:
                    s.first_ts = ts
                if s.last_ts is None or ts > s.last_ts:
                    s.last_ts = ts
            if s.root_uuid is None and not rec.get("isSidechain") and rec.get("uuid"):
                s.root_uuid = rec["uuid"]
            text = prompt_text(rec)
            if text is None:
                continue
            for host, key in JIRA_URL_RE.findall(text):
                s.url_keys.append(key)
                s.hosts.setdefault(key.split("-")[0], f"{host}.atlassian.net")
            s.prompts.append(clean_prompt(text)[:PROMPT_STORE_LIMIT])
    s.title = custom_title or agent_name
    s.branches = list(branches)
    s.cwd = _pick_cwd(cwds, s.project_dir, s.warnings)
    if s.last_ts is None and s.mtime:
        s.last_ts = datetime.fromtimestamp(s.mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    return s


class SessionCache:
    """Holds parsed sessions; re-parses only files with a changed (mtime_ns, size)."""

    def __init__(self, claude_dir: Path) -> None:
        self.projects_dir = Path(claude_dir) / "projects"
        self.parse_count = 0
        self._entries: dict[str, tuple[int, int, Session]] = {}
        self._lock = threading.Lock()

    def load(self) -> list[Session]:
        with self._lock:
            seen: set[str] = set()
            result: list[Session] = []
            try:
                project_dirs = [e for e in os.scandir(self.projects_dir) if e.is_dir()]
            except OSError:
                project_dirs = []
            for project in project_dirs:
                try:
                    files = [e for e in os.scandir(project.path) if e.name.endswith(".jsonl") and e.is_file()]
                except OSError:
                    continue
                for entry in files:
                    try:
                        st = entry.stat()
                    except OSError:
                        continue
                    seen.add(entry.path)
                    cached = self._entries.get(entry.path)
                    if cached and cached[0] == st.st_mtime_ns and cached[1] == st.st_size:
                        result.append(cached[2])
                        continue
                    session = parse_session(Path(entry.path))
                    self.parse_count += 1
                    self._entries[entry.path] = (st.st_mtime_ns, st.st_size, session)
                    result.append(session)
            for stale in set(self._entries) - seen:
                del self._entries[stale]
            return result
```

- [ ] **Step 5: Run the tests, they must pass**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: all tests OK.

- [ ] **Step 6: Quick check on real data (read-only)**

Run:
```bash
python3 -c "
import time, sessions, pathlib
t=time.time(); c=sessions.SessionCache(pathlib.Path.home()/'.claude'); ss=c.load()
print(len(ss), 'sessions', round(time.time()-t,2), 's')
for s in sorted(ss, key=lambda s: s.last_ts or '', reverse=True)[:8]:
    print(s.session_id[:8], s.cwd, s.branch, s.jira_key, len(s.prompts), s.warnings)
"
```
Expected: around 46+ sessions in a few seconds. For `bd117c05`, `jira_key` is `PROJ-563` and cwd is `/Users/me/Documents/Development/acme/shop`. No exception.

- [ ] **Step 7: Commit**

```bash
git add tests/helpers.py tests/test_sessions_parse.py sessions.py
git commit -m "Parse Claude session transcripts with mtime-based cache

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `build_rows`: forks, suspect titles, topic, row

**Files:**
- Create: `tests/test_sessions_rows.py`
- Modify: `sessions.py` (add `SEARCH_LIMIT`, `TITLE_PREFIX_RE`, `cwd_missing`, `is_title_suspect`, `topic_for`, `build_rows`)

**Interfaces:**
- Consumes: `Session`, `parse_session`, helpers (Task 1–2), `tests.helpers`.
- Produces:
  - `cwd_missing(cwd: str) -> bool`
  - `build_rows(sessions: list[Session], dev_root: Path = DEV_ROOT, is_missing=cwd_missing) -> tuple[list[dict], dict[str, str]]` returns `(rows, jira_hosts)`.
  - Each row is a dict with keys: `session_id, cwd, repo, worktree, display_dir, branch, branches, jira_key, jira_keys, topic, title, title_suspect, first_ts, last_ts, prompt_count, is_stub, first_prompt, recent_prompts, search_text, older_copies, warnings, resume_cmd`.
  - `older_copies` is a list of dicts `{session_id, last_ts, prompt_count}`.
  - Rows are sorted by `last_ts` descending.

- [ ] **Step 1: Write failing tests**

`tests/test_sessions_rows.py`:

```python
import shlex
import tempfile
import unittest
from pathlib import Path

import sessions
from tests.helpers import FakeClaude, custom_title, last_prompt, sid, user

ROOT = Path("/w")
SHOP = "/w/acme/shop"
SHOP2 = "/w/acme/shop_2"


class BuildRowsTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeClaude()

    def tearDown(self):
        self.fake.cleanup()

    def session(self, n, records, cwd=SHOP):
        return sessions.parse_session(self.fake.write_session(cwd, sid(n), records))

    def rows(self, items, is_missing=lambda c: False):
        rows, hosts = sessions.build_rows(items, dev_root=ROOT, is_missing=is_missing)
        return {r["session_id"]: r for r in rows}, rows, hosts

    def test_row_fields(self):
        prompts = [user(f"prompt {i} https://acme.atlassian.net/browse/PROJ-563" if i == 0 else f"prompt {i}",
                        ts=f"2026-10-01T10:00:0{i}Z", uuid=f"u{i}", cwd=SHOP, branch="me/bugfix/PROJ-563-dup") for i in range(7)]
        s = self.session(1, prompts + [custom_title("PROJ-563 Contoso: GA orders lower", sid(1))])
        by_id, _, hosts = self.rows([s])
        r = by_id[sid(1)]
        self.assertEqual(r["display_dir"], "acme/shop")
        self.assertEqual(r["repo"], SHOP)
        self.assertIsNone(r["worktree"])
        self.assertEqual(r["branch"], "me/bugfix/PROJ-563-dup")
        self.assertEqual(r["jira_key"], "PROJ-563")
        self.assertEqual(r["topic"], "Contoso: GA orders lower")
        self.assertFalse(r["title_suspect"])
        self.assertEqual(r["prompt_count"], 7)
        self.assertFalse(r["is_stub"])
        self.assertEqual(r["recent_prompts"], [f"prompt {i}" for i in range(2, 7)])
        self.assertTrue(r["first_prompt"].startswith("prompt 0"))
        self.assertIn("prompt 3", r["search_text"])
        self.assertEqual(shlex.split(r["resume_cmd"]), ["cd", SHOP, "&&", "claude", "--resume", sid(1)])
        self.assertEqual(r["older_copies"], [])
        self.assertEqual(r["warnings"], [])
        self.assertEqual(hosts, {"PROJ": "acme.atlassian.net"})

    def test_worktree_row(self):
        cwd = "/w/budget/.claude/worktrees/me-fix"
        r = self.rows([self.session(1, [user("x", ts="2026-01-01T00:00:00Z", uuid="u1", cwd=cwd)], cwd=cwd)])[0][sid(1)]
        self.assertEqual(r["display_dir"], "budget")
        self.assertEqual(r["worktree"], "me-fix")
        self.assertEqual(r["cwd"], cwd)

    def test_fork_is_collapsed_under_newest(self):
        older = self.session(1, [user("a", ts="2026-08-25T11:39:50Z", uuid="root", cwd=SHOP),
                                 user("b", ts="2026-08-26T13:23:00Z", uuid="u2", cwd=SHOP)])
        newer = self.session(2, [user("a", ts="2026-08-25T11:39:50Z", uuid="root", cwd=SHOP),
                                 user("c", ts="2026-09-15T11:29:00Z", uuid="u3", cwd=SHOP)])
        by_id, rows, _ = self.rows([older, newer])
        self.assertEqual(list(by_id), [sid(2)])
        self.assertEqual(by_id[sid(2)]["older_copies"], [{"session_id": sid(1), "last_ts": "2026-08-26T13:23:00Z", "prompt_count": 2}])

    def test_rows_sorted_by_last_activity(self):
        a = self.session(1, [user("a", ts="2026-01-01T00:00:00Z", uuid="u1", cwd=SHOP)])
        b = self.session(2, [user("b", ts="2026-02-01T00:00:00Z", uuid="u2", cwd=SHOP)])
        _, rows, _ = self.rows([a, b])
        self.assertEqual([r["session_id"] for r in rows], [sid(2), sid(1)])

    def test_title_with_foreign_key_is_suspect(self):
        s = self.session(1, [custom_title("PROJ-494 Route partner traffic", sid(1)),
                             user("start on https://acme.atlassian.net/browse/PROJ-563", ts="2026-01-01T00:00:00Z", uuid="u1", cwd=SHOP)])
        r = self.rows([s])[0][sid(1)]
        self.assertTrue(r["title_suspect"])
        self.assertEqual(r["jira_key"], "PROJ-563")
        self.assertEqual(r["topic"], "start on https://acme.atlassian.net/browse/PROJ-563")

    def test_same_title_different_keys_in_same_project_is_suspect(self):
        a = self.session(1, [custom_title("country variables refactoring", sid(1)),
                             user("a", ts="2026-01-01T00:00:00Z", uuid="u1", cwd=SHOP2, branch="me/bug/PROJ-505-x")], cwd=SHOP2)
        b = self.session(2, [custom_title("Country variables refactoring ", sid(2)),
                             user("b", ts="2026-01-02T00:00:00Z", uuid="u2", cwd=SHOP2, branch="me/feature/PROJ-557-y")], cwd=SHOP2)
        by_id = self.rows([a, b])[0]
        self.assertTrue(by_id[sid(1)]["title_suspect"])
        self.assertTrue(by_id[sid(2)]["title_suspect"])
        self.assertEqual(by_id[sid(1)]["topic"], "a")

    def test_same_title_same_key_or_other_project_is_not_suspect(self):
        a = self.session(1, [custom_title("PROJ-560 Prefer visible tree", sid(1)),
                             user("a", ts="2026-01-01T00:00:00Z", uuid="u1", cwd=SHOP, branch="me/bugfix/PROJ-560-x")])
        b = self.session(2, [custom_title("PROJ-560 Prefer visible tree", sid(2)),
                             user("b", ts="2026-01-02T00:00:00Z", uuid="u2", cwd=SHOP, branch="me/bugfix/PROJ-560-x")])
        c = self.session(3, [custom_title("PROJ-560 Prefer visible tree", sid(3)),
                             user("c", ts="2026-01-03T00:00:00Z", uuid="u3", cwd=SHOP2, branch="me/feature/PROJ-999-z")], cwd=SHOP2)
        by_id = self.rows([a, b, c])[0]
        self.assertFalse(by_id[sid(1)]["title_suspect"])
        self.assertFalse(by_id[sid(2)]["title_suspect"])
        self.assertTrue(by_id[sid(3)]["title_suspect"])  # (a): the key from the title is not in its branches

    def test_topic_fallbacks(self):
        url_title = self.session(1, [custom_title("https://acme.atlassian.net/browse/PROJ-369 Remove legacy column", sid(1)),
                                     user("x https://acme.atlassian.net/browse/PROJ-369", ts="2026-01-01T00:00:00Z", uuid="u1", cwd=SHOP)])
        no_title = self.session(2, [user("Looking at\n\nthis account", ts="2026-01-01T00:00:00Z", uuid="u2", cwd=SHOP)])
        only_last = self.session(3, [last_prompt("last thing", sid(3))])
        nothing = self.session(4, [custom_title("PROJ-1", sid(4))])
        by_id = self.rows([url_title, no_title, only_last, nothing])[0]
        self.assertEqual(by_id[sid(1)]["topic"], "Remove legacy column")
        self.assertEqual(by_id[sid(2)]["topic"], "Looking at this account")
        self.assertEqual(by_id[sid(3)]["topic"], "last thing")
        self.assertEqual(by_id[sid(4)]["topic"], "(no description)")
        self.assertTrue(by_id[sid(3)]["is_stub"])
        self.assertIsNone(by_id[sid(3)]["resume_cmd"])

    def test_cwd_missing_warning(self):
        s = self.session(1, [user("x", ts="2026-01-01T00:00:00Z", uuid="u1", cwd=SHOP)])
        r = self.rows([s], is_missing=lambda c: c == SHOP)[0][sid(1)]
        self.assertEqual(r["warnings"], ["cwd-missing"])

    def test_cwd_missing_real_check(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(sessions.cwd_missing(d))
            self.assertTrue(sessions.cwd_missing(d + "/does-not-exist"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests, they must fail**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: ERROR `AttributeError: module 'sessions' has no attribute 'build_rows'`

- [ ] **Step 3: Implement**

Add below the constants in `sessions.py`:

```python
SEARCH_LIMIT = 60_000
TITLE_PREFIX_RE = re.compile(r"^\s*(?:https?://\S+\s*)?(?:[A-Z][A-Z0-9]{1,9}[- ]\d+\b[\s:–—-]*)?")
```

Add at the end of `sessions.py`:

```python
def cwd_missing(cwd: str) -> bool:
    """True only when the directory provably does not exist; other errors (e.g. TCC) = unknown → False."""
    try:
        os.stat(cwd)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def is_title_suspect(s: Session, by_title: dict[tuple[str, str], list[Session]]) -> bool:
    if not s.title:
        return False
    own = set(s.branch_keys) | set(s.url_keys)
    if own and set(s.title_keys) - own:
        return True
    key = s.jira_key
    if key is None:
        return False
    for other in by_title.get((s.project_dir, s.title.strip().lower()), []):
        if other is not s and other.jira_key is not None and other.jira_key != key:
            return True
    return False


def topic_for(s: Session, suspect: bool) -> str:
    if s.title and not suspect:
        stripped = TITLE_PREFIX_RE.sub("", s.title, count=1).strip()
        if stripped:
            return one_line(stripped, 140)
    if s.prompts:
        return one_line(s.prompts[0], 140)
    if s.last_prompt:
        return one_line(clean_prompt(s.last_prompt), 140)
    return "(no description)"


def _search_text(s: Session) -> str:
    text = "\n".join(one_line(p, 300) for p in s.prompts)
    if len(text) > SEARCH_LIMIT:
        text = one_line(s.prompts[0], 300) + "\n" + text[-SEARCH_LIMIT:]
    return text


def _row(s: Session, older: list[Session], by_title, dev_root: Path, is_missing) -> dict:
    suspect = is_title_suspect(s, by_title)
    repo, worktree = split_worktree(s.cwd) if s.cwd else (None, None)
    warnings = list(s.warnings)
    if s.cwd and is_missing(s.cwd):
        warnings.append("cwd-missing")
    return {
        "session_id": s.session_id,
        "cwd": s.cwd,
        "repo": repo,
        "worktree": worktree,
        "display_dir": display_dir(repo, dev_root) if repo else s.project_dir,
        "branch": s.branch,
        "branches": s.branches,
        "jira_key": s.jira_key,
        "jira_keys": s.jira_keys,
        "topic": topic_for(s, suspect),
        "title": s.title,
        "title_suspect": suspect,
        "first_ts": s.first_ts,
        "last_ts": s.last_ts,
        "prompt_count": len(s.prompts),
        "is_stub": not s.prompts,
        "first_prompt": one_line(s.prompts[0], 500) if s.prompts else None,
        "recent_prompts": [one_line(p, 500) for p in s.prompts[-5:]],
        "search_text": _search_text(s),
        "older_copies": [
            {"session_id": o.session_id, "last_ts": o.last_ts, "prompt_count": len(o.prompts)} for o in older
        ],
        "warnings": warnings,
        "resume_cmd": resume_command(s.cwd, s.session_id) if s.cwd else None,
    }


def build_rows(sessions: list[Session], dev_root: Path = DEV_ROOT, is_missing=cwd_missing) -> tuple[list[dict], dict[str, str]]:
    hosts: dict[str, str] = {}
    for s in sessions:
        for prefix, host in s.hosts.items():
            hosts.setdefault(prefix, host)

    chains: dict[str, list[Session]] = {}
    for s in sessions:
        chains.setdefault(s.root_uuid or f"file:{s.path}", []).append(s)
    heads: list[tuple[Session, list[Session]]] = []
    for members in chains.values():
        members.sort(key=lambda m: (m.last_ts or "", m.mtime), reverse=True)
        heads.append((members[0], members[1:]))

    by_title: dict[tuple[str, str], list[Session]] = {}
    for head, _ in heads:
        if head.title:
            by_title.setdefault((head.project_dir, head.title.strip().lower()), []).append(head)

    rows = [_row(head, older, by_title, dev_root, is_missing) for head, older in heads]
    rows.sort(key=lambda r: r["last_ts"] or "", reverse=True)
    return rows, hosts
```

- [ ] **Step 4: Run the tests, they must pass**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: all tests OK.

- [ ] **Step 5: Check on real data**

Run:
```bash
python3 -c "
import sessions, pathlib
rows, hosts = sessions.build_rows(sessions.SessionCache(pathlib.Path.home()/'.claude').load())
print(len(rows), 'rows', hosts)
for r in rows[:15]:
    print(r['session_id'][:8], r['display_dir'], r['jira_key'], '|', r['topic'][:60], '| suspect' if r['title_suspect'] else '', r['warnings'], [c['session_id'][:8] for c in r['older_copies']])
"
```
Expected:
- `hosts` contains `PROJ` → `acme.atlassian.net` and `SD` → `example.atlassian.net`.
- `cd6e5aad` has `b0372375` in `older_copies`.
- The shop_2 sessions with the title "country variables refactoring" are `suspect`.

- [ ] **Step 6: Commit**

```bash
git add tests/test_sessions_rows.py sessions.py
git commit -m "Build dashboard rows: fork collapsing, suspect titles, topics

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `live.py`: running instances

**Files:**
- Create: `tests/test_live.py`
- Create: `live.py`

**Interfaces:**
- Consumes: `tests.helpers.FakeClaude.write_pid`.
- Produces:
  - `parse_ps_output(out: str) -> dict[int, str]`
  - `ps_lstart(pids: list[int]) -> dict[int, str]`
  - `get_live(claude_dir: Path, ps=ps_lstart) -> dict[str, dict]` returns `sessionId → {"status": str, "pid": int, "name": str | None, "updated_at": int | None}`.

- [ ] **Step 1: Write failing tests**

`tests/test_live.py`:

```python
import os
import re
import unittest

import live
from tests.helpers import FakeClaude, sid

START = "Wed Oct  7 10:58:12 2026"


class GetLiveTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeClaude()

    def tearDown(self):
        self.fake.cleanup()

    def pid_file(self, pid, session, start=START, status="busy"):
        self.fake.write_pid(pid, {"pid": pid, "sessionId": session, "cwd": "/w", "status": status,
                                  "procStart": start, "name": "x", "updatedAt": 1791371445884})

    def test_matching_process_is_live(self):
        self.pid_file(100, sid(1))
        result = live.get_live(self.fake.root, ps=lambda pids: {100: "Wed Oct 7 10:58:12 2026"})
        self.assertEqual(result, {sid(1): {"status": "busy", "pid": 100, "name": "x", "updated_at": 1791371445884}})

    def test_dead_or_reused_pid_is_not_live(self):
        self.pid_file(100, sid(1))
        self.pid_file(200, sid(2))
        result = live.get_live(self.fake.root, ps=lambda pids: {200: "Thu Oct  8 09:00:00 2026"})
        self.assertEqual(result, {})

    def test_invalid_pid_files_are_skipped(self):
        (self.fake.root / "sessions" / "300.json").write_text("{nope")
        self.fake.write_pid(400, {"pid": "400", "sessionId": sid(4), "procStart": START})
        self.pid_file(500, sid(5), status="idle")
        seen = []

        def fake_ps(pids):
            seen.extend(pids)
            return {500: START}

        self.assertEqual(list(live.get_live(self.fake.root, ps=fake_ps)), [sid(5)])
        self.assertEqual(seen, [500])

    def test_missing_sessions_dir(self):
        self.assertEqual(live.get_live(self.fake.root / "nope", ps=lambda pids: {}), {})

    def test_parse_ps_output(self):
        out = "  59594 Wed Oct  7 10:58:12 2026\n 1642 Fri Sep 18 15:06:24 2026\ngarbage\n"
        self.assertEqual(live.parse_ps_output(out), {59594: "Wed Oct  7 10:58:12 2026", 1642: "Fri Sep 18 15:06:24 2026"})

    def test_ps_lstart_uses_c_locale_and_utc(self):
        self.assertEqual(live.ps_lstart([]), {})
        mine = live.ps_lstart([os.getpid()])
        self.assertRegex(mine[os.getpid()], r"^[A-Z][a-z]{2} [A-Z][a-z]{2} +\d{1,2} \d\d:\d\d:\d\d \d{4}$")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests, they must fail**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: ERROR `ModuleNotFoundError: No module named 'live'`

- [ ] **Step 3: Implement**

`live.py`:

```python
"""Detection of running Claude Code instances from ~/.claude/sessions/<pid>.json."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _norm(value: str) -> str:
    return " ".join(value.split())


def parse_ps_output(out: str) -> dict[int, str]:
    result: dict[int, str] = {}
    for line in out.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            result[int(parts[0])] = parts[1].strip()
    return result


def ps_lstart(pids: list[int]) -> dict[int, str]:
    """Start times of processes in the same format as `procStart` in the pid files (C locale, UTC)."""
    if not pids:
        return {}
    env = dict(os.environ, LC_ALL="C", TZ="UTC")
    try:
        completed = subprocess.run(
            ["ps", "-o", "pid=,lstart=", "-p", ",".join(str(p) for p in pids)],
            capture_output=True, text=True, env=env, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    # ps returns exit code 1 when some pid does not exist; the output for live pids is valid anyway.
    return parse_ps_output(completed.stdout)


def get_live(claude_dir: Path, ps=ps_lstart) -> dict[str, dict]:
    entries: list[dict] = []
    for path in sorted(Path(claude_dir, "sessions").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        if not isinstance(data.get("pid"), int) or not data.get("sessionId") or not data.get("procStart"):
            continue
        entries.append(data)
    starts = ps([e["pid"] for e in entries])
    result: dict[str, dict] = {}
    for e in entries:
        if _norm(starts.get(e["pid"], "")) == _norm(e["procStart"]):
            result[e["sessionId"]] = {
                "status": e.get("status") or "unknown",
                "pid": e["pid"],
                "name": e.get("name"),
                "updated_at": e.get("updatedAt"),
            }
    return result
```

- [ ] **Step 4: Run the tests, they must pass**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: all tests OK.

- [ ] **Step 5: Check on real data**

Run: `python3 -c "import live, pathlib, json; print(json.dumps(live.get_live(pathlib.Path.home()/'.claude'), indent=1))"`
Expected: records for running sessions, including the current session `3955ef16-…` with status `busy`.

- [ ] **Step 6: Commit**

```bash
git add tests/test_live.py live.py
git commit -m "Detect running Claude instances from pid files

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: `notes.py`: statuses and notes

**Files:**
- Create: `tests/test_notes.py`
- Create: `notes.py`

**Interfaces:**
- Produces:
  - `VALID_STATUSES = ("active", "waiting", "done", "archived")`
  - `MAX_NOTE = 500`
  - `class NotesError(ValueError)`
  - `class NotesStore(path: Path)` with methods:
    - `.all() -> dict[str, dict]`
    - `.update(session_id: str, *, status=<not given>, note=<not given>) -> dict`. Returns `{"status": str | None, "note": str, "updated_at": str}`. `status=None` clears the status.

- [ ] **Step 1: Write failing tests**

`tests/test_notes.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

import notes


class NotesStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "sub" / "notes.json"
        self.store = notes.NotesStore(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_means_no_notes(self):
        self.assertEqual(self.store.all(), {})

    def test_update_persists(self):
        entry = self.store.update("s1", status="waiting", note="waiting for CR")
        self.assertEqual(entry["status"], "waiting")
        self.assertEqual(entry["note"], "waiting for CR")
        self.assertTrue(entry["updated_at"].endswith("Z"))
        reloaded = notes.NotesStore(self.path).all()
        self.assertEqual(reloaded["s1"]["note"], "waiting for CR")
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(raw["version"], 1)

    def test_partial_update_keeps_other_field(self):
        self.store.update("s1", status="active")
        self.store.update("s1", note="note")
        self.assertEqual(self.store.all()["s1"]["status"], "active")
        self.store.update("s1", status="done")
        self.assertEqual(self.store.all()["s1"]["note"], "note")

    def test_validation(self):
        with self.assertRaises(notes.NotesError):
            self.store.update("s1", status="bogus")
        with self.assertRaises(notes.NotesError):
            self.store.update("s1", note=123)
        self.assertFalse(self.path.exists())

    def test_note_is_single_line_and_truncated(self):
        entry = self.store.update("s1", note="a\nb  c\t" + "x" * 600)
        self.assertTrue(entry["note"].startswith("a b c x"))
        self.assertEqual(len(entry["note"]), notes.MAX_NOTE)

    def test_clearing_everything_removes_entry(self):
        self.store.update("s1", status="active", note="x")
        entry = self.store.update("s1", status=None, note="")
        self.assertIsNone(entry["status"])
        self.assertEqual(self.store.all(), {})

    def test_corrupt_file_is_backed_up(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{broken", encoding="utf-8")
        self.assertEqual(self.store.all(), {})
        backups = list(self.path.parent.glob("notes.json.corrupt-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "{broken")

    def test_no_temp_files_left(self):
        self.store.update("s1", status="active")
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["notes.json"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests, they must fail**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: ERROR `ModuleNotFoundError: No module named 'notes'`

- [ ] **Step 3: Implement**

`notes.py`:

```python
"""User statuses and notes for sessions; a JSON file with atomic writes."""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

VALID_STATUSES = ("active", "waiting", "done", "archived")
MAX_NOTE = 500
_UNSET = object()


class NotesError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class NotesStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def all(self) -> dict[str, dict]:
        with self._lock:
            return self._read()

    def update(self, session_id: str, *, status=_UNSET, note=_UNSET) -> dict:
        if status is not _UNSET and status is not None and status not in VALID_STATUSES:
            raise NotesError(f"invalid status: {status!r}")
        if note is not _UNSET and not isinstance(note, str):
            raise NotesError("note must be a string")
        with self._lock:
            data = self._read()
            entry = dict(data.get(session_id) or {})
            if status is not _UNSET:
                entry["status"] = status
            if note is not _UNSET:
                entry["note"] = " ".join(note.split())[:MAX_NOTE]
            entry["updated_at"] = _now()
            if entry.get("status") or entry.get("note"):
                data[session_id] = entry
            else:
                data.pop(session_id, None)
            self._write(data)
        return {"status": entry.get("status"), "note": entry.get("note") or "", "updated_at": entry["updated_at"]}

    def _read(self) -> dict[str, dict]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            parsed = json.loads(raw)
            data = parsed["notes"]
            if not isinstance(data, dict):
                raise TypeError("notes is not an object")
            return data
        except (ValueError, KeyError, TypeError):
            os.replace(self.path, self.path.with_name(f"{self.path.name}.corrupt-{int(time.time())}"))
            return {}

    def _write(self, data: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".notes-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "notes": data}, fh, ensure_ascii=False, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise
```

- [ ] **Step 4: Run the tests, they must pass**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: all tests OK.

- [ ] **Step 5: Commit**

```bash
git add tests/test_notes.py notes.py
git commit -m "Store per-session status and notes with atomic writes

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `iterm.py`: opening resume in iTerm2

**Files:**
- Create: `tests/test_iterm.py`
- Create: `iterm.py`

**Interfaces:**
- Produces:
  - `APPLESCRIPT: str`
  - `build_argv(cmd: str) -> list[str]`
  - `open_in_iterm(cmd: str, runner=subprocess.run) -> tuple[bool, str | None]`

- [ ] **Step 1: Write failing tests**

`tests/test_iterm.py`:

```python
import subprocess
import unittest

import iterm

CMD = "cd '/Users/x/it'\"'\"'s dir' && claude --resume abc"


class ItermTest(unittest.TestCase):
    def test_command_is_passed_as_argv_not_interpolated(self):
        argv = iterm.build_argv(CMD)
        self.assertEqual(argv, ["osascript", "-e", iterm.APPLESCRIPT, CMD])
        self.assertIn("on run argv", iterm.APPLESCRIPT)
        self.assertNotIn("claude --resume", iterm.APPLESCRIPT)

    def test_success(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, "", "")

        self.assertEqual(iterm.open_in_iterm(CMD, runner=runner), (True, None))
        self.assertEqual(calls[0][0][-1], CMD)
        self.assertEqual(calls[0][1]["timeout"], 10)

    def test_automation_permission_denied(self):
        runner = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "execution error: Not authorized to send Apple events to iTerm2. (-1743)")
        ok, error = iterm.open_in_iterm(CMD, runner=runner)
        self.assertFalse(ok)
        self.assertIn("Automatizace", error)

    def test_other_error_is_reported(self):
        runner = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "boom")
        self.assertEqual(iterm.open_in_iterm(CMD, runner=runner), (False, "boom"))

    def test_timeout_and_missing_binary(self):
        def timeout(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 10)

        def missing(argv, **kw):
            raise FileNotFoundError("osascript")

        self.assertFalse(iterm.open_in_iterm(CMD, runner=timeout)[0])
        self.assertIn("osascript", iterm.open_in_iterm(CMD, runner=missing)[1])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests, they must fail**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: ERROR `ModuleNotFoundError: No module named 'iterm'`

- [ ] **Step 3: Implement**

`iterm.py`:

```python
"""Opens a command in a new iTerm2 tab via osascript (the command goes in as argv, never into the script source)."""
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
```

- [ ] **Step 4: Run the tests, they must pass**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: all tests OK.

- [ ] **Step 5: Commit**

```bash
git add tests/test_iterm.py iterm.py
git commit -m "Open resume command in a new iTerm2 tab via osascript

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: `server.py`: HTTP API, security, startup

**Files:**
- Create: `tests/test_server.py`
- Create: `server.py`

**Interfaces:**
- Consumes:
  - `sessions.SessionCache`, `sessions.build_rows`, `sessions.cwd_missing`, `sessions.DEV_ROOT`,
  - `live.get_live`,
  - `notes.NotesStore`, `notes.NotesError`,
  - `iterm.open_in_iterm`,
  - `tests.helpers`.
- Produces:
  - `App(claude_dir, data_dir, *, static_dir, opener, live_fn, dev_root, is_missing)` with `.snapshot() -> dict`, `.find_row(id) -> dict | None`, `.save_note(id, fields) -> dict`, `.open_session(row) -> tuple[int, dict]` and `.port`.
  - `DashboardServer((host, port), app)`.
  - `main(argv=None) -> int`.
  - HTTP:
    - `GET /`, `/index.html`, `/app.js`, `/filter.js`, `/api/health`, `/api/sessions`,
    - `POST /api/notes/<id>` with body `{status?, note?}`,
    - `POST /api/open/<id>`.

- [ ] **Step 1: Write failing tests**

`tests/test_server.py`:

```python
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import server
from tests.helpers import FakeClaude, assistant, sid, user

CWD = "/w/acme/shop"


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeClaude()
        self.data = tempfile.TemporaryDirectory()
        self.static = tempfile.TemporaryDirectory()
        Path(self.static.name, "index.html").write_text("<title>Claude Sessions</title>", encoding="utf-8")
        Path(self.static.name, "app.js").write_text("// app", encoding="utf-8")
        Path(self.static.name, "filter.js").write_text("// filter", encoding="utf-8")
        branch = "me/bugfix/PROJ-563-dup"
        self.fake.write_session(CWD, sid(1), [
            user("Analyzuj https://acme.atlassian.net/browse/PROJ-563", ts="2026-10-01T10:00:00Z", uuid="u1", cwd=CWD, branch=branch),
            assistant(ts="2026-10-01T10:00:05Z", uuid="a1", cwd=CWD, branch=branch),
        ])
        self.opened = []
        self.open_result = (True, None)
        self.missing = False

        def opener(cmd):
            self.opened.append(cmd)
            return self.open_result

        self.app = server.App(
            self.fake.root, self.data.name, static_dir=self.static.name, opener=opener,
            live_fn=lambda d: {sid(1): {"status": "idle", "pid": 42, "name": "x", "updated_at": 1}},
            dev_root=Path("/w"), is_missing=lambda c: self.missing,
        )
        self.httpd = server.DashboardServer(("127.0.0.1", 0), self.app)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.fake.cleanup()
        self.data.cleanup()
        self.static.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=5)
        hdrs = {"Content-Type": "application/json"} if method == "POST" else {}
        hdrs.update(headers or {})
        payload = None if body is None else (body if isinstance(body, str) else json.dumps(body))
        conn.request(method, path, body=payload, headers=hdrs)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        if (res.getheader("Content-Type") or "").startswith("application/json"):
            return res.status, json.loads(raw)
        return res.status, raw.decode("utf-8")

    def rows(self):
        return {r["session_id"]: r for r in self.request("GET", "/api/sessions")[1]["rows"]}

    def test_static_files(self):
        self.assertEqual(self.request("GET", "/"), (200, "<title>Claude Sessions</title>"))
        self.assertEqual(self.request("GET", "/app.js"), (200, "// app"))
        self.assertEqual(self.request("GET", "/filter.js"), (200, "// filter"))
        self.assertEqual(self.request("GET", "/../server.py")[0], 404)

    def test_health(self):
        self.assertEqual(self.request("GET", "/api/health"), (200, {"ok": True}))

    def test_sessions_payload(self):
        status, payload = self.request("GET", "/api/sessions")
        self.assertEqual(status, 200)
        self.assertEqual(payload["jira_hosts"], {"PROJ": "acme.atlassian.net"})
        [row] = payload["rows"]
        self.assertEqual(row["session_id"], sid(1))
        self.assertEqual(row["display_dir"], "acme/shop")
        self.assertEqual(row["jira_key"], "PROJ-563")
        self.assertEqual(row["live"]["status"], "idle")
        self.assertIsNone(row["note"])

    def test_get_rejects_foreign_host(self):
        status, _ = self.request("GET", "/api/sessions", headers={"Host": f"evil.example:{self.app.port}"})
        self.assertEqual(status, 403)

    def test_notes_roundtrip(self):
        status, body = self.request("POST", f"/api/notes/{sid(1)}", {"status": "waiting", "note": "waiting for CR"})
        self.assertEqual(status, 200)
        self.assertEqual(body["note"]["status"], "waiting")
        self.assertEqual(self.rows()[sid(1)]["note"]["note"], "waiting for CR")

    def test_notes_invalid_status(self):
        self.assertEqual(self.request("POST", f"/api/notes/{sid(1)}", {"status": "bogus"})[0], 400)

    def test_post_security_checks(self):
        port = self.app.port
        cases = [
            {"Origin": "http://evil.example"},
            {"Content-Type": "text/plain"},
            {"Host": f"evil.example:{port}"},
        ]
        for headers in cases:
            with self.subTest(headers=headers):
                self.assertEqual(self.request("POST", f"/api/notes/{sid(1)}", {"status": "done"}, headers)[0], 403)
        self.assertIsNone(self.rows()[sid(1)]["note"])
        ok_origin = self.request("POST", f"/api/notes/{sid(1)}", {"status": "done"}, {"Origin": f"http://localhost:{port}"})
        self.assertEqual(ok_origin[0], 200)

    def test_unknown_or_malformed_session(self):
        self.assertEqual(self.request("POST", f"/api/notes/{sid(99)}", {"status": "done"})[0], 404)
        self.assertEqual(self.request("POST", "/api/notes/abc", {"status": "done"})[0], 404)
        self.assertEqual(self.request("GET", "/api/nope")[0], 404)

    def test_body_limits(self):
        self.assertEqual(self.request("POST", f"/api/notes/{sid(1)}", {"note": "x" * 5000})[0], 413)
        self.assertEqual(self.request("POST", f"/api/notes/{sid(1)}", "[1, 2]")[0], 400)
        self.assertEqual(self.request("POST", f"/api/notes/{sid(1)}", "{nope")[0], 400)

    def test_open_calls_opener_with_resume_cmd(self):
        self.assertEqual(self.request("POST", f"/api/open/{sid(1)}", {}), (200, {"ok": True}))
        self.assertEqual(self.opened, [f"cd {CWD} && claude --resume {sid(1)}"])

    def test_open_failure(self):
        self.open_result = (False, "iTerm2 is not running")
        self.assertEqual(self.request("POST", f"/api/open/{sid(1)}", {}), (502, {"ok": False, "error": "iTerm2 is not running"}))

    def test_open_refuses_missing_cwd(self):
        self.missing = True
        status, body = self.request("POST", f"/api/open/{sid(1)}", {})
        self.assertEqual(status, 409)
        self.assertIn("no longer exists", body["error"])
        self.assertEqual(self.opened, [])

    def test_note_inherited_from_older_copy(self):
        self.fake.write_session(CWD, sid(2), [
            user("Analyzuj https://acme.atlassian.net/browse/PROJ-563", ts="2026-10-01T10:00:00Z", uuid="u1", cwd=CWD),
        ])
        self.app.notes.update(sid(2), status="waiting", note="from copy")
        rows = self.rows()
        self.assertEqual(list(rows), [sid(1)])
        self.assertEqual(rows[sid(1)]["note"]["note"], "from copy")
        status, body = self.request("POST", f"/api/notes/{sid(1)}", {"note": "new"})
        self.assertEqual(status, 200)
        self.assertEqual(body["note"], {"status": "waiting", "note": "new", "updated_at": body["note"]["updated_at"]})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests, they must fail**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: ERROR `ModuleNotFoundError: No module named 'server'`

- [ ] **Step 3: Implement**

`server.py`:

```python
"""Claude Sessions Dashboard: local HTTP server (listens on 127.0.0.1 only)."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import iterm
import live
import notes as notes_mod
import sessions

APP_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 7333
SESSION_ID_RE = re.compile(r"^[0-9a-f-]{36}$")
POST_PATH_RE = re.compile(r"/api/(notes|open)/([^/?#]+)")
MAX_BODY = 4096
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/filter.js": ("filter.js", "text/javascript; charset=utf-8"),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class App:
    def __init__(self, claude_dir, data_dir, *, static_dir=APP_DIR / "static", opener=iterm.open_in_iterm,
                 live_fn=live.get_live, dev_root=sessions.DEV_ROOT, is_missing=sessions.cwd_missing) -> None:
        self.claude_dir = Path(claude_dir)
        self.static_dir = Path(static_dir)
        self.cache = sessions.SessionCache(self.claude_dir)
        self.notes = notes_mod.NotesStore(Path(data_dir) / "notes.json")
        self.opener = opener
        self.live_fn = live_fn
        self.dev_root = dev_root
        self.is_missing = is_missing
        self.port = DEFAULT_PORT
        self._rows: dict[str, dict] = {}
        self._lock = threading.Lock()

    def snapshot(self) -> dict:
        rows, hosts = sessions.build_rows(self.cache.load(), dev_root=self.dev_root, is_missing=self.is_missing)
        live_map = self.live_fn(self.claude_dir)
        all_notes = self.notes.all()
        for row in rows:
            row["live"] = live_map.get(row["session_id"])
            row["note"] = all_notes.get(row["session_id"]) or next(
                (all_notes[c["session_id"]] for c in row["older_copies"] if c["session_id"] in all_notes), None
            )
        with self._lock:
            self._rows = {r["session_id"]: r for r in rows}
        return {"generated_at": now_iso(), "jira_hosts": hosts, "rows": rows}

    def find_row(self, session_id: str) -> dict | None:
        with self._lock:
            row = self._rows.get(session_id)
        if row is None:
            self.snapshot()
            with self._lock:
                row = self._rows.get(session_id)
        return row

    def save_note(self, session_id: str, fields: dict) -> dict:
        row = self.find_row(session_id) or {}
        inherited = row.get("note")
        if inherited and session_id not in self.notes.all():
            fields = {"status": inherited.get("status"), "note": inherited.get("note") or "", **fields}
        return self.notes.update(session_id, **fields)

    def open_session(self, row: dict) -> tuple[int, dict]:
        if not row.get("resume_cmd"):
            return 409, {"ok": False, "error": "Session has no known directory."}
        if "cwd-missing" in row.get("warnings", []):
            return 409, {"ok": False, "error": f"Directory {row['cwd']} no longer exists."}
        ok, error = self.opener(row["resume_cmd"])
        return (200, {"ok": True}) if ok else (502, {"ok": False, "error": error})


class Handler(BaseHTTPRequestHandler):
    server_version = "ClaudeDashboard/1.0"

    @property
    def app(self) -> App:
        return self.server.app

    def log_request(self, code="-", size="-"):
        # Polling every 10 s would flood the log; we log only errors.
        try:
            if int(code) < 400:
                return
        except (TypeError, ValueError):
            pass
        super().log_request(code, size)

    def _allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.app.port}", f"localhost:{self.app.port}"}

    def do_GET(self):
        if self.headers.get("Host") not in self._allowed_hosts():
            return self._json({"error": "forbidden host"}, 403)
        path = self.path.split("?", 1)[0]
        if path in STATIC_FILES:
            name, ctype = STATIC_FILES[path]
            return self._file(self.app.static_dir / name, ctype)
        if path == "/api/health":
            return self._json({"ok": True})
        if path == "/api/sessions":
            return self._json(self.app.snapshot())
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        allowed = self._allowed_hosts()
        if self.headers.get("Host") not in allowed:
            return self._json({"error": "forbidden host"}, 403)
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {f"http://{h}" for h in allowed}:
            return self._json({"error": "forbidden origin"}, 403)
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            return self._json({"error": "Content-Type must be application/json"}, 403)
        match = POST_PATH_RE.fullmatch(self.path)
        if not match or not SESSION_ID_RE.match(match.group(2)):
            return self._json({"error": "not found"}, 404)
        body = self._read_body()
        if body is None:
            return None
        action, session_id = match.groups()
        row = self.app.find_row(session_id)
        if row is None:
            return self._json({"error": "unknown session"}, 404)
        if action == "notes":
            fields = {k: body[k] for k in ("status", "note") if k in body}
            try:
                entry = self.app.save_note(session_id, fields)
            except notes_mod.NotesError as e:
                return self._json({"error": str(e)}, 400)
            return self._json({"ok": True, "note": entry})
        status, payload = self.app.open_session(row)
        return self._json(payload, status)

    def _read_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self._json({"error": "invalid Content-Length"}, 400)
            return None
        if length > MAX_BODY:
            self.rfile.read(min(length, 1 << 20))  # drain the body, otherwise the client may get an RST instead of a response
            self._json({"error": "request body too large"}, 413)
            return None
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._json({"error": "invalid JSON"}, 400)
            return None
        if not isinstance(body, dict):
            self._json({"error": "body must be a JSON object"}, 400)
            return None
        return body

    def _json(self, obj, status: int = 200) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _file(self, path: Path, ctype: str) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            return self._json({"error": "not found"}, 404)
        self._send(200, data, ctype)

    def _send(self, status: int, data: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, app: App) -> None:
        self.app = app
        super().__init__(address, Handler)
        app.port = self.server_address[1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Claude Sessions Dashboard")
    parser.add_argument("--port", type=int, default=int(os.environ.get("CLAUDE_DASHBOARD_PORT", DEFAULT_PORT)))
    parser.add_argument("--claude-dir", type=Path, default=Path.home() / ".claude")
    parser.add_argument("--data-dir", type=Path,
                        default=Path.home() / "Library" / "Application Support" / "claude-dashboard")
    args = parser.parse_args(argv)
    app = App(args.claude_dir, args.data_dir)
    try:
        httpd = DashboardServer(("127.0.0.1", args.port), app)
    except OSError as e:
        print(f"Cannot open port {args.port}: {e}", file=sys.stderr, flush=True)
        return 1
    threading.Thread(target=app.cache.load, name="warmup", daemon=True).start()
    print(f"Claude Sessions Dashboard: http://127.0.0.1:{app.port}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests, they must pass**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: all tests OK.

- [ ] **Step 5: Check against real data on the development port**

Run (server runs in the background, data goes to a temp directory so the real `notes.json` is not created):
```bash
python3 server.py --port 7334 --data-dir "$TMPDIR/cd-dev" & SRV=$!; sleep 3
curl -s http://127.0.0.1:7334/api/health
curl -s http://127.0.0.1:7334/api/sessions | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['rows']), 'rows;', sum(1 for r in d['rows'] if r['live']), 'live')"
curl -s -o /dev/null -w '%{http_code}\n' -H 'Host: evil.example:7334' http://127.0.0.1:7334/api/sessions
kill $SRV
```
Expected: `{"ok": true}`, dozens of rows with several `live`, and `403` for a foreign Host.

- [ ] **Step 6: Commit**

```bash
git add tests/test_server.py server.py
git commit -m "Add localhost HTTP API with host/origin checks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Frontend (`static/index.html`, `filter.js`, `app.js`)

**Files:**
- Create: `static/filter.js`
- Create: `tests/js/test_filter.js`
- Create: `tests/test_static.py`
- Create: `static/index.html`
- Create: `static/app.js`

**Interfaces:**
- Consumes: `GET /api/sessions` (row shape from Task 3, plus `live` and `note` from Task 7), `POST /api/notes/<id>`, `POST /api/open/<id>`.
- Produces:
  - `window.DashFilter` / `module.exports` with the functions:
    - `matches(row, prefs) -> bool`,
    - `groupRows(rows, mode) -> [{key, label, rows}]`, where `mode` ∈ `"dir" | "issue" | "none"`,
    - `relTime(ts, now?) -> string`,
    - `dirCounts(rows) -> [{dir, count}]`,
    - `NO_ISSUE`.
  - `prefs` = `{q, dirs: string[], hide: string[], runningOnly, showStubs, group}`.

- [ ] **Step 1: Write failing JS tests and a static test**

`tests/js/test_filter.js`:

```js
"use strict";
const assert = require("assert");
const F = require("../../static/filter.js");

const base = {
  session_id: "s", jira_key: null, jira_keys: [], topic: "", title: null, branches: [], display_dir: "acme/shop",
  worktree: null, search_text: "", note: null, cwd: "/w/acme/shop", is_stub: false, live: null,
  last_ts: "2026-10-01T10:00:00Z",
};
const row = (o) => Object.assign({}, base, o);
const prefs = (o) => Object.assign({ q: "", dirs: [], hide: ["done", "archived"], runningOnly: false, showStubs: false }, o);

const tests = {
  "hides done and archived by default"() {
    assert.strictEqual(F.matches(row({ note: { status: "done" } }), prefs()), false);
    assert.strictEqual(F.matches(row({ note: { status: "archived" } }), prefs()), false);
    assert.strictEqual(F.matches(row({ note: { status: "waiting" } }), prefs()), true);
    assert.strictEqual(F.matches(row(), prefs()), true);
  },
  "hiding 'no status' uses empty string"() {
    assert.strictEqual(F.matches(row(), prefs({ hide: [""] })), false);
  },
  "search finds done session by jira number"() {
    assert.strictEqual(F.matches(row({ jira_keys: ["PROJ-563"], note: { status: "done" } }), prefs({ q: "563" })), true);
  },
  "search finds stub"() {
    assert.strictEqual(F.matches(row({ is_stub: true, branches: ["me/bugfix/PROJ-584-x"] }), prefs({ q: "proj-584" })), true);
  },
  "stubs hidden without search unless enabled"() {
    assert.strictEqual(F.matches(row({ is_stub: true }), prefs()), false);
    assert.strictEqual(F.matches(row({ is_stub: true }), prefs({ showStubs: true })), true);
  },
  "search requires all terms, case insensitive, covers prompts, note, cwd"() {
    const r = row({ topic: "Contoso GA", search_text: "run the tests", note: { status: "", note: "Waiting for JH" } });
    assert.strictEqual(F.matches(r, prefs({ q: "contoso TESTS" })), true);
    assert.strictEqual(F.matches(r, prefs({ q: "waiting" })), true);
    assert.strictEqual(F.matches(r, prefs({ q: "shop" })), true);
    assert.strictEqual(F.matches(r, prefs({ q: "contoso nothing" })), false);
  },
  "dir filter and running-only apply even when searching"() {
    const r = row({ jira_keys: ["PROJ-1"] });
    assert.strictEqual(F.matches(r, prefs({ q: "proj-1", dirs: ["budget"] })), false);
    assert.strictEqual(F.matches(r, prefs({ q: "proj-1", dirs: ["acme/shop"] })), true);
    assert.strictEqual(F.matches(r, prefs({ q: "proj-1", runningOnly: true })), false);
    assert.strictEqual(F.matches(row({ live: { status: "busy" } }), prefs({ runningOnly: true })), true);
  },
  "group by issue puts no-issue last and sorts by recency"() {
    const rows = [
      row({ session_id: "a", jira_key: null, last_ts: "2026-10-07T00:00:00Z" }),
      row({ session_id: "b", jira_key: "PROJ-1", last_ts: "2026-10-01T00:00:00Z" }),
      row({ session_id: "c", jira_key: "PROJ-2", last_ts: "2026-10-05T00:00:00Z" }),
      row({ session_id: "d", jira_key: "PROJ-1", last_ts: "2026-10-06T00:00:00Z" }),
    ];
    const groups = F.groupRows(rows, "issue");
    assert.deepStrictEqual(groups.map((g) => g.key), ["PROJ-1", "PROJ-2", F.NO_ISSUE]);
    assert.deepStrictEqual(groups[0].rows.map((r) => r.session_id), ["d", "b"]);
  },
  "group by dir sorts groups by most recent row"() {
    const rows = [
      row({ session_id: "a", display_dir: "budget", last_ts: "2026-10-01T00:00:00Z" }),
      row({ session_id: "b", display_dir: "acme/shop", last_ts: "2026-10-03T00:00:00Z" }),
    ];
    assert.deepStrictEqual(F.groupRows(rows, "dir").map((g) => g.key), ["acme/shop", "budget"]);
  },
  "group none keeps one unlabeled group sorted by recency"() {
    const rows = [row({ session_id: "a", last_ts: "2026-10-01T00:00:00Z" }), row({ session_id: "b", last_ts: "2026-10-02T00:00:00Z" })];
    const groups = F.groupRows(rows, "none");
    assert.strictEqual(groups.length, 1);
    assert.strictEqual(groups[0].label, "");
    assert.deepStrictEqual(groups[0].rows.map((r) => r.session_id), ["b", "a"]);
  },
  "relTime"() {
    const now = Date.parse("2026-10-07T12:00:00Z");
    assert.strictEqual(F.relTime("2026-10-07T11:59:30Z", now), "just now");
    assert.strictEqual(F.relTime("2026-10-07T11:55:00Z", now), "5 min ago");
    assert.strictEqual(F.relTime("2026-10-07T09:00:00Z", now), "3 h ago");
    assert.strictEqual(F.relTime("2026-10-06T10:00:00Z", now), "yesterday");
    assert.strictEqual(F.relTime("2026-10-02T12:00:00Z", now), "5 days ago");
    assert.strictEqual(F.relTime(null, now), "");
    assert.strictEqual(F.relTime("nonsense", now), "");
  },
  "dirCounts ignores stubs and sorts by name"() {
    const rows = [row({ display_dir: "budget" }), row({ display_dir: "acme/shop" }), row({ display_dir: "acme/shop" }), row({ display_dir: "x", is_stub: true })];
    assert.deepStrictEqual(F.dirCounts(rows), [{ dir: "acme/shop", count: 2 }, { dir: "budget", count: 1 }]);
  },
};

let failed = 0;
for (const [name, fn] of Object.entries(tests)) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (e) {
    failed += 1;
    console.error(`FAIL - ${name}\n${e.stack}`);
  }
}
if (failed) {
  console.error(`${failed} test(s) failed`);
  process.exit(1);
}
console.log("all filter.js tests passed");
```

`tests/test_static.py`:

```python
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"


class StaticFilesTest(unittest.TestCase):
    def test_no_html_injection_apis(self):
        for name in ("app.js", "filter.js"):
            src = (STATIC / name).read_text(encoding="utf-8")
            for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
                with self.subTest(file=name, api=banned):
                    self.assertNotIn(banned, src)

    def test_index_loads_filter_before_app_and_no_external_resources(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertLess(html.index('src="/filter.js"'), html.index('src="/app.js"'))
        self.assertIn("<title>Claude Sessions</title>", html)
        self.assertNotRegex(html, r'(src|href)\s*=\s*["\']?(https?:)?//')  # no external scripts/styles/CDN

    def test_filter_js_under_node(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not on PATH")
        result = subprocess.run([node, str(ROOT / "tests" / "js" / "test_filter.js")], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests, they must fail**

Run: `node tests/js/test_filter.js; python3 -m unittest tests.test_static -v`
Expected: Node fails with `Cannot find module '../../static/filter.js'`, the Python test fails on the missing files (`FileNotFoundError`).

- [ ] **Step 3: Implement `static/filter.js`**

```js
/* Pure dashboard functions – shared by the browser (window.DashFilter) and the Node tests (module.exports). */
(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.DashFilter = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";
  const NO_ISSUE = "no issue";

  function haystack(r) {
    return [
      r.session_id, (r.jira_keys || []).join(" "), r.topic, r.title || "", (r.branches || []).join(" "),
      r.display_dir, r.worktree || "", r.cwd || "", r.search_text || "", (r.note && r.note.note) || "",
    ].join("\n").toLowerCase();
  }

  function matches(r, f) {
    if (f.runningOnly && !r.live) return false;
    if (f.dirs && f.dirs.length && !f.dirs.includes(r.display_dir)) return false;
    const terms = (f.q || "").trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (terms.length) {
      // Search deliberately ignores status and stub hiding: "563" must find even a done session.
      const hay = haystack(r);
      return terms.every((t) => hay.includes(t));
    }
    if (!f.showStubs && r.is_stub) return false;
    const status = (r.note && r.note.status) || "";
    return !(f.hide || []).includes(status);
  }

  const byLastDesc = (a, b) => (b.last_ts || "").localeCompare(a.last_ts || "");

  function groupRows(rows, mode) {
    if (mode === "none") return [{ key: "", label: "", rows: rows.slice().sort(byLastDesc) }];
    const map = new Map();
    for (const r of rows) {
      const key = mode === "issue" ? r.jira_key || NO_ISSUE : r.display_dir;
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(r);
    }
    const groups = Array.from(map, ([key, rs]) => ({ key, label: key, rows: rs.sort(byLastDesc) }));
    groups.sort((a, b) => {
      if (mode === "issue" && (a.key === NO_ISSUE) !== (b.key === NO_ISSUE)) return a.key === NO_ISSUE ? 1 : -1;
      return byLastDesc(a.rows[0], b.rows[0]);
    });
    return groups;
  }

  function relTime(ts, now) {
    if (!ts) return "";
    const t = Date.parse(ts);
    if (Number.isNaN(t)) return "";
    const s = Math.max(0, ((now === undefined ? Date.now() : now) - t) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)} min ago`;
    if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
    const d = Math.floor(s / 86400);
    if (d === 1) return "yesterday";
    if (d < 30) return `${d} days ago`;
    return new Date(t).toLocaleDateString("cs-CZ");
  }

  function dirCounts(rows) {
    const counts = new Map();
    for (const r of rows) {
      if (r.is_stub) continue;
      counts.set(r.display_dir, (counts.get(r.display_dir) || 0) + 1);
    }
    return Array.from(counts, ([dir, count]) => ({ dir, count })).sort((a, b) => a.dir.localeCompare(b.dir));
  }

  return { NO_ISSUE, matches, groupRows, relTime, dirCounts };
});
```

- [ ] **Step 4: Implement `static/index.html`**

```html
<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude Sessions</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E%3Crect width='16' height='16' rx='3' fill='%237aa2f7'/%3E%3Cpath d='M4 5h8M4 8h8M4 11h5' stroke='%230f1115' stroke-width='1.6'/%3E%3C/svg%3E">
<style>
  :root {
    --bg: #0f1115; --panel: #161a21; --row: #141820; --row-hover: #1a1f29; --border: #262c38;
    --text: #d7dce5; --muted: #8a93a3; --faint: #5f6878; --accent: #7aa2f7;
    --green: #4ade80; --yellow: #facc15; --red: #f87171; --chip: #1c2230; --chip-on: #263553;
    --mono: ui-monospace, SFMono-Regular, Menlo, monospace;
    --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  * { box-sizing: border-box; }
  body { margin: 0; background: var(--bg); color: var(--text); font: 13px/1.45 var(--sans); }
  header { position: sticky; top: 0; z-index: 5; background: var(--panel); border-bottom: 1px solid var(--border);
           padding: 10px 16px; display: flex; flex-direction: column; gap: 8px; }
  .bar { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; }
  h1 { font-size: 14px; margin: 0; font-weight: 600; white-space: nowrap; }
  #q { flex: 1 1 280px; min-width: 200px; background: var(--bg); border: 1px solid var(--border); color: var(--text);
       padding: 6px 10px; border-radius: 6px; font: inherit; }
  #q:focus, select:focus, input.note:focus { outline: 1px solid var(--accent); border-color: var(--accent); }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; }
  .chips .lbl { color: var(--faint); font-size: 12px; margin-right: 2px; }
  .chip { background: var(--chip); border: 1px solid var(--border); color: var(--muted); border-radius: 999px;
          padding: 1px 9px; cursor: pointer; font-size: 12px; user-select: none; }
  .chip.on { background: var(--chip-on); color: var(--text); border-color: var(--accent); }
  .chip .n, h2 .n { color: var(--faint); font-size: 11px; }
  label.tog { color: var(--muted); font-size: 12px; display: inline-flex; gap: 4px; align-items: center; cursor: pointer; }
  select, input.note { background: var(--bg); color: var(--text); border: 1px solid var(--border); border-radius: 5px;
                       font: inherit; font-size: 12px; padding: 3px 6px; }
  #count { color: var(--faint); font-size: 12px; margin-left: auto; }
  #banner { background: #3b1d1d; color: var(--red); padding: 6px 16px; border-bottom: 1px solid #5b2626; }
  main { padding: 4px 16px 48px; }
  h2 { font: 600 12px/1.4 var(--mono); color: var(--muted); margin: 18px 0 6px; }
  .row { display: grid; grid-template-columns: 14px minmax(0, 1fr) 170px 96px 290px; gap: 4px 12px; padding: 7px 10px;
         border: 1px solid var(--border); border-radius: 6px; background: var(--row); margin-bottom: 4px; cursor: pointer; }
  .row:hover { background: var(--row-hover); }
  .row.stub { opacity: .55; }
  .row.open { border-color: #34405a; }
  .dot { width: 9px; height: 9px; border-radius: 50%; margin-top: 5px; }
  .dot.busy { background: var(--green); box-shadow: 0 0 6px var(--green); }
  .dot.idle { background: var(--yellow); }
  .body { min-width: 0; }
  .main-line { display: flex; gap: 8px; align-items: baseline; min-width: 0; }
  .key { font: 600 13px var(--mono); color: var(--accent); white-space: nowrap; text-decoration: none; }
  a.key:hover { text-decoration: underline; }
  .topic { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .sub, .prompts { font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .sub { color: var(--muted); font-family: var(--mono); }
  .prompts { color: var(--faint); }
  .dir { font: 12px var(--mono); color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; padding-top: 1px; }
  .when { font-size: 12px; color: var(--muted); white-space: nowrap; padding-top: 1px; }
  .actions { display: flex; flex-wrap: wrap; gap: 4px; justify-content: flex-end; align-content: flex-start; cursor: auto; }
  .actions input.note { flex: 1 1 100%; }
  button { background: var(--chip); color: var(--text); border: 1px solid var(--border); border-radius: 5px; padding: 2px 8px;
           font: inherit; font-size: 12px; cursor: pointer; }
  button:hover:not(:disabled) { border-color: var(--accent); }
  button:disabled { opacity: .4; cursor: not-allowed; }
  .warn { color: var(--yellow); }
  .details { grid-column: 2 / -1; border-top: 1px dashed var(--border); padding-top: 8px; margin-top: 4px; font-size: 12px; cursor: auto; }
  .details dl { display: grid; grid-template-columns: 130px minmax(0, 1fr); gap: 3px 12px; margin: 0 0 8px; }
  .details dt { color: var(--faint); }
  .details dd { margin: 0; font-family: var(--mono); word-break: break-all; white-space: pre-line; }
  .details h3 { font-size: 12px; color: var(--faint); font-weight: 600; margin: 10px 0 4px; }
  .details ol { margin: 0; padding-left: 20px; }
  .details li, .details p.prompt { margin: 0 0 4px; white-space: pre-wrap; word-break: break-word; }
  .suspect { color: var(--faint); }
  .empty { color: var(--faint); padding: 24px 0; }
  #toasts { position: fixed; bottom: 16px; right: 16px; display: flex; flex-direction: column; gap: 6px; z-index: 10; }
  .toast { background: var(--panel); border: 1px solid var(--border); padding: 8px 12px; border-radius: 6px; max-width: 460px;
           word-break: break-all; box-shadow: 0 4px 16px rgba(0, 0, 0, .4); }
  .toast.err { border-color: var(--red); color: var(--red); }
  @media (max-width: 980px) {
    .row { grid-template-columns: 14px minmax(0, 1fr); }
    .dir, .when, .actions { grid-column: 2; }
    .actions { justify-content: flex-start; }
  }
</style>
</head>
<body>
<header>
  <div class="bar">
    <h1>Claude Sessions</h1>
    <input id="q" type="search" placeholder="Search: PROJ-563, branch, directory, prompt text, note…  ( / )" autocomplete="off">
    <label class="tog"><input id="running" type="checkbox"> running only</label>
    <label class="tog"><input id="stubs" type="checkbox"> show empty</label>
    <label class="tog">group by
      <select id="group">
        <option value="dir">directory</option>
        <option value="issue">issue</option>
        <option value="none">none</option>
      </select>
    </label>
    <span id="count"></span>
  </div>
  <div class="chips" id="dirs"></div>
  <div class="chips" id="statuses"></div>
</header>
<div id="banner" hidden></div>
<main id="list"><p class="empty">Loading…</p></main>
<div id="toasts"></div>
<script src="/filter.js"></script>
<script src="/app.js"></script>
</body>
</html>
```

- [ ] **Step 5: Implement `static/app.js`**

```js
/* Claude Sessions Dashboard – rendering and actions. DOM API only (no innerHTML): text from transcripts is untrusted. */
(function () {
  "use strict";
  const F = window.DashFilter;
  const STATUSES = [["", "no status"], ["active", "active"], ["waiting", "waiting"], ["done", "done"], ["archived", "archived"]];
  const WARN_LABELS = {
    "cwd-missing": "directory no longer exists",
    "cwd-mismatch": "cwd does not match the project folder",
    "no-cwd": "unknown directory",
    unreadable: "file cannot be read",
  };
  const PREFS_KEY = "claude-dashboard:prefs:v1";
  const REFRESH_MS = 10000;

  const prefs = loadPrefs();
  const expanded = new Set();
  let data = { rows: [], jira_hosts: {} };
  let loaded = false;
  let pendingRender = false;
  let inFlight = false;

  const $ = (sel) => document.querySelector(sel);

  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "value") node.value = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : String(v));
    }
    for (const c of children.flat(2)) {
      if (c === null || c === undefined || c === false || c === "") continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }

  function loadPrefs() {
    const defaults = { q: "", dirs: [], hide: ["done", "archived"], runningOnly: false, showStubs: false, group: "dir" };
    try {
      const saved = JSON.parse(localStorage.getItem(PREFS_KEY) || "{}");
      return Object.assign(defaults, saved && typeof saved === "object" ? saved : {});
    } catch (e) {
      return defaults;
    }
  }

  function savePrefs() {
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (e) { /* private window etc. */ }
  }

  function toggle(list, value) {
    const i = list.indexOf(value);
    if (i === -1) list.push(value); else list.splice(i, 1);
  }

  function plural(n) {
    if (n === 1) return "prompt";
    // English has only singular and plural.
    return "prompts";
  }

  function trunc(text, n) {
    return text.length > n ? text.slice(0, n - 1) + "…" : text;
  }

  function fmt(ts) {
    return ts ? new Date(ts).toLocaleString("en-GB") : "—";
  }

  function toast(message, isError) {
    const node = el("div", { class: isError ? "toast err" : "toast" }, message);
    $("#toasts").append(node);
    setTimeout(() => node.remove(), isError ? 8000 : 2500);
  }

  async function post(url, body) {
    try {
      const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const json = await res.json().catch(() => ({}));
      if (!res.ok) return { ok: false, error: json.error || `HTTP ${res.status}` };
      return json;
    } catch (e) {
      return { ok: false, error: "Server is not responding." };
    }
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      const ta = el("textarea", {}, text);
      document.body.append(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    toast(`Copied: ${text}`);
  }

  async function openSession(r) {
    if (r.live && !confirm(`This session is running right now (${r.live.status}, pid ${r.live.pid}).\nOpen a second instance anyway?`)) return;
    const res = await post(`/api/open/${encodeURIComponent(r.session_id)}`, {});
    if (res.ok) toast("Opened in iTerm2.");
    else toast(`Could not open: ${res.error}`, true);
  }

  async function saveNote(r, fields) {
    const res = await post(`/api/notes/${encodeURIComponent(r.session_id)}`, fields);
    if (!res.ok) {
      toast(`Save failed: ${res.error}`, true);
      return;
    }
    const saved = res.note && (res.note.status || res.note.note) ? res.note : null;
    // A refresh may have run while typing and replaced data.rows with new objects → also update the current row by id.
    const current = data.rows.find((x) => x.session_id === r.session_id);
    for (const target of current && current !== r ? [r, current] : [r]) target.note = saved;
    toast("Saved.");
    render();
  }

  function jiraLink(key, cls) {
    const host = data.jira_hosts[key.split("-")[0]];
    if (!host) return el("span", { class: cls || null }, key);
    return el("a", { class: cls || null, href: `https://${host}/browse/${key}`, target: "_blank", rel: "noopener" }, key);
  }

  function warnText(r) {
    return r.warnings.map((w) => WARN_LABELS[w] || w).join(", ");
  }

  function renderActions(r) {
    const status = (r.note && r.note.status) || "";
    const noteValue = (r.note && r.note.note) || "";
    const select = el("select", { title: "My status" },
      STATUSES.map(([value, label]) => {
        const option = el("option", { value }, label);
        option.selected = value === status;
        return option;
      }));
    select.addEventListener("change", () => {
      select.blur();
      saveNote(r, { status: select.value || null });
    });
    const note = el("input", { class: "note", type: "text", placeholder: "note…", maxlength: 500, value: noteValue });
    note.addEventListener("keydown", (e) => {
      if (e.key === "Enter") note.blur();
      if (e.key === "Escape") { note.value = noteValue; note.blur(); }
    });
    note.addEventListener("blur", () => {
      const value = note.value.trim();
      if (value !== noteValue) saveNote(r, { note: value });
    });
    return el("div", { class: "actions" },
      el("button", { type: "button", title: r.resume_cmd || "Unknown directory", disabled: !r.resume_cmd, onclick: () => copyText(r.resume_cmd) }, "⧉ Copy"),
      el("button", { type: "button", title: "Open in a new iTerm2 tab", disabled: !r.resume_cmd, onclick: () => openSession(r) }, "▶ Open"),
      select, note);
  }

  function renderDetails(r) {
    const dt = (label) => el("dt", {}, label);
    const items = [
      dt("Session ID"), el("dd", {}, r.session_id),
      dt("Directory"), el("dd", {}, r.cwd || "—"),
      dt("Resume"), el("dd", {}, r.resume_cmd || "—"),
      dt("Branches"), el("dd", {}, r.branches.join(" → ") || "—"),
      dt("Jira"), el("dd", {}, r.jira_keys.length ? r.jira_keys.map((k, i) => [i ? ", " : null, jiraLink(k)]) : "—"),
    ];
    if (r.title) items.push(dt("Title"), el("dd", { class: r.title_suspect ? "suspect" : null }, r.title, r.title_suspect ? "  (possibly inherited)" : null));
    items.push(dt("Started"), el("dd", {}, fmt(r.first_ts)), dt("Last activity"), el("dd", {}, fmt(r.last_ts)));
    if (r.live) items.push(dt("Running"), el("dd", {}, `${r.live.status}, pid ${r.live.pid}`));
    if (r.warnings.length) items.push(dt("Warnings"), el("dd", { class: "warn" }, warnText(r)));
    if (r.older_copies.length) {
      items.push(dt("Older copies"), el("dd", {}, r.older_copies.map((c) => `${c.session_id}  (${fmt(c.last_ts)}, ${c.prompt_count} ${plural(c.prompt_count)})`).join("\n")));
    }
    return el("div", { class: "details" },
      el("dl", {}, items),
      r.first_prompt ? [el("h3", {}, "First prompt"), el("p", { class: "prompt" }, r.first_prompt)] : null,
      r.recent_prompts.length ? [el("h3", {}, "Recent prompts (newest last)"), el("ol", {}, r.recent_prompts.map((p) => el("li", {}, p)))] : null);
  }

  function renderRow(r) {
    const open = expanded.has(r.session_id);
    const row = el("div", { class: `row${r.is_stub ? " stub" : ""}${open ? " open" : ""}` });
    row.addEventListener("click", (e) => {
      if (e.target.closest(".actions, a, .details")) return;
      if (open) expanded.delete(r.session_id); else expanded.add(r.session_id);
      render();
    });
    const sub = [r.branch || "—", r.worktree ? `worktree ${r.worktree}` : null, `${r.prompt_count} ${plural(r.prompt_count)}`]
      .filter(Boolean).join(" · ");
    const lastPrompts = r.recent_prompts.slice(-2).reverse().map((p) => `» ${trunc(p, 120)}`).join("    ");
    row.append(
      el("div", { class: `dot ${r.live ? r.live.status : ""}`, title: r.live ? `running (${r.live.status}), pid ${r.live.pid}` : "not running" }),
      el("div", { class: "body" },
        el("div", { class: "main-line" }, r.jira_key ? jiraLink(r.jira_key, "key") : null, el("span", { class: "topic", title: r.topic }, r.topic)),
        el("div", { class: "sub" }, sub, r.warnings.length ? el("span", { class: "warn", title: warnText(r) }, "  ⚠ " + warnText(r)) : null),
        lastPrompts ? el("div", { class: "prompts" }, lastPrompts) : null),
      el("div", { class: "dir", title: r.cwd || "" }, r.display_dir),
      el("div", { class: "when", title: fmt(r.last_ts) }, F.relTime(r.last_ts)),
      renderActions(r));
    if (open) row.append(renderDetails(r));
    return row;
  }

  function renderChips() {
    // replaceChildren does not unpack arrays → always spread.
    $("#dirs").replaceChildren(el("span", { class: "lbl" }, "directories:"),
      ...F.dirCounts(data.rows).map(({ dir, count }) => el("span", {
        class: prefs.dirs.includes(dir) ? "chip on" : "chip",
        onclick: () => { toggle(prefs.dirs, dir); savePrefs(); render(); },
      }, dir, " ", el("span", { class: "n" }, count))));
    $("#statuses").replaceChildren(el("span", { class: "lbl" }, "show statuses:"),
      ...STATUSES.map(([value, label]) => el("span", {
        class: prefs.hide.includes(value) ? "chip" : "chip on",
        onclick: () => { toggle(prefs.hide, value); savePrefs(); render(); },
      }, label)));
  }

  function isEditing() {
    const a = document.activeElement;
    return !!(a && a.closest && a.closest("#list") && a.matches("input, select"));
  }

  function render() {
    if (isEditing()) { pendingRender = true; return; }
    pendingRender = false;
    renderChips();
    const visible = data.rows.filter((r) => F.matches(r, prefs));
    $("#count").textContent = `showing ${visible.length} of ${data.rows.length}`;
    const out = [];
    for (const group of F.groupRows(visible, prefs.group)) {
      out.push(el("section", {},
        group.label ? el("h2", {}, group.label, " ", el("span", { class: "n" }, group.rows.length)) : null,
        group.rows.map(renderRow)));
    }
    if (!visible.length) out.push(el("p", { class: "empty" }, loaded ? "Nothing matches the filters." : "Loading…"));
    $("#list").replaceChildren(...out);
  }

  async function refresh() {
    if (inFlight || document.hidden) return;
    inFlight = true;
    try {
      const res = await fetch("/api/sessions", { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
      loaded = true;
      const known = new Set(data.rows.map((r) => r.display_dir));
      const kept = prefs.dirs.filter((d) => known.has(d));
      if (kept.length !== prefs.dirs.length) { prefs.dirs = kept; savePrefs(); }
      $("#banner").hidden = true;
      render();
    } catch (e) {
      const banner = $("#banner");
      banner.textContent = `Server is not responding (${e.message}). Showing the last loaded data.`;
      banner.hidden = false;
    } finally {
      inFlight = false;
    }
  }

  function init() {
    const q = $("#q");
    q.value = prefs.q;
    q.addEventListener("input", () => { prefs.q = q.value; savePrefs(); render(); });
    const running = $("#running");
    running.checked = prefs.runningOnly;
    running.addEventListener("change", () => { prefs.runningOnly = running.checked; savePrefs(); render(); });
    const stubs = $("#stubs");
    stubs.checked = prefs.showStubs;
    stubs.addEventListener("change", () => { prefs.showStubs = stubs.checked; savePrefs(); render(); });
    const group = $("#group");
    group.value = prefs.group;
    group.addEventListener("change", () => { prefs.group = group.value; savePrefs(); render(); });
    document.addEventListener("keydown", (e) => {
      const typing = e.target.matches && e.target.matches("input, select, textarea");
      if (e.key === "/" && !typing) { e.preventDefault(); q.focus(); q.select(); }
    });
    document.addEventListener("focusout", () => setTimeout(() => { if (pendingRender) render(); }, 0));
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
    refresh();
    setInterval(refresh, REFRESH_MS);
  }

  init();
})();
```

- [ ] **Step 6: Run the tests, they must pass**

Run: `node tests/js/test_filter.js && python3 -m unittest discover -s tests -t . -v`
Expected: `all filter.js tests passed` and all Python tests OK, including `test_static`.

- [ ] **Step 7: Check against real data**

Run:
```bash
python3 server.py --port 7334 --data-dir "$TMPDIR/cd-dev" & SRV=$!; sleep 3
curl -s -o /dev/null -w 'index %{http_code}\n' http://127.0.0.1:7334/
curl -s -o /dev/null -w 'app.js %{http_code}\n' http://127.0.0.1:7334/app.js
curl -s -o /dev/null -w 'filter.js %{http_code}\n' http://127.0.0.1:7334/filter.js
open http://127.0.0.1:7334/
```
Expected: `200` for all three files and the page opens in the browser. Verify manually:
- rows grouped by directory,
- 🟢/🟡 on running sessions,
- searching "563" shows PROJ-563 in `acme/shop`,
- expanding a row shows the details,
- "Copy" pastes `cd '…/acme/shop' && claude --resume …`,
- changing the status to "done" hides the row, and it shows up again when searching,
- type a note, wait more than 10 s (a refresh runs) and then click outside the field: the note stays and does not revert after the next refresh.

Potom `kill $SRV`.

- [ ] **Step 8: XSS check with a fixture**

Run:
```bash
FX="$TMPDIR/cd-xss"; rm -rf "$FX"; mkdir -p "$FX/projects/-w-x" "$FX/sessions"
printf '%s\n' '{"type":"user","message":{"content":"<img src=x onerror=alert(1)> <b>bold</b>"},"timestamp":"2026-10-07T10:00:00Z","uuid":"u1","cwd":"/w/x","gitBranch":"master"}' > "$FX/projects/-w-x/00000001-0000-4000-8000-000000000000.jsonl"
python3 server.py --port 7335 --claude-dir "$FX" --data-dir "$FX/data" & SRV=$!; sleep 2; open http://127.0.0.1:7335/
```
Expected: the row literally shows the text `<img src=x onerror=alert(1)> <b>bold</b>`. No alert, no bold text. Then `kill $SRV`.

- [ ] **Step 9: Commit**

```bash
git add static tests/js tests/test_static.py
git commit -m "Add dashboard UI: search, grouping, live status, notes, resume actions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: launchd deployment, README, verification on real data

**Files:**
- Create: `launchd/install.sh`
- Create: `launchd/uninstall.sh`
- Create: `README.md`

**Interfaces:**
- Consumes: `server.py`, `sessions.py`, `live.py`, `notes.py`, `iterm.py`, `static/*` and the `server.main` CLI (`--port`).
- Produces: a running LaunchAgent `local.claude-sessions-dashboard` at `http://127.0.0.1:7333/`.

- [ ] **Step 1: Write `launchd/install.sh`**

```bash
#!/bin/bash
# Deploys the dashboard to ~/Library/Application Support/claude-dashboard/app and registers the LaunchAgent.
# Run again after every code change (it copies the files and restarts the agent).
set -euo pipefail

LABEL="local.claude-sessions-dashboard"
PORT="${CLAUDE_DASHBOARD_PORT:-7333}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUPPORT_DIR="$HOME/Library/Application Support/claude-dashboard"
APP_DIR="$SUPPORT_DIR/app"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/claude-dashboard.log"
PYTHON="$(command -v python3)"
DOMAIN="gui/$(id -u)"

mkdir -p "$APP_DIR/static" "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cp "$REPO_DIR"/server.py "$REPO_DIR"/sessions.py "$REPO_DIR"/live.py "$REPO_DIR"/notes.py "$REPO_DIR"/iterm.py "$APP_DIR/"
cp "$REPO_DIR"/static/index.html "$REPO_DIR"/static/app.js "$REPO_DIR"/static/filter.js "$APP_DIR/static/"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$APP_DIR/server.py</string>
    <string>--port</string>
    <string>$PORT</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF

if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
  launchctl bootout "$DOMAIN/$LABEL" || true
  for _ in $(seq 1 20); do
    launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1 || break
    sleep 0.25
  done
fi
launchctl bootstrap "$DOMAIN" "$PLIST"

for _ in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    echo "OK: dashboard is running at http://127.0.0.1:$PORT/"
    exit 0
  fi
  sleep 0.25
done
echo "Server is not responding on port $PORT, see log: $LOG" >&2
tail -n 20 "$LOG" >&2 || true
exit 1
```

- [ ] **Step 2: Write `launchd/uninstall.sh`**

```bash
#!/bin/bash
# Stops and unregisters the LaunchAgent and deletes the deployed copy of the application. Keeps the notes (notes.json).
set -euo pipefail

LABEL="local.claude-sessions-dashboard"
SUPPORT_DIR="$HOME/Library/Application Support/claude-dashboard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
rm -rf "$SUPPORT_DIR/app"
echo "Uninstalled. Notes were kept in: $SUPPORT_DIR/notes.json"
```

Run: `chmod +x launchd/install.sh launchd/uninstall.sh`

- [ ] **Step 3: Write `README.md`**

````markdown
# Claude Sessions Dashboard

A local overview of all Claude Code sessions from `~/.claude`: directory, Jira issue / topic, branch, recent prompts,
live status (🟢 busy / 🟡 idle), custom status and note. A session can be resumed by copying the command
`cd … && claude --resume …` or directly in a new iTerm2 tab.

**Address:** http://127.0.0.1:7333/ (localhost only)

## Install / update

```bash
./launchd/install.sh
```

- Copies the application to `~/Library/Application Support/claude-dashboard/app/` and registers the LaunchAgent
  `local.claude-sessions-dashboard`. It starts at login and restarts after a crash.
- After a code change, run `install.sh` again.
- Different port: `CLAUDE_DASHBOARD_PORT=7400 ./launchd/install.sh`.

## Uninstall

```bash
./launchd/uninstall.sh
```

Notes stay in `~/Library/Application Support/claude-dashboard/notes.json`.

## Development

```bash
python3 server.py --port 7334 --data-dir "$TMPDIR/cd-dev"   # runs directly from the repo
python3 -m unittest discover -s tests -t . -v               # all tests (incl. the filter.js node test)
```

- LaunchAgent log: `~/Library/Logs/claude-dashboard.log`.
- "Open in iTerm2": on first use macOS asks for permission to control iTerm2. If you deny it,
  allow it in System Settings → Privacy & Security → Automation.
- Claude Code deletes old transcripts (the `cleanupPeriodDays` setting). Such sessions disappear from the dashboard
  and cannot be resumed.

Design: `docs/superpowers/specs/2026-10-07-claude-sessions-dashboard-design.md`
````

- [ ] **Step 4: Run all tests**

Run: `python3 -m unittest discover -s tests -t . -v`
Expected: all tests OK (0 failures, 0 errors).

- [ ] **Step 5: Verify the port is free and install**

Run: `lsof -nP -iTCP:7333 -sTCP:LISTEN || echo "port 7333 free"` and then `./launchd/install.sh`
Expected: `port 7333 free` and then `OK: dashboard is running at http://127.0.0.1:7333/`.

- [ ] **Step 6: Verify on real data**

Run:
```bash
curl -s http://127.0.0.1:7333/api/sessions | python3 -c "
import json, sys
d = json.load(sys.stdin); rows = {r['session_id'][:8]: r for r in d['rows']}
print('rows', len(d['rows']), 'live', sum(1 for r in d['rows'] if r['live']), 'hosts', d['jira_hosts'])
r = rows.get('bd117c05'); print('PROJ-563:', r and (r['display_dir'], r['branch'], r['jira_key'], r['resume_cmd']))
c = rows.get('cd6e5aad'); print('fork:', c and [o['session_id'][:8] for o in c['older_copies']])
print('b0372375 as a separate row:', 'b0372375' in rows)
"
launchctl print "gui/$(id -u)/local.claude-sessions-dashboard" | grep -E 'state|pid'
```
Expected:
- `bd117c05` → `('acme/shop', 'me/bugfix/PROJ-563-duplicate-orders', 'PROJ-563', "cd /Users/me/Documents/Development/acme/shop && claude --resume bd117c05-…")`.
- `fork: ['b0372375']`.
- `b0372375 as a separate row: False`.
- `live` ≥ 1.
- `state = running`.

If the log reports `Operation not permitted`, the deployed copy is trying to read from `~/Documents`. Check that the plist points to `~/Library/Application Support/claude-dashboard/app/server.py`.

**TCC check for `cwd-missing`:** the server started via launchd calls `os.stat()` on cwd under `~/Documents`.
1. Check whether macOS showed the dialog "python3 would like to access files in your Documents folder".
   - If the user allows it, `cwd-missing` works.
   - If the user denies it, `stat` returns EPERM. `cwd_missing` then returns False, the warning never shows, but nothing else breaks.
2. Check the actual state. Find a session with a nonexistent directory in `/api/sessions` and see whether it has `cwd-missing`:
   ```bash
   curl -s http://127.0.0.1:7333/api/sessions | python3 -c "import json,sys,os; [print(r['session_id'][:8], r['cwd'], r['warnings']) for r in json.load(sys.stdin)['rows'] if r['cwd'] and not os.path.exists(r['cwd'])]"
   ```
   This command runs from the terminal, so `os.path.exists` sees the truth. If the printed rows differ from expectations (they lack `cwd-missing`), stat under launchd gets EPERM. Tell the user: the warning about deleted directories will work only after python3 is allowed access to Documents in System Settings → Privacy & Security → Files & Folders.

- [ ] **Step 7: Manual test "Open in iTerm2" (with the user)**

Open http://127.0.0.1:7333/, find a non-running session and click "▶ Open".
Expected:
- On first use macOS asks for permission to control iTerm2. Allow it.
- A new iTerm2 tab opens with `cd … && claude --resume …` and Claude resumes the session.

If an error comes up, the UI shows a toast with the text from osascript and a request error (502) is written to `~/Library/Logs/claude-dashboard.log`.

- [ ] **Step 8: Commit**

```bash
git add launchd README.md
git commit -m "Add launchd install/uninstall scripts and README

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
