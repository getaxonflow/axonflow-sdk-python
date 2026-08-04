"""Real-stack assertion: the audit read model parses the REAL 9.x wire
shape (getaxonflow/axonflow-enterprise#3254 additive interim).

The orchestrator's audit_logger.go AuditEntry has never served
query_summary/success/blocked/risk_score/latency_ms/policy_violations/
metadata on the 9.x line - the SDK's pre-#3254 model was built to spec
fiction and silently parsed real audit rows into zero-values. This test
drives the real SDK end to end against a real running agent:

  1. Write two fresh audit rows via `client.audit_tool_call` (one
     success-shaped, one failure-shaped) with a per-run tool-name nonce.
  2. Poll `client.search_audit_logs` (real POST /api/v1/audit/search
     through the agent proxy) until both rows land (the orchestrator's
     AuditLogger batches writes, flush every 10s).
  3. Assert on the TYPED AuditLogEntry: `policy_decision` is populated
     ("allowed" on the success row, "error" on the failure row - the
     verdict set is open), `policy_details` carries the tool name and
     error message, `response_time_ms` parses; while the seven
     deprecated fiction fields stay at their defaults on every row.
  4. Prove the new `action` search filter is READ server-side: a search
     for the failure row's verdict returns only entries with that
     verdict, including our row.
  5. Prove the `request_type` deprecation claim: a search filtered ONLY
     by a nonsense request_type returns unfiltered results (the 9.x
     server does not read the filter - silent no-op).

Usage::

    export AXONFLOW_AGENT_URL=http://localhost:8080
    export AXONFLOW_TENANT_ID=<client id>        # e.g. demo-client
    export AXONFLOW_TENANT_SECRET=<secret>       # e.g. demo-secret
    python runtime-e2e/audit_real_wire_fields/test.py

Community-mode note: audit reads are tenant-scoped to "community" while
tool-call writes through the agent proxy land under the same scope, so
write and read agree with any registered credential. See ../README.md.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import uuid

from axonflow import AxonFlow
from axonflow.types import AuditLogEntry, AuditSearchRequest, AuditToolCallRequest

AGENT_URL = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")
CLIENT_ID = os.environ.get("AXONFLOW_TENANT_ID", "demo-client")
SECRET = os.environ.get("AXONFLOW_TENANT_SECRET", "demo-secret")

_RUN_ID = uuid.uuid4().hex[:12]
OK_TOOL = f"e2e-real-wire-ok-{_RUN_ID}"
FAIL_TOOL = f"e2e-real-wire-fail-{_RUN_ID}"
FAIL_ERROR = f"e2e-real-wire-error-{_RUN_ID}"

# The orchestrator's AuditLogger batches writes (flush every 10s); give
# generous headroom under CI/local load.
POLL_DEADLINE_SECONDS = 45.0
POLL_INTERVAL_SECONDS = 2.0
SEARCH_LIMIT = 100


def _fail(msg: str) -> None:
    sys.stderr.write(f"FAIL: {msg}\n")
    sys.exit(1)


def _tool_name(entry: AuditLogEntry) -> str:
    return str(entry.policy_details.get("tool_name", ""))


async def _poll_for_rows(client: AxonFlow) -> tuple[AuditLogEntry, AuditLogEntry]:
    deadline = time.monotonic() + POLL_DEADLINE_SECONDS
    ok_row = fail_row = None
    while time.monotonic() < deadline:
        result = await client.search_audit_logs(AuditSearchRequest(limit=SEARCH_LIMIT))
        for entry in result.entries:
            if _tool_name(entry) == OK_TOOL:
                ok_row = entry
            elif _tool_name(entry) == FAIL_TOOL:
                fail_row = entry
        if ok_row is not None and fail_row is not None:
            return ok_row, fail_row
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    _fail(
        f"rows did not land within {POLL_DEADLINE_SECONDS}s "
        f"(ok={ok_row is not None} fail={fail_row is not None}); "
        "is the orchestrator's audit batch writer running?"
    )
    raise AssertionError  # unreachable; _fail exits


def _assert_fiction_fields_default(entry: AuditLogEntry, label: str) -> None:
    """The seven deprecated fields are never served on 9.x - they must
    stay at their model defaults on a REAL row."""
    checks = [
        ("query_summary", entry.query_summary, ""),
        ("success", entry.success, True),
        ("blocked", entry.blocked, False),
        ("risk_score", entry.risk_score, 0.0),
        ("latency_ms", entry.latency_ms, 0),
        ("policy_violations", entry.policy_violations, []),
        ("metadata", entry.metadata, {}),
    ]
    for name, got, want in checks:
        if got != want:
            _fail(f"{label}: fiction field {name} = {got!r}, expected default {want!r}")


async def main() -> int:
    async with AxonFlow(
        endpoint=AGENT_URL,
        client_id=CLIENT_ID,
        client_secret=SECRET,
    ) as client:
        # 1. Write one success-shaped and one failure-shaped row.
        ok_write = await client.audit_tool_call(
            AuditToolCallRequest(
                tool_name=OK_TOOL,
                caller_name="sdk-python-runtime-e2e",
                success=True,
                duration_ms=12,
            )
        )
        fail_write = await client.audit_tool_call(
            AuditToolCallRequest(
                tool_name=FAIL_TOOL,
                caller_name="sdk-python-runtime-e2e",
                success=False,
                error_message=FAIL_ERROR,
            )
        )
        print(f"wrote rows: ok={ok_write.audit_id} fail={fail_write.audit_id}")

        # 2. Poll the real search endpoint until both rows land.
        ok_row, fail_row = await _poll_for_rows(client)

        # 3. Typed assertions on the real wire shape.
        if ok_row.policy_decision != "allowed":
            _fail(f"success row policy_decision = {ok_row.policy_decision!r}, want 'allowed'")
        if fail_row.policy_decision != "error":
            _fail(f"failure row policy_decision = {fail_row.policy_decision!r}, want 'error'")
        if fail_row.policy_details.get("error_message") != FAIL_ERROR:
            _fail(
                "failure row policy_details.error_message = "
                f"{fail_row.policy_details.get('error_message')!r}, want {FAIL_ERROR!r}"
            )
        if not isinstance(ok_row.response_time_ms, int) or ok_row.response_time_ms < 0:
            _fail(f"response_time_ms did not parse: {ok_row.response_time_ms!r}")
        _assert_fiction_fields_default(ok_row, "success row")
        _assert_fiction_fields_default(fail_row, "failure row")
        print(
            f"typed parse OK: ok.policy_decision={ok_row.policy_decision!r} "
            f"fail.policy_decision={fail_row.policy_decision!r} "
            "fiction fields at defaults on both rows"
        )

        # 4. The action filter is read server-side.
        filtered = await client.search_audit_logs(
            AuditSearchRequest(action="error", limit=SEARCH_LIMIT)
        )
        wrong = [e.policy_decision for e in filtered.entries if e.policy_decision != "error"]
        if wrong:
            _fail(f"action='error' returned non-error verdicts: {wrong}")
        if not any(_tool_name(e) == FAIL_TOOL for e in filtered.entries):
            _fail("action='error' did not return this run's failure row")
        print(f"action filter OK: {len(filtered.entries)} entries, all verdict 'error'")

        # 5. request_type is a server-side no-op (#3254 deprecation claim).
        noop = await client.search_audit_logs(
            AuditSearchRequest(request_type=f"nonexistent-type-{_RUN_ID}", limit=SEARCH_LIMIT)
        )
        if not any(_tool_name(e) == OK_TOOL for e in noop.entries):
            _fail(
                "request_type=<nonsense> filtered rows out - the server appears to "
                "read request_type after all; re-check the #3254 deprecation"
            )
        print(
            f"request_type no-op confirmed: nonsense filter still returned "
            f"{len(noop.entries)} rows including this run's"
        )

        print("PASS: audit_real_wire_fields")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
