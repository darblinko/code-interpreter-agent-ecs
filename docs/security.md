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
| All Linux capabilities dropped | task definition | Reduces what a compromised process can do inside the container. |
| Log retention 14 days | log group | Bounds how long outputs are stored. |

## What it does not provide

1. **Task role credentials are reachable by model code.** ECS serves them to any process in the container through `AWS_CONTAINER_CREDENTIALS_RELATIVE_URI` and `169.254.170.2`. This is why the role is empty. Do not attach policies to it. Adding S3, DynamoDB or any other grant gives the model that grant.
2. **API keys are in the process environment.** ECS injects `ANTHROPIC_API_KEY` as an environment variable. Code running as the same Linux user can read `/proc/<pid>/environ` of the service process. Mitigations belong in the application: run model code as a different user, or in a child process spawned with a scrubbed environment under a separate UID, and consider short-lived scoped keys with a spend limit set at the provider.
3. **Outbound internet access on 443.** Model code can send data to any HTTPS endpoint, including the contents of `/workspace` and any secret it manages to read. Restricting this needs an egress proxy or AWS Network Firewall with a domain allow list, which this repository does not deploy.
4. **No isolation between sessions in one task.** Sessions share the container, the kernel namespaces and the `/workspace` volume. Per-session directories and resource limits are application controls, not infrastructure controls. Use separate tasks for mutually untrusted users.
5. **No authentication on `/v1/run`.** The only gate is network reachability. The internal ALB limits callers to the VPC. If you expose it publicly, add authentication in front (for example ALB OIDC or an API gateway) before accepting traffic.
6. **No resource caps beyond the task size.** A request can consume the task's CPU and memory until the application's own limits stop it. The CPU and memory alarms report this; they do not prevent it.
7. **Model API cost is unbounded by this stack.** The AWS Budget covers AWS charges only. Set a spend limit in the Anthropic or OpenAI console.
8. **The execution role has one wildcard.** `ecr:GetAuthorizationToken` has no resource-level scope in IAM. The test suite allows this single action on `*` and nothing else.

## Threat model summary

| Threat | Status |
|---|---|
| Model code reads AWS credentials from metadata endpoint | Mitigated: role is empty |
| Model code reaches other AWS services | Mitigated: no permissions; not eliminated for unauthenticated APIs reachable from the VPC |
| Model code reads the API key | Not mitigated by infrastructure (item 2) |
| Model code exfiltrates data over HTTPS | Not mitigated (item 3) |
| Internet user calls `/v1/run` | Mitigated by default (internal ALB); user responsibility in public mode |
| Container escape to another tenant | Reduced by Fargate microVM isolation; AWS shared responsibility model applies |
| Persistent tampering with the image | Mitigated: read-only root filesystem |

## Operational notes

- Use a dedicated AWS account or a sandbox account with a budget. The deploy scripts act with your credentials; review the account id preflight prints.
- Secrets Manager stores the keys encrypted at rest with the AWS managed key. Rotate keys by writing a new secret value and forcing a new deployment: `aws ecs update-service --force-new-deployment`.
- Do not enable ECS Exec on this service. It requires SSM permissions on the task role, which contradicts the empty-role design.
