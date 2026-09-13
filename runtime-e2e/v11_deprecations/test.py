"""Real-stack proof that the SDK reports each deprecated route once per client.

A v11 platform stamps every response from its deprecated legacy policy surface
with ``X-AxonFlow-Removed-In`` and a successor ``Link`` (and, once the
deprecating release is tagged, ``Deprecation``). The client reports each stamped
route ONCE per client through ``PlatformRouteDeprecationWarning``, keyed by method
and path without the query, and a client derived through ``as_user`` shares that
memory. This drives the SDK against a real agent and asserts:

0. MEASURE: the platform stamps both legacy static-policy reads on this stack.
   The headers are read raw and printed, so the assertions below rest on stamps
   the platform actually sent, not on an assumption.
1. ``list_static_policies()`` twice on one client reports the route once, with
   the platform's removal release and successor.
2. A client derived from it with ``as_user`` calls the same route and reports
   nothing new: the memory is shared.
3. ``get_effective_static_policies()``, a different stamped route, reports once,
   and a second call reports nothing new.

Every count is taken with ``simplefilter("always")``, so Python's own warning
filter cannot hide a repeat: the counts are what the SDK emits.

The simulation routes this SDK also deprecates are registered only on an
Evaluation+ licence; their stamps are proven by the Go SDK's live
``v11_deprecations`` leg, and this SDK's handling of them by its unit tests.

Run against a community stack::

    AXONFLOW_AGENT_URL=http://localhost:8080 \\
    AXONFLOW_CLIENT_ID=runtime-e2e AXONFLOW_CLIENT_SECRET=runtime-e2e-secret \\
    python runtime-e2e/v11_deprecations/test.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import warnings
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from axonflow import AxonFlow, PlatformRouteDeprecationWarning  # noqa: E402

ENDPOINT = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")
CLIENT_ID = os.environ.get("AXONFLOW_CLIENT_ID", "runtime-e2e")
CLIENT_SECRET = os.environ.get("AXONFLOW_CLIENT_SECRET", "runtime-e2e-secret")
ROUTES = ["/api/v1/static-policies", "/api/v1/static-policies/effective"]
SUCCESSOR = "/api/v1/typed-policies"

FAILURES: list[str] = []


def check(ok: bool, description: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'}: {description}")
    if not ok:
        FAILURES.append(description)


async def measure() -> dict[str, dict[str, str | None]]:
    """The platform's own stamps on each legacy read, read raw."""
    stamps: dict[str, dict[str, str | None]] = {}
    async with httpx.AsyncClient(base_url=ENDPOINT, auth=(CLIENT_ID, CLIENT_SECRET)) as raw:
        for route in ROUTES:
            response = await raw.get(route, headers={"X-Client-ID": CLIENT_ID})
            stamps[route] = {
                "status": str(response.status_code),
                "X-AxonFlow-Removed-In": response.headers.get("X-AxonFlow-Removed-In"),
                "Link": response.headers.get("Link"),
                "Deprecation": response.headers.get("Deprecation"),
            }
            print(f"  {route}: {stamps[route]}")
    return stamps


def reported(caught: list[warnings.WarningMessage]) -> list[PlatformRouteDeprecationWarning]:
    return [w.message for w in caught if isinstance(w.message, PlatformRouteDeprecationWarning)]


async def run(client: AxonFlow) -> None:
    print("== 0. the platform's stamps on this stack")
    stamps = await measure()
    for route in ROUTES:
        check(
            stamps[route]["X-AxonFlow-Removed-In"] == "v11.1",
            f"the platform stamps {route} with X-AxonFlow-Removed-In: v11.1",
        )
        check(
            SUCCESSOR in (stamps[route]["Link"] or ""),
            f"the platform names {SUCCESSOR} as the successor of {route}",
        )

    print("== 1. one client, the same route twice")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await client.list_static_policies()
        await client.list_static_policies()
    first = reported(caught)
    for w in first:
        print(f"  reported: {w}")
    check(len(first) == 1, "two calls to one stamped route report it once")
    if first:
        check(first[0].route == "GET /api/v1/static-policies", "the report names the route")
        check(first[0].removed_in == "v11.1", "the report carries the platform's removal release")
        check(first[0].successor == SUCCESSOR, "the report carries the platform's successor")

    print("== 2. a derived client, the same route")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await client.as_user(None).list_static_policies()
    check(
        reported(caught) == [],
        "a client derived with as_user shares the memory and reports nothing new",
    )

    print("== 3. a different stamped route, twice")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await client.get_effective_static_policies()
        await client.get_effective_static_policies()
    other = reported(caught)
    for w in other:
        print(f"  reported: {w}")
    check(
        [w.route for w in other] == ["GET /api/v1/static-policies/effective"],
        "a different stamped route is reported once, and its second call reports nothing new",
    )


async def main() -> int:
    print(f"agent: {ENDPOINT}")
    async with AxonFlow(
        endpoint=ENDPOINT, client_id=CLIENT_ID, client_secret=CLIENT_SECRET
    ) as client:
        try:
            await run(client)
        except Exception as e:  # noqa: BLE001 - any failure is reported as a failed proof
            check(False, f"the run raised {type(e).__name__}: {e}")
    if FAILURES:
        print(f"\nFAIL: v11_deprecations ({len(FAILURES)} assertion(s))")
        return 1
    print("\nPASS: v11_deprecations")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
