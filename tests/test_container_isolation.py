"""Builds the image and proves model code cannot read the API key.

Opt in with CIA_CONTAINER_TESTS=1. Needs a Docker daemon and network access to PyPI at build
time. The container runs with the same capability and filesystem settings as the ECS task
definition. CIA_PROVIDER=fake lets /v1/run execute code without a model key.
"""

import json
import os
import shutil
import subprocess
import urllib.request
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
KEY = "sk-ant-TEST-SENTINEL-0123456789"
pytestmark = [
    pytest.mark.container,
    pytest.mark.skipif(
        os.environ.get("CIA_CONTAINER_TESTS") != "1" or shutil.which("docker") is None,
        reason="set CIA_CONTAINER_TESTS=1 and install Docker",
    ),
]


def docker(*args, check=True):
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


@pytest.fixture(scope="module")
def container():
    image, name = f"cia-ecs-test:{uuid.uuid4().hex[:8]}", f"cia-ecs-{uuid.uuid4().hex[:8]}"
    docker("build", "-q", "-t", image, str(REPO))
    docker(
        "run", "-d", "--name", name, "--init", "--read-only",
        "--tmpfs", "/tmp:mode=1777", "--tmpfs", "/workspace:mode=2770,uid=10001,gid=10002",
        "--cap-drop", "ALL", "--cap-add", "SETUID", "--cap-add", "SETGID",
        "-e", "CIA_PROVIDER=fake", "-e", f"ANTHROPIC_API_KEY={KEY}",
        "-p", "127.0.0.1::8080", image,
    )  # fmt: skip
    port = docker("port", name, "8080/tcp").stdout.split(":")[-1].strip()
    base = f"http://127.0.0.1:{port}"
    for _ in range(40):
        try:
            urllib.request.urlopen(f"{base}/health", timeout=2)
            break
        except OSError:
            subprocess.run(["sleep", "0.5"])
    else:
        pytest.fail(docker("logs", name).stdout)
    yield name, base
    docker("rm", "-f", name, check=False)
    docker("rmi", "-f", image, check=False)


def run_code(base, code, session="iso"):
    request = urllib.request.Request(
        f"{base}/v1/run",
        data=json.dumps({"session_id": session, "message": f"run: {code}"}).encode(),
        headers={"Content-Type": "application/json"},
    )
    events = json.load(urllib.request.urlopen(request, timeout=60))["events"]
    return next(e["content"] for e in events if e["type"] == "tool_result")


def exec_as(name, user, code):
    return docker("exec", "--user", user, name, "python", "-c", code, check=False)


FIND_SERVICE = (
    "import os\n"
    "pid = next(p for p in os.listdir('/proc') if p.isdigit() "
    "and open(f'/proc/{p}/cmdline','rb').read() == b'python\\0-m\\0service\\0')\n"
)


def test_health_and_run(container):
    _, base = container
    assert urllib.request.urlopen(f"{base}/health", timeout=5).status == 200
    assert "42" in run_code(base, "print(6*7)")


def test_code_runs_as_sandbox_uid_with_clean_env(container):
    _, base = container
    out = run_code(
        base, "import os; print(os.getuid(), os.getgid(), os.getgroups()); print(dict(os.environ))"
    )
    assert out.startswith("stdout:\n10002 10002 []")
    assert KEY not in out and "ANTHROPIC" not in out


def test_model_code_cannot_read_key_through_the_api(container):
    _, base = container
    attempts = (
        "print(open('/proc/1/environ').read())",
        "import os; print(open(f'/proc/{os.getppid()}/environ').read())",
        "import os; print(os.environ.get('ANTHROPIC_API_KEY'))",
    )
    for code in attempts:
        assert KEY not in run_code(base, code), code


def test_kernel_denies_sandbox_uid_without_the_audit_hook(container):
    """Plain python as uid 10002, so the worker audit hook is not involved."""
    name, _ = container
    probe = FIND_SERVICE + "print(pid)\nfor target in ('1', pid):\n"
    probe += "    try: open(f'/proc/{target}/environ').read(); print('READ', target)\n"
    probe += "    except PermissionError: print('DENIED', target)\n"
    result = exec_as(name, "10002:10002", probe)
    assert result.stdout.count("DENIED") == 2, result
    assert "READ" not in result.stdout


def test_positive_control_same_uid_can_read_a_sibling_environ(container):
    """Shows the kernel allows this between processes of one UID, which is why workers use
    another UID. (The service itself is also non-dumpable because it gained capabilities at
    exec, so it cannot be used as the control.)"""
    name, _ = container
    probe = (
        "import os, subprocess, sys\n"
        f"env = {{'ANTHROPIC_API_KEY': '{KEY}'}}\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'], env=env)\n"
        "print(open(f'/proc/{child.pid}/environ').read())\n"
        "child.kill()\n"
    )
    assert KEY in exec_as(name, "10001:10001", probe).stdout


def test_service_has_only_setuid_and_setgid(container):
    name, _ = container
    probe = FIND_SERVICE + "print(open(f'/proc/{pid}/status').read())"
    status = dict(
        line.split(":\t") for line in exec_as(name, "10001:10001", probe).stdout.splitlines()
        if line.startswith("Cap")
    )  # fmt: skip
    # CAP_SETGID is bit 6 and CAP_SETUID is bit 7: 0xc0.
    for field in ("CapPrm", "CapEff", "CapAmb", "CapBnd"):
        assert int(status[field], 16) == 0xC0, (field, status)
