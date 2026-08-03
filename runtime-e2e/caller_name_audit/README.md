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
3. **Neither** `caller_name` **nor** `tool_type` supplied → the platform's
   default-fallback (getaxonflow/axonflow-enterprise#2903, folded into the
   same #2953 merge) resolves `policy_details.caller_name` to `"unknown"` —
   not the pre-#2903 default of `"claude_code"`. An unidentified caller must
   never be silently attributed to a specific client.

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

## Prerequisite: platform version

`caller_name` support (axonflow-enterprise#2953, which also folded in the
#2903 default-fallback fix) is merged to `axonflow-enterprise` main and
shipped in platform release **v9.11.0**. Point your stack at v9.11.0+ (or a
`main` checkout at/after commit `7a5984ec7`) before running this test.
Against an older platform, the third scenario (`unknown` default) and
possibly `policy_details.caller_name` itself will not resolve as expected,
and the 45s poll of `GET /api/v1/audit/tenant/{tenant_id}` will time out
waiting for a row that never lands with the expected shape — that's a
platform-version mismatch, not a bug in this test.

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

Exits non-zero if `caller_name`, the `tool_type` fallback, or the
neither-supplied `"unknown"` default does not reach
`policy_details.caller_name` on the real row.

## Companion unit coverage

`tests/test_audit_tool_call.py` exercises the same surface through
`httpx_mock` for the wire-body shape (`caller_name` alone, `tool_type` alone,
both together, both omitted). This runtime proof is the real-stack
confirmation the `runtime-e2e/` DoD gate requires.
