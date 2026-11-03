"""Persistent Python worker. Standalone on purpose: it uses only the standard library so the
docker executor can mount this single file into a stock Python image.

Protocol: one JSON object per line on stdin and stdout.
    worker -> parent   {"ready": true}
    parent -> worker   {"code": str, "cpu_s": number}
    worker -> parent   {"stdout": str, "stderr": str, "ok": bool}
After startup, file descriptors 0, 1 and 2 point at /dev/null, so user code cannot corrupt the
protocol by printing; print output is captured in-process.
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import linecache
import math
import os
import sys
import traceback

BLOCKED_EVENTS = frozenset(
    {
        "subprocess.Popen",
        "os.system",
        "os.exec",
        "os.posix_spawn",
        "os.spawn",
        "os.fork",
        "os.forkpty",
        "ctypes.dlopen",
    }
)
PATH_EVENTS = {
    "os.listdir": (0,),
    "os.scandir": (0,),
    "os.chdir": (0,),
    "os.mkdir": (0,),
    "os.rmdir": (0,),
    "os.remove": (0,),
    "os.rename": (0, 1),
    "os.link": (0, 1),
    "os.symlink": (0, 1),
    "os.chmod": (0,),
    "os.chown": (0,),
    "os.truncate": (0,),
    "shutil.copyfile": (0, 1),
    "shutil.copymode": (0, 1),
    "shutil.copystat": (0, 1),
    "shutil.copytree": (0, 1),
    "shutil.move": (0, 1),
    "shutil.rmtree": (0,),
    "glob.glob": (0,),
}
READ_ONLY_EXTRA_ROOTS = (
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    "/etc/fonts",
    "/usr/share/zoneinfo",
    "/proc/self",
    "/proc/cpuinfo",
    "/proc/meminfo",
    "/sys/devices/system/cpu",
    "/sys/fs/cgroup",
    "/dev/null",
    "/dev/zero",
    "/dev/urandom",
    "/dev/random",
)
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC


def _real(path: object) -> str | None:
    if isinstance(path, int) or path is None:
        return None
    try:
        return os.path.realpath(os.fsdecode(path))
    except (TypeError, ValueError):
        return None


def _inside(path: str, roots: list[str]) -> bool:
    return any(path == root or path.startswith(root.rstrip("/") + "/") for root in roots)


def install_confinement(workspace: str) -> None:
    """Install an audit hook that refuses file access outside the workspace and refuses
    process creation. This is a guardrail, not a security boundary: native code bypasses it."""
    write_roots = [os.path.realpath(workspace)]
    read_roots = write_roots + [
        os.path.realpath(p)
        for p in {
            sys.prefix,
            sys.base_prefix,
            sys.exec_prefix,
            sys.base_exec_prefix,
            *[p for p in sys.path if p and os.path.isdir(p)],
            *READ_ONLY_EXTRA_ROOTS,
        }
    ]
    busy = False

    def hook(event: str, args: tuple) -> None:
        nonlocal busy
        if busy:
            return
        if event in BLOCKED_EVENTS:
            raise PermissionError(f"{event} is disabled in this sandbox")
        if event == "open":
            path = _real(args[0])
            if path is None:
                return
            writing = isinstance(args[2], int) and args[2] & WRITE_FLAGS
            if not _inside(path, write_roots if writing else read_roots):
                verb = "write" if writing else "read"
                raise PermissionError(f"Cannot {verb} outside the workspace: {args[0]!r}")
        elif event in PATH_EVENTS:
            busy = True
            try:
                for index in PATH_EVENTS[event]:
                    path = _real(args[index]) if index < len(args) else None
                    if path is not None and not _inside(path, read_roots):
                        raise PermissionError(f"Path is outside the workspace: {args[index]!r}")
            finally:
                busy = False

    sys.addaudithook(hook)


def apply_limits(memory_mb: int) -> None:
    import resource

    if memory_mb > 0:
        limit = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    file_limit = 256 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_FSIZE, (file_limit, file_limit))


def set_cpu_budget(seconds: float) -> None:
    """Allow `seconds` of additional CPU time. The soft limit raises SIGXCPU, which ends the
    worker; the parent reports that as a CPU limit violation."""
    import resource

    used = resource.getrusage(resource.RUSAGE_SELF)
    soft = int(used.ru_utime + used.ru_stime) + max(1, math.ceil(seconds))
    resource.setrlimit(resource.RLIMIT_CPU, (soft, resource.RLIM_INFINITY))


def _run_source(code: str, filename: str, namespace: dict) -> None:
    """Execute code; if the last statement is an expression, print its repr like a notebook."""
    tree = ast.parse(code, filename, "exec")
    last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
    exec(compile(tree, filename, "exec"), namespace)
    if last is not None:
        value = eval(compile(ast.Expression(last.value), filename, "eval"), namespace)
        if value is not None:
            print(repr(value))


def execute(code: str, namespace: dict, cell_number: int) -> dict:
    filename = f"<cell {cell_number}>"
    linecache.cache[filename] = (len(code), None, code.splitlines(True), filename)
    out, err = io.StringIO(), io.StringIO()
    ok = True
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            _run_source(code, filename, namespace)
        except BaseException as error:
            ok = False
            frame = error.__traceback__
            while frame is not None and not frame.tb_frame.f_code.co_filename.startswith("<cell"):
                frame = frame.tb_next
            traceback.print_exception(type(error), error, frame, file=err)
    return {"stdout": out.getvalue(), "stderr": err.getvalue(), "ok": ok}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--memory-mb", type=int, default=0)
    parser.add_argument("--no-confine", action="store_true")
    args = parser.parse_args(argv)

    protocol_in = os.fdopen(os.dup(0), "r", encoding="utf-8")
    protocol_out = os.fdopen(os.dup(1), "w", encoding="utf-8")
    devnull = os.open(os.devnull, os.O_RDWR)
    for descriptor in (0, 1, 2):
        os.dup2(devnull, descriptor)

    sys.dont_write_bytecode = True
    os.chdir(args.workspace)
    apply_limits(args.memory_mb)
    if not args.no_confine:
        install_confinement(args.workspace)

    namespace: dict = {"__name__": "__main__"}
    protocol_out.write(json.dumps({"ready": True}) + "\n")
    protocol_out.flush()
    for cell_number, line in enumerate(protocol_in, start=1):
        request = json.loads(line)
        set_cpu_budget(float(request.get("cpu_s", 30)))
        response = execute(request["code"], namespace, cell_number)
        protocol_out.write(json.dumps(response) + "\n")
        protocol_out.flush()


if __name__ == "__main__":
    main()
