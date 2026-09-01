#!/usr/bin/env python3
"""AuthZEN-native authorization against a running AxonFlow gateway.

This example exercises the happy path AND the refusals, because the refusals
are the half a new integration gets wrong: this surface answers "I cannot
evaluate that" rather than evaluating around what it cannot read, and a caller
that treats every error as a deny will block traffic it should have allowed.

It also demonstrates the tri-state, which is the part of the API that has no
equivalent in the older surface. ``None`` cannot express three states, so the
SDK gives you an explicit one — and the difference between "the source says
there is no value" and "the source could not be reached" decides whether the
request is sent at all.

Run it against a local stack::

    export AXONFLOW_AGENT_URL=http://localhost:8080
    export AXONFLOW_CLIENT_ID=...        # required outside community mode
    export AXONFLOW_CLIENT_SECRET=...
    python3 examples/authzen_evaluation.py

Exits non-zero if any step does not behave as documented, so it is usable as a
smoke test rather than only as a demonstration.
"""

from __future__ import annotations

import asyncio
import os
import sys

from axonflow import (
    AUTHZEN_UNKNOWN_RESOLUTION_FAILED,
    AuthZENAction,
    AuthZENAttribute,
    AuthZENBulk,
    AuthZENDecision,
    AuthZENProtocolError,
    AuthZENRefusal,
    AuthZENRequest,
    AuthZENResource,
    AuthZENSubject,
    AxonFlow,
)

GATEWAY_ID = "example-gateway-01"
BENIGN = "summarise yesterday's incident report"

failures: list[str] = []


def step(name: str) -> None:
    print(f"\n=== {name} ===")


def ok(name: str) -> None:
    print(f"ok    {name}")


def failed(name: str, detail: str) -> None:
    print(f"FAIL  {name}: {detail}")
    failures.append(name)


def llm_request(query: object = BENIGN) -> AuthZENRequest:
    return AuthZENRequest(
        subject=AuthZENSubject(type="gateway", id=GATEWAY_ID),
        action=AuthZENAction(name="llm.completion"),
        resource=AuthZENResource(type="llm", id="llm"),
        context={"args": {"query": query}},
    )


def describe(decision: AuthZENDecision) -> None:
    """Print what a Policy Enforcement Point would act on."""
    print(f"  allowed:  {decision.allowed}")
    print(f"  state:    {decision.state}")
    print(f"  reason:   {decision.reason} ({decision.category})")
    print(f"  id:       {decision.decision_id}")
    for obligation in decision.obligations:
        # A MANDATORY obligation that cannot be discharged means the operation
        # must NOT proceed, even though `allowed` is true.
        print(
            f"  obligation: {obligation.type} "
            f"(mandatory={obligation.mandatory}, from {obligation.source_policy})"
        )
    if decision.approval is not None:
        print(f"  approval required, expires {decision.approval.expires_at}")


async def expect_refusal(
    client: AxonFlow, name: str, request: AuthZENRequest, want_code: str
) -> None:
    """Assert the gateway refuses `request` with `want_code`, and show why."""
    step(f"refused: {name}")
    try:
        decision = await client.evaluate(request)
    except AuthZENRefusal as refusal:
        if refusal.code != want_code:
            failed(name, f"code {refusal.code!r}, want {want_code!r}")
            return
        print(f"  code:      {refusal.code}")
        print(f"  pointer:   {refusal.pointer}")
        print(f"  refused by:{refusal.refused_by}")
        print(f"  retryable: {refusal.retryable}")
        print(f"  message:   {refusal.message}")
        if refusal.supported:
            print(f"  supported: {refusal.supported}")
        ok(name)
    else:
        failed(name, f"expected a refusal, got a decision: allowed={decision.allowed}")


async def show_single_evaluation(client: AxonFlow) -> None:
    """1. The happy path: one subject, one action, one resource."""
    step("a single evaluation")
    try:
        decision = await client.evaluate(llm_request())
    except (AuthZENRefusal, AuthZENProtocolError) as exc:
        failed("a single evaluation", str(exc))
    else:
        describe(decision)
        ok("a single evaluation")


async def show_bulk_evaluation(client: AxonFlow) -> None:
    """2. Several preconditions of ONE operation.

    The reply is one decision, not one per entry: a denied entry denies the
    operation. Anything an entry omits is inherited from the shared base.
    """
    name = "several preconditions of one operation"
    step(name)
    try:
        decision = await client.evaluate_all(
            AuthZENBulk(
                subject=AuthZENSubject(type="gateway", id=GATEWAY_ID),
                action=AuthZENAction(name="tool.call"),
                context={"args": {"query": BENIGN}},
                evaluations=[
                    AuthZENRequest(resource=AuthZENResource(type="tool", id="jira/move_issue")),
                    AuthZENRequest(resource=AuthZENResource(type="tool", id="jira/update_project")),
                ],
            )
        )
    except (AuthZENRefusal, AuthZENProtocolError) as exc:
        failed(name, str(exc))
    else:
        describe(decision)
        ok(name)


async def show_absent_attribute(client: AxonFlow) -> None:
    """3a. ABSENT is resolved data, so the request is still sent."""
    name = "an ABSENT attribute is data, so the request is still sent"
    step(name)
    try:
        decision = await client.evaluate(
            AuthZENRequest(
                subject=AuthZENSubject(type="gateway", id=GATEWAY_ID),
                action=AuthZENAction(name="llm.completion"),
                resource=AuthZENResource(type="llm", id="llm"),
                context={
                    "args": {"query": BENIGN},
                    # The correlation source ran and established there is no
                    # session for this call. That is a fact, so the member is
                    # simply omitted and the gateway decides.
                    "correlation": {"session_id": AuthZENAttribute.absent()},
                },
            )
        )
    except (AuthZENRefusal, AuthZENProtocolError) as exc:
        failed(name, str(exc))
    else:
        print(f"  allowed: {decision.allowed} (the member was omitted, not invented)")
        ok(name)


async def show_unknown_attribute(client: AxonFlow) -> None:
    """3b. UNKNOWN is a failure to resolve, so nothing is sent."""
    name = "an UNKNOWN attribute stops the request before it is sent"
    step(name)
    try:
        await client.evaluate(
            AuthZENRequest(
                subject=AuthZENSubject(type="gateway", id=GATEWAY_ID),
                action=AuthZENAction(name="llm.completion"),
                resource=AuthZENResource(type="llm", id="llm"),
                context={
                    "args": {"query": BENIGN},
                    # The directory did not answer. Sending anyway would get a
                    # decision computed as though there were no session — and
                    # every audit of it would record that the session was
                    # considered.
                    "correlation": {
                        "session_id": AuthZENAttribute.unknown(AUTHZEN_UNKNOWN_RESOLUTION_FAILED)
                    },
                },
            )
        )
    except AuthZENRefusal as refusal:
        if refusal.refused_by != "client":
            failed(name, "the request reached the gateway")
        else:
            print(f"  refused locally at {refusal.pointer}")
            print(f"  retryable: {refusal.retryable}")
            ok(name)
    else:
        failed(name, "the request was sent anyway")


async def show_refusals(client: AxonFlow) -> None:
    """4. The refusals. Each names the member to fix."""
    await expect_refusal(
        client,
        "an attribute the evaluator cannot read",
        AuthZENRequest(
            subject=AuthZENSubject(
                type="gateway", id=GATEWAY_ID, properties={"clearance": "secret"}
            ),
            action=AuthZENAction(name="llm.completion"),
            resource=AuthZENResource(type="llm", id="llm"),
            context={"args": {"query": BENIGN}},
        ),
        "unevaluable_attribute",
    )
    await expect_refusal(
        client,
        "an end-user subject, which needs the identity plane",
        AuthZENRequest(
            subject=AuthZENSubject(type="user", id="alice@example.com"),
            action=AuthZENAction(name="llm.completion"),
            resource=AuthZENResource(type="llm", id="llm"),
            context={"args": {"query": BENIGN}},
        ),
        "unsupported_subject",
    )
    await expect_refusal(
        client,
        "an action outside the evaluable set",
        AuthZENRequest(
            subject=AuthZENSubject(type="gateway", id=GATEWAY_ID),
            action=AuthZENAction(name="jira.transition_issue"),
            resource=AuthZENResource(type="llm", id="llm"),
            context={"args": {"query": BENIGN}},
        ),
        "unsupported_action",
    )
    await expect_refusal(
        client,
        "an action and a resource that describe different operations",
        AuthZENRequest(
            subject=AuthZENSubject(type="gateway", id=GATEWAY_ID),
            action=AuthZENAction(name="llm.completion"),
            resource=AuthZENResource(type="tool", id="jira/create_issue"),
            context={"args": {"query": BENIGN}},
        ),
        "unsupported_resource",
    )
    await expect_refusal(
        client,
        "nothing to evaluate",
        AuthZENRequest(
            subject=AuthZENSubject(type="gateway", id=GATEWAY_ID),
            action=AuthZENAction(name="llm.completion"),
            resource=AuthZENResource(type="llm", id="llm"),
            context={"args": {}},
        ),
        "missing_evaluable_content",
    )


async def show_local_validation(client: AxonFlow) -> None:
    """5. A malformed envelope never reaches the network.

    The generated types carry the rules the type system cannot: the singular
    member has no shared base to inherit an action or a resource from.
    """
    name = "an incomplete evaluation fails before the round trip"
    step(name)
    try:
        await client.evaluate(AuthZENRequest(subject=AuthZENSubject(type="gateway", id=GATEWAY_ID)))
    except ValueError as exc:
        print(f"  caught locally: {str(exc).splitlines()[0]}")
        ok(name)
    except AuthZENRefusal as refusal:
        if refusal.refused_by != "client":
            failed(name, "the gateway answered; this should have been caught locally")
        else:
            print(f"  caught locally at {refusal.pointer}")
            ok(name)
    else:
        failed(name, "an incomplete evaluation was accepted")


async def main() -> int:
    endpoint = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")
    async with AxonFlow(
        endpoint=endpoint,
        client_id=os.environ.get("AXONFLOW_CLIENT_ID"),
        client_secret=os.environ.get("AXONFLOW_CLIENT_SECRET"),
    ) as client:
        await show_single_evaluation(client)
        await show_bulk_evaluation(client)
        await show_absent_attribute(client)
        await show_unknown_attribute(client)
        await show_refusals(client)
        await show_local_validation(client)

    print()
    if failures:
        print(f"{len(failures)} step(s) failed: {', '.join(failures)}")
        return 1
    print("All AuthZEN steps behaved as documented.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
