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

delete_stack() {
  local stack="$1"
  stack_exists "$stack" || { echo "$stack does not exist. Skipping."; return; }
  read -r -p "Type the stack name to delete it ($stack): " typed
  [ "$typed" = "$stack" ] || { echo "Cancelled."; exit 0; }
  aws cloudformation delete-stack --stack-name "$stack" --region "$REGION"
  aws cloudformation wait stack-delete-complete --stack-name "$stack" --region "$REGION"
  echo "$stack deleted."
}

# Retained resources outlive their stack, so they are looked up by name after the stack is gone.
# This also lets a later "--purge" run remove what an earlier run without it left behind.
retained_log_groups() {
  aws logs describe-log-groups --log-group-name-prefix "$SERVICE_STACK-Logs" --region "$REGION" \
    --query 'logGroups[].logGroupName' --output text 2>/dev/null || true
}

secret_exists() {
  aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" >/dev/null 2>&1
}

delete_stack "$SERVICE_STACK"
for log_group in $(retained_log_groups); do
  [ "$log_group" = "None" ] && continue
  if [ "$purge" = 1 ]; then aws logs delete-log-group --log-group-name "$log_group" --region "$REGION" && echo "Deleted log group $log_group."
  else echo "Retained log group: $log_group (14 day retention). Re-run with --purge to remove it."; fi
done

if stack_exists "$SECRETS_STACK"; then
  echo "The secrets stack holds the API key secret. Delete it only if you will not redeploy soon."
  delete_stack "$SECRETS_STACK"
fi
if ! stack_exists "$SECRETS_STACK" && secret_exists; then
  if [ "$purge" = 1 ]; then
    aws secretsmanager delete-secret --secret-id "$SECRET_NAME" \
      --force-delete-without-recovery --region "$REGION" && echo "Secret permanently deleted."
  else
    echo "Retained secret: $SECRET_NAME. Re-run with --purge to delete it without a recovery window."
  fi
fi
echo "Not removed by this script: the CDK bootstrap stack (CDKToolkit) and image assets in its ECR repository."
