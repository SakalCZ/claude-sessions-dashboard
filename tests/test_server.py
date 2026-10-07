import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

import server
from tests.helpers import FakeClaude, assistant, sid, user

CWD = "/w/acme/shop"


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeClaude()
        self.data = tempfile.TemporaryDirectory()
        self.static = tempfile.TemporaryDirectory()
        Path(self.static.name, "index.html").write_text("<title>Claude Sessions</title>", encoding="utf-8")
        Path(self.static.name, "app.js").write_text("// app", encoding="utf-8")
        Path(self.static.name, "filter.js").write_text("// filter", encoding="utf-8")
        branch = "me/bugfix/PROJ-563-dup"
        self.fake.write_session(CWD, sid(1), [
            user("Analyze https://acme.atlassian.net/browse/PROJ-563", ts="2026-10-01T10:00:00Z", uuid="u1", cwd=CWD, branch=branch),
            assistant(ts="2026-10-01T10:00:05Z", uuid="a1", cwd=CWD, branch=branch),
        ])
        self.opened = []
        self.open_result = (True, None)
        self.missing = False

        def opener(cmd):
            self.opened.append(cmd)
            return self.open_result

        self.app = server.App(
            self.fake.root, self.data.name, static_dir=self.static.name, opener=opener,
            live_fn=lambda d: {sid(1): {"status": "idle", "pid": 42, "name": "x", "updated_at": 1}},
            dev_root=Path("/w"), is_missing=lambda c: self.missing,
        )
        self.httpd = server.DashboardServer(("127.0.0.1", 0), self.app)
        threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.fake.cleanup()
        self.data.cleanup()
        self.static.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=5)
        hdrs = {"Content-Type": "application/json"} if method == "POST" else {}
        hdrs.update(headers or {})
        payload = None if body is None else (body if isinstance(body, str) else json.dumps(body))
        conn.request(method, path, body=payload, headers=hdrs)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        if (res.getheader("Content-Type") or "").startswith("application/json"):
            return res.status, json.loads(raw)
        return res.status, raw.decode("utf-8")

    def rows(self):
        return {r["session_id"]: r for r in self.request("GET", "/api/sessions")[1]["rows"]}

    def test_static_files(self):
        self.assertEqual(self.request("GET", "/"), (200, "<title>Claude Sessions</title>"))
        self.assertEqual(self.request("GET", "/app.js"), (200, "// app"))
        self.assertEqual(self.request("GET", "/filter.js"), (200, "// filter"))
        self.assertEqual(self.request("GET", "/../server.py")[0], 404)

    def test_health(self):
        self.assertEqual(self.request("GET", "/api/health"), (200, {"ok": True}))

    def test_sessions_payload(self):
        status, payload = self.request("GET", "/api/sessions")
        self.assertEqual(status, 200)
        self.assertEqual(payload["jira_hosts"], {"PROJ": "acme.atlassian.net"})
        [row] = payload["rows"]
        self.assertEqual(row["session_id"], sid(1))
        self.assertEqual(row["display_dir"], "acme/shop")
        self.assertEqual(row["jira_key"], "PROJ-563")
        self.assertEqual(row["live"]["status"], "idle")
        self.assertIsNone(row["note"])

    def test_get_rejects_foreign_host(self):
        status, _ = self.request("GET", "/api/sessions", headers={"Host": f"evil.example:{self.app.port}"})
        self.assertEqual(status, 403)

    def test_notes_roundtrip(self):
        status, body = self.request("POST", f"/api/notes/{sid(1)}", {"status": "waiting", "note": "waiting for CR"})
        self.assertEqual(status, 200)
        self.assertEqual(body["note"]["status"], "waiting")
        self.assertEqual(self.rows()[sid(1)]["note"]["note"], "waiting for CR")

    def test_notes_invalid_status(self):
        self.assertEqual(self.request("POST", f"/api/notes/{sid(1)}", {"status": "bogus"})[0], 400)

    def test_post_security_checks(self):
        port = self.app.port
        cases = [
            {"Origin": "http://evil.example"},
            {"Content-Type": "text/plain"},
            {"Host": f"evil.example:{port}"},
        ]
        for headers in cases:
            with self.subTest(headers=headers):
                self.assertEqual(self.request("POST", f"/api/notes/{sid(1)}", {"status": "done"}, headers)[0], 403)
        self.assertIsNone(self.rows()[sid(1)]["note"])
        ok_origin = self.request("POST", f"/api/notes/{sid(1)}", {"status": "done"}, {"Origin": f"http://localhost:{port}"})
        self.assertEqual(ok_origin[0], 200)

    def test_unknown_or_malformed_session(self):
        self.assertEqual(self.request("POST", f"/api/notes/{sid(99)}", {"status": "done"})[0], 404)
        self.assertEqual(self.request("POST", "/api/notes/abc", {"status": "done"})[0], 404)
        self.assertEqual(self.request("GET", "/api/nope")[0], 404)

    def test_body_limits(self):
        self.assertEqual(self.request("POST", f"/api/notes/{sid(1)}", {"note": "x" * 5000})[0], 413)
        self.assertEqual(self.request("POST", f"/api/notes/{sid(1)}", "[1, 2]")[0], 400)
        self.assertEqual(self.request("POST", f"/api/notes/{sid(1)}", "{nope")[0], 400)

    def test_open_calls_opener_with_resume_cmd(self):
        self.assertEqual(self.request("POST", f"/api/open/{sid(1)}", {}), (200, {"ok": True}))
        self.assertEqual(self.opened, [f"cd {CWD} && claude --resume {sid(1)}"])

    def test_open_failure(self):
        self.open_result = (False, "iTerm2 is not running")
        self.assertEqual(self.request("POST", f"/api/open/{sid(1)}", {}), (502, {"ok": False, "error": "iTerm2 is not running"}))

    def test_open_refuses_missing_cwd(self):
        self.missing = True
        status, body = self.request("POST", f"/api/open/{sid(1)}", {})
        self.assertEqual(status, 409)
        self.assertIn("no longer exists", body["error"])
        self.assertEqual(self.opened, [])

    def test_note_inherited_from_older_copy(self):
        self.fake.write_session(CWD, sid(2), [
            user("Analyze https://acme.atlassian.net/browse/PROJ-563", ts="2026-10-01T10:00:00Z", uuid="u1", cwd=CWD),
        ])
        self.app.notes.update(sid(2), status="waiting", note="from copy")
        rows = self.rows()
        self.assertEqual(list(rows), [sid(1)])
        self.assertEqual(rows[sid(1)]["note"]["note"], "from copy")
        status, body = self.request("POST", f"/api/notes/{sid(1)}", {"note": "new"})
        self.assertEqual(status, 200)
        self.assertEqual(body["note"], {"status": "waiting", "note": "new", "updated_at": body["note"]["updated_at"]})


    def test_open_rechecks_missing_cwd_after_snapshot(self):
        self.rows()  # snapshot while the directory exists
        self.missing = True  # directory deleted between the refresh and the click
        status, body = self.request("POST", f"/api/open/{sid(1)}", {})
        self.assertEqual(status, 409)
        self.assertIn("no longer exists", body["error"])
        self.assertEqual(self.opened, [])

    def test_sessions_survive_failing_live_fn(self):
        def boom(_):
            raise TypeError("bad pid file")
        self.app.live_fn = boom
        status, payload = self.request("GET", "/api/sessions")
        self.assertEqual(status, 200)
        self.assertIsNone(payload["rows"][0]["live"])

    def test_unexpected_error_returns_json_500(self):
        def boom():
            raise RuntimeError("kaboom")
        self.app.snapshot = boom
        status, body = self.request("GET", "/api/sessions")
        self.assertEqual(status, 500)
        self.assertIn("error", body)

    def test_inherited_status_can_be_cleared(self):
        self.fake.write_session(CWD, sid(2), [
            user("Analyze https://acme.atlassian.net/browse/PROJ-563", ts="2026-10-01T10:00:00Z", uuid="u1", cwd=CWD),
        ])
        self.app.notes.update(sid(2), status="done")
        self.assertEqual(self.rows()[sid(1)]["note"]["status"], "done")
        status, _ = self.request("POST", f"/api/notes/{sid(1)}", {"status": None})
        self.assertEqual(status, 200)
        self.assertIsNone(self.rows()[sid(1)]["note"])


if __name__ == "__main__":
    unittest.main()
