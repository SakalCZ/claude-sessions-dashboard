"""Optional <data-dir>/config.json with typed defaults for thresholds and intervals."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

DEFAULTS = {
    "permission_notify_after_s": 60,
    "waiting_notify_after_s": 600,
    "resource_reminder_after_s": 3600,
    "cpu_load_factor": 1.0,
    "cpu_sustain_s": 120,
    "process_sample_interval_s": 10,
    "docker_sample_interval_s": 30,
    "notifications_enabled": True,
    "docker_project_dirs": {},
}


def _valid(default, value) -> bool:
    if isinstance(default, bool):
        return isinstance(value, bool)
    if isinstance(default, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
    return isinstance(value, type(default))


def load_config(path: Path) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return cfg
    except (OSError, ValueError) as e:
        print(f"config: ignoring {path}: {e}", file=sys.stderr, flush=True)
        return cfg
    if not isinstance(raw, dict):
        print(f"config: ignoring {path}: not a JSON object", file=sys.stderr, flush=True)
        return cfg
    for key, default in DEFAULTS.items():
        if key in raw and _valid(default, raw[key]):
            cfg[key] = copy.deepcopy(raw[key])
    cfg["docker_project_dirs"] = {
        k: v for k, v in cfg["docker_project_dirs"].items() if isinstance(k, str) and isinstance(v, str)
    }
    return cfg
