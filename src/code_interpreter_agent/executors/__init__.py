from __future__ import annotations

from typing import Any

from .base import ExecResult, Executor
from .docker_executor import DockerExecutor
from .subprocess_executor import SubprocessExecutor

EXECUTOR_NAMES = ("subprocess", "docker")


def get_executor(name: str = "subprocess", **options: Any) -> Executor:
    if name == "subprocess":
        return SubprocessExecutor(**options)
    if name == "docker":
        return DockerExecutor(**options)
    raise ValueError(f"Unknown executor {name!r}; choose from {', '.join(EXECUTOR_NAMES)}")


__all__ = [
    "EXECUTOR_NAMES",
    "DockerExecutor",
    "ExecResult",
    "Executor",
    "SubprocessExecutor",
    "get_executor",
]
