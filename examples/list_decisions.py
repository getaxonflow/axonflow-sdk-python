"""Example: list the recent AxonFlow policy decisions VISIBLE TO THE CALLER.

Implements the GET /api/v1/decisions contract — companion to the
explain_decision flow. Returns the slim DecisionSummary page; tier-cap
429s surface as RateLimitError carrying the V1 upgrade envelope.

Whose decisions come back (platform #2922)
------------------------------------------

Not the tenant's — the caller's. On an enterprise stack a tenant-wide role
(admin/owner/policy_admin) lists the whole tenant, any other identity lists only
its own rows, and a caller presenting NO identity lists nothing whatsoever. That
last case used to look exactly like a quiet tenant; the SDK now refuses it as a
ReadScopeError instead of reporting an empty page as data.

Mint an identity the way the E2E workflow does::

    export AXONFLOW_USER_TOKEN=$(./scripts/generate-jwt.sh --kind user \
        --email dev@acme.com --org-id "$AXONFLOW_CLIENT_ID" --role developer --quiet)

``./scripts/setup-e2e-testing.sh`` already exports exactly this variable.
Community deployments are single-operator and need none of it.

Required env vars:

  AXONFLOW_AGENT_URL          (default: http://localhost:8080)
  AXONFLOW_CLIENT_ID
  AXONFLOW_CLIENT_SECRET
  AXONFLOW_USER_TOKEN         the per-user identity to scope the read to
                              (required on an enterprise stack)

Optional filters:

  AXONFLOW_LIST_DECISION       allowed|blocked|redacted|needs_approval|error
                               (canonical audit verdicts, platform 9.0.0+;
                               pre-9.0.0 allow|deny|require_approval now 400)
  AXONFLOW_LIST_POLICY_ID      e.g. sys_sqli_stacked_drop
  AXONFLOW_LIST_LIMIT          integer (server-capped per tier)
"""

from __future__ import annotations

import asyncio
import os
import sys

from axonflow.client import AxonFlow
from axonflow.decisions import ListDecisionsOptions
from axonflow.exceptions import RateLimitError
from axonflow.read_identity import ReadScopeError


async def main() -> int:
    endpoint = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")
    client_id = os.environ.get("AXONFLOW_CLIENT_ID")
    client_secret = os.environ.get("AXONFLOW_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("AXONFLOW_CLIENT_ID and AXONFLOW_CLIENT_SECRET must be set", file=sys.stderr)
        return 1

    opts_kwargs: dict = {}
    if d := os.environ.get("AXONFLOW_LIST_DECISION"):
        opts_kwargs["decision"] = d
    if p := os.environ.get("AXONFLOW_LIST_POLICY_ID"):
        opts_kwargs["policy_id"] = p
    if lim := os.environ.get("AXONFLOW_LIST_LIMIT"):
        opts_kwargs["limit"] = int(lim)
    opts = ListDecisionsOptions(**opts_kwargs) if opts_kwargs else None

    client = AxonFlow(
        endpoint=endpoint,
        client_id=client_id,
        client_secret=client_secret,
        # The read-path identity this listing is scoped to. See the module
        # docstring: leaving it unset against an enterprise stack is what made
        # this example report a confident, empty page.
        user_token=os.environ.get("AXONFLOW_USER_TOKEN") or None,
    )

    try:
        decisions = await client.list_decisions(opts)
    except ReadScopeError as err:
        if not err.identity_missing:
            raise
        print("=== This read was unscoped ===", file=sys.stderr)
        print(f"  {err}\n", file=sys.stderr)
        print(
            "  The platform returned zero rows because it resolved no identity to scope on,",
            file=sys.stderr,
        )
        print(
            "  not because your tenant has no decisions. Set AXONFLOW_USER_TOKEN:", file=sys.stderr
        )
        print(
            "    export AXONFLOW_USER_TOKEN=$(./scripts/generate-jwt.sh --kind user \\",
            file=sys.stderr,
        )
        print(
            '        --email dev@acme.com --org-id "$AXONFLOW_CLIENT_ID" --role developer --quiet)',
            file=sys.stderr,
        )
        return 3
    except RateLimitError as rle:
        print(f"=== Tier limit reached ({rle.limit_type}) ===", file=sys.stderr)
        print(f"  current tier: {rle.tier}", file=sys.stderr)
        print(f"  limit:        {rle.limit}", file=sys.stderr)
        print(f"  reason:       {rle.message}", file=sys.stderr)
        if rle.upgrade is not None:
            print(file=sys.stderr)
            print(
                f"  upgrade to {rle.upgrade.tier}: {rle.upgrade.wording}",
                file=sys.stderr,
            )
            print(f"    compare:    {rle.upgrade.compare_url}", file=sys.stderr)
            print(f"    buy:        {rle.upgrade.buy_url}", file=sys.stderr)
        return 2

    print(f"=== Recent decisions ({len(decisions)}) ===")
    for d in decisions:
        policy = d.policy_id or "-"
        tool = d.tool_signature or "-"
        print(
            f"  {d.timestamp.isoformat()} {d.decision:18s} "
            f"{d.decision_id} policy={policy} tool={tool}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
