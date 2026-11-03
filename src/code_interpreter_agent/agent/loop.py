"""The agent loop: call the model, run requested tools, repeat until the model stops."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from ..executors.base import Executor
from ..providers.base import Completion, Message, Provider, TextChunk, Usage
from ..tools.base import Tool, ToolRegistry
from ..tools.python_tool import PythonTool
from .events import Event, FinalEvent, TextDelta, ToolCallEvent, ToolResultEvent
from .prompts import DEFAULT_SYSTEM_PROMPT


class Agent:
    """Conversation state is kept per session id, never globally. Run one message at a time
    per session; different sessions may run concurrently from different threads."""

    def __init__(
        self,
        provider: Provider,
        executor: Executor | None = None,
        tools: Iterable[Tool] | ToolRegistry | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        *,
        max_iterations: int = 10,
        max_total_tokens: int | None = None,
    ) -> None:
        self.provider = provider
        self.executor = executor
        if tools is None:
            tools = [PythonTool(executor)] if executor is not None else []
        self.tools = tools if isinstance(tools, ToolRegistry) else ToolRegistry(tools)
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.max_total_tokens = max_total_tokens
        self._histories: dict[str, list[Message]] = {}

    def history(self, session_id: str) -> list[Message]:
        return list(self._histories.get(session_id, []))

    def reset(self, session_id: str) -> None:
        """Forget the conversation and, if the executor supports it, its state and files."""
        self._histories.pop(session_id, None)
        if self.executor is not None:
            self.executor.reset(session_id)

    def run(self, session_id: str, message: str) -> Iterator[Event]:
        history = self._histories.setdefault(session_id, [])
        history.append({"role": "user", "content": message})
        usage = Usage()
        last_text = ""
        for iteration in range(1, self.max_iterations + 1):
            if self.max_total_tokens is not None and usage.total >= self.max_total_tokens:
                yield FinalEvent(last_text, "token_budget", iteration - 1, usage)
                return
            completion = yield from self._model_turn(history)
            usage += completion.usage
            last_text = completion.text or last_text
            history.append(_assistant_message(completion))
            if not completion.tool_calls:
                stop = "max_tokens" if completion.stop_reason == "max_tokens" else "end_turn"
                yield FinalEvent(completion.text, stop, iteration, usage)
                return
            results = []
            for call in completion.tool_calls:
                yield ToolCallEvent(call)
                result = self.tools.run(session_id, call.name, call.arguments)
                yield ToolResultEvent(call, result)
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call.id,
                        "content": result.content,
                        "is_error": result.is_error,
                    }
                )
            history.append({"role": "user", "content": results})
        yield FinalEvent(last_text, "max_iterations", self.max_iterations, usage)

    def _model_turn(self, history: list[Message]) -> Iterator[Event]:
        """Stream one model call, forwarding text deltas. Returns the Completion."""
        stream = self.provider.stream(
            system=self.system_prompt, messages=history, tools=self.tools.specs()
        )
        for item in stream:
            if isinstance(item, TextChunk):
                yield TextDelta(item.text)
            elif isinstance(item, Completion):
                return item
        raise RuntimeError("Provider stream ended without a Completion")


def _assistant_message(completion: Completion) -> Message:
    blocks: list[dict] = []
    if completion.text:
        blocks.append({"type": "text", "text": completion.text})
    for call in completion.tool_calls:
        blocks.append(
            {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
        )
    return {"role": "assistant", "content": blocks or [{"type": "text", "text": "(no response)"}]}
