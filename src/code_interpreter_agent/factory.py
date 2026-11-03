"""One place that turns plain settings into a ready Agent. Used by the CLI and the UI."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .agent import DEFAULT_SYSTEM_PROMPT, Agent
from .executors import Executor, get_executor
from .providers import Provider, get_provider
from .tools import PythonTool, Tool


def build_agent(
    provider: Provider | str = "anthropic",
    *,
    model: str | None = None,
    executor: Executor | str = "subprocess",
    executor_options: dict[str, Any] | None = None,
    extra_tools: Iterable[Tool] = (),
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    max_iterations: int = 10,
    max_total_tokens: int | None = None,
) -> Agent:
    """Build an Agent with the python tool plus any extra tools.

    `provider` and `executor` accept either a name or a ready object."""
    if isinstance(provider, str):
        provider = get_provider(provider, model)
    if isinstance(executor, str):
        executor = get_executor(executor, **(executor_options or {}))
    return Agent(
        provider,
        executor,
        [PythonTool(executor), *extra_tools],
        system_prompt,
        max_iterations=max_iterations,
        max_total_tokens=max_total_tokens,
    )
