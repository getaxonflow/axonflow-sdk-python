"""Example: declaring an enforcement point's capabilities with the PEP handshake.

An enforcement point (a PEP) declares, on each governed call, the exact
obligation types and schema versions it can discharge. The platform reads the
declaration from v10.4.0. From v11.0.0, on every edition, the engine refuses a
mandatory obligation the declaration cannot discharge: an organization's redact
override on ``decide`` refuses a caller that does not declare redaction. On an
Enterprise deployment, in addition, an allow carrying a mandatory obligation
outside the declared set becomes a deny, so the enforcement point is never
handed an instruction it would drop.

This example builds a declaration once for the client, overrides it for one
call (one process can be two enforcement points), and shows that a declaration
the platform would refuse fails here, before anything is sent.

After a document with an organization-scope constraint is activated, a decide
that does not supply the attribute the constraint conditions on is denied
fail-closed with reasons ["unknown_constraint"]; supply the attribute or run
this example on a fresh stack. From v11.0.0 the deny's first reason is that
code, followed by one naming each constraint it could not evaluate and the
attribute it needed (getaxonflow/axonflow-enterprise#4247).

Env vars:

* ``AXONFLOW_AGENT_URL``      (default: http://localhost:8080)
* ``AXONFLOW_CLIENT_ID``      (default: community)
* ``AXONFLOW_CLIENT_SECRET``  (default: empty)

Run::

    python examples/pep_handshake.py

Exits non-zero if a step fails, so it is usable as a smoke test.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Awaitable, Callable

from axonflow import (
    AxonFlow,
    DecideRequest,
    DecisionTarget,
    PEPCapability,
    PEPHandshake,
    PEPHandshakeError,
)
from axonflow.exceptions import AxonFlowError

AUDIENCE = "https://pep.example.com"


async def main() -> int:
    # The request path redacts fields; it declares exactly that.
    request_path = PEPHandshake(
        pep_id="checkout-gateway",
        audience=AUDIENCE,
        capabilities=[PEPCapability("field_redact", 1)],
    )
    request = DecideRequest(
        stage="tool",
        query="look up the weather",
        target=DecisionTarget(type="tool", tool="search"),
    )

    failures = 0

    async def step(name: str, fn: Callable[[], Awaitable[None]]) -> None:
        nonlocal failures
        print(f"\n=== {name} ===")
        try:
            await fn()
            print("ok")
        except AxonFlowError as e:
            print(f"FAILED: {e}")
            failures += 1

    async with AxonFlow(
        endpoint=os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080"),
        client_id=os.environ.get("AXONFLOW_CLIENT_ID", "community"),
        client_secret=os.environ.get("AXONFLOW_CLIENT_SECRET", ""),
        pep_handshake=request_path,
    ) as client:

        async def with_the_clients() -> None:
            decision = await client.decide(request)
            reasons = decision.reasons or []
            print(
                f"verdict={decision.verdict} obligations={len(decision.obligations)} "
                f"reasons={reasons}"
            )

        # The response path masks fields instead. It declares its own set for
        # this call, in place of the client's.
        async def with_a_per_call_one() -> None:
            response_path = PEPHandshake(
                pep_id="checkout-gateway-response",
                audience=AUDIENCE,
                capabilities=[PEPCapability("field_mask", 1)],
            )
            decision = await client.decide(request, pep_handshake=response_path)
            reasons = decision.reasons or []
            print(
                f"verdict={decision.verdict} obligations={len(decision.obligations)} "
                f"reasons={reasons}"
            )

        await step("decide with the client's declaration", with_the_clients)
        await step("decide with a per-call declaration", with_a_per_call_one)

    # A declaration the platform would refuse fails at construction, naming the
    # member at fault, instead of the first governed call answering 400.
    async def refused() -> None:
        try:
            PEPHandshake(pep_id="Checkout:Gateway", audience=AUDIENCE, capabilities=[])
        except PEPHandshakeError as refusal:
            print(f"refused at {refusal.pointer}: {refusal}")
            return
        msg = "want a PEPHandshakeError, got none"
        raise AxonFlowError(msg)

    await step("a declaration the platform would refuse", refused)

    if failures:
        print(f"\n{failures} step(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
