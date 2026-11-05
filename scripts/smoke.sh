#!/usr/bin/env bash
# Calls GET /health and one POST /v1/run. Read-only against AWS (describe-stacks only).
# BASE_URL overrides the stack output. SMOKE_BODY overrides the request body.
# SMOKE_TOKEN, if set, is sent as a bearer token (needed when SERVICE_API_TOKEN is enabled).
# Request schema: {"session_id": "...", "message": "..."}. The reply is {"session_id", "events"}.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
resolve_region

scheme="$(stack_output "$SERVICE_STACK" AlbScheme)"
if [ "$scheme" = "internet-facing" ] && [ -z "${BASE_URL:-}" ]; then
  die "Public mode: the certificate covers your domain, not the ALB host name. Point that domain at the AlbDnsName output and run BASE_URL=https://<your domain> scripts/smoke.sh"
fi
BASE_URL="${BASE_URL:-$(stack_output "$SERVICE_STACK" BaseUrl)}"
[ -n "$BASE_URL" ] && [ "$BASE_URL" != "None" ] || die "No BASE_URL and no stack output found for $SERVICE_STACK."
DEFAULT_BODY='{"session_id":"smoke","message":"Use the python tool to compute 6*7 and reply with the number only."}'
SMOKE_BODY="${SMOKE_BODY:-$DEFAULT_BODY}"
echo "Target: $BASE_URL"

unreachable_hint() {
  echo "Could not connect within 10 seconds."
  if [ "$scheme" = "internal" ]; then
    echo "The ALB is internal: run this script from inside the VPC (see docs/deploy-walkthrough.md step 8)."
  fi
  exit 1
}

echo "GET /health"
for attempt in 1 2 3 4 5 6; do
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$BASE_URL/health" || true)"
  [ "$code" = "200" ] && break
  [ "$code" = "000" ] && [ "$attempt" = 1 ] && unreachable_hint
  echo "  attempt $attempt: HTTP $code, retrying in 10 s"; sleep 10
done
[ "$code" = "200" ] || die "/health did not return 200 (last: $code)"
ok "/health returned 200"

echo "POST /v1/run"
response="$(curl -s -w '\n%{http_code}' --max-time 180 -H 'Content-Type: application/json' ${SMOKE_TOKEN:+-H "Authorization: Bearer $SMOKE_TOKEN"} -d "$SMOKE_BODY" "$BASE_URL/v1/run")"
code="${response##*$'\n'}"
echo "${response%$'\n'*}" | head -c 2000; echo
[ "$code" = "200" ] || die "/v1/run returned HTTP $code"
ok "/v1/run returned 200"
