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
* ``AXONFLOW_USER_TOKEN``      the PER-USER identity this read is scoped to
  (required on an enterprise stack — see below)

Optional:

* ``AXONFLOW_DECISION_ID``     the decision to explain. When unset this example
  asks the platform for the most recent decision THIS identity can see.

Why ``AXONFLOW_USER_TOKEN`` is not optional here (platform #2922)
----------------------------------------------------------------

``client_id``/``client_secret`` say which ORGANIZATION is asking. Explain
answers from WHO is asking. On an enterprise stack a developer or viewer
explains only their own decisions, a tenant-wide role (admin/owner/policy_admin)
explains the whole tenant, and a caller presenting NO identity explains NOTHING
— the endpoint answers not-found for every id, including ids that plainly
exist. That is why this example failed on every enterprise stack until the SDK
grew a read-path identity: it was asking anonymously.

Mint one the way the E2E workflow does::

    export AXONFLOW_USER_TOKEN=$(./scripts/generate-jwt.sh --kind user \
        --email dev@acme.com --org-id "$AXONFLOW_CLIENT_ID" --role developer --quiet)

``./scripts/setup-e2e-testing.sh`` already exports exactly this variable.
Community deployments are single-operator and need none of it.

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
from axonflow.decisions import ListDecisionsOptions
from axonflow.read_identity import ReadScopeError


def _scope_hint(err: ReadScopeError) -> str:
    """The sentence a reader of this example actually needs.

    Without it the distinct causes behind "not found" arrive looking identical.
    """
    if err.identity_missing:
        return (
            "\n  -> This read presented no per-user identity the platform could resolve, so it "
            "returned nothing by construction. Set AXONFLOW_USER_TOKEN (see the module docstring) "
            "— and check the address is not in a reserved domain."
        )
    return (
        "\n  -> The identity in AXONFLOW_USER_TOKEN is scoped to its own rows and this decision "
        "is not among them. Use an admin, owner or policy_admin token to read the whole tenant."
    )


async def _most_recent_visible(client: AxonFlow) -> str:
    """The newest decision this identity can see, or exit with the reason."""
    print("AXONFLOW_DECISION_ID is unset - looking up the most recent visible decision...")
    try:
        recent = await client.list_decisions(ListDecisionsOptions(limit=1))
    except ReadScopeError as err:
        print(f"could not find a decision to explain: {err}{_scope_hint(err)}", file=sys.stderr)
        sys.exit(1)
    if not recent:
        print(
            "no decisions are visible to this identity yet - make a governed call first "
            "(see the curl in the module docstring), then re-run",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"  using decision_id={recent[0].decision_id}")
    return recent[0].decision_id


async def main() -> None:
    decision_id = os.environ.get("AXONFLOW_DECISION_ID", "")
    user_token = os.environ.get("AXONFLOW_USER_TOKEN", "")

    endpoint = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")
    print(f"Initializing AxonFlow client at {endpoint}...")
    if not user_token:
        print(
            "note: AXONFLOW_USER_TOKEN is unset - this read is unscoped. On an enterprise stack "
            "it will explain nothing; see the module docstring."
        )
    async with AxonFlow(
        endpoint=endpoint,
        client_id=os.environ.get("AXONFLOW_CLIENT_ID", "community"),
        client_secret=os.environ.get("AXONFLOW_CLIENT_SECRET", ""),
        # The read-path identity. Empty is legal and means "ask anonymously",
        # which on an enterprise stack explains nothing.
        user_token=user_token or None,
    ) as client:
        # No id given: ask for one this identity can actually see, so the
        # example explains a real decision rather than failing on a placeholder.
        if not decision_id:
            decision_id = await _most_recent_visible(client)

        print(f"Explaining decision {decision_id}...\n")
        try:
            exp = await client.explain_decision(decision_id)
        except ReadScopeError as err:
            print(f"explain_decision failed: {err}{_scope_hint(err)}", file=sys.stderr)
            sys.exit(1)

        # An explanation that came back without the id it was asked about is
        # not an explanation - fail loudly rather than print an empty report.
        if not exp.decision_id:
            print(
                f"the platform returned an explanation with no decision_id for {decision_id}",
                file=sys.stderr,
            )
            sys.exit(1)

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
        print(f"  historical_hit_count_session: {exp.historical_hit_count_session}")
        if exp.policy_source_link:
            print(f"  policy_source_link:           {exp.policy_source_link}")


if __name__ == "__main__":
    asyncio.run(main())
