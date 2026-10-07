"""Parsování Claude Code transcriptů (~/.claude/projects/*/*.jsonl) na řádky dashboardu."""
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
