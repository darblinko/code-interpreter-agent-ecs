"""Deterministic provider for container smoke tests and the isolation test. It needs no key.

A message that starts with `run:` is executed as Python code; the tool output is then
returned as the final answer. Any other message is echoed. Enable with CIA_PROVIDER=fake.
Never enable it in a deployment that serves real users.
"""

from __future__ import annotations

from collections.abc import Iterator

from code_interpreter_agent.providers import (
    Completion,
    Message,
    TextChunk,
    ToolCall,
    ToolSpec,
    Usage,
)

PREFIX = "run:"


class EchoProvider:
    name = "fake"

    def __init__(self, model: str = "echo") -> None:
        self.model = model

    def stream(
        self, *, system: str, messages: list[Message], tools: list[ToolSpec]
    ) -> Iterator[TextChunk | Completion]:
        last = messages[-1]
        usage = Usage(input_tokens=len(messages), output_tokens=1)
        if isinstance(last["content"], list):
            text = str(last["content"][0]["content"])
            yield TextChunk(text)
            yield Completion(text, (), usage, "end_turn")
        elif last["content"].startswith(PREFIX):
            code = last["content"][len(PREFIX) :].strip()
            call = ToolCall("call_1", "python", {"code": code})
            yield Completion("", (call,), usage, "tool_use")
        else:
            yield TextChunk(last["content"])
            yield Completion(last["content"], (), usage, "end_turn")
