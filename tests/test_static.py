import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"


class StaticFilesTest(unittest.TestCase):
    def test_no_html_injection_apis(self):
        for name in ("app.js", "filter.js"):
            src = (STATIC / name).read_text(encoding="utf-8")
            for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
                with self.subTest(file=name, api=banned):
                    self.assertNotIn(banned, src)

    def test_index_loads_filter_before_app_and_no_external_resources(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertLess(html.index('src="/filter.js"'), html.index('src="/app.js"'))
        self.assertIn("<title>Claude Sessions</title>", html)
        self.assertNotRegex(html, r'(src|href)\s*=\s*["\']?(https?:)?//')  # no external scripts/styles/CDN

    def test_filter_js_under_node(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not on PATH")
        result = subprocess.run([node, str(ROOT / "tests" / "js" / "test_filter.js")], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
