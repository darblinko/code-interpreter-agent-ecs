# Shared helpers. Sourced by the other scripts; not executable on its own.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INFRA_DIR="$REPO_ROOT/infra"
CDK_CLI_VERSION="2.1147.0"
SECRETS_STACK="CodeInterpreterAgentSecrets"
SERVICE_STACK="CodeInterpreterAgentService"
SECRET_NAME="code-interpreter-agent/api-keys"

ok()   { printf '[ OK ]  %s\n' "$*"; }
warn() { printf '[WARN]  %s\n' "$*"; }
fail() { printf '[FAIL]  %s\n' "$*"; }
die()  { printf 'Error: %s\n' "$*" >&2; exit 1; }

resolve_region() {
  REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region 2>/dev/null || true)}}"
}

# Turn "key=value" arguments into "-c key=value" for the CDK CLI.
context_args() {
  for pair in "$@"; do
    case "$pair" in
      *=*) printf -- '-c\n%s\n' "$pair" ;;
      *) die "context overrides must look like key=value, got: $pair" ;;
    esac
  done
}

ensure_venv() {
  if [ ! -x "$INFRA_DIR/.venv/bin/python" ]; then
    echo "Creating infra/.venv and installing pinned requirements ..."
    python3 -m venv "$INFRA_DIR/.venv"
    "$INFRA_DIR/.venv/bin/pip" install --quiet -r "$INFRA_DIR/requirements.txt"
  fi
}

run_cdk() {
  (cd "$INFRA_DIR" && npx --yes "aws-cdk@$CDK_CLI_VERSION" "$@" --app ".venv/bin/python app.py" --no-version-reporting)
}

stack_output() { # stack, output key
  aws cloudformation describe-stacks --stack-name "$1" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue" --output text 2>/dev/null || true
}

secret_has_value() { # secret name
  local versions
  versions="$(aws secretsmanager list-secret-version-ids --secret-id "$1" --region "$REGION" \
    --query 'length(Versions)' --output text 2>/dev/null || echo 0)"
  [ "$versions" != "0" ] && [ "$versions" != "None" ]
}
