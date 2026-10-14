import json
import os
import tempfile
import unittest
import zoneinfo
from datetime import timezone
from pathlib import Path

import config
import limits

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "limits" / "claude.json"
NOW = 1791985200.0          # 2026-10-14T13:40:00Z
FIVE_RESET = 1791996599     # 2026-10-14T16:49:59Z
WEEK_RESET = 1792385999     # 2026-10-19T04:59:59Z (a Monday)
SID_A = "aaaaaaaa-0000-4000-8000-000000000000"
SID_B = "bbbbbbbb-0000-4000-8000-000000000000"


class LimitsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data = root / "data"
        self.claude_json = root / ".claude.json"
        self.cfg = config.load_config(root / "none.json")
        self.now = NOW
        self.reader = limits.LimitsReader(self.data, self.claude_json, self.cfg, clock=lambda: self.now,
                                          tz=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def write_cache(self, mutate=None):
        data = json.loads(FIXTURE.read_text(encoding="utf-8"))
        if mutate:
            mutate(data)
        self.claude_json.write_text(json.dumps(data), encoding="utf-8")

    def write_tap(self, session_id, captured_at, five=None, week=None):
        rate = {}
        if five:
            rate["five_hour"] = {"used_percentage": five[0], "resets_at": five[1]}
        if week:
            rate["seven_day"] = {"used_percentage": week[0], "resets_at": week[1]}
        folder = self.data / "rate_limits"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{session_id}.json"
        path.write_text(json.dumps({"captured_at": limits.iso(captured_at), "session_id": session_id,
                                    "rate_limits": rate}), encoding="utf-8")
        os.utime(path, (captured_at, captured_at))
        return path

    def windows(self):
        return {w["kind"]: w for w in self.reader.snapshot()["windows"]}

    def test_nothing_available(self):
        self.assertEqual(self.reader.snapshot(), {"windows": [], "extra_usage": None, "as_of": None, "alerts": []})

    def test_cache_only(self):
        self.write_cache()
        snap = self.reader.snapshot()
        self.assertEqual([w["kind"] for w in snap["windows"]], ["five_hour", "seven_day", "weekly:Fable"])
        self.assertEqual(snap["windows"][0], {
            "kind": "five_hour", "label": "5h", "used_pct": 32.0, "resets_at": "2026-10-14T16:49:59Z",
            "as_of": "2026-10-14T13:25:33Z", "source": "cache", "level": "normal"})
        self.assertEqual((snap["windows"][2]["label"], snap["windows"][2]["used_pct"]), ("Week · Fable", 3.0))
        self.assertEqual(snap["extra_usage"], {"enabled": True, "used": 0.0, "limit": 10.0, "currency": "EUR",
                                               "limit_reached": False, "as_of": "2026-10-14T13:25:33Z"})
        self.assertEqual((snap["as_of"], snap["alerts"]), ("2026-10-14T13:25:33Z", []))

    def test_session_value_beats_lower_cache_and_keeps_newest_as_of(self):
        self.write_cache()
        self.write_tap(SID_A, NOW - 60, five=(40, FIVE_RESET), week=(27, WEEK_RESET))
        w = self.windows()
        self.assertEqual((w["five_hour"]["used_pct"], w["five_hour"]["source"], w["five_hour"]["as_of"]),
                         (40.0, "statusline", "2026-10-14T13:39:00Z"))
        self.assertEqual((w["seven_day"]["source"], w["seven_day"]["as_of"]), ("statusline", "2026-10-14T13:39:00Z"))

    def test_idle_session_never_lowers_the_value(self):
        self.write_tap(SID_A, NOW - 300, five=(55, FIVE_RESET))  # working session, last change 5 min ago
        self.write_tap(SID_B, NOW - 5, five=(41, FIVE_RESET))    # idle session redrew with its old response
        w = self.windows()["five_hour"]
        self.assertEqual((w["used_pct"], w["as_of"]), (55.0, "2026-10-14T13:35:00Z"))

    def test_later_window_beats_earlier(self):
        self.write_cache()
        self.write_tap(SID_A, NOW, five=(3, FIVE_RESET + 5 * 3600))
        self.assertEqual(self.windows()["five_hour"]["used_pct"], 3.0)

    def test_same_window_tolerance(self):
        self.write_tap(SID_A, NOW - 60, five=(50, FIVE_RESET))
        self.write_tap(SID_B, NOW - 30, five=(20, FIVE_RESET + limits.SAME_WINDOW_S - 1))
        self.assertEqual(self.windows()["five_hour"]["used_pct"], 50.0)
        self.write_tap(SID_B, NOW - 29, five=(20, FIVE_RESET + limits.SAME_WINDOW_S + 1))
        self.assertEqual(self.windows()["five_hour"]["used_pct"], 20.0)

    def test_expired_windows_are_dropped(self):
        self.write_cache()
        self.write_tap(SID_A, NOW, five=(90, FIVE_RESET))
        self.now = FIVE_RESET + 1
        w = self.windows()
        self.assertNotIn("five_hour", w)
        self.assertIn("seven_day", w)

    def test_cache_of_another_account_is_ignored(self):
        for mutate in (lambda d: d["oauthAccount"].update(accountUuid="00000000-0000-4000-8000-0000000000bb"),
                       lambda d: d.pop("oauthAccount"),
                       lambda d: d["cachedUsageUtilization"].pop("accountUuid")):
            with self.subTest(mutate=mutate):
                self.write_cache(mutate)
                snap = self.reader.snapshot()
                self.assertEqual((snap["windows"], snap["extra_usage"]), ([], None))

    def test_unparsable_claude_json_keeps_last_good(self):
        self.write_cache()
        self.assertEqual(len(self.reader.snapshot()["windows"]), 3)
        self.claude_json.write_text('{"cachedUsageUtilization": {', encoding="utf-8")
        self.assertEqual(len(self.reader.snapshot()["windows"]), 3)
        self.claude_json.unlink()
        self.assertEqual(self.reader.snapshot()["windows"], [])

    def test_reads_files_only_when_changed(self):
        self.write_cache()
        self.reader.snapshot()
        st = self.claude_json.stat()
        text = self.claude_json.read_text(encoding="utf-8").replace('"utilization": 32', '"utilization": 77')
        self.claude_json.write_text(text, encoding="utf-8")
        os.utime(self.claude_json, ns=(st.st_atime_ns, st.st_mtime_ns))
        self.assertEqual(self.windows()["five_hour"]["used_pct"], 32.0)  # same mtime and size: not re-read
        os.utime(self.claude_json, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
        self.assertEqual(self.windows()["five_hour"]["used_pct"], 77.0)

    def test_old_session_files_are_deleted(self):
        old = self.write_tap(SID_A, NOW - 9 * 86400, five=(90, FIVE_RESET))
        fresh = self.write_tap(SID_B, NOW - 60, five=(10, FIVE_RESET))
        self.assertEqual(self.windows()["five_hour"]["used_pct"], 10.0)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_levels_and_alerts(self):
        self.write_tap(SID_A, NOW, five=(85, FIVE_RESET), week=(100, WEEK_RESET))
        snap = self.reader.snapshot()
        self.assertEqual([w["level"] for w in snap["windows"]], ["warn", "critical"])
        self.assertEqual(snap["alerts"], [
            {"kind": "limit:five_hour", "level": "warn", "title": "Claude limits: warn",
             "message": "Claude 5h limit at 85% · resets 16:49"},
            {"kind": "limit:seven_day", "level": "critical", "title": "Claude limits: critical",
             "message": "Claude Week limit reached · resets Mon 04:59"},
        ])

    def test_per_model_alert(self):
        self.write_cache(lambda d: d["cachedUsageUtilization"]["utilization"]["limits"][2].update(percent=81))
        [alert] = self.reader.snapshot()["alerts"]
        self.assertEqual(alert["message"], "Claude Week · Fable limit at 81% · resets Mon 04:59")

    def test_threshold_edges_and_config(self):
        for pct, expected in ((79.9, "normal"), (80, "warn"), (94.9, "warn"), (95, "critical"), (130, "critical")):
            with self.subTest(pct=pct):
                self.assertEqual(limits.level(pct, self.cfg), expected)
        self.cfg["limit_warn_pct"] = 30
        self.write_cache()
        self.assertEqual(self.windows()["five_hour"]["level"], "warn")

    def test_reset_text(self):
        utc = timezone.utc
        self.assertEqual(limits.reset_text(FIVE_RESET, NOW, utc), "16:49")
        self.assertEqual(limits.reset_text(WEEK_RESET, NOW, utc), "Mon 04:59")
        late = 1792020600  # 2026-10-14T23:30:00Z
        self.assertEqual(limits.reset_text(late + 2400, late, utc), "Thu 00:10")
        prague = zoneinfo.ZoneInfo("Europe/Prague")
        before = 1792886400  # 2026-10-25T00:00:00Z = 02:00 CEST; clocks go back at 03:00 CEST
        self.assertEqual(limits.reset_text(before + 3 * 3600, before, prague), "04:00")

    def test_parse_ts(self):
        self.assertEqual(limits.parse_ts(1791996599), 1791996599.0)
        self.assertEqual(limits.parse_ts("2026-10-14T16:49:59Z"), 1791996599.0)
        self.assertAlmostEqual(limits.parse_ts("2026-10-14T16:49:59.701245+00:00"), 1791996599.701245, places=5)
        for bad in ("2026-10-14T16:49:59", "soon", None, True, [1]):
            with self.subTest(bad=bad):
                self.assertIsNone(limits.parse_ts(bad))

    def test_malformed_inputs(self):
        def mutate(d):
            d["cachedUsageUtilization"]["utilization"].update(
                five_hour={"utilization": "high", "resets_at": "2026-10-14T16:49:59Z"}, seven_day=None,
                limits={"x": 1}, extra_usage=[])
        self.write_cache(mutate)
        folder = self.data / "rate_limits"
        folder.mkdir(parents=True)
        (folder / f"{SID_A}.json").write_text("[1, 2]", encoding="utf-8")
        (folder / f"{SID_B}.json").write_text("{nope", encoding="utf-8")
        (folder / "notes.txt").write_text("x", encoding="utf-8")
        snap = self.reader.snapshot()
        self.assertEqual((snap["windows"], snap["extra_usage"], snap["alerts"]), ([], None, []))
        self.claude_json.write_text("[]", encoding="utf-8")
        self.assertEqual(self.reader.snapshot()["windows"], [])


if __name__ == "__main__":
    unittest.main()
