"""Detekce běžících Claude Code instancí z ~/.claude/sessions/<pid>.json."""
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
    """Start procesů ve stejném formátu jako `procStart` v pid souborech (C locale, UTC)."""
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
    # ps vrací kód 1, když některý pid neexistuje; výstup pro živé pid je i tak platný.
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
