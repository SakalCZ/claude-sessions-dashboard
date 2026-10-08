import os
import re
import unittest

import live
from tests.helpers import FakeClaude, sid

START = "Wed Oct  7 10:58:12 2026"


class GetLiveTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeClaude()

    def tearDown(self):
        self.fake.cleanup()

    def pid_file(self, pid, session, start=START, status="busy"):
        self.fake.write_pid(pid, {"pid": pid, "sessionId": session, "cwd": "/w", "status": status,
                                  "procStart": start, "name": "x", "updatedAt": 1791371445884})

    def test_matching_process_is_live(self):
        self.pid_file(100, sid(1))
        result = live.get_live(self.fake.root, ps=lambda pids: {100: "Wed Oct 7 10:58:12 2026"})
        self.assertEqual(result, {sid(1): {"status": "busy", "pid": 100, "name": "x", "updated_at": 1791371445884,
                                           "status_updated_at": None}})

    def test_dead_or_reused_pid_is_not_live(self):
        self.pid_file(100, sid(1))
        self.pid_file(200, sid(2))
        result = live.get_live(self.fake.root, ps=lambda pids: {200: "Thu Oct  8 09:00:00 2026"})
        self.assertEqual(result, {})

    def test_invalid_pid_files_are_skipped(self):
        (self.fake.root / "sessions" / "300.json").write_text("{nope")
        self.fake.write_pid(400, {"pid": "400", "sessionId": sid(4), "procStart": START})
        self.pid_file(500, sid(5), status="idle")
        seen = []

        def fake_ps(pids):
            seen.extend(pids)
            return {500: START}

        self.assertEqual(list(live.get_live(self.fake.root, ps=fake_ps)), [sid(5)])
        self.assertEqual(seen, [500])

    def test_non_string_fields_are_skipped(self):
        self.fake.write_pid(600, {"pid": 600, "sessionId": sid(6), "procStart": 1791370693253})
        self.fake.write_pid(700, {"pid": 700, "sessionId": 7, "procStart": START})
        self.pid_file(800, sid(8))
        result = live.get_live(self.fake.root, ps=lambda pids: {600: START, 700: START, 800: START})
        self.assertEqual(list(result), [sid(8)])

    def test_status_updated_at_is_passed_through(self):
        self.fake.write_pid(100, {"pid": 100, "sessionId": sid(1), "procStart": START, "status": "idle",
                                  "statusUpdatedAt": 1791453600000})
        result = live.get_live(self.fake.root, ps=lambda pids: {100: START})
        self.assertEqual(result[sid(1)]["status_updated_at"], 1791453600000)

    def test_missing_sessions_dir(self):
        self.assertEqual(live.get_live(self.fake.root / "nope", ps=lambda pids: {}), {})

    def test_parse_ps_output(self):
        out = "  59594 Wed Oct  7 10:58:12 2026\n 1642 Fri Sep 18 15:06:24 2026\ngarbage\n"
        self.assertEqual(live.parse_ps_output(out), {59594: "Wed Oct  7 10:58:12 2026", 1642: "Fri Sep 18 15:06:24 2026"})

    def test_ps_lstart_uses_c_locale_and_utc(self):
        self.assertEqual(live.ps_lstart([]), {})
        mine = live.ps_lstart([os.getpid()])
        self.assertRegex(mine[os.getpid()], r"^[A-Z][a-z]{2} [A-Z][a-z]{2} +\d{1,2} \d\d:\d\d:\d\d \d{4}$")


if __name__ == "__main__":
    unittest.main()
