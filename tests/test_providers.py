"""Offline contract tests for the vendored provider adapters, with SDK stream objects faked.

Taken from tests/test_providers.py in code-interpreter-agent. The upstream test for replaying
thinking blocks is left out, because the vendored copy predates that change (see VENDORED.md).
"""

import json
from types import SimpleNamespace as NS

import pytest

from code_interpreter_agent import AnthropicProvider, MissingCredentialsError, OpenAIProvider
from code_interpreter_agent.providers import (
    ANTHROPIC_MODELS,
    Completion,
    TextChunk,
    ToolSpec,
    get_provider,
)
from code_interpreter_agent.providers.openai import to_openai_messages

TOOLS = [ToolSpec("python", "run code", {"type": "object"})]


def test_current_anthropic_model_ids():
    assert ANTHROPIC_MODELS == ("claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5")


@pytest.mark.parametrize(
    "name,variable", [("anthropic", "ANTHROPIC_API_KEY"), ("openai", "OPENAI_API_KEY")]
)
def test_missing_key_fails_clearly(monkeypatch, name, variable):
    monkeypatch.delenv(variable, raising=False)
    with pytest.raises(MissingCredentialsError, match=variable):
        get_provider(name)


def test_unknown_provider():
    with pytest.raises(ValueError, match="Unknown provider"):
        get_provider("nope")


def test_model_comes_from_env(monkeypatch):
    monkeypatch.setenv("OPENAI_MODEL", "my-model")
    assert OpenAIProvider(client=object()).model == "my-model"
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-haiku-5-5")
    assert AnthropicProvider(client=object()).model == "claude-haiku-5-5"
    assert AnthropicProvider("claude-opus-5-5", client=object()).model == "claude-opus-5-5"


class FakeAnthropicStream:
    text_stream = ["Hel", "lo"]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get_final_message(self):
        return NS(
            content=[
                NS(type="text", text="Hello"),
                NS(type="tool_use", id="tu1", name="python", input={"code": "1"}),
            ],
            usage=NS(input_tokens=11, output_tokens=7),
            stop_reason="tool_use",
        )


def test_anthropic_stream_maps_response():
    captured = {}

    def stream(**kwargs):
        captured.update(kwargs)
        return FakeAnthropicStream()

    provider = AnthropicProvider(client=NS(messages=NS(stream=stream)))
    items = list(
        provider.stream(system="sys", messages=[{"role": "user", "content": "x"}], tools=TOOLS)
    )
    assert [i.text for i in items if isinstance(i, TextChunk)] == ["Hel", "lo"]
    done = items[-1]
    assert isinstance(done, Completion) and done.tool_calls[0].arguments == {"code": "1"}
    assert done.usage.total == 18
    assert captured["model"] == "claude-sonnet-5-5" and captured["system"] == "sys"
    assert captured["tools"][0]["input_schema"] == {"type": "object"}


def make_chunk(content=None, tool_calls=None, finish=None, usage=None, choices=True):
    delta = NS(content=content, tool_calls=tool_calls)
    return NS(choices=[NS(delta=delta, finish_reason=finish)] if choices else [], usage=usage)


def test_openai_stream_assembles_tool_call_fragments():
    fragments = [
        make_chunk(content="Run"),
        make_chunk(
            tool_calls=[NS(index=0, id="call_1", function=NS(name="python", arguments='{"co'))]
        ),
        make_chunk(
            tool_calls=[NS(index=0, id=None, function=NS(name=None, arguments='de": "2"}'))]
        ),
        make_chunk(finish="tool_calls"),
        make_chunk(choices=False, usage=NS(prompt_tokens=5, completion_tokens=3)),
    ]
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return iter(fragments)

    provider = OpenAIProvider("m", client=NS(chat=NS(completions=NS(create=create))))
    items = list(
        provider.stream(system="sys", messages=[{"role": "user", "content": "x"}], tools=TOOLS)
    )
    done = items[-1]
    assert done.text == "Run" and done.stop_reason == "tool_use"
    assert done.tool_calls[0].id == "call_1" and done.tool_calls[0].arguments == {"code": "2"}
    assert done.usage.total == 8
    assert captured["tools"][0]["function"]["name"] == "python"
    assert captured["messages"][0] == {"role": "system", "content": "sys"}


def test_openai_message_conversion():
    messages = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "ok"},
                {"type": "tool_use", "id": "c1", "name": "python", "input": {"code": "1"}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "c1", "content": "out", "is_error": False}
            ],
        },
    ]
    converted = to_openai_messages("sys", messages)
    assert converted[0]["role"] == "system"
    assert converted[2]["tool_calls"][0]["function"]["arguments"] == json.dumps({"code": "1"})
    assert converted[3] == {"role": "tool", "tool_call_id": "c1", "content": "out"}


def test_openai_conversion_skips_thinking_blocks():
    messages = [
        {"role": "user", "content": "x"},
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "s"},
                {"type": "text", "text": "hi"},
            ],
        },
    ]
    converted = to_openai_messages("sys", messages)
    assert converted[-1] == {"role": "assistant", "content": "hi"}
