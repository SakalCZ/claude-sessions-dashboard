import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = ROOT / "hook" / "claude_hook.py"
FIXTURES = ROOT / "tests" / "fixtures" / "hooks"
SID = "11111111-0000-4000-8000-000000000000"


def load_hook():
    spec = importlib.util.spec_from_file_location("claude_hook", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class HookTest(unittest.TestCase):
    def setUp(self):
        self.hook = load_hook()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data, self.claude = root / "data", root / "claude"
        (self.claude / "sessions").mkdir(parents=True)
        (self.claude / "sessions" / "400.json").write_text("{}")
        self.table = {500: (400, "??"), 400: (300, "ttys003"), 300: (1, "ttys003")}
        self.ps_calls = []
        self.ticks = iter(f"2026-10-08T10:00:{i:02d}Z" for i in range(60))

    def tearDown(self):
        self.tmp.cleanup()

    def ps(self, pid):
        self.ps_calls.append(pid)
        return self.table.get(pid)

    def fire(self, event, **fields):
        payload = {"session_id": SID, "hook_event_name": event, "cwd": "/w/acme/shop", **fields}
        self.hook.handle(payload, ddir=str(self.data), cdir=str(self.claude), parent_pid=500,
                         ps_info=self.ps, now=lambda: next(self.ticks))
        return self.state()

    def state(self):
        path = self.data / "agents" / f"{SID}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def test_turn_cycle(self):
        self.assertEqual(self.fire("SessionStart", source="startup")["state"], "idle")
        self.assertEqual(self.fire("UserPromptSubmit")["state"], "working")
        s = self.fire("Stop", last_assistant_message="\n\nAll tests pass.\nDetails follow.")
        self.assertEqual((s["state"], s["note"], s["event"]), ("waiting", "All tests pass.", "Stop"))
        self.assertEqual((s["claude_pid"], s["tty"], s["cwd"]), (400, "ttys003", "/w/acme/shop"))
        self.assertEqual(s["version"], 1)

    def test_permission_since_is_kept_and_cleared_by_post_tool_use(self):
        self.fire("UserPromptSubmit")
        first = self.fire("PermissionRequest", tool_name="Bash")
        self.assertEqual((first["state"], first["note"]), ("permission", "Bash"))
        again = self.fire("Notification", notification_type="permission_prompt",
                          message="Claude needs your permission to use Bash")
        self.assertEqual(again["since"], first["since"])
        self.assertEqual(again["note"], "Claude needs your permission to use Bash")
        self.assertEqual(self.fire("PostToolUse", tool_name="Bash")["state"], "working")

    def test_post_tool_use_does_not_touch_other_states(self):
        self.fire("Stop", last_assistant_message="done")
        before = self.state()
        self.fire("PostToolUse", tool_name="Bash")
        self.assertEqual(self.state(), before)

    def test_question_failure_end_and_ignored_notifications(self):
        self.assertEqual(self.fire("Notification", notification_type="elicitation_dialog", message="Pick one")["state"], "question")
        before = self.state()
        self.fire("Notification", notification_type="idle_prompt", message="Claude is waiting for your input")
        self.assertEqual(self.state(), before)
        failed = self.fire("StopFailure", error_type="rate_limit")
        self.assertEqual((failed["state"], failed["note"]), ("failed", "rate_limit"))
        self.assertEqual(self.fire("SessionEnd", reason="prompt_input_exit")["state"], "ended")

    def test_idle_prompt_after_interrupt_goes_idle(self):
        self.fire("UserPromptSubmit")
        self.assertEqual(self.fire("Notification", notification_type="idle_prompt", message="waiting")["state"], "idle")

    def test_claude_pid_resolved_once_and_again_on_session_start(self):
        self.fire("UserPromptSubmit")
        calls = len(self.ps_calls)
        self.assertGreater(calls, 0)
        self.fire("Stop", last_assistant_message="x")
        self.assertEqual(len(self.ps_calls), calls)
        self.fire("SessionStart", source="resume")
        self.assertGreater(len(self.ps_calls), calls)

    def test_unknown_ancestry_gives_nulls(self):
        self.table = {}
        s = self.fire("UserPromptSubmit")
        self.assertIsNone(s["claude_pid"])
        self.assertIsNone(s["tty"])

    def test_invalid_input_does_nothing(self):
        for payload in (None, [], {"session_id": "../../etc/x", "hook_event_name": "Stop"},
                        {"session_id": SID}, {"session_id": SID, "hook_event_name": "Bogus"}):
            self.hook.handle(payload, ddir=str(self.data), cdir=str(self.claude), parent_pid=500,
                             ps_info=self.ps, now=lambda: "2026-10-08T10:00:00Z")
        self.assertFalse((self.data / "agents").exists())

    def test_long_note_is_truncated(self):
        s = self.fire("Stop", last_assistant_message="x" * 500)
        self.assertEqual(len(s["note"]), 200)
        self.assertTrue(s["note"].endswith("…"))

    def test_script_is_silent_and_exits_zero(self):
        env = dict(os.environ, CLAUDE_DASHBOARD_DATA_DIR=str(self.data), CLAUDE_DASHBOARD_CLAUDE_DIR=str(self.claude))
        for stdin in ("not json", json.dumps({"session_id": SID, "hook_event_name": "Stop", "last_assistant_message": "hi"})):
            r = subprocess.run([sys.executable, str(HOOK_PATH)], input=stdin, capture_output=True, text=True,
                               env=env, timeout=20)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        self.assertEqual(self.state()["state"], "waiting")

    def test_captured_fixtures(self):
        expected = {"SessionStart": "idle", "UserPromptSubmit": "working", "PermissionRequest": "permission",
                    "Notification-permission_prompt": "permission", "Stop": "waiting", "StopFailure": "failed",
                    "SessionEnd": "ended"}
        files = sorted(FIXTURES.glob("*.json"))
        if not files:
            self.skipTest("no captured fixtures")
        for f in files:
            with self.subTest(fixture=f.name):
                payload = json.loads(f.read_text())
                payload["session_id"] = SID
                self.hook.handle(payload, ddir=str(self.data), cdir=str(self.claude), parent_pid=500,
                                 ps_info=self.ps, now=lambda: "2026-10-08T10:00:00Z")
                if f.stem in expected:
                    self.assertEqual(self.state()["state"], expected[f.stem])


if __name__ == "__main__":
    unittest.main()
