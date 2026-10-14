import importlib.util
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TAP_PATH = ROOT / "hook" / "statusline_tap.py"
INPUT = (ROOT / "tests" / "fixtures" / "limits" / "statusline-input.json").read_bytes()
SID = json.loads(INPUT)["session_id"]
T = 1791985200.0  # 2026-10-14T13:40:00Z


def load_tap():
    spec = importlib.util.spec_from_file_location("statusline_tap", TAP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RecordLimitsTest(unittest.TestCase):
    def setUp(self):
        self.tap = load_tap()
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.file = self.data / "rate_limits" / f"{SID}.json"

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, payload, at):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.tap.record_limits(raw, str(self.data), now=lambda: at)

    def stored(self):
        return json.loads(self.file.read_text(encoding="utf-8"))

    def test_records_rate_limits_per_session(self):
        self.record(INPUT, T)
        self.assertEqual(self.stored(), {"captured_at": "2026-10-14T13:40:00Z", "session_id": SID,
                                         "rate_limits": json.loads(INPUT)["rate_limits"]})

    def test_unchanged_values_are_rewritten_only_after_60_s(self):
        self.record(INPUT, T)
        self.record(INPUT, T + 59)
        self.assertEqual(self.stored()["captured_at"], "2026-10-14T13:40:00Z")
        self.record(INPUT, T + 61)
        self.assertEqual(self.stored()["captured_at"], "2026-10-14T13:41:01Z")

    def test_changed_values_are_written_at_once(self):
        self.record(INPUT, T)
        payload = json.loads(INPUT)
        payload["rate_limits"]["five_hour"]["used_percentage"] += 1
        self.record(payload, T + 5)
        self.assertEqual(self.stored()["captured_at"], "2026-10-14T13:40:05Z")
        self.assertEqual(self.stored()["rate_limits"], payload["rate_limits"])

    def test_nothing_recorded_without_limits_or_valid_session(self):
        payload = json.loads(INPUT)
        cases = [
            {**payload, "rate_limits": None}, {**payload, "rate_limits": {}},
            {**payload, "rate_limits": {"spend_limit": {"used_percentage": 5}}},
            {**payload, "session_id": "../../../../tmp/x"}, {**payload, "session_id": SID + "\n"},
            {**payload, "session_id": None}, [1, 2],
        ]
        for case in cases:
            with self.subTest(case=str(case)[:60]):
                self.record(case, T)
                self.assertFalse((self.data / "rate_limits").exists())


class TapScriptTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name, "data")
        self.data.mkdir()
        self.env = {**os.environ, "CLAUDE_DASHBOARD_DATA_DIR": str(self.data)}

    def tearDown(self):
        self.tmp.cleanup()

    def set_command(self, command):
        (self.data / "statusline.json").write_text(json.dumps({"command": command}), encoding="utf-8")

    def run_tap(self, stdin=INPUT, cwd=None):
        return subprocess.run([sys.executable, "-I", "-S", str(TAP_PATH)], input=stdin, capture_output=True,
                              env=self.env, cwd=cwd, timeout=20)

    def test_original_command_gets_stdin_and_passes_output_and_exit_code(self):
        self.set_command("cat; printf ' cwd=%s' \"$(/bin/pwd -P)\"; echo oops >&2; exit 3")
        r = self.run_tap(cwd=self.tmp.name)
        self.assertEqual(r.stdout, INPUT + f" cwd={os.path.realpath(self.tmp.name)}".encode())
        self.assertEqual(r.stderr, b"oops\n")
        self.assertEqual(r.returncode, 3)
        self.assertTrue((self.data / "rate_limits" / f"{SID}.json").exists())

    def test_command_that_ignores_stdin(self):
        self.set_command("echo static line")
        r = self.run_tap(stdin=INPUT * 2000)  # far more than a pipe buffer
        self.assertEqual((r.stdout, r.stderr, r.returncode), (b"static line\n", b"", 0))

    def test_no_command_prints_nothing(self):
        path = self.data / "statusline.json"
        for store in (None, {"command": None}, {"command": "  "}, "{nope"):
            with self.subTest(store=store):
                if store is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(store if isinstance(store, str) else json.dumps(store), encoding="utf-8")
                r = self.run_tap()
                self.assertEqual((r.stdout, r.stderr, r.returncode), (b"", b"", 0))

    def test_broken_input_or_unwritable_data_still_runs_the_command(self):
        self.set_command("cat")
        (self.data / "rate_limits" / f"{SID}.json").mkdir(parents=True)  # a directory where the file should go
        for stdin in (INPUT, b"{nope", b"\xff\xfe", b""):
            with self.subTest(stdin=stdin[:10]):
                r = self.run_tap(stdin=stdin)
                self.assertEqual((r.stdout, r.stderr, r.returncode), (stdin, b"", 0))
        self.assertEqual([p.name for p in (self.data / "rate_limits").iterdir()], [f"{SID}.json"])  # no temp files left

    def test_overhead_is_small(self):
        self.set_command("true")
        times = []
        for _ in range(5):
            start = time.perf_counter()
            self.run_tap()
            times.append(time.perf_counter() - start)
        self.assertLess(statistics.median(times), 0.5)


if __name__ == "__main__":
    unittest.main()
