import subprocess
import unittest

import iterm

CMD = "cd '/Users/x/it'\"'\"'s dir' && claude --resume abc"


class ItermTest(unittest.TestCase):
    def test_command_is_passed_as_argv_not_interpolated(self):
        argv = iterm.build_argv(CMD)
        self.assertEqual(argv, ["osascript", "-e", iterm.APPLESCRIPT, CMD])
        self.assertIn("on run argv", iterm.APPLESCRIPT)
        self.assertNotIn("claude --resume", iterm.APPLESCRIPT)

    def test_success(self):
        calls = []

        def runner(argv, **kwargs):
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, "", "")

        self.assertEqual(iterm.open_in_iterm(CMD, runner=runner), (True, None))
        self.assertEqual(calls[0][0][-1], CMD)
        self.assertEqual(calls[0][1]["timeout"], 10)

    def test_automation_permission_denied(self):
        runner = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "execution error: Not authorized to send Apple events to iTerm2. (-1743)")
        ok, error = iterm.open_in_iterm(CMD, runner=runner)
        self.assertFalse(ok)
        self.assertIn("Automation", error)

    def test_other_error_is_reported(self):
        runner = lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", "boom")
        self.assertEqual(iterm.open_in_iterm(CMD, runner=runner), (False, "boom"))

    def test_timeout_and_missing_binary(self):
        def timeout(argv, **kw):
            raise subprocess.TimeoutExpired(argv, 10)

        def missing(argv, **kw):
            raise FileNotFoundError("osascript")

        self.assertFalse(iterm.open_in_iterm(CMD, runner=timeout)[0])
        self.assertIn("osascript", iterm.open_in_iterm(CMD, runner=missing)[1])



class FocusTest(unittest.TestCase):
    def run_with(self, stdout="", returncode=0, stderr=""):
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            return subprocess.CompletedProcess(argv, returncode, stdout, stderr)
        return calls, runner

    def test_ok_and_argv(self):
        calls, runner = self.run_with("ok\n")
        self.assertEqual(iterm.focus_tty("ttys003", runner=runner), ("ok", None))
        self.assertEqual(calls[0], ["osascript", "-e", iterm.FOCUS_SCRIPT, "/dev/ttys003"])
        self.assertNotIn("ttys003", iterm.FOCUS_SCRIPT)

    def test_notfound_and_errors(self):
        self.assertEqual(iterm.focus_tty("ttys003", runner=self.run_with("notfound")[1]), ("notfound", None))
        status, error = iterm.focus_tty("ttys003", runner=self.run_with("", 1, "boom")[1])
        self.assertEqual((status, error), ("error", "boom"))
        status, error = iterm.focus_tty("ttys003", runner=self.run_with("", 1, "Not authorized (-1743)")[1])
        self.assertEqual(status, "error")
        self.assertIn("Automation", error)

    def test_invalid_tty_never_runs_osascript(self):
        calls, runner = self.run_with("ok")
        for tty in (None, "", "/dev/ttys003", "ttys003; rm -rf /", "console"):
            self.assertEqual(iterm.focus_tty(tty, runner=runner)[0], "error")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
