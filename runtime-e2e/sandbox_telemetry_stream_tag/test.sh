#!/usr/bin/env bash
# Runtime proof — Python SDK v8 sandbox-mode telemetry fires with stream=sandbox.
#
# Builds a tiny Python program that uses the LOCAL SDK (via pip install -e ..)
# in sandbox mode against an unreachable agent endpoint. The SDK fires its
# anonymous telemetry ping during AxonFlow() construction. We then query the
# deployed checkpoint Lambda's CloudWatch logs for the audit line that
# should record stream=sandbox in DynamoDB.
#
# Pre-v8 this test would have produced ZERO pings (sandbox-mode silent
# suppression). Post-v8 we expect exactly one ping with stream=sandbox.
#
# Stack-state assumptions:
#   - axonflow-enterprise PR #2005 is deployed (server-side stream allowlist
#     accepts and persists "sandbox" — without that, this row is stored
#     as stream=heartbeat, defeating the test's purpose).
#   - AWS credentials with read access on /aws/lambda/prod-axonflow-checkpoint.
#
# Usage:
#   AWS_REGION=us-east-1 ./test.sh

set -uo pipefail

REGION=${AWS_REGION:-us-east-1}
LOG_GROUP=${LOG_GROUP:-/aws/lambda/prod-axonflow-checkpoint}
RUN_TAG=$(date -u +%s)
SDK_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }

# Build a transient venv that imports the local SDK + creates a Sandbox-mode
# client. The unreachable :65530 endpoint is intentional — we only want the
# anonymous heartbeat to fire, not any platform call.
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

python3 -m venv "$WORK/venv"
# shellcheck source=/dev/null
. "$WORK/venv/bin/activate"
pip install --quiet --upgrade pip
pip install --quiet -e "$SDK_ROOT"

cat > "$WORK/main.py" <<'EOF'
import os
import time
from datetime import datetime, timezone

# Clear AXONFLOW_TELEMETRY so the ping fires; conftest-level autouse env
# is a pytest construct and doesn't apply to this standalone runtime test.
os.environ.pop("AXONFLOW_TELEMETRY", None)

from axonflow import AxonFlow, Mode

ts = datetime.now(timezone.utc).isoformat()
print(f"[{ts}] Constructing sandbox-mode client (unreachable agent)...")
client = AxonFlow(
    endpoint="http://localhost:65530",
    client_id="rt-test",
    client_secret="rt-test",
    mode=Mode.SANDBOX,
)
ts = datetime.now(timezone.utc).isoformat()
print(f"[{ts}] AxonFlow() returned. Sleeping 2s for inflight HTTP...")
time.sleep(2)
ts = datetime.now(timezone.utc).isoformat()
print(f"[{ts}] Done.")
EOF

T0_MS=$(($(date -u +%s)*1000))
echo "Run tag: $RUN_TAG"
echo "T0 (ms): $T0_MS"
echo

python "$WORK/main.py" 2>&1 || {
	red "FAIL: subprocess errored before completing"
	exit 1
}

echo
echo "Waiting 10s for CloudWatch log delivery..."
sleep 10

# Look for the audit row our run produced — match by sdk=python/8 against
# logs since T0.
echo "Querying CloudWatch logs since T0 for sdk=python/8 event_stored entries..."
HITS=$(aws --region "$REGION" logs filter-log-events \
	--log-group-name "$LOG_GROUP" \
	--start-time "$T0_MS" \
	--filter-pattern '"event_stored" "sdk=python/8"' \
	--query 'events[*].message' \
	--output text 2>&1)

if [ -z "$HITS" ]; then
	red "FAIL: no event_stored sdk=python/8 row landed in checkpoint logs since T0"
	red "  Expected: one audit row tagged stream=sandbox"
	red "  CloudWatch query window: $T0_MS → now"
	exit 1
fi

echo "Audit rows found:"
echo "$HITS"
echo

if echo "$HITS" | grep -q 'stream=sandbox'; then
	green "PASS: Python SDK sandbox-mode ping landed with stream=sandbox"
else
	red "FAIL: audit row did not include stream=sandbox"
	red "  This usually means PR #2005 (server-side allowlist) is not yet deployed —"
	red "  the server still hardcodes stream=heartbeat regardless of payload."
	exit 1
fi
