#!/usr/bin/env bash
# Two stage deploy with a typed confirmation before anything is created.
#   scripts/deploy.sh secrets [key=value ...]   creates the empty secret
#   scripts/deploy.sh service [key=value ...]   builds the image and creates the service
# key=value pairs override cdk.json context (for example egress_mode=public_ip).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

stage="${1:-}"; shift || true
case "$stage" in
  secrets) stack="$SECRETS_STACK"; preflight_flag="" ;;
  service) stack="$SERVICE_STACK"; preflight_flag="--require-secret" ;;
  *) die "usage: scripts/deploy.sh secrets|service [key=value ...]" ;;
esac

"$REPO_ROOT/scripts/preflight.sh" $preflight_flag
resolve_region
account="$(aws sts get-caller-identity --query Account --output text)"
ensure_venv

summary_secrets() {
  cat <<TXT
This will create one EMPTY Secrets Manager secret named code-interpreter-agent/api-keys.
It holds no value until you add your API keys in the next step. Cost: about \$0.40 per month.
TXT
}

summary_service() {
  cat <<TXT
This will create, in account $account / region $REGION:
  - a VPC with 2 availability zones, ECS cluster and a Fargate service
  - a container image built from ./Dockerfile and pushed to the CDK bootstrap ECR repository
  - an Application Load Balancer (internal unless public_https=true was passed)
  - security groups, IAM roles (task role has no permissions), log group (14 days), 2 alarms
The service runs code written by an AI model. Read docs/security.md first.
TXT
  python3 "$REPO_ROOT/scripts/estimate_cost.py" "$@"
}

echo
"summary_$stage" "$@"
echo
echo "Stack to deploy: $stack"
read -r -p "Type the stack name to proceed (anything else cancels): " typed
[ "$typed" = "$stack" ] || { echo "Cancelled. Nothing was deployed."; exit 0; }

mapfile -t ctx < <(context_args "$@")
run_cdk deploy "$stack" --require-approval never "${ctx[@]}"

if [ "$stage" = "secrets" ]; then
  cat <<TXT

Next: store your keys (the value is read from your terminal, not saved in shell history):
  read -r -s -p "Anthropic key: " K; echo
  aws secretsmanager put-secret-value --region $REGION --secret-id code-interpreter-agent/api-keys \\
    --secret-string "{\"ANTHROPIC_API_KEY\":\"\$K\"}"; unset K
Then run: scripts/deploy.sh service
TXT
else
  echo
  echo "Service deployed. Base URL: $(stack_output "$SERVICE_STACK" BaseUrl)"
  echo "Next: scripts/smoke.sh (an internal ALB is reachable only from inside the VPC)."
fi
