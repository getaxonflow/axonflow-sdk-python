"""Real-stack proof that the PEP capability handshake reaches the platform.

The unit tests prove what the SDK puts on the wire. This driver proves the
platform READ it. After each governed call it reads the agent's own counter,
``axonflow_pep_handshake_total{outcome, plane}`` on ``/prometheus``, which the
agent moves once per inbound request on each plane that resolves the
declaration. Every assertion is therefore something the agent recorded, not
something the SDK reports about itself:

1. With a client-level declaration, ``decide``, AuthZEN ``evaluate`` and
   ``evaluate_all``, ``mcp_check_input``, ``mcp_check_output`` and the gateway
   ``pre_check`` each move exactly one series by one: ``outcome="accepted"`` on
   that call's plane. The agent decoded the SDK's bytes and admitted the
   declaration.
2. ``fulfill_request`` presents it on its engine round-trip.
3. A per-call declaration replaces the client's on that call. It declares an
   approval-family capability, which a Community agent does not issue: the
   agent drops it and counts ``over_advertised`` (an Enterprise agent keeps it
   and counts ``accepted``). The client's own declaration names no such
   capability, so the outcome tells the two documents apart at the agent.
4. A client with no declaration counts ``outcome="absent"``: the SDK sends
   nothing it was not given.
5. The synchronous client presents the declaration too.

Run::

    AXONFLOW_AGENT_URL=http://localhost:8080 \\
    AXONFLOW_CLIENT_ID=runtime-e2e AXONFLOW_CLIENT_SECRET=runtime-e2e-secret \\
    python runtime-e2e/pep_handshake_planes/test.py
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from axonflow import (  # noqa: E402
    AuthZENAction,
    AuthZENBulk,
    AuthZENRequest,
    AuthZENResource,
    AuthZENSubject,
    AxonFlow,
    AxonFlowError,
    DecideRequest,
    DecideResponse,
    PEPCapability,
    PEPHandshake,
)

ENDPOINT = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")
CLIENT_ID = os.environ.get("AXONFLOW_CLIENT_ID", "runtime-e2e")
CLIENT_SECRET = os.environ.get("AXONFLOW_CLIENT_SECRET", "runtime-e2e-secret")

AUDIENCE = "https://pep.example.test"
DECLARED = PEPHandshake(
    pep_id="sdk-python-e2e",
    audience=AUDIENCE,
    capabilities=[PEPCapability("field_redact", 1)],
)
OVERRIDE = PEPHandshake(
    pep_id="sdk-python-e2e-override",
    audience=AUDIENCE,
    capabilities=[PEPCapability("approval_challenge", 1), PEPCapability("field_redact", 1)],
)

QUERY = "look up the weather"
DECIDE = DecideRequest(stage="tool", query=QUERY, target={"type": "tool", "tool": "search"})
EVALUATION = AuthZENRequest(
    subject=AuthZENSubject(type="gateway", id="sdk-python-e2e"),
    action=AuthZENAction(name="llm.completion"),
    resource=AuthZENResource(type="llm", id="llm"),
    context={"args": {"query": QUERY}},
)
BULK = AuthZENBulk(
    subject=AuthZENSubject(type="gateway", id="sdk-python-e2e"),
    action=AuthZENAction(name="llm.completion"),
    context={"args": {"query": QUERY}},
    evaluations=[AuthZENRequest(resource=AuthZENResource(type="llm", id="llm"))],
)
# A decision carrying the request-phase redaction obligation, so
# fulfill_request makes its engine round-trip. It is the caller's input, as a
# decision from decide would be; the round-trip itself goes to the real agent.
REDACTING = DecideResponse.model_validate(
    {
        "verdict": "allow",
        "decision_id": "sdk-python-e2e",
        "stage": "tool",
        "evaluated_policies": [],
        "obligations": [
            {
                "type": "redact_pii",
                "fulfillment": {
                    "endpoint": "/api/v1/mcp/check-input",
                    "method": "POST",
                    "phase": "request",
                    "content_types": ["text/plain"],
                },
            }
        ],
    }
)

_SERIES = re.compile(r"^axonflow_pep_handshake_total\{([^}]*)\}\s+(\S+)$")
_LABEL = re.compile(r'(\w+)="([^"]*)"')

Counts = dict[tuple[str, str], float]
FAILURES: list[str] = []


def check(ok: bool, description: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'}: {description}")
    if not ok:
        FAILURES.append(description)


def scrape() -> Counts:
    """The agent's handshake counter, keyed by (outcome, plane)."""
    response = httpx.get(f"{ENDPOINT}/prometheus", timeout=10)
    response.raise_for_status()
    counts: Counts = {}
    for line in response.text.splitlines():
        match = _SERIES.match(line)
        if match:
            labels = dict(_LABEL.findall(match.group(1)))
            counts[(labels["outcome"], labels["plane"])] = float(match.group(2))
    return counts


def moved(before: Counts, after: Counts) -> Counts:
    return {
        key: after.get(key, 0.0) - before.get(key, 0.0)
        for key in sorted(set(before) | set(after))
        if after.get(key, 0.0) != before.get(key, 0.0)
    }


def render(counts: Counts) -> str:
    return (
        ", ".join(f"{outcome}@{plane} +{n:g}" for (outcome, plane), n in counts.items())
        or "nothing"
    )


def expect(description: str, got: Counts, want: Counts) -> None:
    print(f"  {description}: the agent counted {render(got)}")
    check(got == want, f"{description}: {render(want)}")


async def counted(description: str, call: Callable[[], Awaitable[object]], want: Counts) -> None:
    before = scrape()
    try:
        await call()
    except AxonFlowError as e:
        check(False, f"{description} raised {type(e).__name__}: {e}")
        return
    expect(description, moved(before, scrape()), want)


async def async_legs(override_outcome: str) -> None:
    async with AxonFlow(
        endpoint=ENDPOINT,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        pep_handshake=DECLARED,
    ) as client:
        legs: list[tuple[str, Callable[[], Awaitable[object]], Counts]] = [
            ("decide", lambda: client.decide(DECIDE), {("accepted", "decision"): 1}),
            (
                "evaluate",
                lambda: client.evaluate(EVALUATION),
                {("accepted", "access_evaluation"): 1},
            ),
            (
                "evaluate_all",
                lambda: client.evaluate_all(BULK),
                {("accepted", "access_evaluation"): 1},
            ),
            (
                "mcp_check_input",
                lambda: client.mcp_check_input("postgres", "SELECT 1"),
                {("accepted", "mcp"): 1},
            ),
            (
                "mcp_check_output",
                lambda: client.mcp_check_output("postgres", message="hello"),
                {("accepted", "mcp"): 1},
            ),
            (
                "pre_check",
                lambda: client.pre_check(user_token="tok", query="hello"),
                {("accepted", "gateway"): 1},
            ),
            (
                "fulfill_request",
                lambda: client.fulfill_request(
                    REDACTING, "email the receipt to jane.doe@example.com"
                ),
                {("accepted", "mcp"): 1},
            ),
            (
                "decide with a per-call declaration",
                lambda: client.decide(DECIDE, pep_handshake=OVERRIDE),
                {(override_outcome, "decision"): 1},
            ),
        ]
        for description, call, want in legs:
            print(f"== {description}")
            await counted(description, call, want)

    async with AxonFlow(
        endpoint=ENDPOINT, client_id=CLIENT_ID, client_secret=CLIENT_SECRET
    ) as bare:
        print("== decide with no declaration")
        await counted(
            "decide with no declaration", lambda: bare.decide(DECIDE), {("absent", "decision"): 1}
        )


def sync_leg() -> None:
    print("== the sync client")
    client = AxonFlow.sync(ENDPOINT, CLIENT_ID, CLIENT_SECRET, pep_handshake=DECLARED)
    before = scrape()
    try:
        client.decide(DECIDE)
    except AxonFlowError as e:
        check(False, f"the sync client's decide raised {type(e).__name__}: {e}")
        return
    finally:
        client.close()
    expect("the sync client's decide", moved(before, scrape()), {("accepted", "decision"): 1})


def main() -> int:
    edition = httpx.get(f"{ENDPOINT}/health", timeout=10).json().get("edition", "")
    print(f"agent: {ENDPOINT} (edition {edition!r})")
    # The platform drops approval-family capabilities only for a Community
    # enforcement point; any other edition admits the per-call document whole.
    override_outcome = "over_advertised" if edition == "community" else "accepted"
    asyncio.run(async_legs(override_outcome))
    sync_leg()
    if FAILURES:
        print(f"\nFAIL: pep_handshake_planes ({len(FAILURES)} assertion(s))")
        return 1
    print("\nPASS: pep_handshake_planes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
