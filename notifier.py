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
