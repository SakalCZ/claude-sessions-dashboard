"""Background sampling of Claude process trees, system memory/CPU, top apps and Docker usage (macOS)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

PRESSURE_LEVELS = {"1": "normal", "2": "warn", "4": "critical"}
MEM_UNITS = {"b": 1 / 1024 / 1024, "kib": 1 / 1024, "kb": 1 / 1000, "mib": 1, "mb": 1,
             "gib": 1024, "gb": 1000, "tib": 1024 * 1024}
SWAP_UNITS = {"K": 1 / 1024, "M": 1, "G": 1024}
PS_ARGS = ["ps", "-axo", "pid=,ppid=,rss=,%cpu=,tty=,comm="]


@dataclass(frozen=True)
class Proc:
    pid: int
    ppid: int
    rss_kb: int
    cpu: float
    tty: str
    comm: str


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ps(out: str) -> list[Proc]:
    procs = []
    for line in out.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        try:
            procs.append(Proc(int(parts[0]), int(parts[1]), int(parts[2]), float(parts[3].replace(",", ".")),
                              parts[4], parts[5].strip()))
        except ValueError:
            continue
    return procs


def app_name(comm: str) -> str:
    i = comm.find(".app/")
    if i != -1:
        return os.path.basename(comm[:i])
    if comm.endswith(".app"):
        return os.path.basename(comm[:-4])
    return os.path.basename(comm) or comm


def top_apps(procs: list[Proc], n: int = 5) -> list[dict]:
    rss: dict[str, int] = defaultdict(int)
    cpu: dict[str, float] = defaultdict(float)
    for p in procs:
        name = app_name(p.comm)
        rss[name] += p.rss_kb
        cpu[name] += p.cpu
    names = sorted(rss, key=lambda k: rss[k], reverse=True)[:n]
    return [{"name": k, "rss_mb": round(rss[k] / 1024), "cpu": round(cpu[k], 1)} for k in names]


def subtree(procs: list[Proc], root: int) -> list[Proc]:
    by_pid = {p.pid: p for p in procs}
    if root not in by_pid:
        return []
    children: dict[int, list[Proc]] = defaultdict(list)
    for p in procs:
        if p.pid != p.ppid:
            children[p.ppid].append(p)
    out, stack, seen = [], [by_pid[root]], set()
    while stack:
        p = stack.pop()
        if p.pid in seen:
            continue
        seen.add(p.pid)
        out.append(p)
        stack.extend(children.get(p.pid, []))
    return out


def tree_usage(procs: list[Proc], root: int) -> dict | None:
    tree = subtree(procs, root)
    if not tree:
        return None
    top = sorted(tree, key=lambda p: p.rss_kb, reverse=True)[:5]
    return {
        "rss_mb": round(sum(p.rss_kb for p in tree) / 1024),
        "cpu": round(sum(p.cpu for p in tree), 1),
        "processes": len(tree),
        "top": [{"pid": p.pid, "name": app_name(p.comm), "rss_mb": round(p.rss_kb / 1024), "cpu": round(p.cpu, 1)}
                for p in top],
        "tty": tree[0].tty if tree[0].tty not in ("??", "-", "") else None,
    }


def parse_pressure_level(out: str) -> str | None:
    return PRESSURE_LEVELS.get(out.strip())


def parse_memory_free(out: str) -> int | None:
    m = re.search(r"free percentage:\s*(\d+)%", out)
    return int(m.group(1)) if m else None


def parse_swap(out: str) -> tuple[int | None, int | None]:
    def value(name: str) -> int | None:
        m = re.search(name + r"\s*=\s*([\d.,]+)([KMG])", out)
        return round(float(m.group(1).replace(",", ".")) * SWAP_UNITS[m.group(2)]) if m else None
    return value("used"), value("total")


def parse_mem_mb(text: str) -> float | None:
    m = re.match(r"\s*([\d.]+)\s*([A-Za-z]+)", text or "")
    if not m or m.group(2).lower() not in MEM_UNITS:
        return None
    return float(m.group(1)) * MEM_UNITS[m.group(2).lower()]


def _labels(text: str) -> dict[str, str]:
    return dict(item.split("=", 1) for item in (text or "").split(",") if "=" in item)


def parse_docker_ps(out: str) -> list[dict]:
    result = []
    for line in out.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        labels = _labels(d.get("Labels", ""))
        result.append({"name": d.get("Names"), "project": labels.get("com.docker.compose.project"),
                       "working_dir": labels.get("com.docker.compose.project.working_dir")})
    return result


def parse_docker_stats(out: str) -> dict[str, tuple[float, float]]:
    result = {}
    for line in out.splitlines():
        try:
            d = json.loads(line)
            mem = parse_mem_mb(str(d.get("MemUsage", "")).split("/")[0])
            cpu = float(str(d.get("CPUPerc", "0")).rstrip("%") or 0)
        except (ValueError, AttributeError):
            continue
        result[d.get("Name")] = (mem or 0.0, cpu)
    return result


def docker_projects(containers: list[dict], stats: dict[str, tuple[float, float]]) -> list[dict]:
    groups: dict[str, dict] = {}
    for c in containers:
        key = c["project"] or c["name"]
        g = groups.setdefault(key, {"project": key, "working_dir": c["working_dir"], "rss": 0.0, "cpu": 0.0,
                                    "containers": 0})
        mem, cpu = stats.get(c["name"], (0.0, 0.0))
        g["rss"] += mem
        g["cpu"] += cpu
        g["containers"] += 1
    projects = [{"project": g["project"], "working_dir": g["working_dir"], "rss_mb": round(g["rss"]),
                 "cpu": round(g["cpu"], 1), "containers": g["containers"]} for g in groups.values()]
    return sorted(projects, key=lambda p: p["rss_mb"], reverse=True)


def _inside(path: str, base: str) -> bool:
    base = base.rstrip("/")
    return path == base or path.startswith(base + "/")


def stack_for(row: dict, projects: list[dict], mapping: dict[str, str], dev_root) -> str | None:
    repo, cwd = row.get("repo") or row.get("cwd"), row.get("cwd")
    for p in projects:
        wd = p.get("working_dir")
        if wd and repo and (_inside(wd, repo) or _inside(repo, wd)):
            return p["project"]
        target = mapping.get(p["project"])
        if target and cwd:
            absolute = target if target.startswith("/") else str(Path(dev_root) / target)
            if _inside(cwd, absolute):
                return p["project"]
    return None


class _Sustain:
    def __init__(self) -> None:
        self.since: float | None = None

    def update(self, over: bool, now: float) -> float:
        if not over:
            self.since = None
            return 0.0
        if self.since is None:
            self.since = now
        return now - self.since


class Monitor:
    def __init__(self, config: dict, runner=subprocess.run, clock=time.time, loadavg=os.getloadavg, ncpu=None):
        self.config = config
        self._run, self._clock, self._loadavg = runner, clock, loadavg
        self._ncpu = ncpu or os.cpu_count() or 1
        self._env = dict(os.environ, LC_ALL="C")
        self._lock = threading.Lock()
        self._procs: list[Proc] = []
        self._system: dict | None = None
        self._docker = {"running": False, "sampled_at": None, "projects": []}
        self._cpu = _Sustain()
        self._alert_since: dict[tuple[str, str], float] = {}
        self._stop = threading.Event()

    def _cmd(self, args: list[str], timeout: float = 10) -> str | None:
        try:
            r = self._run(args, capture_output=True, text=True, timeout=timeout, env=self._env)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout if r.returncode == 0 else None

    def sample(self) -> None:
        now = self._clock()
        out = self._cmd(PS_ARGS)
        procs = parse_ps(out) if out else None
        level = self._cmd(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"])
        free = self._cmd(["memory_pressure", "-Q"])
        swap = self._cmd(["sysctl", "-n", "vm.swapusage"])
        used, total = parse_swap(swap) if swap else (None, None)
        memory = {"pressure": parse_pressure_level(level) if level else None,
                  "free_pct": parse_memory_free(free) if free else None,
                  "swap_used_mb": used, "swap_total_mb": total}
        load1 = self._loadavg()[0]
        over_for = self._cpu.update(load1 > self._ncpu * self.config["cpu_load_factor"], now)
        alerts = []
        if memory["pressure"] in ("warn", "critical"):
            free_text = f", {memory['free_pct']}% free" if memory["free_pct"] is not None else ""
            alerts.append({"kind": "memory", "level": memory["pressure"],
                           "message": f"Memory pressure is {memory['pressure']}{free_text}."})
        if over_for >= self.config["cpu_sustain_s"]:
            alerts.append({"kind": "cpu", "level": "warn",
                           "message": f"CPU overloaded: load {load1:.1f} on {self._ncpu} cores for {int(over_for // 60)} min."})
        active = {(a["kind"], a["level"]) for a in alerts}
        self._alert_since = {k: v for k, v in self._alert_since.items() if k in active}
        for a in alerts:
            a["since"] = iso(self._alert_since.setdefault((a["kind"], a["level"]), now))
        system = {"sampled_at": iso(now), "memory": memory, "cpu": {"load1": round(load1, 2), "ncpu": self._ncpu},
                  "top_apps": top_apps(procs) if procs is not None else None, "processes_ok": procs is not None,
                  "alerts": alerts}
        with self._lock:
            self._procs = procs or []
            self._system = system

    def sample_docker(self) -> None:
        now = self._clock()
        ps_out = self._cmd(["docker", "ps", "--format", "{{json .}}"])
        if ps_out is None:
            docker = {"running": False, "sampled_at": iso(now), "projects": []}
        else:
            stats_out = self._cmd(["docker", "stats", "--no-stream", "--format", "{{json .}}"]) or ""
            docker = {"running": True, "sampled_at": iso(now),
                      "projects": docker_projects(parse_docker_ps(ps_out), parse_docker_stats(stats_out))}
        with self._lock:
            self._docker = docker

    def snapshot(self) -> dict:
        with self._lock:
            system = dict(self._system) if self._system else {
                "sampled_at": None, "memory": None, "cpu": None, "top_apps": None, "processes_ok": False, "alerts": []}
            system["alerts"] = [dict(a) for a in system["alerts"]]
            system["docker"] = json.loads(json.dumps(self._docker))
        return system

    def tree_usage(self, pid: int) -> dict | None:
        with self._lock:
            procs = self._procs
        return tree_usage(procs, pid) if procs else None

    def start(self, on_sample=None) -> None:
        def loop(fn, interval, after=None):
            while not self._stop.is_set():
                try:
                    fn()
                    if after is not None:
                        after()
                except Exception as e:  # the background loop must survive anything
                    print(f"monitor: {e!r}", file=sys.stderr, flush=True)
                self._stop.wait(max(1, interval))
        threading.Thread(target=loop, args=(self.sample, self.config["process_sample_interval_s"], on_sample),
                         name="monitor", daemon=True).start()
        threading.Thread(target=loop, args=(self.sample_docker, self.config["docker_sample_interval_s"]),
                         name="docker", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
