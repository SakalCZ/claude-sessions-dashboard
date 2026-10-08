import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

import settings_merge as sm

CMD = "\"/usr/local/bin/python3\" '/Users/me/Library/Application Support/claude-dashboard/app/hook/claude_hook.py'"
OURS = {"hooks": [{"type": "command", "command": CMD, "async": True, "timeout": 5}]}
CC = {"hooks": [{"type": "command", "command": "/Users/me/.config/iterm2/cc-status"}]}


class SettingsMergeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name, "settings.json")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data):
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def read(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def backups(self):
        return sorted(p.name for p in self.path.parent.glob("settings.json.bak-claude-dashboard-*"))

    def test_install_keeps_others(self):
        self.write({"model": "opus", "hooks": {"Stop": [CC], "PreToolUse": [CC]}, "theme": "dark"})
        sm.install(self.path, CMD)
        data = self.read()
        self.assertEqual(list(data), ["model", "hooks", "theme"])
        self.assertEqual(data["hooks"]["Stop"], [CC, OURS])
        self.assertEqual(data["hooks"]["PreToolUse"], [CC])
        self.assertEqual(set(data["hooks"]), {"PreToolUse", *sm.EVENTS})
        for event in sm.EVENTS:
            self.assertEqual(data["hooks"][event][-1], OURS)
        self.assertEqual(len(self.backups()), 1)

    def test_shared_group_keeps_foreign_hooks(self):
        shared = {"hooks": [CC["hooks"][0], OURS["hooks"][0]]}
        self.write({"hooks": {"Stop": [shared]}})
        sm.install(self.path, CMD)
        self.assertEqual(self.read()["hooks"]["Stop"], [CC, OURS])
        sm.uninstall(self.path)
        self.assertEqual(self.read(), {"hooks": {"Stop": [CC]}})

    def test_symlinked_settings_stays_a_symlink(self):
        target_dir = Path(self.tmp.name, "dotfiles")
        target_dir.mkdir()
        target = target_dir / "settings.json"
        target.write_text(json.dumps({"hooks": {"Stop": [CC]}}), encoding="utf-8")
        os.symlink(target, self.path)
        sm.install(self.path, CMD)
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["hooks"]["Stop"], [CC, OURS])
        sm.uninstall(self.path)
        self.assertTrue(self.path.is_symlink())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"hooks": {"Stop": [CC]}})

    def test_install_is_idempotent(self):
        self.write({"hooks": {"Stop": [CC]}})
        sm.install(self.path, CMD)
        first = self.path.read_text(encoding="utf-8")
        sm.install(self.path, CMD)
        self.assertEqual(self.path.read_text(encoding="utf-8"), first)
        self.assertEqual(len(self.backups()), 1)

    def test_install_replaces_our_old_command(self):
        self.write({"hooks": {"Stop": [CC]}})
        sm.install(self.path, CMD)
        newer = CMD.replace("python3", "python3.14")
        sm.install(self.path, newer)
        stop = self.read()["hooks"]["Stop"]
        self.assertEqual(len(stop), 2)
        self.assertEqual(stop[1]["hooks"][0]["command"], newer)

    def test_invalid_json_aborts_unchanged(self):
        self.path.write_text("{nope", encoding="utf-8")
        with self.assertRaises(sm.MergeError):
            sm.install(self.path, CMD)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{nope")
        self.assertEqual(self.backups(), [])

    def test_non_object_hooks_aborts(self):
        for data in ({"hooks": []}, {"hooks": {"Stop": {"x": 1}}}, [1, 2]):
            with self.subTest(data=data):
                self.write(data)
                before = self.path.read_text(encoding="utf-8")
                with self.assertRaises(sm.MergeError):
                    sm.install(self.path, CMD)
                self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_missing_file_is_created(self):
        sm.install(self.path, CMD)
        self.assertEqual(set(self.read()["hooks"]), set(sm.EVENTS))
        self.assertEqual(self.backups(), [])

    def test_uninstall_removes_only_ours(self):
        self.write({"hooks": {"Stop": [CC]}, "permissions": {"allow": []}})
        sm.install(self.path, CMD)
        sm.uninstall(self.path)
        self.assertEqual(self.read(), {"hooks": {"Stop": [CC]}, "permissions": {"allow": []}})
        self.assertEqual(len(self.backups()), 2)

    def test_uninstall_without_our_hooks_is_noop(self):
        self.write({"hooks": {"Stop": [CC], "Empty": []}})
        before = self.path.read_text(encoding="utf-8")
        sm.uninstall(self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertEqual(self.backups(), [])

    def test_uninstall_missing_file(self):
        sm.uninstall(self.path)
        self.assertFalse(self.path.exists())

    def test_keeps_file_mode(self):
        self.write({"hooks": {}})
        os.chmod(self.path, 0o600)
        sm.install(self.path, CMD)
        self.assertEqual(stat.S_IMODE(os.stat(self.path).st_mode), 0o600)

    def test_main_cli(self):
        self.assertEqual(sm.main(["install", str(self.path), CMD]), 0)
        self.assertEqual(sm.main(["uninstall", str(self.path)]), 0)
        self.assertEqual(sm.main(["bogus"]), 2)
        self.path.write_text("{nope", encoding="utf-8")
        self.assertEqual(sm.main(["install", str(self.path), CMD]), 1)


if __name__ == "__main__":
    unittest.main()
