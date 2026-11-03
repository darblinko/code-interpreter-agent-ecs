"""Provider protocol and the neutral types shared by all providers.

Conversation messages use the Anthropic content-block layout:

    {"role": "user" | "assistant", "content": str | list[block]}
    block = {"type": "text", "text": str}
          | {"type": "tool_use", "id": str, "name": str, "input": dict}
          | {"type": "tool_result", "tool_use_id": str, "content": str, "is_error": bool}

Providers that use another wire format convert inside the provider.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

Message = dict[str, Any]


class MissingCredentialsError(RuntimeError):
    """Raised when a provider needs an environment variable that is not set."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
        )


@dataclass(frozen=True)
class TextChunk:
    """A piece of assistant text, emitted while the model is still generating."""

    text: str


@dataclass(frozen=True)
class Completion:
    """The finished model turn. Always the last item a provider stream yields."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = "end_turn"


StreamItem = TextChunk | Completion


class Provider(Protocol):
    name: str
    model: str

    def stream(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
    ) -> Iterator[StreamItem]:
        """Yield zero or more TextChunk items, then exactly one Completion."""
        ...


def require_env(variable: str, provider_name: str) -> str:
    import os

    value = os.environ.get(variable, "").strip()
    if not value:
        raise MissingCredentialsError(
            f"{provider_name} provider needs the {variable} environment variable. "
            f"Export it in your shell; the key is never read from files or arguments."
        )
    return value
