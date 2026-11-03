"""Service settings read from environment variables once at startup."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw in (None, "") else int(raw)


@dataclass(frozen=True)
class Settings:
    provider: str
    model: str | None
    api_token: str | None
    workspace_root: str
    sandbox_uid: int | None
    sandbox_gid: int | None
    max_body_bytes: int
    max_message_chars: int
    rate_limit_per_minute: int
    max_concurrent_runs: int
    max_iterations: int
    max_total_tokens: int
    exec_timeout_s: float
    exec_memory_mb: int

    @classmethod
    def from_env(cls) -> Settings:
        uid = os.environ.get("SANDBOX_UID")
        gid = os.environ.get("SANDBOX_GID")
        return cls(
            provider=os.environ.get("CIA_PROVIDER", "anthropic"),
            model=os.environ.get("CIA_MODEL") or None,
            api_token=os.environ.get("SERVICE_API_TOKEN") or None,
            workspace_root=os.environ.get("WORKSPACE_ROOT", "/workspace"),
            sandbox_uid=int(uid) if uid else None,
            sandbox_gid=int(gid) if gid else None,
            max_body_bytes=_int("MAX_BODY_BYTES", 65_536),
            max_message_chars=_int("MAX_MESSAGE_CHARS", 20_000),
            rate_limit_per_minute=_int("RATE_LIMIT_PER_MINUTE", 30),
            max_concurrent_runs=_int("MAX_CONCURRENT_RUNS", 4),
            max_iterations=_int("MAX_ITERATIONS", 8),
            max_total_tokens=_int("MAX_TOTAL_TOKENS", 100_000),
            exec_timeout_s=float(os.environ.get("EXEC_TIMEOUT_S", "30")),
            exec_memory_mb=_int("EXEC_MEMORY_MB", 1024),
        )
