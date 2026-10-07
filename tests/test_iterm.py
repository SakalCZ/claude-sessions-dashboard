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


if __name__ == "__main__":
    unittest.main()
