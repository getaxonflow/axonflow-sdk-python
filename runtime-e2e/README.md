# SDK runtime tests

Per CLAUDE.md HARD RULE #0: a user-facing feature is not done until you
have demonstrated it working through the SDK's actual runtime — a real
httpx call from a real `from axonflow import AxonFlow` against a real
running AxonFlow agent.

**Tests in this directory MUST hit a real endpoint.** No `unittest.mock`,
no `MagicMock`, no `httpx_mock.add_response`, no fixture servers. The
`scripts/lint-no-mocks-in-runtime-e2e.sh` lint enforces this.
<!-- allow-mocks-here: this README documents the forbidden-pattern list itself; no executable usage. -->


**Convention.** Each test lives in `runtime-e2e/<feature>/test.py`.

**How to run locally.** Set `AXONFLOW_AGENT_URL` (default
`http://localhost:8080`). Bring up a local agent and register a tenant.
Then:

```
export AXONFLOW_AGENT_URL=http://localhost:8080
RESP=$(curl -s -X POST $AXONFLOW_AGENT_URL/api/v1/register \
  -H "Content-Type: application/json" -d '{"label":"sdk-runtime-e2e"}')
export AXONFLOW_TENANT_ID=$(echo "$RESP" | jq -r .tenant_id)
export AXONFLOW_TENANT_SECRET=$(echo "$RESP" | jq -r .secret)

for d in runtime-e2e/*/; do
  python3 "$d/test.py" || exit 1
done
```

**What counts as a test.** Each test.py exits non-zero if the SDK's real
wire output to a real agent isn't what you expect. Capture an agent
log line or response field that echoes a value the SDK sent.
