"""Typed events produced by Agent.run."""

from __future__ import annotations

from dataclasses import dataclass

from ..providers.base import ToolCall, Usage
from ..tools.base import ToolResult


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCallEvent:
    call: ToolCall


@dataclass(frozen=True)
class ToolResultEvent:
    call: ToolCall
    result: ToolResult


@dataclass(frozen=True)
class FinalEvent:
    """Always the last event. `stop_reason` is one of end_turn, max_iterations, token_budget,
    or max_tokens (the model hit its output limit)."""

    text: str
    stop_reason: str
    iterations: int
    usage: Usage


Event = TextDelta | ToolCallEvent | ToolResultEvent | FinalEvent
