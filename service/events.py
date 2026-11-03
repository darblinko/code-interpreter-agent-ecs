"""Event serialization for the HTTP responses."""

from __future__ import annotations

from dataclasses import asdict

from code_interpreter_agent import (
    Event,
    FinalEvent,
    TextDelta,
    ToolCallEvent,
    ToolResultEvent,
)


def event_to_dict(event: Event) -> dict:
    if isinstance(event, TextDelta):
        return {"type": "text_delta", "text": event.text}
    if isinstance(event, ToolCallEvent):
        return {
            "type": "tool_call",
            "id": event.call.id,
            "name": event.call.name,
            "arguments": event.call.arguments,
        }
    if isinstance(event, ToolResultEvent):
        return {
            "type": "tool_result",
            "id": event.call.id,
            "content": event.result.content,
            "is_error": event.result.is_error,
            "files": list(event.result.files),
        }
    if isinstance(event, FinalEvent):
        return {
            "type": "final",
            "text": event.text,
            "stop_reason": event.stop_reason,
            "iterations": event.iterations,
            "usage": asdict(event.usage),
        }
    raise TypeError(f"Unknown event {event!r}")
