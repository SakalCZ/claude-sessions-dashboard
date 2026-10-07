"""Parsování Claude Code transcriptů (~/.claude/projects/*/*.jsonl) na řádky dashboardu."""
from __future__ import annotations

import json
import os
import re
import shlex
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
PROMPT_STORE_LIMIT = 2000


def encode_cwd(cwd: str) -> str:
    """Stejné kódování, jakým Claude Code pojmenovává složky v ~/.claude/projects."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def prompt_text(rec: dict) -> str | None:
    """Text skutečného promptu uživatele, nebo None pro meta/systémové záznamy a tool results."""
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
    """Nahradí vložený obsah krátkou ukázkou; řádkování ponechá."""

    def replace(match: re.Match) -> str:
        flat = " ".join(match.group(1).split())
        if not flat:
            return "[vloženo]"
        return f"[vloženo: {flat[:60]}{'…' if len(flat) > 60 else ''}]"

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


class _SessionParser:
    """Akumuluje stav z JSONL řádků. Transcripty jsou append-only, takže parser umí pokračovat
    od posledního kompletního řádku; neukončený poslední řádek nechá na příště."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.offset = 0
        self.lines = 0
        self.cwds: list[str] = []
        self.branches: dict[str, None] = {}
        self.custom_title: str | None = None
        self.agent_name: str | None = None
        self.last_prompt: str | None = None
        self.prompts: list[str] = []
        self.first_ts: str | None = None
        self.last_ts: str | None = None
        self.root_uuid: str | None = None
        self.url_keys: list[str] = []
        self.hosts: dict[str, str] = {}

    def feed(self) -> None:
        """Zpracuje kompletní řádky od `offset` do konce souboru. OSError propaguje."""
        with open(self.path, "rb") as fh:
            fh.seek(self.offset)
            data = fh.read()
        end = data.rfind(b"\n")
        if end == -1:
            return
        for raw in data[:end].split(b"\n"):
            self.lines += 1
            self._line(raw)
        self.offset += end + 1

    def _line(self, raw: bytes) -> None:
        try:
            rec = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            return
        if not isinstance(rec, dict):
            return
        rtype = rec.get("type")
        if rtype == "custom-title":
            self.custom_title = rec.get("customTitle") or self.custom_title
            return
        if rtype == "agent-name":
            self.agent_name = rec.get("agentName") or self.agent_name
            return
        if rtype == "last-prompt":
            self.last_prompt = rec.get("lastPrompt") or self.last_prompt
            return
        cwd = rec.get("cwd")
        if isinstance(cwd, str) and cwd and cwd not in self.cwds:
            self.cwds.append(cwd)
        branch = rec.get("gitBranch")
        if isinstance(branch, str) and branch:
            self.branches.pop(branch, None)
            self.branches[branch] = None
        if rtype not in ("user", "assistant"):
            return
        ts = rec.get("timestamp")
        if isinstance(ts, str):
            if self.first_ts is None or ts < self.first_ts:
                self.first_ts = ts
            if self.last_ts is None or ts > self.last_ts:
                self.last_ts = ts
        if self.root_uuid is None and not rec.get("isSidechain") and rec.get("uuid"):
            self.root_uuid = rec["uuid"]
        text = prompt_text(rec)
        if text is None:
            return
        for host, key in JIRA_URL_RE.findall(text):
            self.url_keys.append(key)
            self.hosts.setdefault(key.split("-")[0], f"{host}.atlassian.net")
        self.prompts.append(clean_prompt(text)[:PROMPT_STORE_LIMIT])

    def session(self, mtime: float) -> Session:
        """Nový Session (kopie stavu), aby další `feed` neměnil už vydané objekty."""
        s = Session(session_id=self.path.stem, path=str(self.path), project_dir=self.path.parent.name, mtime=mtime)
        s.title = self.custom_title or self.agent_name
        s.last_prompt = self.last_prompt
        s.prompts = list(self.prompts)
        s.first_ts, s.last_ts = self.first_ts, self.last_ts
        s.root_uuid = self.root_uuid
        s.url_keys = list(self.url_keys)
        s.hosts = dict(self.hosts)
        s.branches = list(self.branches)
        s.cwd = _pick_cwd(self.cwds, s.project_dir, s.warnings)
        if s.last_ts is None and mtime:
            s.last_ts = datetime.fromtimestamp(mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return s


def _unreadable(path: Path) -> Session:
    s = Session(session_id=path.stem, path=str(path), project_dir=path.parent.name)
    s.warnings.append("unreadable")
    return s


def parse_session(path: Path) -> Session:
    path = Path(path)
    parser = _SessionParser(path)
    try:
        mtime = path.stat().st_mtime
        parser.feed()
    except OSError:
        return _unreadable(path)
    return parser.session(mtime)


@dataclass
class _CacheEntry:
    mtime_ns: int
    size: int
    ino: int
    parser: _SessionParser | None
    session: Session


class SessionCache:
    """Drží rozparsované sessions. Nezměněný soubor (mtime_ns, size) se nečte; soubor, který jen narostl,
    se dočte od posledního kompletního řádku; jinak (zkrácený / přepsaný) se parsuje znovu celý."""

    def __init__(self, claude_dir: Path) -> None:
        self.projects_dir = Path(claude_dir) / "projects"
        self.parse_count = 0
        self.lines_parsed = 0
        self._entries: dict[str, _CacheEntry] = {}
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
                    if cached and cached.mtime_ns == st.st_mtime_ns and cached.size == st.st_size:
                        result.append(cached.session)
                        continue
                    result.append(self._refresh(entry.path, st, cached))
            for stale in set(self._entries) - seen:
                del self._entries[stale]
            return result

    def _refresh(self, path: str, st: os.stat_result, cached: _CacheEntry | None) -> Session:
        self.parse_count += 1
        grown = cached and cached.parser and cached.ino == st.st_ino and st.st_size > cached.size
        parser = cached.parser if grown else _SessionParser(Path(path))
        before = parser.lines
        try:
            parser.feed()
            session = parser.session(st.st_mtime)
        except OSError:
            parser, session = None, _unreadable(Path(path))
        else:
            self.lines_parsed += parser.lines - before
        self._entries[path] = _CacheEntry(st.st_mtime_ns, st.st_size, st.st_ino, parser, session)
        return session
