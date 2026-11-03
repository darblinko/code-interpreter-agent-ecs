"""Optional executor that runs each session's worker inside a Docker container."""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

from .process import WorkerExecutor

WORKER_SCRIPT = Path(__file__).with_name("worker.py")
CONTAINER_WORKER = "/opt/cia/worker.py"
DEFAULT_IMAGE = "python:3.12-slim"


class DockerExecutor(WorkerExecutor):
    """Same protocol as SubprocessExecutor. The container has no network by default, a
    read-only root filesystem, dropped capabilities, and memory, CPU and pid limits. Only the
    session workspace is writable (plus a small tmpfs at /tmp).

    The image needs Python 3.11+. Use docker/Dockerfile for an image with numpy and matplotlib.
    """

    def __init__(
        self,
        *,
        image: str = DEFAULT_IMAGE,
        network: str = "none",
        cpus: float = 1.0,
        pids_limit: int = 128,
        docker_binary: str = "docker",
        startup_timeout_s: float = 120.0,
        **kwargs,
    ) -> None:
        super().__init__(startup_timeout_s=startup_timeout_s, **kwargs)
        self.image = image
        self.network = network
        self.cpus = cpus
        self.pids_limit = pids_limit
        self.docker_binary = docker_binary
        self._containers: dict[str, str] = {}

    def build_run_command(self, container_name: str, workspace: Path) -> list[str]:
        memory = f"{self.memory_mb}m"
        return [
            self.docker_binary, "run", "--interactive", "--rm",
            "--name", container_name,
            "--network", self.network,
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m",
            "--memory", memory, "--memory-swap", memory,
            "--cpus", str(self.cpus),
            "--pids-limit", str(self.pids_limit),
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", f"{os.getuid()}:{os.getgid()}",
            "--volume", f"{workspace}:/workspace:rw",
            "--volume", f"{WORKER_SCRIPT}:{CONTAINER_WORKER}:ro",
            "--workdir", "/workspace",
            "--env", "HOME=/workspace",
            "--env", "TMPDIR=/tmp",
            "--env", "MPLCONFIGDIR=/workspace/.cache/matplotlib",
            "--env", "MPLBACKEND=Agg",
            self.image,
            "python", "-I", CONTAINER_WORKER,
            "--workspace", "/workspace", "--no-confine",
        ]  # fmt: skip

    def _spawn(self, session_id: str, workspace: Path) -> subprocess.Popen:
        name = f"cia-{session_id}-{uuid.uuid4().hex[:8]}"
        self._containers[session_id] = name
        return subprocess.Popen(
            self.build_run_command(name, workspace),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )

    def _terminate(self, session_id: str, process: subprocess.Popen) -> None:
        name = self._containers.pop(session_id, None)
        if name:
            subprocess.run(
                [self.docker_binary, "kill", name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        process.kill()
