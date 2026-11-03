"""The python tool: runs code through an Executor."""

from __future__ import annotations

from typing import Any

from ..executors.base import ExecResult, Executor
from .base import ToolResult

DESCRIPTION = (
    "Run Python code in a persistent interpreter. Variables, imports and functions survive "
    "between calls in the same session. The working directory is the session workspace: read "
    "input files from it and save outputs (charts, CSV, text) into it. Use print() to see "
    "values; the value of a final bare expression is also printed. Errors are returned as "
    "tracebacks so you can fix the code and retry. Subprocesses, access to paths outside the "
    "workspace, and calls longer than the time limit are refused."
)


class PythonTool:
    name = "python"
    description = DESCRIPTION
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"code": {"type": "string", "description": "Python source to run."}},
        "required": ["code"],
    }

    def __init__(self, executor: Executor) -> None:
        self._executor = executor

    def run(self, session_id: str, arguments: dict[str, Any]) -> ToolResult:
        code = arguments.get("code")
        if not isinstance(code, str) or not code.strip():
            return ToolResult("Argument 'code' must be a non-empty string.", is_error=True)
        result = self._executor.run(session_id, code)
        return ToolResult(format_result(result), is_error=not result.ok, files=result.files)


def format_result(result: ExecResult) -> str:
    sections = []
    if result.stdout:
        sections.append(f"stdout:\n{result.stdout.rstrip()}")
    if result.stderr:
        sections.append(f"stderr:\n{result.stderr.rstrip()}")
    if result.files:
        sections.append("files created or modified: " + ", ".join(result.files))
    return "\n\n".join(sections) or "(no output)"
