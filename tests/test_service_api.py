"""API tests. Offline: FakeProvider plus the real subprocess executor."""

import dataclasses
import json
import threading

import pytest
from fastapi.testclient import TestClient

from code_interpreter_agent import Agent, FakeProvider, SubprocessExecutor
from code_interpreter_agent.providers import code_turn, text_turn
from service.app import create_app
from service.settings import Settings

BASE = Settings(
    provider="fake", model=None, api_token=None, workspace_root="", sandbox_uid=None,
    sandbox_gid=None, max_body_bytes=2000, max_message_chars=500, rate_limit_per_minute=100,
    max_concurrent_runs=4, max_iterations=3, max_total_tokens=100_000, exec_timeout_s=10,
    exec_memory_mb=1024,
)  # fmt: skip


def make_client(tmp_path, script, **overrides):
    settings = dataclasses.replace(BASE, workspace_root=str(tmp_path), **overrides)

    def factory(settings):
        executor = SubprocessExecutor(workspace_root=tmp_path, timeout_s=10)
        return Agent(
            FakeProvider(script), executor, max_iterations=settings.max_iterations,
            max_total_tokens=settings.max_total_tokens,
        )  # fmt: skip

    return TestClient(create_app(settings, factory))


def body(message="hi", session="s1"):
    return {"session_id": session, "message": message}


def test_health_ready_and_not_ready(tmp_path):
    with make_client(tmp_path, []) as client:
        assert client.get("/health").status_code == 200

    def failing(settings):
        raise ValueError("no key")

    with TestClient(create_app(BASE, failing)) as client:
        response = client.get("/health")
        assert response.status_code == 503
        assert "no key" in response.json()["error"]


def test_run_json(tmp_path):
    script = [code_turn("print(6*7)"), text_turn("42")]
    with make_client(tmp_path, script) as client:
        response = client.post("/v1/run", json=body("compute"))
    assert response.status_code == 200
    events = response.json()["events"]
    assert [e["type"] for e in events if e["type"] != "text_delta"] == [
        "tool_call", "tool_result", "final",
    ]  # fmt: skip
    assert "42" in events[[e["type"] for e in events].index("tool_result")]["content"]
    assert events[-1]["stop_reason"] == "end_turn"


def test_run_sse(tmp_path):
    with make_client(tmp_path, [text_turn("hello there")]) as client:
        response = client.post("/v1/run", json=body(), headers={"Accept": "text/event-stream"})
    assert response.headers["content-type"].startswith("text/event-stream")
    blocks = [b for b in response.text.split("\n\n") if b]
    payloads = [json.loads(b.split("data: ", 1)[1]) for b in blocks]
    assert payloads[-1]["type"] == "final"
    assert "".join(p["text"] for p in payloads if p["type"] == "text_delta") == "hello there"


def test_auth_required_when_token_set(tmp_path):
    with make_client(tmp_path, [text_turn("ok")], api_token="s3cret") as client:
        assert client.post("/v1/run", json=body()).status_code == 401
        wrong = {"Authorization": "Bearer nope"}
        assert client.post("/v1/run", json=body(), headers=wrong).status_code == 401
        assert client.get("/health").status_code == 200
        right = {"Authorization": "Bearer s3cret"}
        assert client.post("/v1/run", json=body(), headers=right).status_code == 200


def test_no_auth_when_token_unset(tmp_path):
    with make_client(tmp_path, [text_turn("ok")]) as client:
        assert client.post("/v1/run", json=body()).status_code == 200


def test_request_size_limits(tmp_path):
    with make_client(tmp_path, []) as client:
        assert client.post("/v1/run", json=body("x" * 3000)).status_code == 413
        assert client.post("/v1/run", json=body("x" * 600)).status_code == 413
        assert (
            client.post("/v1/run", json={"session_id": "../x", "message": "m"}).status_code == 422
        )
        assert client.post("/v1/run", content=b"not json").status_code == 422


def test_rate_limit(tmp_path):
    script = [text_turn("a"), text_turn("b")]
    with make_client(tmp_path, script, rate_limit_per_minute=2) as client:
        assert client.post("/v1/run", json=body()).status_code == 200
        assert client.post("/v1/run", json=body()).status_code == 200
        limited = client.post("/v1/run", json=body())
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"


def test_iteration_budget(tmp_path):
    script = [code_turn("print(1)")] * 5
    with make_client(tmp_path, script, max_iterations=2) as client:
        events = client.post("/v1/run", json=body()).json()["events"]
    assert events[-1]["stop_reason"] == "max_iterations"
    assert events[-1]["iterations"] == 2


def test_token_budget(tmp_path):
    script = [code_turn("print(1)")] * 5
    with make_client(tmp_path, script, max_total_tokens=1) as client:
        events = client.post("/v1/run", json=body()).json()["events"]
    assert events[-1]["stop_reason"] == "token_budget"


def test_provider_error_is_502(tmp_path):
    with make_client(tmp_path, []) as client:  # empty script raises on first call
        response = client.post("/v1/run", json=body())
    assert response.status_code == 502


def test_session_isolation(tmp_path):
    script = [
        code_turn("secret = 123; open('a.txt','w').write('A')"), text_turn("done"),
        code_turn("print('secret' in globals()); import os; print(os.listdir('.'))"),
        text_turn("done"),
    ]  # fmt: skip
    with make_client(tmp_path, script) as client:
        client.post("/v1/run", json=body("one", "alpha"))
        events = client.post("/v1/run", json=body("two", "beta")).json()["events"]
    output = next(e["content"] for e in events if e["type"] == "tool_result")
    assert "False" in output
    assert "a.txt" not in output
    assert (tmp_path / "alpha" / "a.txt").exists()


def test_same_session_concurrent_run_is_rejected(tmp_path):
    started, release = threading.Event(), threading.Event()

    def slow_turn(messages):
        started.set()
        release.wait(10)
        return text_turn("late")

    with make_client(tmp_path, [slow_turn, text_turn("x")]) as client:
        worker = threading.Thread(target=lambda: client.post("/v1/run", json=body()))
        worker.start()
        assert started.wait(10)
        assert client.post("/v1/run", json=body()).status_code == 409
        assert client.post("/v1/run", json=body(session="other")).status_code == 200
        release.set()
        worker.join(10)


@pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
def test_docs_are_not_exposed(tmp_path, path):
    with make_client(tmp_path, []) as client:
        assert client.get(path).status_code == 404
