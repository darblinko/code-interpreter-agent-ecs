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
