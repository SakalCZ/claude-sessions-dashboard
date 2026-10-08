import json
import subprocess
import unittest

import config
import monitor

PS_OUT = """    1     0   12000   0,5 ??       /sbin/launchd
  400   300  220000   3.0 ttys003  claude
  500   400   10000   1.0 ttys003  /bin/zsh
  501   500  150000  50.0 ttys003  /usr/local/bin/php
  600     1  900000  20.0 ??       /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
  601   600 1200000  30.0 ??       /Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/Versions/1/Helpers/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer)
garbage line
"""
DOCKER_PS = "\n".join(json.dumps(d) for d in [
    {"Names": "budget_mysql8", "Labels": "com.docker.compose.project=budget,com.docker.compose.project.working_dir=/w/budget/Docker,com.docker.compose.service=mysql"},
    {"Names": "budget_php", "Labels": "com.docker.compose.project=budget,com.docker.compose.project.working_dir=/w/budget/Docker"},
    {"Names": "mailpit", "Labels": ""},
])
DOCKER_STATS = "\n".join(json.dumps(d) for d in [
    {"Name": "budget_mysql8", "CPUPerc": "0.90%", "MemUsage": "558.9MiB / 7.748GiB"},
    {"Name": "budget_php", "CPUPerc": "1.10%", "MemUsage": "88.62MiB / 7.748GiB"},
    {"Name": "mailpit", "CPUPerc": "0.00%", "MemUsage": "14.84MiB / 7.748GiB"},
])
PS_ARGS = ("ps", "-axo", "pid=,ppid=,rss=,%cpu=,tty=,comm=")


class FakeRunner:
    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((tuple(args), kwargs))
        key = tuple(args)
        if key not in self.outputs:
            raise FileNotFoundError(args[0])
        out = self.outputs[key]
        if isinstance(out, Exception):
            raise out
        return subprocess.CompletedProcess(args, 0 if out is not None else 1, out or "", "")


class ParserTest(unittest.TestCase):
    def test_parse_ps(self):
        procs = monitor.parse_ps(PS_OUT)
        self.assertEqual(len(procs), 6)
        self.assertEqual(procs[0].cpu, 0.5)
        self.assertTrue(procs[5].comm.endswith("Google Chrome Helper (Renderer)"))

    def test_app_name(self):
        procs = monitor.parse_ps(PS_OUT)
        self.assertEqual([monitor.app_name(p.comm) for p in procs],
                         ["launchd", "claude", "zsh", "php", "Google Chrome", "Google Chrome"])

    def test_top_apps(self):
        top = monitor.top_apps(monitor.parse_ps(PS_OUT), n=2)
        self.assertEqual(top, [{"name": "Google Chrome", "rss_mb": 2051, "cpu": 50.0},
                               {"name": "claude", "rss_mb": 215, "cpu": 3.0}])

    def test_tree_usage(self):
        usage = monitor.tree_usage(monitor.parse_ps(PS_OUT), 400)
        self.assertEqual((usage["rss_mb"], usage["cpu"], usage["processes"], usage["tty"]), (371, 54.0, 3, "ttys003"))
        self.assertEqual([p["pid"] for p in usage["top"]], [400, 501, 500])
        self.assertIsNone(monitor.tree_usage(monitor.parse_ps(PS_OUT), 999))
        self.assertIsNone(monitor.tree_usage(monitor.parse_ps(PS_OUT), 600)["tty"])

    def test_system_parsers(self):
        self.assertEqual([monitor.parse_pressure_level(x) for x in ("1\n", "2", "4", "x")],
                         ["normal", "warn", "critical", None])
        self.assertEqual(monitor.parse_memory_free("blah\nSystem-wide memory free percentage: 38%\n"), 38)
        self.assertIsNone(monitor.parse_memory_free("nothing"))
        self.assertEqual(monitor.parse_swap("total = 10240.00M  used = 9297.25M  free = 942.75M  (encrypted)"), (9297, 10240))
        self.assertEqual(monitor.parse_swap("total = 10240,00M  used = 9297,25M  free = 942,75M"), (9297, 10240))
        self.assertEqual(monitor.parse_swap("total = 2.00G  used = 1.50G  free = 0.50G"), (1536, 2048))
        self.assertEqual(monitor.parse_swap("garbage"), (None, None))

    def test_docker_parsers(self):
        self.assertEqual(monitor.parse_mem_mb("1.5GiB"), 1536)
        self.assertEqual(monitor.parse_mem_mb("512KiB"), 0.5)
        self.assertIsNone(monitor.parse_mem_mb("--"))
        projects = monitor.docker_projects(monitor.parse_docker_ps(DOCKER_PS), monitor.parse_docker_stats(DOCKER_STATS))
        self.assertEqual(projects, [
            {"project": "budget", "working_dir": "/w/budget/Docker", "rss_mb": 648, "cpu": 2.0, "containers": 2},
            {"project": "mailpit", "working_dir": None, "rss_mb": 15, "cpu": 0.0, "containers": 1},
        ])

    def test_stack_for(self):
        projects = [{"project": "budget", "working_dir": "/w/budget/Docker"},
                    {"project": "shared-stack", "working_dir": "/w/acme/shop_local/application/local-app"}]
        mapping = {"shared-stack": "acme/shop_local"}
        self.assertEqual(monitor.stack_for({"repo": "/w/budget", "cwd": "/w/budget"}, projects, mapping, "/w"), "budget")
        wt = {"repo": "/w/acme/shop_local/shop", "cwd": "/w/acme/shop_local/shop/.claude/worktrees/PROJ-1"}
        self.assertEqual(monitor.stack_for(wt, projects, mapping, "/w"), "shared-stack")
        self.assertIsNone(monitor.stack_for({"repo": "/w/acme/shop", "cwd": "/w/acme/shop"}, projects, mapping, "/w"))
        self.assertIsNone(monitor.stack_for({"repo": "/w/budget", "cwd": "/w/budget"}, projects[1:], {}, "/w"))


class MonitorTest(unittest.TestCase):
    def outputs(self, over=None):
        base = {
            PS_ARGS: PS_OUT,
            ("sysctl", "-n", "kern.memorystatus_vm_pressure_level"): "2\n",
            ("memory_pressure", "-Q"): "System-wide memory free percentage: 38%\n",
            ("sysctl", "-n", "vm.swapusage"): "total = 10240.00M  used = 9297.25M  free = 942.75M",
            ("docker", "ps", "--format", "{{json .}}"): DOCKER_PS,
            ("docker", "stats", "--no-stream", "--format", "{{json .}}"): DOCKER_STATS,
        }
        base.update(over or {})
        return base

    def make(self, outputs, load=1.0):
        self.now = 1000.0
        self.load = load
        cfg = config.load_config("/nonexistent/config.json")
        self.runner = FakeRunner(outputs)
        return monitor.Monitor(cfg, runner=self.runner, clock=lambda: self.now,
                               loadavg=lambda: (self.load, 0, 0), ncpu=8)

    def test_sample_builds_system_snapshot(self):
        m = self.make(self.outputs())
        self.assertIsNone(m.snapshot()["sampled_at"])
        m.sample()
        s = m.snapshot()
        self.assertEqual(s["memory"], {"pressure": "warn", "free_pct": 38, "swap_used_mb": 9297, "swap_total_mb": 10240})
        self.assertEqual(s["cpu"], {"load1": 1.0, "ncpu": 8})
        self.assertEqual(s["top_apps"][0]["name"], "Google Chrome")
        self.assertEqual([(a["kind"], a["level"]) for a in s["alerts"]], [("memory", "warn")])
        self.assertEqual(m.tree_usage(400)["rss_mb"], 371)
        for args, kwargs in self.runner.calls:
            self.assertEqual(kwargs["env"]["LC_ALL"], "C")
            self.assertIn("timeout", kwargs)

    def test_cpu_alert_needs_sustain(self):
        m = self.make(self.outputs({("sysctl", "-n", "kern.memorystatus_vm_pressure_level"): "1"}), load=9.0)
        m.sample()
        self.assertEqual(m.snapshot()["alerts"], [])
        self.now += 130
        m.sample()
        [alert] = m.snapshot()["alerts"]
        self.assertEqual((alert["kind"], alert["level"]), ("cpu", "warn"))
        self.assertEqual(alert["since"], "1970-01-01T00:18:50Z")  # first sample with the alert raised (now=1130)
        self.load = 2.0
        m.sample()
        self.assertEqual(m.snapshot()["alerts"], [])

    def test_alert_since_is_stable(self):
        m = self.make(self.outputs())
        m.sample()
        first = m.snapshot()["alerts"][0]["since"]
        self.now += 60
        m.sample()
        self.assertEqual(m.snapshot()["alerts"][0]["since"], first)

    def test_docker_sample(self):
        m = self.make(self.outputs())
        m.sample_docker()
        d = m.snapshot()["docker"]
        self.assertTrue(d["running"])
        self.assertEqual(d["projects"][0]["project"], "budget")

    def test_docker_unavailable(self):
        for failure in (None, subprocess.TimeoutExpired("docker", 10), FileNotFoundError("docker")):
            with self.subTest(failure=repr(failure)):
                m = self.make(self.outputs({("docker", "ps", "--format", "{{json .}}"): failure}))
                m.sample_docker()
                self.assertEqual(m.snapshot()["docker"]["running"], False)
                self.assertEqual(m.snapshot()["docker"]["projects"], [])

    def test_missing_commands_degrade(self):
        outputs = self.outputs()
        del outputs[("memory_pressure", "-Q")]
        outputs[PS_ARGS] = None
        m = self.make(outputs)
        m.sample()
        s = m.snapshot()
        self.assertIsNone(s["memory"]["free_pct"])
        self.assertIsNone(s["top_apps"])
        self.assertFalse(s["processes_ok"])
        self.assertIsNone(m.tree_usage(400))


if __name__ == "__main__":
    unittest.main()
