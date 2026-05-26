"""Indonesia Compliance Example.

Demonstrates Indonesian PII detection (NIK), audit log querying with
cross-border data transfer fields, and policy filtering by the
pii-indonesia category.

Requirements:
    pip install axonflow
    export AXONFLOW_AGENT_URL=http://localhost:8080
    export AXONFLOW_CLIENT_ID=your-client-id
    export AXONFLOW_CLIENT_SECRET=your-client-secret
"""

from __future__ import annotations

import asyncio
import os

from axonflow import AxonFlow
from axonflow.exceptions import AxonFlowError
from axonflow.policies import PolicyCategory


async def main() -> None:
    endpoint = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")
    client_id = os.environ.get("AXONFLOW_CLIENT_ID", "")
    client_secret = os.environ.get("AXONFLOW_CLIENT_SECRET", "")

    msg = "AXONFLOW_CLIENT_ID and AXONFLOW_CLIENT_SECRET must be set"
    if not client_id or not client_secret:
        raise SystemExit(msg)

    client = AxonFlow(
        endpoint=endpoint,
        client_id=client_id,
        client_secret=client_secret,
    )

    print("=== Indonesia Compliance Example ===\n")

    # 1. Verify PII Indonesia category constant
    print(f"PII Indonesia category: {PolicyCategory.PII_INDONESIA.value}")

    # 2. Send a request containing an Indonesian NIK
    print("\nSending governed request with NIK...")
    try:
        resp = await client.proxy_llm_call(
            user_token="",
            query="Customer NIK is 3204110507900003 and their name is Budi Santoso",
            request_type="chat",
            context={"purpose": "identity_verification"},
        )
        print(f"Response blocked: {resp.blocked}")
        if resp.policy_info:
            print(f"Policies evaluated: {resp.policy_info.policies_evaluated}")
    except AxonFlowError as e:
        print(f"Request error (expected if no LLM configured): {e}")

    # 3. Query audit logs to demonstrate cross-border fields
    print("\nQuerying audit logs...")
    try:
        from axonflow.types import AuditSearchRequest

        audit_resp = await client.search_audit_logs(
            AuditSearchRequest(limit=5),
        )
        print(f"Found {len(audit_resp.entries)} audit entries")
        for entry in audit_resp.entries:
            line = f"  [{entry.timestamp}] type={entry.request_type} blocked={entry.blocked}"
            if getattr(entry, "data_residency", None):
                line += f" residency={entry.data_residency}"
            if getattr(entry, "transfer_basis", None):
                line += f" basis={entry.transfer_basis}"
            print(line)
    except AxonFlowError as e:
        print(f"Audit search error: {e}")

    # 4. List policies filtered by Indonesia PII category
    print("\nListing Indonesia PII policies...")
    try:
        from axonflow.policies import ListStaticPoliciesOptions

        policies = await client.list_static_policies(
            ListStaticPoliciesOptions(category=PolicyCategory.PII_INDONESIA),
        )
        print(f"Found {len(policies)} Indonesia PII policies")
        for p in policies:
            print(f"  {p.name}: {p.description}")
    except AxonFlowError as e:
        print(f"Policy list error: {e}")

    print("\n=== Done ===")


if __name__ == "__main__":
    asyncio.run(main())
