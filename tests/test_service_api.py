"""API tests. Offline: FakeProvider plus the real subprocess executor."""

import asyncio
import contextlib
import dataclasses
import ipaddress
import json
import os
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


def make_client(tmp_path, script, peer=("testclient", 50000), **overrides):
    settings = dataclasses.replace(BASE, workspace_root=str(tmp_path), **overrides)

    def factory(settings):
        executor = SubprocessExecutor(workspace_root=tmp_path, timeout_s=10)
        return Agent(
            FakeProvider(script), executor, max_iterations=settings.max_iterations,
            max_total_tokens=settings.max_total_tokens,
        )  # fmt: skip

    return TestClient(create_app(settings, factory), client=peer)


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


def test_unwritable_workspace_root_fails_readiness(tmp_path):
    from service.app import check_workspace_root

    check_workspace_root(str(tmp_path))
    with pytest.raises(ValueError, match="not a writable directory"):
        check_workspace_root(str(tmp_path / "missing"))
    locked = tmp_path / "locked"
    locked.mkdir(mode=0o500)
    if os.geteuid() != 0:
        with pytest.raises(ValueError, match="not a writable directory"):
            check_workspace_root(str(locked))


def test_auth_required_with_empty_token_fails_closed(tmp_path):
    with make_client(tmp_path, [text_turn("ok")], auth_required=True, api_token=None) as client:
        health = client.get("/health")
        assert health.status_code == 503
        assert "SERVICE_API_TOKEN" in health.json()["error"]
        assert client.post("/v1/run", json=body()).status_code == 503


def test_non_ascii_bearer_token_is_401(tmp_path):
    with make_client(tmp_path, [text_turn("ok")], api_token="s3cret") as client:
        headers = [(b"authorization", b"Bearer s3cr\xe9t")]
        response = client.post("/v1/run", json=body(), headers=headers)
    assert response.status_code == 401


def test_forwarded_for_is_ignored_from_untrusted_peers(tmp_path):
    script = [text_turn("a"), text_turn("b")]
    with make_client(tmp_path, script, rate_limit_per_minute=1) as client:
        first = {"X-Forwarded-For": "198.51.100.1"}
        second = {"X-Forwarded-For": "198.51.100.2"}
        assert client.post("/v1/run", json=body(), headers=first).status_code == 200
        assert client.post("/v1/run", json=body(), headers=second).status_code == 429


def test_forwarded_for_is_used_from_trusted_proxies(tmp_path):
    script = [text_turn("a"), text_turn("b")]
    overrides = {
        "rate_limit_per_minute": 1,
        "trusted_proxies": (ipaddress.ip_network("10.0.0.0/16"),),
        "peer": ("10.0.1.20", 50000),
    }
    with make_client(tmp_path, script, **overrides) as client:
        for address in ("198.51.100.1", "198.51.100.2"):
            headers = {"X-Forwarded-For": f"10.9.9.9, {address}"}
            assert client.post("/v1/run", json=body(), headers=headers).status_code == 200


def call_asgi(app, payload, send):
    """Drive one request through the ASGI app with a caller-supplied send."""
    data = json.dumps(payload).encode()
    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1", "method": "POST", "scheme": "http", "path": "/v1/run",
        "raw_path": b"/v1/run", "query_string": b"", "root_path": "",
        "client": ("127.0.0.1", 1), "server": ("testserver", 80),
        "headers": [
            (b"content-type", b"application/json"), (b"accept", b"text/event-stream"),
            (b"content-length", str(len(data)).encode()),
        ],
    }  # fmt: skip
    messages = [{"type": "http.request", "body": data, "more_body": False}]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def run():
        with contextlib.suppress(Exception):
            await app(scope, receive, send)

    asyncio.run(run())


def test_sse_slot_is_released_when_client_disconnects_before_streaming(tmp_path):
    async def send(message):
        if message["type"] == "http.response.start":
            raise OSError("client went away")

    script = [text_turn("a"), text_turn("b")]
    with make_client(tmp_path, script, max_concurrent_runs=1) as client:
        call_asgi(client.app, body(), send)
        assert client.post("/v1/run", json=body()).status_code == 200


def test_chunked_body_over_the_limit_is_rejected(tmp_path):
    def chunks():
        for _ in range(10):
            yield b"x" * 1000

    with make_client(tmp_path, []) as client:
        response = client.post("/v1/run", content=chunks())
    assert response.status_code == 413
