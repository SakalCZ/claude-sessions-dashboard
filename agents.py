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
# Hook `since` is rounded down to the second; a status change must be clearly newer to override it.
STATUS_GRACE_MS = 1500
# Claude's own status (pid file) refines the hook state, because some transitions fire no hook event
# (Esc or deny on a permission prompt, an approved tool that runs long, another subagent's tool calls).
BUSY_OVERRIDES = {"permission", "question", "waiting", "failed", "idle"}
IDLE_OVERRIDES = {"working", "permission", "question"}


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
        status, live_ms, hook_ms = live.get("status"), live.get("status_updated_at"), iso_to_ms(since)
        newer = (hook_ms is not None and isinstance(live_ms, (int, float)) and not isinstance(live_ms, bool)
                 and live_ms > hook_ms + STATUS_GRACE_MS)
        if status == "waiting" and state not in ("permission", "question"):
            # Claude is blocked on the user right now (pid status "waiting" is only set for a pending prompt).
            state, since = "permission", ms_to_iso(live_ms) or since
        elif newer and status == "busy" and state in BUSY_OVERRIDES:
            state = "working"
        elif newer and status == "idle" and state in IDLE_OVERRIDES:
            state = "idle"
    else:
        state = {"idle": "waiting", "waiting": "permission"}.get(live.get("status"), "working")
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
