"""Uživatelské stavy a poznámky k sessions; JSON soubor s atomickým zápisem."""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

VALID_STATUSES = ("active", "waiting", "done", "archived")
MAX_NOTE = 500
_UNSET = object()


class NotesError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class NotesStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def all(self) -> dict[str, dict]:
        with self._lock:
            return self._read()

    def update(self, session_id: str, *, status=_UNSET, note=_UNSET) -> dict:
        if status is not _UNSET and status is not None and status not in VALID_STATUSES:
            raise NotesError(f"neplatný stav: {status!r}")
        if note is not _UNSET and not isinstance(note, str):
            raise NotesError("poznámka musí být text")
        with self._lock:
            data = self._read()
            entry = dict(data.get(session_id) or {})
            if status is not _UNSET:
                entry["status"] = status
            if note is not _UNSET:
                entry["note"] = " ".join(note.split())[:MAX_NOTE]
            entry["updated_at"] = _now()
            if entry.get("status") or entry.get("note"):
                data[session_id] = entry
            else:
                data.pop(session_id, None)
            self._write(data)
        return {"status": entry.get("status"), "note": entry.get("note") or "", "updated_at": entry["updated_at"]}

    def _read(self) -> dict[str, dict]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            parsed = json.loads(raw)
            data = parsed["notes"]
            if not isinstance(data, dict):
                raise TypeError("notes is not an object")
            return data
        except (ValueError, KeyError, TypeError):
            os.replace(self.path, self.path.with_name(f"{self.path.name}.corrupt-{int(time.time())}"))
            return {}

    def _write(self, data: dict[str, dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".notes-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump({"version": 1, "notes": data}, fh, ensure_ascii=False, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise
