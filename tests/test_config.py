import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import config


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name, "config.json")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, obj):
        self.path.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")

    def test_missing_file_gives_defaults(self):
        self.assertEqual(config.load_config(self.path), config.DEFAULTS)

    def test_overrides_are_type_checked(self):
        self.write({
            "waiting_notify_after_s": 300, "cpu_load_factor": 2, "notifications_enabled": False,
            "permission_notify_after_s": "soon", "process_sample_interval_s": True, "cpu_sustain_s": -5,
            "unknown_key": 1, "docker_project_dirs": {"shared-stack": "acme/shop_local", "bad": 5},
        })
        cfg = config.load_config(self.path)
        self.assertEqual(cfg["waiting_notify_after_s"], 300)
        self.assertEqual(cfg["cpu_load_factor"], 2)
        self.assertFalse(cfg["notifications_enabled"])
        self.assertEqual(cfg["permission_notify_after_s"], 60)
        self.assertEqual(cfg["process_sample_interval_s"], 10)
        self.assertEqual(cfg["cpu_sustain_s"], 120)
        self.assertNotIn("unknown_key", cfg)
        self.assertEqual(cfg["docker_project_dirs"], {"shared-stack": "acme/shop_local"})

    def test_invalid_file_gives_defaults_and_logs(self):
        for content in ("{nope", "[1, 2]"):
            with self.subTest(content=content):
                self.write(content)
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(config.load_config(self.path), config.DEFAULTS)
                self.assertIn("config:", err.getvalue())

    def test_defaults_are_not_shared(self):
        cfg = config.load_config(self.path)
        cfg["docker_project_dirs"]["x"] = "y"
        self.assertEqual(config.DEFAULTS["docker_project_dirs"], {})


if __name__ == "__main__":
    unittest.main()
