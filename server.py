"""Claude Sessions Dashboard: lokální HTTP server (poslouchá jen na 127.0.0.1)."""
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

import iterm
import live
import notes as notes_mod
import sessions

APP_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 7333
SESSION_ID_RE = re.compile(r"^[0-9a-f-]{36}$")
POST_PATH_RE = re.compile(r"/api/(notes|open)/([^/?#]+)")
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
                 live_fn=live.get_live, dev_root=sessions.DEV_ROOT, is_missing=sessions.cwd_missing) -> None:
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

    def snapshot(self) -> dict:
        rows, hosts = sessions.build_rows(self.cache.load(), dev_root=self.dev_root, is_missing=self.is_missing)
        try:
            live_map = self.live_fn(self.claude_dir)
        except Exception as e:  # poškozený pid soubor apod. – přehled musí fungovat i bez živého stavu
            print(f"live: {e!r}", file=sys.stderr, flush=True)
            live_map = {}
        all_notes = self.notes.all()
        for row in rows:
            row["live"] = live_map.get(row["session_id"])
            row["note"] = all_notes.get(row["session_id"]) or next(
                (all_notes[c["session_id"]] for c in row["older_copies"] if c["session_id"] in all_notes), None
            )
        with self._lock:
            self._rows = {r["session_id"]: r for r in rows}
        return {"generated_at": now_iso(), "jira_hosts": hosts, "rows": rows}

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
            # Převzatá poznámka se přesouvá: jinak by se po smazání na hlavním řádku vrátila ze starší kopie.
            self.notes.remove([c["session_id"] for c in row.get("older_copies", [])])
            return entry
        return self.notes.update(session_id, **fields)

    def open_session(self, row: dict) -> tuple[int, dict]:
        if not row.get("resume_cmd"):
            return 409, {"ok": False, "error": "Session nemá známý adresář."}
        if "cwd-missing" in row.get("warnings", []) or self.is_missing(row["cwd"]):
            return 409, {"ok": False, "error": f"Adresář {row['cwd']} už neexistuje."}
        ok, error = self.opener(row["resume_cmd"])
        return (200, {"ok": True}) if ok else (502, {"ok": False, "error": error})


class Handler(BaseHTTPRequestHandler):
    server_version = "ClaudeDashboard/1.0"

    @property
    def app(self) -> App:
        return self.server.app

    def log_request(self, code="-", size="-"):
        # Polling každých 10 s by zaplavil log; logujeme jen chyby.
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
        except Exception as e:  # server musí běžet dál a klient dostat odpověď (spec §10)
            self.log_error("chyba při zpracování %s: %r", self.path, e)
            try:
                self._json({"error": f"interní chyba: {e}"}, 500)
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
            return self._json({"error": "Content-Type musí být application/json"}, 403)
        match = POST_PATH_RE.fullmatch(self.path)
        if not match or not SESSION_ID_RE.match(match.group(2)):
            return self._json({"error": "not found"}, 404)
        body = self._read_body()
        if body is None:
            return None
        action, session_id = match.groups()
        row = self.app.find_row(session_id)
        if row is None:
            return self._json({"error": "neznámá session"}, 404)
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
            self._json({"error": "neplatná Content-Length"}, 400)
            return None
        if length > MAX_BODY:
            self.rfile.read(min(length, 1 << 20))  # dočíst, jinak může klient místo odpovědi dostat RST
            self._json({"error": "tělo požadavku je příliš velké"}, 413)
            return None
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            self._json({"error": "nevalidní JSON"}, 400)
            return None
        if not isinstance(body, dict):
            self._json({"error": "tělo musí být JSON objekt"}, 400)
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
        print(f"Nelze otevřít port {args.port}: {e}", file=sys.stderr, flush=True)
        return 1
    threading.Thread(target=app.cache.load, name="warmup", daemon=True).start()
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
