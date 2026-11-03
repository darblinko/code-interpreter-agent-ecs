"""Scripted provider for offline tests, examples and demos."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from .base import Completion, Message, StreamItem, TextChunk, ToolCall, ToolSpec, Usage


@dataclass(frozen=True)
class Turn:
    """One scripted model turn: optional text and optional tool calls."""

    text: str = ""
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)


def text_turn(text: str) -> Turn:
    return Turn(text=text)


def code_turn(code: str, text: str = "", *, call_id: str | None = None) -> Turn:
    """A turn that calls the python tool with the given code."""
    identifier = call_id or f"fake_{abs(hash(code)) % 10**8}"
    return Turn(text=text, tool_calls=(ToolCall(identifier, "python", {"code": code}),))


TurnSource = Turn | Callable[[list[Message]], Turn]


class FakeProvider:
    """Replays a list of turns in order. A turn may also be a function of the messages seen so
    far. Token usage is a rough word count so budget logic can be tested."""

    name = "fake"

    def __init__(self, script: list[TurnSource], model: str = "fake-model") -> None:
        self.model = model
        self._script = list(script)
        self._position = 0
        self.requests: list[list[Message]] = []

    def stream(
        self, *, system: str, messages: list[Message], tools: list[ToolSpec]
    ) -> Iterator[StreamItem]:
        if self._position >= len(self._script):
            raise RuntimeError("FakeProvider script is exhausted")
        self.requests.append([dict(m) for m in messages])
        source = self._script[self._position]
        self._position += 1
        turn = source(messages) if callable(source) else source
        for piece in re.findall(r"\S+\s*", turn.text):
            yield TextChunk(piece)
        usage = Usage(
            input_tokens=len(json.dumps(messages).split()),
            output_tokens=len(turn.text.split()) + 10 * len(turn.tool_calls),
        )
        stop = "tool_use" if turn.tool_calls else "end_turn"
        yield Completion(turn.text, turn.tool_calls, usage, stop)
