#!/usr/bin/env bash
# Deletes the service stack, then optionally the secrets stack. Requires typing the stack name.
# Resources with a Retain policy (secret, log group) survive stack deletion unless --purge is given.
#   scripts/destroy.sh [--purge]
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
resolve_region
[ -n "$REGION" ] || die "No region configured."
purge=0; [ "${1:-}" = "--purge" ] && purge=1

stack_exists() { aws cloudformation describe-stacks --stack-name "$1" --region "$REGION" >/dev/null 2>&1; }

retained_ids() { # stack, resource type -> physical ids marked Retain in the deployed template
  aws cloudformation get-template --stack-name "$1" --region "$REGION" --query TemplateBody --output json |
    python3 -c "
import json, sys
t = json.load(sys.stdin)
print(*[k for k, v in t['Resources'].items() if v.get('DeletionPolicy') == 'Retain' and v['Type'] == sys.argv[1]], sep='\n')" "$2"
}

delete_stack() {
  local stack="$1"
  stack_exists "$stack" || { echo "$stack does not exist. Skipping."; return; }
  read -r -p "Type the stack name to delete it ($stack): " typed
  [ "$typed" = "$stack" ] || { echo "Cancelled."; exit 0; }
  aws cloudformation delete-stack --stack-name "$stack" --region "$REGION"
  aws cloudformation wait stack-delete-complete --stack-name "$stack" --region "$REGION"
  echo "$stack deleted."
}

log_group="$(aws cloudformation describe-stack-resources --stack-name "$SERVICE_STACK" --region "$REGION" \
  --query "StackResources[?ResourceType=='AWS::Logs::LogGroup'].PhysicalResourceId" --output text 2>/dev/null || true)"
retained_logs="$(stack_exists "$SERVICE_STACK" && retained_ids "$SERVICE_STACK" AWS::Logs::LogGroup || true)"

delete_stack "$SERVICE_STACK"
if [ -n "$retained_logs" ] && [ -n "$log_group" ]; then
  if [ "$purge" = 1 ]; then aws logs delete-log-group --log-group-name "$log_group" --region "$REGION" && echo "Deleted log group $log_group."
  else echo "Retained log group: $log_group (14 day retention). Re-run with --purge to remove it."; fi
fi

if stack_exists "$SECRETS_STACK"; then
  echo "The secrets stack holds the API key secret. Delete it only if you will not redeploy soon."
  secret_retained="$(retained_ids "$SECRETS_STACK" AWS::SecretsManager::Secret || true)"
  delete_stack "$SECRETS_STACK"
  if [ -n "$secret_retained" ]; then
    if [ "$purge" = 1 ]; then
      aws secretsmanager delete-secret --secret-id code-interpreter-agent/api-keys \
        --force-delete-without-recovery --region "$REGION" && echo "Secret permanently deleted."
    else
      echo "Retained secret: code-interpreter-agent/api-keys. Re-run with --purge to delete it without a recovery window."
    fi
  fi
fi
echo "Not removed by this script: the CDK bootstrap stack (CDKToolkit) and image assets in its ECR repository."
