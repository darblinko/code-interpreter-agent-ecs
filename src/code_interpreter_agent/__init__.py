"""code_interpreter_agent: a small agent loop around a sandboxed Python interpreter.

Public API (stable):
    Agent, build_agent
    events: TextDelta, ToolCallEvent, ToolResultEvent, FinalEvent
    providers: Provider, get_provider, AnthropicProvider, OpenAIProvider, FakeProvider
    executors: Executor, ExecResult, SubprocessExecutor, DockerExecutor, get_executor
    tools: Tool, ToolResult, ToolRegistry, PythonTool
"""

from .agent import (
    DEFAULT_SYSTEM_PROMPT,
    Agent,
    Event,
    FinalEvent,
    TextDelta,
    ToolCallEvent,
    ToolResultEvent,
)
from .executors import (
    DockerExecutor,
    ExecResult,
    Executor,
    SubprocessExecutor,
    get_executor,
)
from .factory import build_agent
from .providers import (
    AnthropicProvider,
    FakeProvider,
    MissingCredentialsError,
    OpenAIProvider,
    Provider,
    ToolCall,
    get_provider,
)
from .tools import PythonTool, Tool, ToolRegistry, ToolResult

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_SYSTEM_PROMPT",
    "Agent",
    "AnthropicProvider",
    "DockerExecutor",
    "Event",
    "ExecResult",
    "Executor",
    "FakeProvider",
    "FinalEvent",
    "MissingCredentialsError",
    "OpenAIProvider",
    "Provider",
    "PythonTool",
    "SubprocessExecutor",
    "TextDelta",
    "Tool",
    "ToolCall",
    "ToolCallEvent",
    "ToolRegistry",
    "ToolResult",
    "ToolResultEvent",
    "__version__",
    "build_agent",
    "get_executor",
    "get_provider",
]
