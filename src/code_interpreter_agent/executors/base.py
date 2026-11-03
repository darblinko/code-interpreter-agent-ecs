"""Executor protocol and result type."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class ExecResult:
    stdout: str
    stderr: str
    ok: bool
    files: tuple[str, ...] = field(default_factory=tuple)
    duration_s: float = 0.0


class Executor(Protocol):
    def run(self, session_id: str, code: str) -> ExecResult:
        """Run code in the session's persistent interpreter. Variables survive between calls."""
        ...

    def reset(self, session_id: str) -> None:
        """Discard the session's interpreter state and workspace files."""
        ...

    def close(self) -> None:
        """Stop every session and release resources."""
        ...

    def workspace_path(self, session_id: str) -> Path:
        """Directory that holds the session's files. Created on first use."""
        ...


def validate_session_id(session_id: str) -> str:
    if not SESSION_ID_PATTERN.match(session_id):
        raise ValueError(
            f"Invalid session id {session_id!r}: use 1 to 64 characters from A-Z a-z 0-9 _ . -, "
            "starting with a letter or digit"
        )
    return session_id
