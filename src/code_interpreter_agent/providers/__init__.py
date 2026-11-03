from __future__ import annotations

from .anthropic import ANTHROPIC_MODELS, AnthropicProvider
from .base import (
    Completion,
    Message,
    MissingCredentialsError,
    Provider,
    StreamItem,
    TextChunk,
    ToolCall,
    ToolSpec,
    Usage,
)
from .fake import FakeProvider, Turn, code_turn, text_turn
from .openai import OpenAIProvider

PROVIDER_NAMES = ("anthropic", "openai")


def get_provider(name: str, model: str | None = None) -> Provider:
    """Build a real provider by name. Raises MissingCredentialsError without a key."""
    if name == "anthropic":
        return AnthropicProvider(model)
    if name == "openai":
        return OpenAIProvider(model)
    raise ValueError(f"Unknown provider {name!r}; choose from {', '.join(PROVIDER_NAMES)}")


__all__ = [
    "ANTHROPIC_MODELS",
    "PROVIDER_NAMES",
    "AnthropicProvider",
    "Completion",
    "FakeProvider",
    "Message",
    "MissingCredentialsError",
    "OpenAIProvider",
    "Provider",
    "StreamItem",
    "TextChunk",
    "ToolCall",
    "ToolSpec",
    "Turn",
    "Usage",
    "code_turn",
    "get_provider",
    "text_turn",
]
