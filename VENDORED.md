# Vendored code

`src/code_interpreter_agent/` is a copy of the `code_interpreter_agent` package from the
sibling repository `code-interpreter-agent`.

| Item | Value |
|---|---|
| Source commit | f795a2c8cdb67c6047119d94a3ef30f874194963 |
| License | Apache-2.0 (see `LICENSE`; the source tree carries the same license) |
| Omitted | `cli/` and `ui/` (not used by the service) |
| Local changes | `executors/worker.py`, `executors/process.py`, `executors/subprocess_executor.py`: captured output is bounded in the worker (`--max-output-chars`), protocol lines are read with a 2,000,000 character limit and a 4-line queue, and the reader thread is drained when a worker stops. |

Update by copying the package again and recording the new commit hash here.
