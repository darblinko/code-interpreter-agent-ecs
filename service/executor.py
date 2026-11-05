"""Executor that starts code workers under a separate unprivileged UID.

The service process holds the model API keys in its environment. Workers run as another
UID with an empty environment, so the kernel refuses their reads of the service's
/proc/<pid>/environ. The service holds CAP_SETUID, CAP_SETGID and CAP_KILL as ambient
capabilities (granted by the entrypoint) to start workers under another UID and to kill them
on timeout. A change between two nonzero UIDs does not clear capabilities, so each worker is
started through setpriv, which switches the UID and clears the inheritable and ambient sets
before it executes Python. The worker therefore starts with empty capability sets.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from code_interpreter_agent import SubprocessExecutor
from code_interpreter_agent.executors.subprocess_executor import (
    WORKER_SCRIPT,
    minimal_environment,
)


class DroppedPrivilegeExecutor(SubprocessExecutor):
    """SubprocessExecutor whose workers run as `uid`:`gid` with no supplementary groups.

    Workspaces are group writable by `gid` and the service is a member of that group, so the
    service can still list and delete what a worker wrote."""

    def __init__(self, *, uid: int, gid: int, **options) -> None:
        super().__init__(**options)
        self.uid = uid
        self.gid = gid

    def _spawn(self, session_id: str, workspace: Path) -> subprocess.Popen:
        tmp_dir = workspace / ".tmp"
        tmp_dir.mkdir(exist_ok=True)
        for directory in (workspace, tmp_dir):
            os.chown(directory, -1, self.gid)
            directory.chmod(0o2770)
        command = [
            "setpriv",
            f"--reuid={self.uid}",
            f"--regid={self.gid}",
            "--clear-groups",
            "--inh-caps=-all",
            "--ambient-caps=-all",
            "--",
            sys.executable,
            "-I",
            str(WORKER_SCRIPT),
            "--workspace",
            str(workspace),
            "--memory-mb",
            str(self.memory_mb),
            "--max-output-chars",
            str(self.max_output_chars),
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
            umask=0o002,
        )
