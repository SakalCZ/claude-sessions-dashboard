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
EPISODE_TTL_S = 24 * 3600  # remember notified queue episodes this long, so one bad tick cannot re-notify


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
        self._episodes: dict[tuple[str, str], float] = {}
        self._primed = False
        self._levels: dict[str, int] = {}
        self._last_sent: dict[str, float] = {}
        self._last_level: dict[str, int] = {}

    def _deliver(self, title: str, message: str) -> None:
        if self.config["notifications_enabled"]:
            self._send(title, message)

    def check_queue(self, rows: list[dict]) -> None:
        now = self._clock()
        for r in rows:
            a = r.get("attention") or {}
            since = _parse_iso(a.get("since"))
            if not a.get("in_queue") or since is None or a.get("source") == "estimate":
                continue
            key = (r["session_id"], a["since"])
            limit = (self.config["permission_notify_after_s"] if a.get("group") == "blocking"
                     else self.config["waiting_notify_after_s"])
            age = now - since
            if not self._primed and age >= limit:
                self._episodes.setdefault(key, now)  # start-up: already overdue → treat as notified, no burst
            if age >= limit and key not in self._episodes:
                self._episodes[key] = now
                label = r.get("jira_key") or r.get("topic") or r["session_id"][:8]
                self._deliver("Claude is waiting", f"{label} · {r.get('display_dir')} · {a['state']} {int(age // 60)} min")
        self._episodes = {k: t for k, t in self._episodes.items() if now - t < EPISODE_TTL_S}
        self._primed = True

    def check_alerts(self, alerts: list[dict]) -> None:
        now = self._clock()
        by_kind = {a["kind"]: a for a in alerts}
        for kind in set(self._levels) | set(by_kind):
            alert = by_kind.get(kind)
            current = LEVEL_RANK.get(alert["level"], 0) if alert else 0
            previous = self._levels.get(kind, 0)
            last = self._last_sent.get(kind)
            quiet = last is not None and now - last < self.config["resource_reminder_after_s"]
            # A rise notifies unless it only returns to an already-notified level within the reminder window
            # (a flapping alert); a level that stays raised is reminded once per window.
            rise = current > previous and (current > self._last_level.get(kind, 0) or not quiet)
            remind = current and current == previous and not quiet
            if rise or remind:
                self._deliver(f"Mac resources: {alert['level']}", alert["message"])
                self._last_sent[kind] = now
                self._last_level[kind] = current
            self._levels[kind] = current
