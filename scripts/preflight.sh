#!/usr/bin/env bash
# Read-only checks. Never deploys, never writes to AWS.
# Usage: scripts/preflight.sh [--require-secret]
set -uo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

REQUIRE_SECRET=0
[ "${1:-}" = "--require-secret" ] && REQUIRE_SECRET=1
failures=0
bad() { fail "$*"; failures=$((failures + 1)); }

check_tools() {
  if command -v aws >/dev/null; then ok "AWS CLI: $(aws --version 2>&1 | head -1)"; else bad "AWS CLI not found. Install AWS CLI v2."; fi
  command -v node >/dev/null && command -v npx >/dev/null && ok "Node $(node --version) and npx found" \
    || bad "node/npx not found. Node 18 or later is required to run the CDK CLI."
  command -v python3 >/dev/null && ok "$(python3 --version)" || bad "python3 not found"
}

check_credentials() {
  local identity
  if identity="$(aws sts get-caller-identity --output text --query '[Account,Arn]' 2>&1)"; then
    ok "Credentials valid. Account: $(echo "$identity" | cut -f1)  Identity: $(echo "$identity" | cut -f2)"
    echo "        Confirm this is the account you intend to pay for."
  else
    bad "No usable AWS credentials ($identity). See docs/deploy-walkthrough.md step 3."
  fi
}

check_region_and_bootstrap() {
  resolve_region
  if [ -z "$REGION" ]; then bad "No region. Set AWS_REGION or run: aws configure set region <region>"; return; fi
  ok "Region: $REGION"
  local status
  status="$(aws cloudformation describe-stacks --stack-name CDKToolkit --region "$REGION" \
    --query 'Stacks[0].StackStatus' --output text 2>/dev/null || true)"
  case "$status" in
    CREATE_COMPLETE|UPDATE_COMPLETE) ok "CDK bootstrap present in $REGION ($status)" ;;
    "") bad "CDK is not bootstrapped in $REGION. Run: npx aws-cdk@$CDK_CLI_VERSION bootstrap (see docs)" ;;
    *) bad "CDKToolkit stack status is $status in $REGION" ;;
  esac
}

check_docker() {
  if ! docker info >/dev/null 2>&1; then bad "Docker is not running or not reachable."; return; fi
  ok "Docker daemon reachable"
  local arch
  arch="$(python3 -c "import json;print(json.load(open('$INFRA_DIR/cdk.json'))['context']['cpu_arch'])")"
  if [ "$arch" = "arm64" ] && [ "$(uname -m)" != "aarch64" ] && [ ! -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]; then
    warn "cpu_arch=arm64 on a non-ARM host without qemu emulation. Image build will fail."
    echo "        Fix: docker run --privileged --rm tonistiigi/binfmt --install arm64"
    echo "        Or deploy with: cpu_arch=x86_64"
  fi
}

check_secret() {
  local name="code-interpreter-agent/api-keys"
  if ! aws secretsmanager describe-secret --secret-id "$name" --region "$REGION" >/dev/null 2>&1; then
    if [ "$REQUIRE_SECRET" = 1 ]; then bad "Secret $name not found. Deploy the secrets stack first."
    else warn "Secret $name does not exist yet. 'scripts/deploy.sh secrets' creates it empty."; fi
  elif secret_has_value "$name"; then
    ok "Secret $name exists and has a value (value not read)"
  elif [ "$REQUIRE_SECRET" = 1 ]; then
    bad "Secret $name exists but is empty. See docs/deploy-walkthrough.md step 6."
  else
    warn "Secret $name exists but is empty. Fill it before deploying the service."
  fi
}

check_tools
check_credentials
[ "$failures" -eq 0 ] && check_region_and_bootstrap
if command -v docker >/dev/null; then check_docker; else bad "docker not found"; fi
[ -n "${REGION:-}" ] && [ "$failures" -eq 0 ] && check_secret
echo
if [ "$failures" -eq 0 ]; then echo "Preflight passed. Nothing was deployed."; else echo "Preflight found $failures blocking problem(s). Nothing was deployed."; exit 1; fi
