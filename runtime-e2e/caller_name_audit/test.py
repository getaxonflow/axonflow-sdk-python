"""Real-stack assertion: AuditToolCallRequest.caller_name reaches
policy_details.caller_name on the audit_logs row (#2912, sub-issue of
epic #2905).

getaxonflow/axonflow-enterprise PR #2953 added a `caller_name` field to
the orchestrator's tool-call audit path alongside the legacy `tool_type`
field (kept as a deprecated input fallback). The server resolves caller
identity as: caller_name if supplied -> legacy tool_type if supplied ->
a default. This test drives the real SDK's `client.audit_tool_call`
against a real running agent+orchestrator and proves the new field
actually lands in `policy_details.caller_name` on the persisted
audit_logs row -- not just that it marshals correctly onto the wire
(that's covered by the httpx_mock-based unit tests in
tests/test_audit_tool_call.py).

Sister proof to the Go SDK's runtime-e2e/caller_name_audit/main.go.

Steps:
  1. Call the real SDK's `audit_tool_call` with `caller_name` set to a
     distinctive, non-default value and a unique `workflow_id` so we can
     find the resulting row.
  2. Call it again with only the legacy `tool_type` set (no `caller_name`)
     to prove the backward-compat fallback still resolves into
     `policy_details.caller_name` on a real row.
  3. The orchestrator's AuditLogger batches writes (flush every 10s), so
     poll `GET /api/v1/audit/tenant/{tenant_id}` (the same route
     `client.get_audit_logs_by_tenant` hits) until each row lands.
  4. The SDK's typed `AuditLogEntry` does not surface `policy_details`
     (it's an internal JSONB blob), so this step reads the raw JSON body
     directly -- the only way to observe `policy_details.caller_name`
     from outside the platform -- and asserts it equals what we sent.

Enterprise-mode note: the tenant-audit read endpoint scopes non-tenant-wide
callers to their own `user_email` (#2922). This test trust-gates a
distinctive `X-User-Email` on every SDK request via a small httpx patch (the
same monkeypatch-the-real-transport technique used by
`runtime-e2e/x-client-id/test.py`) so the write and the read agree on
identity. This requires `AXONFLOW_TRUST_IDENTITY_HEADERS=true` on the
platform (already the case for the local-dev Enterprise stack); in
Community mode reads are tenant-wide regardless, so this is a no-op there.

Usage::

    export AXONFLOW_AGENT_URL=http://localhost:8080
    export AXONFLOW_TENANT_ID=local-dev-org
    export AXONFLOW_TENANT_SECRET=<your local AXONFLOW_CLIENT_SECRET>
    python runtime-e2e/caller_name_audit/test.py

See ../README.md for how to register/obtain a tenant against a local stack.
"""

from __future__ import annotations

import asyncio
import base64
import os
import sys
import time
import uuid

import httpx

from axonflow import AxonFlow
from axonflow.types import AuditToolCallRequest

AGENT_URL = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")
CLIENT_ID = os.environ.get("AXONFLOW_TENANT_ID", "buku-e-py-e2e")
SECRET = os.environ.get("AXONFLOW_TENANT_SECRET", "buku-e-secret")

# Distinctive per-run identity so the write and the read agree on scope
# under Enterprise-mode's role-scoped audit reads (#2922), and so repeated
# runs never collide on a stale row.
_RUN_ID = uuid.uuid4().hex[:12]
SCOPE_EMAIL = f"e2e-caller-name-{_RUN_ID}@runtime-e2e.axonflow.test"

WANT_CALLER_NAME = f"e2e-caller-name-probe-{_RUN_ID}"
WORKFLOW_CALLER_NAME = f"e2e-caller-name-wf-{_RUN_ID}"
WORKFLOW_TOOL_TYPE_FALLBACK = f"e2e-tool-type-fallback-wf-{_RUN_ID}"
WANT_TOOL_TYPE_FALLBACK = "mcp"

# Batch writer flushes every 10s (platform/orchestrator/audit_logger.go);
# give it generous headroom under CI/local load.
POLL_DEADLINE_SECONDS = 45.0
POLL_INTERVAL_SECONDS = 2.0

# Patch the real httpx transport (not a mock -- we still send real
# requests over the real wire) so every SDK request carries a trust-gated
# X-User-Email identifying this test run.
_orig_request = httpx.AsyncClient.request


async def _patched_request(self, method, url, **kw):
    headers = dict(kw.get("headers") or {})
    headers["X-User-Email"] = SCOPE_EMAIL
    kw["headers"] = headers
    return await _orig_request(self, method, url, **kw)


httpx.AsyncClient.request = _patched_request


def _fail(msg: str) -> None:
    sys.stderr.write(f"FAIL: {msg}\n")
    sys.exit(1)


def _fetch_policy_details(workflow_id: str) -> dict[str, object]:
    """Poll GET /api/v1/audit/tenant/{tenant_id} for the row with this
    workflow_id (audit_logs.request_id) and return its raw policy_details.

    Uses raw httpx (not the SDK) because AuditLogEntry does not declare a
    policy_details field -- it's the only way to observe it from outside
    the platform.
    """
    auth = base64.b64encode(f"{CLIENT_ID}:{SECRET}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth}",
        "X-User-Email": SCOPE_EMAIL,
    }
    deadline = time.monotonic() + POLL_DEADLINE_SECONDS
    last_seen_total = None
    while True:
        resp = httpx.get(
            f"{AGENT_URL}/api/v1/audit/tenant/{CLIENT_ID}?limit=50",
            headers=headers,
            timeout=15.0,
        )
        if resp.status_code != 200:  # noqa: PLR2004
            _fail(f"GET /api/v1/audit/tenant/{CLIENT_ID} HTTP {resp.status_code}: {resp.text}")
        body = resp.json()
        last_seen_total = body.get("total")
        for entry in body.get("entries", []):
            if entry.get("request_id") == workflow_id:
                return entry.get("policy_details") or {}
        if time.monotonic() > deadline:
            _fail(
                f"audit row for workflow_id={workflow_id!r} did not appear within "
                f"{POLL_DEADLINE_SECONDS}s (batch flush may not have fired; "
                f"last seen total={last_seen_total})"
            )
        time.sleep(POLL_INTERVAL_SECONDS)


async def main() -> None:
    async with AxonFlow(endpoint=AGENT_URL, client_id=CLIENT_ID, client_secret=SECRET) as client:
        # 1. caller_name reaches policy_details.caller_name verbatim.
        resp1 = await client.audit_tool_call(
            AuditToolCallRequest(
                tool_name="e2eCallerNameTool",
                caller_name=WANT_CALLER_NAME,
                workflow_id=WORKFLOW_CALLER_NAME,
            )
        )
        print(
            f"SDK audit_tool_call (caller_name) -> audit_id={resp1.audit_id} status={resp1.status}"
        )

        # 2. Legacy tool_type-only (no caller_name) still resolves into
        # policy_details.caller_name -- the deprecated backward-compat path.
        resp2 = await client.audit_tool_call(
            AuditToolCallRequest(
                tool_name="e2eToolTypeFallbackTool",
                tool_type=WANT_TOOL_TYPE_FALLBACK,
                workflow_id=WORKFLOW_TOOL_TYPE_FALLBACK,
            )
        )
        print(
            f"SDK audit_tool_call (tool_type fallback) -> audit_id={resp2.audit_id} status={resp2.status}"
        )

    policy_details_1 = _fetch_policy_details(WORKFLOW_CALLER_NAME)
    got_caller_name = policy_details_1.get("caller_name")
    if got_caller_name != WANT_CALLER_NAME:
        _fail(
            f"policy_details.caller_name = {got_caller_name!r}, want {WANT_CALLER_NAME!r} "
            f"(full policy_details: {policy_details_1})"
        )
    print(
        f"PASS: policy_details.caller_name = {got_caller_name!r} reached the audit_logs "
        f"row via the real agent+orchestrator stack"
    )
    print(f"Wire policy_details (caller_name case): {policy_details_1}")

    policy_details_2 = _fetch_policy_details(WORKFLOW_TOOL_TYPE_FALLBACK)
    got_fallback = policy_details_2.get("caller_name")
    if got_fallback != WANT_TOOL_TYPE_FALLBACK:
        _fail(
            f"backward-compat: policy_details.caller_name = {got_fallback!r} "
            f"(from legacy tool_type={WANT_TOOL_TYPE_FALLBACK!r}), want {WANT_TOOL_TYPE_FALLBACK!r} "
            f"(full policy_details: {policy_details_2})"
        )
    print(
        f"PASS: legacy tool_type={WANT_TOOL_TYPE_FALLBACK!r} still resolves into "
        f"policy_details.caller_name = {got_fallback!r} (deprecated fallback intact)"
    )
    print(f"Wire policy_details (tool_type fallback case): {policy_details_2}")

    print("ALL PASS: caller_name (#2912) verified end-to-end through the real SDK + platform")


if __name__ == "__main__":
    asyncio.run(main())
