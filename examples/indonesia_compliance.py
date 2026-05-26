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
from axonflow.policies import PolicyCategory


async def main() -> None:
    agent_url = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")
    client_id = os.environ.get("AXONFLOW_CLIENT_ID", "")
    client_secret = os.environ.get("AXONFLOW_CLIENT_SECRET", "")

    if not client_id or not client_secret:
        raise SystemExit("AXONFLOW_CLIENT_ID and AXONFLOW_CLIENT_SECRET must be set")

    client = AxonFlow(
        agent_url=agent_url,
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
    except Exception as e:
        print(f"Request error (expected if no LLM configured): {e}")

    # 3. Query audit logs to demonstrate cross-border fields
    print("\nQuerying audit logs...")
    try:
        audit_resp = await client.search_audit_logs(limit=5)
        print(f"Found {len(audit_resp.entries)} audit entries")
        for entry in audit_resp.entries:
            line = f"  [{entry.timestamp}] type={entry.request_type} blocked={entry.blocked}"
            if entry.data_residency:
                line += f" residency={entry.data_residency}"
            if entry.transfer_basis:
                line += f" basis={entry.transfer_basis}"
            print(line)
    except Exception as e:
        print(f"Audit search error: {e}")

    # 4. List policies filtered by Indonesia PII category
    print("\nListing Indonesia PII policies...")
    try:
        policies = await client.list_static_policies(
            category=PolicyCategory.PII_INDONESIA,
        )
        print(f"Found {len(policies)} Indonesia PII policies")
        for p in policies:
            print(f"  {p.name}: {p.description} (severity={p.severity}, action={p.action})")
    except Exception as e:
        print(f"Policy list error: {e}")

    print("\n=== Done ===")


if __name__ == "__main__":
    asyncio.run(main())
