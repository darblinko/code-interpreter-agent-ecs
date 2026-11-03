"""Anthropic provider (Messages API, streaming)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

from .base import (
    Completion,
    Message,
    StreamItem,
    TextChunk,
    ToolCall,
    ToolSpec,
    Usage,
    require_env,
)

ANTHROPIC_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5")
DEFAULT_ANTHROPIC_MODEL = ANTHROPIC_MODELS[0]


class AnthropicProvider:
    name = "anthropic"

    def __init__(
        self,
        model: str | None = None,
        *,
        max_tokens: int = 4096,
        client: Any = None,
    ) -> None:
        self.model = model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_ANTHROPIC_MODEL
        self.max_tokens = max_tokens
        if client is None:
            api_key = require_env("ANTHROPIC_API_KEY", "Anthropic")
            try:
                import anthropic
            except ImportError as error:
                raise ImportError(
                    "Install the Anthropic SDK: pip install 'code-interpreter-agent[anthropic]'"
                ) from error
            client = anthropic.Anthropic(api_key=api_key)
        self._client = client

    def stream(
        self, *, system: str, messages: list[Message], tools: list[ToolSpec]
    ) -> Iterator[StreamItem]:
        tool_params = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]
        with self._client.messages.stream(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            tools=tool_params,
        ) as stream:
            for text in stream.text_stream:
                yield TextChunk(text)
            final = stream.get_final_message()
        yield _completion_from_message(final)


def _completion_from_message(message: Any) -> Completion:
    text_parts: list[str] = []
    calls: list[ToolCall] = []
    for block in message.content:
        if block.type == "text":
            text_parts.append(block.text)
        elif block.type == "tool_use":
            calls.append(ToolCall(block.id, block.name, dict(block.input)))
    usage = Usage(message.usage.input_tokens, message.usage.output_tokens)
    return Completion("".join(text_parts), tuple(calls), usage, message.stop_reason or "end_turn")
