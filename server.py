"""Claude Sessions Dashboard: local HTTP server (listens on 127.0.0.1 only)."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import agents
import config as config_mod
import iterm
import live
import monitor as monitor_mod
import notes as notes_mod
import notifier as notifier_mod
import sessions

APP_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 7333
SESSION_ID_RE = re.compile(r"^[0-9a-f-]{36}$")
POST_PATH_RE = re.compile(r"/api/(notes|open|seen|focus)/([^/?#]+)")
MAX_BODY = 4096
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/filter.js": ("filter.js", "text/javascript; charset=utf-8"),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class App:
    def __init__(self, claude_dir, data_dir, *, static_dir=APP_DIR / "static", opener=iterm.open_in_iterm,
                 live_fn=live.get_live, dev_root=sessions.DEV_ROOT, is_missing=sessions.cwd_missing,
                 config=None, monitor=None, notifier=None, focuser=iterm.focus_tty) -> None:
        self.claude_dir = Path(claude_dir)
        self.static_dir = Path(static_dir)
        self.cache = sessions.SessionCache(self.claude_dir)
        self.notes = notes_mod.NotesStore(Path(data_dir) / "notes.json")
        self.opener = opener
        self.live_fn = live_fn
        self.dev_root = dev_root
        self.is_missing = is_missing
        self.port = DEFAULT_PORT
        self._rows: dict[str, dict] = {}
        self._lock = threading.Lock()
        data_dir = Path(data_dir)
        self.config = config if config is not None else config_mod.load_config(data_dir / "config.json")
        self.agents_dir = data_dir / "agents"
        self.seen = agents.SeenStore(data_dir / "seen.json")
        self.monitor = monitor if monitor is not None else monitor_mod.Monitor(self.config)
        self.notifier = notifier if notifier is not None else notifier_mod.Notifier(self.config)
        self.focuser = focuser

    def snapshot(self) -> dict:
        rows, hosts = sessions.build_rows(self.cache.load(), dev_root=self.dev_root, is_missing=self.is_missing)
        try:
            live_map = self.live_fn(self.claude_dir)
        except Exception as e:  # a malformed pid file etc. – the overview must work even without live status
            print(f"live: {e!r}", file=sys.stderr, flush=True)
            live_map = {}
        all_notes = self.notes.all()
        for row in rows:
            row["live"] = live_map.get(row["session_id"])
            row["note"] = all_notes.get(row["session_id"]) or next(
                (all_notes[c["session_id"]] for c in row["older_copies"] if c["session_id"] in all_notes), None
            )
        hook_states = agents.read_hook_states(self.agents_dir)
        seen = self.seen.all()
        system = self.monitor.snapshot()
        projects = (system.get("docker") or {}).get("projects") or []
        for row in rows:
            live_entry = row["live"]
            hook = hook_states.get(row["session_id"])
            row["attention"] = agents.attention_for(hook, live_entry, seen.get(row["session_id"]))
            usage = self.monitor.tree_usage(live_entry["pid"]) if live_entry else None
            row["tty"] = ((hook or {}).get("tty") or (usage or {}).get("tty")) if live_entry else None
            if usage is not None:
                usage = {k: v for k, v in usage.items() if k != "tty"}
                usage["stack"] = monitor_mod.stack_for(row, projects, self.config["docker_project_dirs"], self.dev_root)
            row["resources"] = usage
        with self._lock:
            self._rows = {r["session_id"]: r for r in rows}
        return {"generated_at": now_iso(), "jira_hosts": hosts, "rows": rows,
                "queue": agents.build_queue(rows), "system": system}

    def mark_seen(self, session_id: str) -> str:
        return self.seen.mark(session_id, now_iso())

    def focus_session(self, row: dict) -> tuple[int, dict]:
        if not row.get("live"):
            return 409, {"ok": False, "error": "Session is not running."}
        tty = row.get("tty")
        if not tty:
            return 409, {"ok": False, "error": "Session has no known terminal."}
        result, error = self.focuser(tty)
        if result == "ok":
            return 200, {"ok": True}
        if result == "notfound":
            return 404, {"ok": False, "error": f"No iTerm2 tab found for {tty}."}
        return 502, {"ok": False, "error": error or "osascript failed"}

    def tick(self) -> None:
        payload = self.snapshot()
        self.notifier.check_queue(payload["rows"])
        self.notifier.check_alerts(payload["system"].get("alerts") or [])

    def start_background(self) -> None:
        self.monitor.start(on_sample=self.tick)

    def find_row(self, session_id: str) -> dict | None:
        with self._lock:
            row = self._rows.get(session_id)
        if row is None:
            self.snapshot()
            with self._lock:
                row = self._rows.get(session_id)
        return row

    def save_note(self, session_id: str, fields: dict) -> dict:
        row = self.find_row(session_id) or {}
        inherited = row.get("note")
        if inherited and session_id not in self.notes.all():
            fields = {"status": inherited.get("status"), "note": inherited.get("note") or "", **fields}
            entry = self.notes.update(session_id, **fields)
            # The inherited note is moved: otherwise clearing it on the head row would bring it back from the older copy.
            self.notes.remove([c["session_id"] for c in row.get("older_copies", [])])
            return entry
        return self.notes.update(session_id, **fields)

    def open_session(self, row: dict) -> tuple[int, dict]:
        if not row.get("resume_cmd"):
            return 409, {"ok": False, "error": "Session has no known directory."}
        if "cwd-missing" in row.get("warnings", []) or self.is_missing(row["cwd"]):
            return 409, {"ok": False, "error": f"Directory {row['cwd']} no longer exists."}
        ok, error = self.opener(row["resume_cmd"])
        return (200, {"ok": True}) if ok else (502, {"ok": False, "error": error})


class Handler(BaseHTTPRequestHandler):
    server_version = "ClaudeDashboard/1.0"

    @property
    def app(self) -> App:
        return self.server.app

    def log_request(self, code="-", size="-"):
        # Polling every 10 s would flood the log; log errors only.
        try:
            if int(code) < 400:
                return
        except (TypeError, ValueError):
            pass
        super().log_request(code, size)

    def _allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.app.port}", f"localhost:{self.app.port}"}

    def do_GET(self):
        self._safely(self._handle_get)

    def do_POST(self):
        self._safely(self._handle_post)

    def _safely(self, handler) -> None:
        try:
            handler()
        except Exception as e:  # the server must keep running and the client must get a response (spec §10)
            self.log_error("error while handling %s: %r", self.path, e)
            try:
                self._json({"error": f"internal error: {e}"}, 500)
            except OSError:
                pass

    def _handle_get(self):
        if self.headers.get("Host") not in self._allowed_hosts():
            return self._json({"error": "forbidden host"}, 403)
        path = self.path.split("?", 1)[0]
        if path in STATIC_FILES:
            name, ctype = STATIC_FILES[path]
            return self._file(self.app.static_dir / name, ctype)
        if path == "/api/health":
            return self._json({"ok": True})
        if path == "/api/sessions":
            return self._json(self.app.snapshot())
        if path == "/api/system":
            return self._json(self.app.monitor.snapshot())
        return self._json({"error": "not found"}, 404)

    def _handle_post(self):
        allowed = self._allowed_hosts()
        if self.headers.get("Host") not in allowed:
            return self._json({"error": "forbidden host"}, 403)
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {f"http://{h}" for h in allowed}:
            return self._json({"error": "forbidden origin"}, 403)
        ctype = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        if ctype != "application/json":
            return self._json({"error": "Content-Type must be application/json"}, 403)
        match = POST_PATH_RE.fullmatch(self.path)
        if not match or not SESSION_ID_RE.match(match.group(2)):
            return self._json({"error": "not found"}, 404)
        body = self._read_body()
        if body is None:
            return None
        action, session_id = match.groups()
        row = self.app.find_row(session_id)
        if row is None:
            return self._json({"error": "unknown session"}, 404)
        if action == "seen":
            return self._json({"ok": True, "seen_at": self.app.mark_seen(session_id)})
        if action == "focus":
            self.app.snapshot()
            status, payload = self.app.focus_session(self.app.find_row(session_id) or row)
            return self._json(payload, status)
        if action == "notes":
            fields = {k: body[k] for k in ("status", "note") if k in body}
            try:
                entry = self.app.save_note(session_id, fields)
            except notes_mod.NotesError as e:
                return self._json({"error": str(e)}, 400)
            return self._json({"ok": True, "note": entry})
        status, payload = self.app.open_session(row)
        return self._json(payload, status)

    def _read_body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self._json({"error": "invalid Content-Length"}, 400)
            return None
        if length > MAX_BODY:
            self.rfile.read(min(length, 1 << 20))  # drain it, otherwise the client may get an RST instead of the response
            self._json({"error": "request body too large"}, 413)
            return None
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._json({"error": "invalid JSON"}, 400)
            return None
        if not isinstance(body, dict):
            self._json({"error": "body must be a JSON object"}, 400)
            return None
        return body

    def _json(self, obj, status: int = 200) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _file(self, path: Path, ctype: str) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            return self._json({"error": "not found"}, 404)
        self._send(200, data, ctype)

    def _send(self, status: int, data: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, app: App) -> None:
        self.app = app
        super().__init__(address, Handler)
        app.port = self.server_address[1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Claude Sessions Dashboard")
    parser.add_argument("--port", type=int, default=int(os.environ.get("CLAUDE_DASHBOARD_PORT", DEFAULT_PORT)))
    parser.add_argument("--claude-dir", type=Path, default=Path.home() / ".claude")
    parser.add_argument("--data-dir", type=Path,
                        default=Path.home() / "Library" / "Application Support" / "claude-dashboard")
    args = parser.parse_args(argv)
    app = App(args.claude_dir, args.data_dir)
    try:
        httpd = DashboardServer(("127.0.0.1", args.port), app)
    except OSError as e:
        print(f"Cannot open port {args.port}: {e}", file=sys.stderr, flush=True)
        return 1
    app.start_background()
    print(f"Claude Sessions Dashboard: http://127.0.0.1:{app.port}/", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
