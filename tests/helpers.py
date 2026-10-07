"""Pomocníci pro testy: falešný ~/.claude adresář s JSONL transcripty a pid soubory."""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path


def sid(n: int) -> str:
    """Deterministické id ve tvaru UUID (36 znaků, jen [0-9a-f-])."""
    return f"{n:08x}-0000-4000-8000-000000000000"


class FakeClaude:
    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "projects").mkdir()
        (self.root / "sessions").mkdir()

    def cleanup(self) -> None:
        self._tmp.cleanup()

    def write_session(self, cwd: str, session_id: str, records: list, project_dir: str | None = None) -> Path:
        folder = self.root / "projects" / (project_dir or re.sub(r"[^A-Za-z0-9]", "-", cwd))
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{session_id}.jsonl"
        lines = [r if isinstance(r, str) else json.dumps(r, ensure_ascii=False) for r in records]
        path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
        return path

    def write_pid(self, pid: int, data: dict) -> None:
        (self.root / "sessions" / f"{pid}.json").write_text(json.dumps(data), encoding="utf-8")


def user(content, *, ts: str, uuid: str, cwd: str = "/w/proj", branch: str = "master", **extra) -> dict:
    return {
        "type": "user", "message": {"role": "user", "content": content}, "timestamp": ts,
        "uuid": uuid, "parentUuid": None, "cwd": cwd, "gitBranch": branch, "isSidechain": False, **extra,
    }


def assistant(*, ts: str, uuid: str, cwd: str = "/w/proj", branch: str = "master") -> dict:
    return {
        "type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "ok"}]},
        "timestamp": ts, "uuid": uuid, "parentUuid": None, "cwd": cwd, "gitBranch": branch, "isSidechain": False,
    }


def tool_result(*, ts: str, uuid: str, cwd: str = "/w/proj", branch: str = "master") -> dict:
    return user([{"type": "tool_result", "tool_use_id": "t1", "content": "done"}], ts=ts, uuid=uuid, cwd=cwd, branch=branch)


def custom_title(title: str, session_id: str) -> dict:
    return {"type": "custom-title", "customTitle": title, "sessionId": session_id}


def agent_name(name: str, session_id: str) -> dict:
    return {"type": "agent-name", "agentName": name, "sessionId": session_id}


def last_prompt(text: str, session_id: str) -> dict:
    return {"type": "last-prompt", "lastPrompt": text, "sessionId": session_id}
