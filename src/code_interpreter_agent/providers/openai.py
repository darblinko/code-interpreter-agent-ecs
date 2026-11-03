"""OpenAI provider (Chat Completions, streaming). Also works with OpenAI-compatible servers
through OPENAI_BASE_URL, which the OpenAI SDK reads itself."""

from __future__ import annotations

import json
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

DEFAULT_OPENAI_MODEL = "gpt-5"


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str | None = None, *, client: Any = None) -> None:
        self.model = model or os.environ.get("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
        if client is None:
            api_key = require_env("OPENAI_API_KEY", "OpenAI")
            try:
                import openai
            except ImportError as error:
                raise ImportError(
                    "Install the OpenAI SDK: pip install 'code-interpreter-agent[openai]'"
                ) from error
            client = openai.OpenAI(api_key=api_key)
        self._client = client

    def stream(
        self, *, system: str, messages: list[Message], tools: list[ToolSpec]
    ) -> Iterator[StreamItem]:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": to_openai_messages(system, messages),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            request["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in tools
            ]
        text_parts: list[str] = []
        pending: dict[int, dict[str, str]] = {}
        usage = Usage()
        finish_reason = "stop"
        for chunk in self._client.chat.completions.create(**request):
            if getattr(chunk, "usage", None):
                usage = Usage(chunk.usage.prompt_tokens, chunk.usage.completion_tokens)
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if delta.content:
                text_parts.append(delta.content)
                yield TextChunk(delta.content)
            for call in delta.tool_calls or []:
                slot = pending.setdefault(call.index, {"id": "", "name": "", "arguments": ""})
                slot["id"] += call.id or ""
                if call.function:
                    slot["name"] += call.function.name or ""
                    slot["arguments"] += call.function.arguments or ""
            finish_reason = choice.finish_reason or finish_reason
        calls = tuple(
            ToolCall(slot["id"], slot["name"], _parse_arguments(slot["arguments"]))
            for _, slot in sorted(pending.items())
        )
        stop = "tool_use" if calls else ("max_tokens" if finish_reason == "length" else "end_turn")
        yield Completion("".join(text_parts), calls, usage, stop)


def _parse_arguments(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"_unparsed_arguments": raw}
    return parsed if isinstance(parsed, dict) else {"_unparsed_arguments": raw}


def to_openai_messages(system: str, messages: list[Message]) -> list[dict[str, Any]]:
    """Convert neutral content-block messages to the Chat Completions format."""
    converted: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for message in messages:
        content = message["content"]
        if isinstance(content, str):
            converted.append({"role": message["role"], "content": content})
        elif message["role"] == "assistant":
            converted.append(_assistant_message(content))
        else:
            converted.extend(_user_messages(content))
    return converted


def _assistant_message(blocks: list[dict[str, Any]]) -> dict[str, Any]:
    text = "".join(b["text"] for b in blocks if b["type"] == "text")
    result: dict[str, Any] = {"role": "assistant", "content": text or None}
    calls = [
        {
            "id": b["id"],
            "type": "function",
            "function": {"name": b["name"], "arguments": json.dumps(b["input"])},
        }
        for b in blocks
        if b["type"] == "tool_use"
    ]
    if calls:
        result["tool_calls"] = calls
    return result


def _user_messages(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for block in blocks:
        if block["type"] == "tool_result":
            converted.append(
                {"role": "tool", "tool_call_id": block["tool_use_id"], "content": block["content"]}
            )
        elif block["type"] == "text":
            converted.append({"role": "user", "content": block["text"]})
    return converted
