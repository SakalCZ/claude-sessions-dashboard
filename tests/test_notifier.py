import subprocess
import unittest

import config
import notifier

SINCE = "2026-10-08T10:00:00Z"
T_SINCE = 1791453600.0


def row(sid="s1", state="permission", group="blocking", since=SINCE, in_queue=True, source="hook"):
    return {"session_id": sid, "jira_key": "PROJ-1", "topic": "t", "display_dir": "acme/shop",
            "attention": {"state": state, "group": group, "since": since, "in_queue": in_queue, "source": source}}


class NotifierTest(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load_config("/nonexistent")
        self.sent = []
        self.now = T_SINCE
        self.n = notifier.Notifier(self.cfg, send=lambda t, m: self.sent.append((t, m)), clock=lambda: self.now)
        self.n.check_queue([])  # the first check primes the notifier (start-up rule)

    def test_blocking_after_threshold_once_per_episode(self):
        self.now = T_SINCE + 30
        self.n.check_queue([row()])
        self.assertEqual(self.sent, [])
        self.now = T_SINCE + 70
        self.n.check_queue([row()])
        self.n.check_queue([row()])
        self.assertEqual(self.sent, [("Claude is waiting", "PROJ-1 · acme/shop · permission 1 min")])
        self.now = T_SINCE + 400
        self.n.check_queue([row(since="2026-10-08T10:05:00Z")])
        self.assertEqual(len(self.sent), 2)

    def test_waiting_uses_longer_threshold(self):
        self.now = T_SINCE + 300
        self.n.check_queue([row(state="waiting", group="done")])
        self.assertEqual(self.sent, [])
        self.now = T_SINCE + 601
        self.n.check_queue([row(state="waiting", group="done")])
        self.assertEqual(len(self.sent), 1)

    def test_seen_or_not_queued_never_notifies(self):
        self.now = T_SINCE + 3600
        self.n.check_queue([row(in_queue=False, group=None), row(sid="s2", since=None)])
        self.assertEqual(self.sent, [])

    def test_estimates_never_notify(self):
        self.now = T_SINCE + 3600
        self.n.check_queue([row(source="estimate")])
        self.assertEqual(self.sent, [])

    def test_no_burst_on_first_check(self):
        fresh = notifier.Notifier(self.cfg, send=lambda t, m: self.sent.append((t, m)), clock=lambda: self.now)
        self.now = T_SINCE + 3600
        fresh.check_queue([row(sid="old")])
        self.assertEqual(self.sent, [])
        fresh.check_queue([row(sid="old")])
        self.assertEqual(self.sent, [])
        fresh.check_queue([row(sid="new", since="2026-10-08T10:59:30Z")])
        self.assertEqual(self.sent, [])
        self.now += 120
        fresh.check_queue([row(sid="new", since="2026-10-08T10:59:30Z")])
        self.assertEqual(len(self.sent), 1)

    def test_disabled_sends_nothing(self):
        self.cfg["notifications_enabled"] = False
        self.now = T_SINCE + 3600
        self.n.check_queue([row()])
        self.n.check_alerts([{"kind": "memory", "level": "warn", "message": "m"}])
        self.assertEqual(self.sent, [])

    def test_resource_alert_rise_reminder_clear(self):
        warn = [{"kind": "memory", "level": "warn", "message": "Memory pressure is warn."}]
        crit = [{"kind": "memory", "level": "critical", "message": "Memory pressure is critical."}]
        self.n.check_alerts(warn)
        self.assertEqual(self.sent, [("Mac resources: warn", "Memory pressure is warn.")])
        self.now += 1800
        self.n.check_alerts(warn)
        self.assertEqual(len(self.sent), 1)
        self.now += 1801
        self.n.check_alerts(warn)
        self.assertEqual(len(self.sent), 2)
        self.n.check_alerts(crit)
        self.assertEqual(self.sent[-1], ("Mac resources: critical", "Memory pressure is critical."))
        self.n.check_alerts([])
        self.n.check_alerts(warn)
        self.assertEqual(len(self.sent), 4)

    def test_send_notification_argv_and_failure(self):
        calls = []

        def ok(args, **kw):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, "", "")

        self.assertTrue(notifier.send_notification("Title", "Message", runner=ok))
        self.assertEqual(calls[0], ["osascript", "-e", notifier.NOTIFY_SCRIPT, "Message", "Title"])
        failing = lambda args, **kw: subprocess.CompletedProcess(args, 1, "", "nope")
        self.assertFalse(notifier.send_notification("T", "M", runner=failing))


if __name__ == "__main__":
    unittest.main()
