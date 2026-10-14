"""Claude subscription usage limits from the status line tap's session files and Claude Code's own usage cache."""
from __future__ import annotations

import contextlib
import json
import os
import threading
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
        self._lock = threading.Lock()  # HTTP threads and the monitor tick share the file cache

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
        with self._lock:
            now = self.clock()
            cache_windows, extra = cache_usage(self._load(self.claude_json))
            candidates = self._session_windows(now) + cache_windows
        windows, alerts = [], []
        for w in merge(candidates, now):
            w = {**w, "level": level(w["used_pct"], self.config)}
            alert = alert_for(w, now, self.tz)
            if alert:
                alerts.append(alert)
            windows.append({**w, "resets_at": iso(w["resets_at"]), "as_of": iso(w["as_of"])})
        as_of = max((w["as_of"] for w in windows), default=None)
        return {"windows": windows, "extra_usage": extra, "as_of": as_of, "alerts": alerts}
