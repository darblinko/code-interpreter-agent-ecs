"""FastAPI application: GET /health and POST /v1/run."""

from __future__ import annotations

import hmac
import json
import os
import threading
from collections.abc import Callable, Iterator

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from code_interpreter_agent import Agent, MissingCredentialsError, build_agent

from .events import event_to_dict
from .executor import DroppedPrivilegeExecutor
from .fake_provider import EchoProvider
from .limits import RateLimiter
from .settings import Settings

SESSION_ID_REGEX = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"


class RunRequest(BaseModel):
    session_id: str = Field(pattern=SESSION_ID_REGEX)
    message: str = Field(min_length=1)


def check_workspace_root(path: str) -> None:
    """Fail readiness, with the reason on /health, when the workspace volume is not writable."""
    if not os.path.isdir(path) or not os.access(path, os.W_OK | os.X_OK):
        raise ValueError(f"WORKSPACE_ROOT {path} is not a writable directory for UID {os.getuid()}")


def build_service_agent(settings: Settings) -> Agent:
    check_workspace_root(settings.workspace_root)
    options = {
        "workspace_root": settings.workspace_root,
        "timeout_s": settings.exec_timeout_s,
        "memory_mb": settings.exec_memory_mb,
    }
    if settings.sandbox_uid is not None and settings.sandbox_gid is not None:
        executor = DroppedPrivilegeExecutor(
            uid=settings.sandbox_uid, gid=settings.sandbox_gid, **options
        )
    else:
        from code_interpreter_agent import SubprocessExecutor

        executor = SubprocessExecutor(**options)
    provider = EchoProvider() if settings.provider == "fake" else settings.provider
    return build_agent(
        provider,
        model=settings.model,
        executor=executor,
        max_iterations=settings.max_iterations,
        max_total_tokens=settings.max_total_tokens,
    )


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def create_app(
    settings: Settings | None = None,
    agent_factory: Callable[[Settings], Agent] | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(
        title="code-interpreter-agent-ecs", docs_url=None, redoc_url=None, openapi_url=None
    )
    state = {"agent": None, "not_ready": "starting"}
    limiter = RateLimiter(settings.rate_limit_per_minute)
    run_slots = threading.BoundedSemaphore(settings.max_concurrent_runs)
    active_sessions: set[str] = set()
    sessions_lock = threading.Lock()

    @app.on_event("startup")
    def start() -> None:
        try:
            state["agent"] = (agent_factory or build_service_agent)(settings)
            state["not_ready"] = ""
        except (MissingCredentialsError, ValueError) as error:
            state["not_ready"] = str(error)

    @app.on_event("shutdown")
    def stop() -> None:
        if state["agent"] is not None and state["agent"].executor is not None:
            state["agent"].executor.close()

    @app.get("/health")
    def health():
        if state["agent"] is None:
            return _error(503, f"not ready: {state['not_ready']}")
        return {"status": "ok"}

    def authorized(request: Request) -> bool:
        if settings.api_token is None:
            return True
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        return scheme.lower() == "bearer" and hmac.compare_digest(token, settings.api_token)

    def client_key(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()
        return request.client.host if request.client else "unknown"

    async def read_request(request: Request) -> RunRequest | JSONResponse:
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > settings.max_body_bytes:
            return _error(413, f"Body exceeds {settings.max_body_bytes} bytes")
        body = await request.body()
        if len(body) > settings.max_body_bytes:
            return _error(413, f"Body exceeds {settings.max_body_bytes} bytes")
        try:
            parsed = RunRequest.model_validate_json(body)
        except ValidationError as error:
            return _error(
                422,
                "Invalid request: "
                + "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in error.errors()),
            )
        if len(parsed.message) > settings.max_message_chars:
            return _error(413, f"message exceeds {settings.max_message_chars} characters")
        return parsed

    def events_for(parsed: RunRequest) -> Iterator[dict]:
        for event in state["agent"].run(parsed.session_id, parsed.message):
            yield event_to_dict(event)

    def release(session_id: str) -> None:
        with sessions_lock:
            active_sessions.discard(session_id)
        run_slots.release()

    def sse_stream(parsed: RunRequest) -> Iterator[str]:
        try:
            for item in events_for(parsed):
                yield f"event: {item['type']}\ndata: {json.dumps(item)}\n\n"
        except Exception as error:
            yield f"event: error\ndata: {json.dumps({'type': 'error', 'error': str(error)})}\n\n"
        finally:
            release(parsed.session_id)

    def json_run(parsed: RunRequest) -> JSONResponse:
        try:
            return JSONResponse(
                {"session_id": parsed.session_id, "events": list(events_for(parsed))}
            )
        except Exception as error:
            return _error(502, f"Run failed: {error}")
        finally:
            release(parsed.session_id)

    @app.post("/v1/run")
    async def run(request: Request):
        if state["agent"] is None:
            return _error(503, f"not ready: {state['not_ready']}")
        if not authorized(request):
            return JSONResponse(
                {"error": "Unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not limiter.allow(client_key(request)):
            return JSONResponse(
                {"error": "Rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": "60"},
            )
        parsed = await read_request(request)
        if isinstance(parsed, JSONResponse):
            return parsed
        if not run_slots.acquire(blocking=False):
            return _error(503, "Too many concurrent runs")
        with sessions_lock:
            busy = parsed.session_id in active_sessions
            active_sessions.add(parsed.session_id)
        if busy:
            run_slots.release()
            return _error(409, "A run for this session is already in progress")
        if "text/event-stream" in request.headers.get("accept", ""):
            return StreamingResponse(sse_stream(parsed), media_type="text/event-stream")
        return await run_in_threadpool(json_run, parsed)

    return app
