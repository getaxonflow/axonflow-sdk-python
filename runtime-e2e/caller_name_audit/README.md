# caller_name_audit (#2912)

Real-stack proof for `AuditToolCallRequest.caller_name`
(getaxonflow/axonflow-enterprise#2912, sub-issue of epic #2905).

`tool_type` on `audit_tool_call` was misleadingly named — every real caller
(claude_code/codex/cursor/openclaw) used it to identify *which client* made
the call, not any property of the tool. `caller_name` is the correctly-named
replacement; `tool_type` is kept as a deprecated input fallback (not removed).

## What this proves

Drives the real SDK's `client.audit_tool_call(...)` against a real running
agent+orchestrator and confirms the value actually lands in
`policy_details.caller_name` on the persisted `audit_logs` row — not just
that it marshals onto the wire correctly (that's covered by the
`httpx_mock`-based unit tests in `tests/test_audit_tool_call.py`):

1. `caller_name="e2e-caller-name-probe-<run>"` → `policy_details.caller_name`
   equals that value verbatim.
2. Legacy `tool_type="mcp"` with **no** `caller_name` → the deprecated
   fallback still resolves `policy_details.caller_name` to `"mcp"`.

The SDK's typed `AuditLogEntry` does not declare a `policy_details` field
(it's an internal JSONB blob), so the read side uses a raw `httpx` call
against `GET /api/v1/audit/tenant/{tenant_id}` (the same route
`client.get_audit_logs_by_tenant` hits) to observe it — the only way to see
it from outside the platform.

## Enterprise-mode identity scoping

The tenant-audit read endpoint scopes non-tenant-wide callers to their own
`user_email` (#2922). This test patches the SDK's real httpx transport (not
a mock of the SDK — the same monkeypatch-the-transport technique
`runtime-e2e/x-client-id/test.py` uses) so every request, read and write,
carries a distinctive trust-gated `X-User-Email`. That requires
`AXONFLOW_TRUST_IDENTITY_HEADERS=true` on the platform (already set for the
local-dev Enterprise stack in `axonflow-enterprise/main-tree/.env`). In
Community mode, tenant-audit reads are tenant-wide regardless, so the header
is a no-op there.

## Prerequisite: platform support is not yet on `main`

`caller_name` support (axonflow-enterprise#2953) is implemented but, as of
this writing, still an open PR on the `feat/2912-caller-name-tool-type-deprecation`
branch — not yet merged to `axonflow-enterprise` main. Against a stack built
from `axonflow-enterprise` main, this test will FAIL (the 45s poll of
`GET /api/v1/audit/tenant/{tenant_id}` times out waiting for
`policy_details.caller_name`, which the server doesn't write yet) — that's
not a bug in this test, it means the platform side isn't deployed on
whatever stack you're pointed at. Point your local `axonflow-enterprise`
checkout at that branch (or a later commit that includes it) before running
this test.

## Run

```
export AXONFLOW_AGENT_URL=http://localhost:8080
export AXONFLOW_TENANT_ID=local-dev-org
export AXONFLOW_TENANT_SECRET=<AXONFLOW_CLIENT_SECRET from your local axonflow-enterprise .env>
python runtime-e2e/caller_name_audit/test.py
```

The orchestrator's `AuditLogger` batches writes (flush every 10s per
`platform/orchestrator/audit_logger.go`), so the test polls
`GET /api/v1/audit/tenant/{tenant_id}` for up to 45s before failing.

Exits non-zero if `caller_name` (or the `tool_type` fallback) does not reach
`policy_details.caller_name` on the real row.

## Companion unit coverage

`tests/test_audit_tool_call.py` exercises the same surface through
`httpx_mock` for the wire-body shape (`caller_name` alone, `tool_type` alone,
both together, both omitted). This runtime proof is the real-stack
confirmation the `runtime-e2e/` DoD gate requires.
