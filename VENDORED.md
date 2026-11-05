# Vendored code

`src/code_interpreter_agent/` is a copy of the `code_interpreter_agent` package from the
repository https://github.com/darblinko/code-interpreter-agent.

| Item | Value |
|---|---|
| Source commit | f7e30e0493062503de5b15064cb6825e1b852eee (author-rewritten equivalent of the original f795a2c; same content) |
| License | Apache-2.0 (see `LICENSE`; the source tree carries the same license) |
| Omitted | `cli/` and `ui/` (not used by the service) |
| Local changes | `executors/worker.py`, `executors/process.py`, `executors/subprocess_executor.py`: captured output is bounded in the worker (`--max-output-chars`), protocol lines are read with a 2,000,000 character limit and a 4-line queue, and the reader thread is drained when a worker stops. |

Update by copying the package again and recording the new commit hash here.
