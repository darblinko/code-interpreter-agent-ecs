import subprocess

from service.executor import DroppedPrivilegeExecutor


class Captured(Exception):
    pass


def test_worker_is_started_under_the_sandbox_uid_with_a_scrubbed_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-sentinel")
    monkeypatch.setattr("os.chown", lambda *args, **kwargs: None)
    seen = {}

    def fake_popen(command, **kwargs):
        seen.update(kwargs, command=command)
        raise Captured

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    executor = DroppedPrivilegeExecutor(uid=10002, gid=10002, workspace_root=tmp_path)
    workspace = tmp_path / "s1"
    workspace.mkdir()
    try:
        executor._spawn("s1", workspace)
    except Captured:
        pass
    finally:
        executor.close()
    # setpriv switches the UID and clears the inheritable and ambient capability sets, which a
    # plain change between two nonzero UIDs would keep.
    separator = seen["command"].index("--")
    assert seen["command"][:separator] == [
        "setpriv",
        "--reuid=10002",
        "--regid=10002",
        "--clear-groups",
        "--inh-caps=-all",
        "--ambient-caps=-all",
    ]
    assert not {"user", "group", "extra_groups"} & set(seen)
    assert "ANTHROPIC_API_KEY" not in seen["env"]
    assert "sk-ant-sentinel" not in "".join(seen["env"].values())
    assert seen["umask"] == 0o002


def test_large_output_is_bounded_in_the_worker(tmp_path):
    from code_interpreter_agent import SubprocessExecutor

    with SubprocessExecutor(workspace_root=tmp_path, max_output_chars=1000) as executor:
        result = executor.run("s1", "print('x' * 5_000_000)")
    assert result.ok
    assert result.stdout.startswith("x" * 1000)
    assert result.stdout.endswith(f"[{5_000_001 - 1000} characters truncated]")
    assert len(result.stdout) < 1100


def test_raw_write_to_the_protocol_descriptor_is_cut_off(tmp_path):
    """fd 4 is the worker's protocol output; code can write to it without the capture buffer."""
    from code_interpreter_agent import SubprocessExecutor

    with SubprocessExecutor(workspace_root=tmp_path, timeout_s=10) as executor:
        result = executor.run("s1", "import os; os.write(4, b'x' * 3_000_000)")
        assert not result.ok
        assert "protocol limit" in result.stderr
        assert executor.run("s1", "print(1)").stdout == "1\n"
