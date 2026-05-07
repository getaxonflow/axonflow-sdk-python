"""Example: explain a previously-made AxonFlow policy decision.

Implements the ADR-043 explainability flow. Given a decision_id (typically
surfaced on the response of a blocked governed call, an audit_logs row, or
the ``explain_decision`` MCP tool), this example fetches the structured
explanation and renders the matched policies, risk level, and override
availability.

Required env vars:

* ``AXONFLOW_AGENT_URL``       (default: http://localhost:8080)
* ``AXONFLOW_CLIENT_ID``       (default: community)
* ``AXONFLOW_CLIENT_SECRET``   (default: empty)
* ``AXONFLOW_DECISION_ID``     the decision to explain

Get a decision_id quickly by hitting a known-blocked policy::

    curl -u "$AXONFLOW_CLIENT_ID:$AXONFLOW_CLIENT_SECRET" \\
         -X POST $AXONFLOW_AGENT_URL/api/v1/mcp/check-input \\
         -H 'Content-Type: application/json' \\
         -d '{"connector_type":"postgres","operation":"execute",
              "statement":"SELECT 1; DROP TABLE users;--","user_token":"u1"}'

then read decision_id from the block response or the most recent audit row.
"""

import asyncio
import os
import sys

from axonflow import AxonFlow


async def main() -> None:
    decision_id = os.environ.get("AXONFLOW_DECISION_ID", "")
    if not decision_id:
        print(
            "AXONFLOW_DECISION_ID must be set "
            "(a decision_id from a recent blocked call)",
            file=sys.stderr,
        )
        sys.exit(2)

    endpoint = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")
    print(f"Initializing AxonFlow client at {endpoint}...")
    async with AxonFlow(
        endpoint=endpoint,
        client_id=os.environ.get("AXONFLOW_CLIENT_ID", "community"),
        client_secret=os.environ.get("AXONFLOW_CLIENT_SECRET", ""),
    ) as client:
        print(f"Explaining decision {decision_id}...\n")
        exp = await client.explain_decision(decision_id)

        print("=== Decision Explanation ===")
        print(f"  decision_id: {exp.decision_id}")
        print(f"  timestamp:   {exp.timestamp.isoformat()}")
        print(f"  decision:    {exp.decision}")
        print(f"  reason:      {exp.reason}")
        if exp.risk_level:
            print(f"  risk_level:  {exp.risk_level}")
        if exp.tool_signature:
            print(f"  tool:        {exp.tool_signature}")

        print(f"\n  policy_matches ({len(exp.policy_matches)}):")
        for i, m in enumerate(exp.policy_matches):
            print(
                f"    [{i}] {m.policy_id} ({m.policy_name or '(unnamed)'}) — "
                f"action={m.action or '-'} risk={m.risk_level or '-'} "
                f"allow_override={m.allow_override}"
            )

        if exp.matched_rules:
            print(f"\n  matched_rules ({len(exp.matched_rules)}):")
            for r in exp.matched_rules:
                print(
                    f"    {r.policy_id} on {r.rule_id or '(no rule id)'}: "
                    f"matched={r.matched_on or '-'}"
                )

        print(f"\n  override_available:           {exp.override_available}")
        if exp.override_existing_id:
            print(f"  override_existing_id:         {exp.override_existing_id}")
        print(
            f"  historical_hit_count_session: "
            f"{exp.historical_hit_count_session}"
        )
        if exp.policy_source_link:
            print(f"  policy_source_link:           {exp.policy_source_link}")


if __name__ == "__main__":
    asyncio.run(main())
