"""Default executor: one persistent Python subprocess per session on the local machine."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .process import WorkerExecutor

WORKER_SCRIPT = Path(__file__).with_name("worker.py")


def minimal_environment(workspace: Path) -> dict[str, str]:
    """Environment for the worker. Nothing is inherited, so API keys and tokens do not leak."""
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(workspace),
        "TMPDIR": str(workspace / ".tmp"),
        "MPLCONFIGDIR": str(workspace / ".cache" / "matplotlib"),
        "MPLBACKEND": "Agg",
        "LANG": "C.UTF-8",
        # Numeric libraries reserve address space per thread; one thread keeps RLIMIT_AS usable.
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
    }


class SubprocessExecutor(WorkerExecutor):
    """Limits per worker: address space (`memory_mb`), CPU seconds per call (`cpu_s`),
    wall clock per call (`timeout_s`), file size. File access is confined to the session
    workspace by an audit hook. See the README security notes for what this does not stop."""

    def _spawn(self, session_id: str, workspace: Path) -> subprocess.Popen:
        (workspace / ".tmp").mkdir(exist_ok=True)
        command = [
            sys.executable,
            "-I",
            str(WORKER_SCRIPT),
            "--workspace",
            str(workspace),
            "--memory-mb",
            str(self.memory_mb),
        ]
        return subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=minimal_environment(workspace),
            cwd=workspace,
            text=True,
            encoding="utf-8",
            start_new_session=True,
        )
