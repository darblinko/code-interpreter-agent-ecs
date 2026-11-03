"""Tool protocol and registry."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..providers.base import ToolSpec


@dataclass(frozen=True)
class ToolResult:
    content: str
    is_error: bool = False
    files: tuple[str, ...] = field(default_factory=tuple)


class Tool(Protocol):
    name: str
    description: str
    input_schema: dict[str, Any]

    def run(self, session_id: str, arguments: dict[str, Any]) -> ToolResult: ...


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool

    def specs(self) -> list[ToolSpec]:
        return [ToolSpec(t.name, t.description, t.input_schema) for t in self._tools.values()]

    def run(self, session_id: str, name: str, arguments: dict[str, Any]) -> ToolResult:
        """Run a tool. Unknown tools and tool exceptions come back as error results so the
        model can react to them."""
        tool = self._tools.get(name)
        if tool is None:
            known = ", ".join(self._tools) or "none"
            return ToolResult(f"Unknown tool {name!r}. Available tools: {known}.", is_error=True)
        try:
            return tool.run(session_id, arguments)
        except Exception as error:
            return ToolResult(f"Tool {name!r} failed: {type(error).__name__}: {error}", True)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
