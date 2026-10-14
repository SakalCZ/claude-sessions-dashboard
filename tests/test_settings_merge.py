import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

import settings_merge as sm

CMD = "\"/usr/local/bin/python3\" '/Users/me/Library/Application Support/claude-dashboard/app/hook/claude_hook.py'"
TAP = "\"/usr/local/bin/python3\" -I -S '/Users/me/Library/Application Support/claude-dashboard/app/hook/statusline_tap.py'"
USER_LINE = {"type": "command", "command": "bash ~/.claude/statusline-command.sh", "padding": 0}
OURS = {"hooks": [{"type": "command", "command": CMD, "async": True, "timeout": 5}]}
CC = {"hooks": [{"type": "command", "command": "/Users/me/.config/iterm2/cc-status"}]}


class SettingsMergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name, "settings.json")
        self.store = Path(self.tmp.name, "statusline.json")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data):
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def backups(self):
        return sorted(p.name for p in self.path.parent.glob("settings.json.bak-claude-dashboard-*"))

    def install(self, cmd=CMD, tap=TAP):
        sm.install(self.path, cmd, tap, self.store)

    def uninstall(self):
        sm.uninstall(self.path, self.store)

    def stored(self):
        return json.loads(self.store.read_text(encoding="utf-8"))

    def test_install_keeps_others(self):
        self.write({"model": "opus", "hooks": {"Stop": [CC], "PreToolUse": [CC]}, "theme": "dark"})
        self.install()
        data = self.read()
        self.assertEqual(list(data), ["model", "hooks", "theme", "statusLine"])
        self.assertEqual(data["hooks"]["Stop"], [CC, OURS])
        self.assertEqual(data["hooks"]["PreToolUse"], [CC])
        self.assertEqual(set(data["hooks"]), {"PreToolUse", *sm.EVENTS})
        for event in sm.EVENTS:
            self.assertEqual(data["hooks"][event][-1], OURS)
        self.assertEqual(len(self.backups()), 1)

    def test_shared_group_keeps_foreign_hooks(self):
        shared = {"hooks": [CC["hooks"][0], OURS["hooks"][0]]}
        self.write({"hooks": {"Stop": [shared]}})
        self.install()
        self.assertEqual(self.read()["hooks"]["Stop"], [CC, OURS])
        self.uninstall()
        self.assertEqual(self.read(), {"hooks": {"Stop": [CC]}})

    def test_symlinked_settings_stays_a_symlink(self):
        target_dir = Path(self.tmp.name, "dotfiles")
        target_dir.mkdir()
        target = target_dir / "settings.json"
        target.write_text(json.dumps({"hooks": {"Stop": [CC]}}), encoding="utf-8")
        os.symlink(target, self.path)
        self.install()
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["hooks"]["Stop"], [CC, OURS])
        self.uninstall()
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["hooks"], {"Stop": [CC]})

    def test_install_is_idempotent(self):
        self.write({"hooks": {"Stop": [CC]}})
        self.install()
        first = self.path.read_text(encoding="utf-8")
        self.install()
        self.assertEqual(self.path.read_text(encoding="utf-8"), first)
        self.assertEqual(len(self.backups()), 1)

    def test_install_replaces_our_old_command(self):
        self.write({"hooks": {"Stop": [CC]}})
        self.install()
        newer = CMD.replace("python3", "python3.14")
        self.install(cmd=newer)
        stop = self.read()["hooks"]["Stop"]
        self.assertEqual(len(stop), 2)
        self.assertEqual(stop[1]["hooks"][0]["command"], newer)

    def test_invalid_json_aborts_unchanged(self):
        self.path.write_text("{nope", encoding="utf-8")
        with self.assertRaises(sm.MergeError):
            self.install()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{nope")
        self.assertEqual(self.backups(), [])

    def test_non_object_hooks_aborts(self):
        for data in ({"hooks": []}, {"hooks": {"Stop": {"x": 1}}}, [1, 2]):
            with self.subTest(data=data):
                self.write(data)
                before = self.path.read_text(encoding="utf-8")
                with self.assertRaises(sm.MergeError):
                    self.install()
                self.assertEqual(self.path.read_text(encoding="utf-8"), before)
                self.assertFalse(self.store.exists())

    def test_missing_file_is_created(self):
        self.install()
        self.assertEqual(set(self.read()["hooks"]), set(sm.EVENTS))
        self.assertEqual(self.backups(), [])

    def test_uninstall_removes_only_ours(self):
        self.write({"hooks": {"Stop": [CC]}, "permissions": {"allow": []}})
        self.install()
        self.uninstall()
        self.assertEqual(self.read(), {"hooks": {"Stop": [CC]}, "permissions": {"allow": []}})
        self.assertEqual(len(self.backups()), 2)

    def test_uninstall_without_our_hooks_is_noop(self):
        self.write({"hooks": {"Stop": [CC], "Empty": []}})
        before = self.path.read_text(encoding="utf-8")
        self.uninstall()
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertEqual(self.backups(), [])

    def test_uninstall_missing_file(self):
        self.uninstall()
        self.assertFalse(self.path.exists())

    def test_keeps_file_mode(self):
        self.write({"hooks": {}})
        os.chmod(self.path, 0o600)
        self.install()
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_wrap_existing_status_line(self):
        self.write({"statusLine": USER_LINE, "hooks": {"Stop": [CC]}})
        self.install()
        self.assertEqual(self.read()["statusLine"], {**USER_LINE, "command": TAP})
        self.assertEqual(self.stored(), {"command": USER_LINE["command"]})
        self.assertEqual(len(self.backups()), 1)

    def test_wrap_without_status_line(self):
        self.write({"hooks": {"Stop": [CC]}})
        self.install()
        self.assertEqual(self.read()["statusLine"], {"type": "command", "command": TAP})
        self.assertEqual(self.stored(), {"command": None})

    def test_reinstall_keeps_stored_original(self):
        self.write({"statusLine": USER_LINE})
        self.install()
        newer = TAP.replace("python3", "python3.14")
        self.install(tap=newer)
        self.assertEqual(self.read()["statusLine"]["command"], newer)
        self.assertEqual(self.stored(), {"command": USER_LINE["command"]})
        self.install(tap=newer)
        self.assertEqual(len(self.backups()), 2)

    def test_rewrap_after_user_replaced_status_line(self):
        self.write({"statusLine": USER_LINE})
        self.install()
        data = self.read()
        data["statusLine"] = {"type": "command", "command": "~/new-line.sh"}
        self.write(data)
        self.install()
        self.assertEqual(self.read()["statusLine"]["command"], TAP)
        self.assertEqual(self.stored(), {"command": "~/new-line.sh"})

    def test_unwrap_restores_original(self):
        self.write({"hooks": {"Stop": [CC]}, "statusLine": USER_LINE})
        before = self.read()
        self.install()
        self.uninstall()
        self.assertEqual(self.read(), before)
        self.assertFalse(self.store.exists())

    def test_unwrap_without_stored_command_removes_status_line(self):
        self.write({"hooks": {"Stop": [CC]}})
        self.install()
        self.uninstall()
        self.assertEqual(self.read(), {"hooks": {"Stop": [CC]}})
        self.install()
        self.store.unlink()
        self.uninstall()
        self.assertEqual(self.read(), {"hooks": {"Stop": [CC]}})

    def test_unwrap_leaves_foreign_status_line(self):
        self.write({"statusLine": USER_LINE})
        self.store.write_text(json.dumps({"command": "something else"}), encoding="utf-8")
        before = self.path.read_text(encoding="utf-8")
        self.uninstall()
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertEqual(self.backups(), [])
        self.assertFalse(self.store.exists())

    def test_invalid_status_line_aborts_unchanged(self):
        for line in ([1], "bash x.sh", {"type": "static", "command": "x"}, {"command": "x"},
                     {"type": "command", "command": 5}):
            with self.subTest(line=line):
                self.write({"statusLine": line})
                before = self.path.read_text(encoding="utf-8")
                with self.assertRaises(sm.MergeError):
                    self.install()
                self.assertEqual(self.path.read_text(encoding="utf-8"), before)
                self.assertFalse(self.store.exists())
        self.assertEqual(self.backups(), [])

    def test_wrap_and_unwrap_through_symlink(self):
        target = Path(self.tmp.name, "dotfiles-settings.json")
        target.write_text(json.dumps({"statusLine": USER_LINE}), encoding="utf-8")
        os.symlink(target, self.path)
        self.install()
        self.uninstall()
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"statusLine": USER_LINE, "hooks": {}})

    def test_main_cli(self):
        self.assertEqual(sm.main(["install", str(self.path), CMD, TAP, str(self.store)]), 0)
        self.assertEqual(self.read()["statusLine"]["command"], TAP)
        self.assertEqual(sm.main(["uninstall", str(self.path), str(self.store)]), 0)
        self.assertNotIn("statusLine", self.read())
        self.assertEqual(sm.main(["install", str(self.path), CMD]), 2)
        self.assertEqual(sm.main(["uninstall", str(self.path)]), 2)
        self.assertEqual(sm.main(["bogus"]), 2)
        self.path.write_text("{nope", encoding="utf-8")
        self.assertEqual(sm.main(["install", str(self.path), CMD, TAP, str(self.store)]), 1)


if __name__ == "__main__":
    unittest.main()
