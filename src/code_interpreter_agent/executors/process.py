"""Shared logic for executors that keep one long-lived worker process per session."""

from __future__ import annotations

import json
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from .base import ExecResult, validate_session_id

Snapshot = dict[str, tuple[int, int]]
EOF = ""  # a real protocol line always ends in a newline, so "" is a safe end marker


@dataclass
class _Session:
    workspace: Path
    lock: threading.Lock = field(default_factory=threading.Lock)
    process: subprocess.Popen | None = None
    lines: queue.Queue | None = None


def snapshot_workspace(workspace: Path) -> Snapshot:
    """Map relative file path to (mtime_ns, size). Hidden directories are skipped."""
    state: Snapshot = {}
    for path in workspace.rglob("*"):
        relative = path.relative_to(workspace)
        if any(part.startswith(".") for part in relative.parts) or not path.is_file():
            continue
        stat = path.stat()
        state[relative.as_posix()] = (stat.st_mtime_ns, stat.st_size)
    return state


def changed_files(before: Snapshot, after: Snapshot) -> tuple[str, ...]:
    return tuple(sorted(name for name, meta in after.items() if before.get(name) != meta))


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return text[:limit] + f"\n... [{omitted} characters truncated]"


class WorkerExecutor(ABC):
    """Template for executors built on the JSON line protocol in worker.py.

    Subclasses provide `_spawn`. Calls on one session are serialized; different sessions run
    in parallel. A timeout or a worker crash discards that session's interpreter state.
    """

    def __init__(
        self,
        *,
        workspace_root: Path | str | None = None,
        timeout_s: float = 30.0,
        cpu_s: float = 30.0,
        memory_mb: int = 2048,
        max_output_chars: int = 20_000,
        startup_timeout_s: float = 30.0,
    ) -> None:
        self.timeout_s = timeout_s
        self.cpu_s = cpu_s
        self.memory_mb = memory_mb
        self.max_output_chars = max_output_chars
        self.startup_timeout_s = startup_timeout_s
        self._owns_root = workspace_root is None
        self._root = Path(workspace_root or tempfile.mkdtemp(prefix="cia-")).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, _Session] = {}
        self._registry_lock = threading.Lock()

    @abstractmethod
    def _spawn(self, session_id: str, workspace: Path) -> subprocess.Popen:
        """Start a worker with stdin and stdout pipes and return it."""

    def _terminate(self, session_id: str, process: subprocess.Popen) -> None:
        process.kill()

    def _session(self, session_id: str) -> _Session:
        validate_session_id(session_id)
        with self._registry_lock:
            session = self._sessions.get(session_id)
            if session is None:
                workspace = self._root / session_id
                workspace.mkdir(parents=True, exist_ok=True)
                session = self._sessions[session_id] = _Session(workspace)
            return session

    def workspace_path(self, session_id: str) -> Path:
        return self._session(session_id).workspace

    def run(self, session_id: str, code: str) -> ExecResult:
        session = self._session(session_id)
        started = time.monotonic()
        with session.lock:
            before = snapshot_workspace(session.workspace)
            result = self._run_locked(session_id, session, code)
            files = changed_files(before, snapshot_workspace(session.workspace))
        return ExecResult(
            stdout=truncate(result["stdout"], self.max_output_chars),
            stderr=truncate(result["stderr"], self.max_output_chars),
            ok=result["ok"],
            files=files,
            duration_s=round(time.monotonic() - started, 3),
        )

    def _run_locked(self, session_id: str, session: _Session, code: str) -> dict:
        if session.process is None or session.process.poll() is not None:
            failure = self._start(session_id, session)
            if failure:
                return {"stdout": "", "stderr": failure, "ok": False}
        request = json.dumps({"code": code, "cpu_s": self.cpu_s}) + "\n"
        try:
            session.process.stdin.write(request)
            session.process.stdin.flush()
        except OSError:
            return self._discard(session_id, session, None)
        reply = self._read_line(session, self.timeout_s)
        if reply is None:
            limit = f"Execution exceeded the {self.timeout_s:g} second time limit."
            return self._discard(session_id, session, limit)
        if reply == EOF:
            return self._discard(session_id, session, None)
        return json.loads(reply)

    def _start(self, session_id: str, session: _Session) -> str:
        session.process = self._spawn(session_id, session.workspace)
        session.lines = queue.Queue()
        threading.Thread(
            target=_pump, args=(session.process.stdout, session.lines), daemon=True
        ).start()
        ready = self._read_line(session, self.startup_timeout_s)
        if ready is None or ready == EOF:
            return self._discard(session_id, session, "Worker failed to start.")["stderr"]
        return ""

    def _read_line(self, session: _Session, timeout_s: float) -> str | None:
        """Next line from the worker, EOF if the worker closed its output, None on timeout."""
        try:
            return session.lines.get(timeout=timeout_s)
        except queue.Empty:
            return None

    def _discard(self, session_id: str, session: _Session, reason: str | None) -> dict:
        """Kill the worker and describe why. A reason of None means the worker died by itself."""
        process = session.process
        self._terminate(session_id, process)
        status = process.wait()
        session.process = None
        session.lines = None
        if reason is None:
            reason = _describe_exit(status)
        return {
            "stdout": "",
            "stderr": f"{reason} The interpreter was restarted; earlier variables are gone.",
            "ok": False,
        }

    def reset(self, session_id: str) -> None:
        session = self._session(session_id)
        with session.lock:
            if session.process is not None:
                self._terminate(session_id, session.process)
                session.process.wait()
                session.process = None
                session.lines = None
            shutil.rmtree(session.workspace, ignore_errors=True)
            session.workspace.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        with self._registry_lock:
            sessions = dict(self._sessions)
            self._sessions.clear()
        for session_id, session in sessions.items():
            with session.lock:
                if session.process is not None:
                    self._terminate(session_id, session.process)
                    session.process.wait()
        if self._owns_root:
            shutil.rmtree(self._root, ignore_errors=True)

    def __enter__(self) -> WorkerExecutor:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


def _pump(stream, lines: queue.Queue) -> None:
    for line in stream:
        lines.put(line)
    lines.put(EOF)


def _describe_exit(status: int) -> str:
    if status < 0:
        name = signal.Signals(-status).name
        hint = {"SIGXCPU": " (CPU time limit)", "SIGKILL": " (memory limit or external kill)"}
        return f"Worker was terminated by {name}{hint.get(name, '')}."
    return f"Worker exited with status {status}."
