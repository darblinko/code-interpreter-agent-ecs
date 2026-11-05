# Security

## What runs here

The service executes Python written by a language model, steered by whatever text reaches `POST /v1/run`. Treat that code as hostile. The questions are what it can reach and what it can break.

## What the deployment provides

| Control | Where | Effect |
|---|---|---|
| Fargate task isolation | AWS | Each task runs in its own microVM. Code in one task cannot reach another task or the host. |
| Empty task role | `TaskRole`, no policies | Credentials obtained from the task metadata endpoint grant no AWS permissions. |
| Scoped execution role | CDK-generated | Pulls the image, writes logs, reads one secret by exact ARN. The container process cannot obtain these credentials. |
| No inbound except from the ALB | security groups | Task security group accepts only TCP 8080 from the ALB security group. |
| Internal ALB by default | `public_https=false` | The ALB accepts TCP 80 only from the VPC CIDR. |
| Restricted public mode | `public_https=true` | Requires an ACM certificate and a non-`0.0.0.0/0` CIDR; the stack refuses to synthesize otherwise. |
| Egress limited to TCP 443 | task security group | Blocks other protocols and ports. DNS goes through the VPC resolver. |
| Read-only root filesystem | task definition | Code cannot modify the image contents. Writable paths are `/workspace` and `/tmp` only. |
| Capabilities: Fargate defaults dropped except `SETUID`, `SETGID` and `KILL` | task definition | The API process needs these three to start code workers under another UID and to kill them on timeout. Fargate accepts only `SYS_PTRACE` as an added capability, so the task definition adds nothing and drops the other 11 defaults. A test asserts the remaining set. |
| Workers start with no capabilities | `service/executor.py` | A change between two nonzero UIDs keeps capabilities, so workers are started through `setpriv`, which clears the inheritable and ambient sets before Python runs. The container test asserts `CapInh`, `CapPrm`, `CapEff` and `CapAmb` are 0 in a worker and that `os.setuid(0)` fails. |
| Model code runs as UID 10002, API as UID 10001 | image entrypoint, `service/executor.py` | The kernel refuses code workers read access to `/proc/<pid>/environ` of the API process and of PID 1. Workers get an empty environment (8 fixed variables). |
| `no_new_privs`, setuid bits removed | image | Workers cannot gain privileges by running setuid programs. |
| Log retention 14 days | log group | Bounds how long outputs are stored. |

## What it does not provide

1. **Task role credentials are reachable by model code.** ECS serves them to any process in the container through `AWS_CONTAINER_CREDENTIALS_RELATIVE_URI` and `169.254.170.2`. This is why the role is empty. Do not attach policies to it. Adding S3, DynamoDB or any other grant gives the model that grant.
2. **API keys: mitigated, with residual exposure.** ECS injects `ANTHROPIC_API_KEY` into the container environment. The entrypoint starts as root, runs `setpriv` to switch to UID 10001 and execs the API, so the key sits in the environment of a process that code workers (UID 10002) cannot read. `tests/test_container_isolation.py` (run with `CIA_CONTAINER_TESTS=1`) builds the image, runs it with the task definition's capabilities and read-only root, and checks: worker env has no key; as UID 10002 without the audit hook, `/proc/1/environ` and the API process's `/proc/<pid>/environ` return `EACCES`; the API process holds exactly `CapPrm=CapEff=CapAmb=CapBnd=0xe0` (KILL, SETGID, SETUID); a worker holds no capabilities and cannot call `os.setuid(0)`; a worker that sleeps past the timeout is killed. The test reproduces the capability set with `docker run --cap-drop`; it has not been run on Fargate. Residual exposure:
   - The API process keeps `CAP_SETUID`, `CAP_SETGID` and `CAP_KILL`. A remote code execution bug in the API (not in model code) could use them to become any UID, including root, within the container, and read the key. Model code cannot reach the API process except through the HTTP interface.
   - The key is in the memory of the API process and in PID 1's original environment block (root only). Anything that breaks the UID boundary (a kernel privilege escalation, which Fargate microVM isolation does not prevent inside the task) exposes it.
   - Model code can still make the API process do work with the key, for example by sending prompts, because it is the agent. It cannot read the key. A provider-side spend limit remains advisable.
   - All sessions share UID 10002. Session separation inside a task is the audit hook and per-session directories (item 4).
3. **Outbound internet access on 443.** Model code can send data to any HTTPS endpoint, including the contents of `/workspace` and any secret it manages to read. Restricting this needs an egress proxy or AWS Network Firewall with a domain allow list, which this repository does not deploy.
4. **No isolation between sessions in one task.** Sessions share the container, the kernel namespaces and the `/workspace` volume. Per-session directories and resource limits are application controls, not infrastructure controls. Use separate tasks for mutually untrusted users.
5. **Authentication is optional.** By default the only gate is network reachability (internal ALB). `SERVICE_API_TOKEN` (deploy with `-c api_token_enabled=true`) adds a shared bearer token on `/v1/run`; it is one token for all callers, sent over HTTP inside the VPC unless you enable `public_https`. For public exposure add per-user authentication in front (ALB OIDC or an API gateway).
6. **Resource caps are per execution, not per task.** Each code run has a 30 s wall clock, CPU and address space limits. The service limits requests to 30 per minute per client and 4 concurrent runs, but state is per task and in memory, and 4 runs at 1024 MB each exceed a 1024 MiB task. Set `MAX_CONCURRENT_RUNS` and `EXEC_MEMORY_MB` to fit the task size. The alarms report overload; they do not prevent it.
7. **Model API cost is unbounded by this stack.** The AWS Budget covers AWS charges only. Set a spend limit in the Anthropic or OpenAI console.
8. **The execution role has one wildcard.** `ecr:GetAuthorizationToken` has no resource-level scope in IAM. The test suite allows this single action on `*` and nothing else.

## Threat model summary

| Threat | Status |
|---|---|
| Model code reads AWS credentials from metadata endpoint | Mitigated: role is empty |
| Model code reaches other AWS services | Mitigated: no permissions; not eliminated for unauthenticated APIs reachable from the VPC |
| Model code reads the API key | Mitigated by UID separation (item 2); residual: API process holds SETUID, SETGID and KILL |
| Model code exfiltrates data over HTTPS | Not mitigated (item 3) |
| Internet user calls `/v1/run` | Mitigated by default (internal ALB); user responsibility in public mode |
| Container escape to another tenant | Reduced by Fargate microVM isolation; AWS shared responsibility model applies |
| Persistent tampering with the image | Mitigated: read-only root filesystem |

## Operational notes

- Use a dedicated AWS account or a sandbox account with a budget. The deploy scripts act with your credentials; review the account id preflight prints.
- Secrets Manager stores the keys encrypted at rest with the AWS managed key. Rotate keys by writing a new secret value and forcing a new deployment: `aws ecs update-service --force-new-deployment`.
- Do not enable ECS Exec on this service. It requires SSM permissions on the task role, which contradicts the empty-role design.
