"""Real-stack assertion: RegistrySummary's real wire fields (#3254
pin-advance batch) parse from a live masfeat registry-summary response.

Drives the real SDK's `masfeat_get_registry_summary()` against a real
running agent and asserts the TYPED dataclass:
  - the #3254 additions (org_id/assessments_due/kill_switches_triggered)
    and the correct-by-fallback materiality counts parse from the real
    wire names (high_materiality etc., masfeat/types.go @ v9.13.0);
  - the deprecated fiction fields (by_use_case/by_status) stay {} on a
    real response - the server has never sent them on 9.x.

Posture note: the masfeat surface is Enterprise-gated. On a COMMUNITY
deployment the route 404s; that outcome is DIAGNOSED (the stack must
still prove reachable via /health through the same client) and reported
as GATED, not silently skipped and not treated as a pass of the
assertions above. Run against an Enterprise stack for full coverage.

Usage::

    export AXONFLOW_AGENT_URL=http://localhost:8080
    export AXONFLOW_TENANT_ID=<client id>
    export AXONFLOW_TENANT_SECRET=<secret>
    python runtime-e2e/masfeat_real_wire_fields/test.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from axonflow import AxonFlow
from axonflow.exceptions import AxonFlowError

AGENT_URL = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")
CLIENT_ID = os.environ.get("AXONFLOW_TENANT_ID", "demo-client")
SECRET = os.environ.get("AXONFLOW_TENANT_SECRET", "demo-secret")


def _fail(msg: str) -> None:
    sys.stderr.write(f"FAIL: {msg}\n")
    sys.exit(1)


async def main() -> int:
    async with AxonFlow(
        endpoint=AGENT_URL,
        client_id=CLIENT_ID,
        client_secret=SECRET,
    ) as client:
        try:
            summary = await client.masfeat_get_registry_summary()
        except AxonFlowError as exc:
            if "404" not in str(exc):
                _fail(f"masfeat_get_registry_summary failed non-404: {exc}")
            # Diagnose, don't skip: the 404 must come from a live stack.
            if not await client.health_check():
                _fail(
                    f"masfeat route 404 AND /health not healthy at {AGENT_URL} - "
                    "that is an unreachable/broken stack, not a gated surface"
                )
            print(
                "GATED: masfeat routes are not served by this deployment "
                f"(HTTP 404 at {AGENT_URL}, /health healthy) - the masfeat "
                "surface is Enterprise-gated; run this suite against an "
                "Enterprise stack for full coverage. Diagnosed, not skipped."
            )
            return 0

        # Enterprise path: typed assertions on the real wire shape.
        if summary.total_systems < 0:
            _fail(f"total_systems parsed negative: {summary.total_systems}")
        counts = (
            summary.high_materiality_count
            + summary.medium_materiality_count
            + summary.low_materiality_count
        )
        if counts > summary.total_systems:
            _fail(
                f"materiality counts {counts} exceed total_systems "
                f"{summary.total_systems} - real-name fallback parse suspect"
            )
        if not isinstance(summary.assessments_due, int) or not isinstance(
            summary.kill_switches_triggered, int
        ):
            _fail("#3254 additions did not parse as ints")
        if summary.by_use_case != {} or summary.by_status != {}:
            _fail(
                "deprecated fiction fields populated on a real response: "
                f"by_use_case={summary.by_use_case!r} by_status={summary.by_status!r} "
                "- the 9.x server never sends them; if a future server does, "
                "revisit the #3254 deprecation before shipping"
            )
        print(
            f"PASS: RegistrySummary parsed from live stack: org_id={summary.org_id!r} "
            f"total={summary.total_systems} active={summary.active_systems} "
            f"assessments_due={summary.assessments_due} "
            f"kill_switches_triggered={summary.kill_switches_triggered}"
        )
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
