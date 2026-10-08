import json
import tempfile
import unittest
from pathlib import Path

import agents

T0, T1, T2 = "2026-10-08T10:00:00Z", "2026-10-08T10:05:00Z", "2026-10-08T10:10:00Z"
LIVE_IDLE = {"status": "idle", "pid": 42, "status_updated_at": 1791453600000}
LIVE_BUSY = {"status": "busy", "pid": 42, "status_updated_at": 1791453900000}


class AttentionTest(unittest.TestCase):
    def test_hook_state_wins(self):
        a = agents.attention_for({"state": "permission", "since": T1, "note": "Bash"}, LIVE_BUSY, None)
        self.assertEqual(a, {"state": "permission", "since": T1, "note": "Bash", "source": "hook",
                             "in_queue": True, "group": "blocking"})

    def test_interrupted_turn_becomes_idle(self):
        hook = {"state": "working", "since": T0}
        self.assertEqual(agents.attention_for(hook, dict(LIVE_IDLE, status_updated_at=1791453900000), None)["state"], "idle")
        self.assertEqual(agents.attention_for(hook, dict(LIVE_IDLE, status_updated_at=1791453602000), None)["state"], "working")
        self.assertEqual(agents.attention_for(hook, LIVE_BUSY, None)["state"], "working")
        perm = {"state": "permission", "since": T0}
        # Esc on a permission prompt fires no hook event; Claude's own status goes waiting → idle.
        self.assertEqual(agents.attention_for(perm, dict(LIVE_IDLE, status_updated_at=1791453900000), None)["state"], "idle")
        waiting = {"status": "waiting", "pid": 42, "status_updated_at": 1791453900000}
        self.assertEqual(agents.attention_for(perm, waiting, None)["state"], "permission")
        self.assertEqual(agents.iso_to_ms(T0), 1791453600000)
        self.assertIsNone(agents.iso_to_ms("x"))

    def test_estimate_from_live_status(self):
        a = agents.attention_for(None, LIVE_IDLE, None)
        self.assertEqual((a["state"], a["since"], a["source"], a["in_queue"], a["group"]),
                         ("waiting", T0, "estimate", True, "done"))
        b = agents.attention_for(None, LIVE_BUSY, None)
        self.assertEqual((b["state"], b["since"], b["in_queue"], b["group"]), ("working", T1, False, None))

    def test_estimate_maps_waiting_status_to_permission(self):
        a = agents.attention_for(None, {"status": "waiting", "pid": 42, "status_updated_at": 1791453600000}, None)
        self.assertEqual((a["state"], a["source"], a["group"]), ("permission", "estimate", "blocking"))

    def test_not_live_is_ended(self):
        a = agents.attention_for({"state": "waiting", "since": T1, "note": "x"}, None, None)
        self.assertEqual((a["state"], a["in_queue"], a["group"], a["source"]), ("ended", False, None, "hook"))

    def test_seen_hides_until_next_since(self):
        hook = {"state": "waiting", "since": T1}
        self.assertFalse(agents.attention_for(hook, LIVE_IDLE, T2)["in_queue"])
        self.assertTrue(agents.attention_for(hook, LIVE_IDLE, T0)["in_queue"])

    def test_non_queue_states_and_missing_since(self):
        for state in ("working", "idle", "ended"):
            self.assertFalse(agents.attention_for({"state": state, "since": T1}, LIVE_IDLE, None)["in_queue"])
        no_time = dict(LIVE_IDLE, status_updated_at=None)
        self.assertFalse(agents.attention_for(None, no_time, None)["in_queue"])

    def test_build_queue_order(self):
        def row(sid, group, since, in_queue=True):
            return {"session_id": sid, "attention": {"in_queue": in_queue, "group": group, "since": since}}
        rows = [row("a", "done", T0), row("b", "blocking", T2), row("c", "blocking", T1),
                row("d", None, T0, in_queue=False), row("e", "done", T1)]
        self.assertEqual(agents.build_queue(rows), ["c", "b", "a", "e"])

    def test_ms_to_iso(self):
        self.assertEqual(agents.ms_to_iso(1791453600000), T0)
        self.assertIsNone(agents.ms_to_iso(None))
        self.assertIsNone(agents.ms_to_iso("x"))


class StoresTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_read_hook_states(self):
        d = self.root / "agents"
        d.mkdir()
        (d / "aaaa.json").write_text(json.dumps({"state": "waiting", "since": T0}))
        (d / "bbbb.json").write_text("{broken")
        (d / "cccc.json").write_text(json.dumps({"state": "waiting"}))
        (d / "dddd.lock").write_text("")
        self.assertEqual(list(agents.read_hook_states(d)), ["aaaa"])
        self.assertEqual(agents.read_hook_states(self.root / "missing"), {})

    def test_seen_store(self):
        store = agents.SeenStore(self.root / "sub" / "seen.json")
        self.assertEqual(store.all(), {})
        self.assertEqual(store.mark("s1", T1), T1)
        self.assertEqual(agents.SeenStore(self.root / "sub" / "seen.json").all(), {"s1": T1})
        (self.root / "sub" / "seen.json").write_text("{broken")
        self.assertEqual(store.all(), {})


if __name__ == "__main__":
    unittest.main()
