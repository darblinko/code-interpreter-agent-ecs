# code-interpreter-agent-ecs

Deploys a code-interpreting agent service to AWS ECS Fargate with an AWS CDK app (Python). The service runs Python code written by an AI model, so the infrastructure is built around that risk: empty task role, no public ingress by default, read-only root filesystem, and a single scoped secret.

The repository holds the infrastructure, the deployment aids, and the application: a FastAPI service (`service/`) around a vendored copy of `code_interpreter_agent` (`src/`, see `VENDORED.md`).

## Container contract

| Item | Value |
|---|---|
| Build | `./Dockerfile` at the repository root, built by CDK |
| Port | 8080 |
| `GET /health` | 200 when ready |
| `POST /v1/run` | runs one agent request |
| Secrets | `ANTHROPIC_API_KEY`, optional `OPENAI_API_KEY` and `SERVICE_API_TOKEN`, injected by ECS from Secrets Manager as environment variables |
| Workspaces | written under `/workspace` (ephemeral task volume, writable) |
| Scratch | `/tmp` is a second writable ephemeral volume |
| Root filesystem | read-only; the image must not write anywhere except `/workspace` and `/tmp` |
| Health probe | the container health check runs `python` against `http://127.0.0.1:8080/health`, so the image needs `python` on PATH |
| Ownership | `/workspace` is created in the image as `10001:10002`, mode `2770`; Fargate volumes inherit ownership from an existing image directory |

## The application

The image starts as root only to run `setpriv`, which switches to UID 10001 (`app`) with `CAP_SETUID`, `CAP_SETGID` and `CAP_KILL` as the only capabilities and `no_new_privs` set. The API process runs as `app` and holds the model keys. Model-written code runs in worker processes started through `setpriv` under UID 10002 (`sandbox`) with an empty environment and empty inheritable, permitted, effective and ambient capability sets. See `docs/security.md` for what this protects.

### API

`GET /health` returns 200 `{"status":"ok"}` when the provider is configured, otherwise 503.

`POST /v1/run` with `{"session_id": "...", "message": "..."}` (session id: 1 to 64 characters of `A-Za-z0-9_.-`, starting alphanumeric).

| Accept header | Response |
|---|---|
| default | `200 {"session_id": "...", "events": [...]}` after the run ends |
| `text/event-stream` | one SSE message per event (`event:` is the event type, `data:` is the JSON) |

Event types: `text_delta`, `tool_call`, `tool_result`, `final` (`stop_reason`, `iterations`, `usage`). Errors: 401 bad or missing token, 409 session already running, 413 body or message too large, 422 invalid body, 429 rate limit, 502 provider failure (JSON mode; SSE sends an `error` event), 503 not ready or all run slots busy.

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `CIA_PROVIDER` | `anthropic` | `anthropic`, `openai`, or `fake` (test provider, no key; never for real traffic) |
| `CIA_MODEL` | provider default | Model id |
| `SERVICE_API_TOKEN` | unset | If set, `/v1/run` requires `Authorization: Bearer <token>`. `/health` stays open. Enable with `-c api_token_enabled=true` and add the field to the secret. |
| `MAX_BODY_BYTES` / `MAX_MESSAGE_CHARS` | 65536 / 20000 | Request size limits |
| `RATE_LIMIT_PER_MINUTE` | 30 | Per client address (last `X-Forwarded-For` entry, else peer), per task |
| `MAX_CONCURRENT_RUNS` | 4 | Simultaneous runs per task |
| `MAX_ITERATIONS` / `MAX_TOTAL_TOKENS` | 8 / 100000 | Budget per request. Tokens are checked before each model call. |
| `EXEC_TIMEOUT_S` / `EXEC_MEMORY_MB` | 30 / 1024 | Per code execution |

Default access control is network only (internal ALB). Rate limits and concurrency are per task and in memory.

### Local run and tests

    python -m venv .venv && .venv/bin/pip install -r service/requirements.txt pytest httpx
    CIA_PROVIDER=fake WORKSPACE_ROOT=/tmp/ws PYTHONPATH=.:src .venv/bin/python -m service
    .venv/bin/python -m pytest                       # API, executor and infra tests, offline
    CIA_CONTAINER_TESTS=1 .venv/bin/python -m pytest tests/test_container_isolation.py   # builds the image

Without `SANDBOX_UID` and `SANDBOX_GID` the service uses the plain subprocess executor (no UID switch), which is the mode for local development only.

## Architecture

```
            (internal by default)
client in VPC --> ALB :80 --> Fargate task :8080 --> HTTPS 443 --> model APIs
                    |              |  read-only root, /workspace, /tmp
                    |              +-- secrets injected from Secrets Manager
              security group       task role: no permissions
              allows VPC CIDR      logs --> CloudWatch (14 days)
```

Two CDK stacks:

| Stack | Contents |
|---|---|
| `CodeInterpreterAgentSecrets` | one empty Secrets Manager secret `code-interpreter-agent/api-keys` |
| `CodeInterpreterAgentService` | VPC, ECS cluster, Fargate service, ALB, security groups, roles, log group, alarms, optional budget |

The stacks are separate so the secret can be filled before the service starts. A service deployed against an empty secret cannot start its tasks.

## Quick start

Prerequisites: AWS account, AWS CLI v2 with credentials, Node 18+, Python 3.11+, Docker. See `docs/deploy-walkthrough.md` for a first-time walkthrough.

```
scripts/preflight.sh                 # read-only checks, never deploys
scripts/deploy.sh secrets            # creates the empty secret
# store your key (command printed by the script)
scripts/deploy.sh service            # builds the image, creates the service
scripts/smoke.sh                     # /health and one /v1/run
scripts/destroy.sh --purge           # tear everything down
```

Each deploy prints a summary and the estimated monthly cost, then requires typing the stack name.

## Configuration

Defaults are in `infra/cdk.json`. Override with `key=value` arguments to `deploy.sh`.

| Key | Default | Meaning |
|---|---|---|
| `egress_mode` | `nat` | `nat`: tasks in private subnets behind one NAT gateway. `public_ip`: tasks in public subnets with a public IP, no NAT (about $33 per month cheaper, see `docs/cost.md`). Inbound is blocked in both modes. |
| `cpu_arch` | `arm64` | `arm64` or `x86_64`. ARM is about 20 percent cheaper; building it on an x86 host needs qemu emulation. |
| `cpu`, `memory_mib` | 512, 1024 | Fargate task size |
| `desired_count` | 1 | Task count |
| `autoscaling`, `max_count` | false, 2 | CPU target tracking at 60 percent when enabled |
| `openai_enabled` | false | Also inject `OPENAI_API_KEY`. The secret must then contain that field. |
| `api_token_enabled` | false | Also inject `SERVICE_API_TOKEN`; `/v1/run` then requires that bearer token. The secret must contain the field. |
| `public_https` | false | Internet-facing ALB with HTTPS. Requires `certificate_arn` (ACM) and `public_ingress_cidr`. `0.0.0.0/0` is refused. |
| `notification_email` | empty | Enables the monthly budget and emails alarms |
| `monthly_budget_usd` | 100 | Budget limit; alerts at 80 percent actual and 100 percent forecast |
| `destroy_on_delete` | false | `true` sets removal policy DESTROY on the secret and log group at deploy time |

## Why a NAT gateway by default

The agent calls model APIs on the internet, so a fully NAT-less private design is not possible without a proxy. Interface VPC endpoints do not reach `api.anthropic.com` or `api.openai.com`, and Bedrock is out of scope. The choices are a NAT gateway (about $37 per month including its public IPv4 address) or public IPs on tasks. The default keeps tasks without public addresses. `egress_mode=public_ip` removes the NAT cost; the security group still allows no inbound traffic.

## Tests

```
python3 -m venv infra/.venv && infra/.venv/bin/pip install -r infra/requirements-dev.txt -r service/requirements.txt httpx
infra/.venv/bin/python -m pytest
```

The tests synthesize both stacks offline and assert on the CloudFormation templates. They make no AWS calls. The API tests use `FakeProvider`. The container isolation tests are skipped unless `CIA_CONTAINER_TESTS=1` and Docker is available.

## Documents

- `docs/deploy-walkthrough.md`: first deployment, step by step
- `docs/cost.md`: itemized monthly estimate
- `docs/security.md`: threat model for running model-written code

## License

Apache-2.0 (the LICENSE file is added with the application code).
