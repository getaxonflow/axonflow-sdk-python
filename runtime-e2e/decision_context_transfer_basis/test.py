"""Real-stack assertion for the v8.4.0 SDK surface (platform epic #2508).

Drives the production code path against a real running AxonFlow agent — no
test doubles — and asserts:

  * ``DecisionSummary.context`` / ``DecisionExplanation.context`` surface the
    sanitized request context a PEP attaches to a Decision Mode call. We act as
    the PEP via a raw ``POST /api/v1/decide`` (that endpoint is intentionally
    not SDK-wrapped per ADR-056), then read the decision back through the SDK's
    ``list_decisions`` + ``explain_decision`` and confirm ``context`` is
    populated with the forwarded keys.
  * ``AuditLogEntry.transfer_basis = "pasal_56b_dpa"`` (Pasal 56(b) explicit DPA
    tag) round-trips through serialize → deserialize verbatim.

Usage::

    export AXONFLOW_AGENT_URL=http://localhost:8080
    export AXONFLOW_TENANT_ID=buku-e-py-e2e
    export AXONFLOW_TENANT_SECRET=buku-e-secret
    python runtime-e2e/decision_context_transfer_basis/test.py

Exits non-zero if the SDK does not surface the new fields. Companion
mock-free unit coverage lives in ``tests/test_decisions.py`` +
``tests/test_indonesia_pii_audit.py``.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys

import httpx

from axonflow import AxonFlow
from axonflow.decisions import ListDecisionsOptions
from axonflow.types import TRANSFER_BASIS_PASAL_56B_DPA, AuditLogEntry

AGENT_URL = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")
CLIENT_ID = os.environ.get("AXONFLOW_TENANT_ID", "buku-e-py-e2e")
SECRET = os.environ.get("AXONFLOW_TENANT_SECRET", "buku-e-secret")

WANT_CONTEXT = {
    "x_ai_agent": "refund-bot",
    "x_session_id": "sess-buku-42",
    "x_leader_identity": "ops-lead",
}


def _fail(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def create_decision_with_context() -> str:
    """Act as the PEP: the request context lives in the body's ``context`` map."""
    auth = base64.b64encode(f"{CLIENT_ID}:{SECRET}".encode()).decode()
    body = {
        "stage": "llm",
        "query": "summarize this support ticket",
        "target": {"type": "llm", "model": "gpt-4", "provider": "openai"},
        "context": {
            "x-ai-agent": "refund-bot",
            "x-session-id": "sess-buku-42",
            "x-leader-identity": "ops-lead",
        },
    }
    resp = httpx.post(
        f"{AGENT_URL}/api/v1/decide",
        json=body,
        headers={"X-Client-ID": CLIENT_ID, "Authorization": f"Basic {auth}"},
        timeout=15.0,
    )
    if resp.status_code != 200:
        _fail(f"decide HTTP {resp.status_code}: {resp.text}")
    print(f"server /decide response: {resp.text}")
    decision_id = resp.json().get("decision_id")
    if not decision_id:
        _fail(f"no decision_id in response: {resp.text}")
    return decision_id


async def main() -> None:
    decision_id = create_decision_with_context()
    print(f"PEP decide -> decision_id={decision_id}")

    async with AxonFlow(endpoint=AGENT_URL, client_id=CLIENT_ID, client_secret=SECRET) as client:
        rows = await client.list_decisions(ListDecisionsOptions(limit=5))
        found = next((r for r in rows if r.decision_id == decision_id), None)
        if found is None:
            _fail(f"list_decisions did not return {decision_id} (got {len(rows)} rows)")
        print(f"SDK list_decisions -> {json.dumps(found.model_dump(mode='json'))}")
        if found.context != WANT_CONTEXT:
            _fail(f"list_decisions context = {found.context}, want {WANT_CONTEXT}")
        print(
            f"PASS: list_decisions DecisionSummary.context populated "
            f"with {len(found.context)} PEP-forwarded keys"
        )

        exp = await client.explain_decision(decision_id)
        print(
            f"SDK explain_decision -> context={json.dumps(exp.context)} "
            f"context_truncated={exp.context_truncated}"
        )
        if exp.context != WANT_CONTEXT:
            _fail(f"explain_decision context = {exp.context}, want {WANT_CONTEXT}")
        print(
            f"PASS: explain_decision returned full context "
            f"(context_truncated={exp.context_truncated})"
        )

    # transfer_basis = pasal_56b_dpa round-trip (Pasal 56(b)).
    entry = AuditLogEntry(
        id="e2e-audit",
        timestamp="2026-05-30T10:00:00Z",
        data_residency="ID",
        transfer_basis=TRANSFER_BASIS_PASAL_56B_DPA,
    )
    restored = AuditLogEntry.model_validate_json(entry.model_dump_json())
    if restored.transfer_basis != "pasal_56b_dpa":
        _fail(f"transfer_basis round-trip = {restored.transfer_basis!r}, want pasal_56b_dpa")
    print(f"SDK AuditLogEntry round-trip -> {entry.model_dump_json()}")
    print(f"PASS: AuditLogEntry.transfer_basis = {restored.transfer_basis!r} round-trips verbatim")

    print("ALL PASS: v8.4.0 context + pasal_56b_dpa verified through SDK runtime")


if __name__ == "__main__":
    asyncio.run(main())
