"""Real-stack assertion: Decision Mode PEP decide -> fulfill -> forward (#2571 / #2563).

Per CLAUDE.md HARD RULE #0 this test MUST hit a real running AxonFlow agent —
no mocks. It proves the engine-fulfillable obligation contract end to end:

  1. ``client.decide(...)`` on a PII-bearing request returns verdict=allow with a
     self-describing ``redact_pii`` obligation whose fulfillment names the
     check-input engine endpoint (request phase, text/plain).
  2. ``client.fulfill_request(...)`` discharges it by round-tripping the statement
     through that engine endpoint and returns ENGINE-redacted content — the
     original PII no longer appears, and the masking is the engine's (the SDK
     contains no local redaction path).
  3. ``client.decide_and_fulfill(...)`` does both in one call.
  4. Demo / wrong credentials are refused (401 -> AuthenticationError); the PEP
     cannot decide with credentials the enterprise PDP does not accept.

Enterprise auth is HTTP Basic (org:license) — the SDK builds it from
``client_id`` + ``client_secret``.

Run locally (after `source /tmp/axonflow-e2e-env.sh` from the enterprise
setup script):

    AXONFLOW_AGENT_URL=http://localhost:8080 \
    AXONFLOW_CLIENT_ID="$AXONFLOW_CLIENT_ID" \
    AXONFLOW_CLIENT_SECRET="$AXONFLOW_CLIENT_SECRET" \
    python3 runtime-e2e/decide_fulfill_obligation/test.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from axonflow import AxonFlow
from axonflow.exceptions import AuthenticationError, ObligationNotFulfillableError
from axonflow.pep import OBLIGATION_REDACT_PII, PHASE_REQUEST, VERDICT_ALLOW
from axonflow.types import DecideRequest

ENDPOINT = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")
CLIENT_ID = os.environ.get("AXONFLOW_CLIENT_ID")
CLIENT_SECRET = os.environ.get("AXONFLOW_CLIENT_SECRET")

# The PII the request carries. The engine's redactor must mask the email +
# credit card; neither raw value may survive into the fulfilled content.
RAW_EMAIL = "john.doe@example.com"
RAW_CARD = "4111111111111111"
QUERY = f"Send the receipt to {RAW_EMAIL} and charge card {RAW_CARD}"


def _fail(msg: str) -> None:
    sys.stderr.write(f"FAIL: {msg}\n")
    sys.exit(1)


async def main() -> None:
    # Track presence as booleans so the secret value never flows into a log
    # expression (keeps CodeQL's clear-text-logging taint analysis happy).
    present = {
        "AXONFLOW_CLIENT_ID": bool(CLIENT_ID),
        "AXONFLOW_CLIENT_SECRET": bool(CLIENT_SECRET),
    }
    missing = [name for name, ok in present.items() if not ok]
    if missing:
        sys.stderr.write(f"required env vars not set: {', '.join(missing)}; see module docstring\n")
        sys.exit(2)

    async with AxonFlow(
        endpoint=ENDPOINT, client_id=CLIENT_ID, client_secret=CLIENT_SECRET
    ) as client:
        # 1. decide() surfaces the engine-fulfillable redact_pii obligation.
        decision = await client.decide(
            DecideRequest(
                stage="tool",
                query=QUERY,
                target={"type": "tool", "tool": "send_receipt"},
                caller_identity={"gateway_id": "sdk-runtime-e2e"},
            )
        )
        print(f"decide -> verdict={decision.verdict} obligations={len(decision.obligations)}")
        if decision.verdict != VERDICT_ALLOW:
            _fail(f"expected allow, got {decision.verdict} ({decision.error})")
        if not decision.trace_id:
            _fail("decide response did not surface a trace_id")
        redact = [o for o in decision.obligations if o.type == OBLIGATION_REDACT_PII]
        if not redact:
            _fail(f"no redact_pii obligation on a PII request; got {decision.obligations}")
        ful = redact[0].fulfillment
        if ful is None or ful.phase != PHASE_REQUEST:
            _fail(f"obligation not request-phase engine-fulfillable: {ful}")
        if "check-input" not in ful.endpoint:
            _fail(f"fulfillment endpoint is not the request-redaction endpoint: {ful.endpoint}")
        print(
            f"  obligation fulfillment -> {ful.endpoint} phase={ful.phase} types={ful.content_types}"
        )

        # 2. fulfill_request() returns ENGINE-redacted content; raw PII is gone.
        content, did_redact = await client.fulfill_request(decision, QUERY)
        print(f"fulfill_request -> did_redact={did_redact} content={content!r}")
        if not did_redact:
            _fail("engine reported no redaction on a request that carries PII")
        if RAW_EMAIL in content:
            _fail(f"raw email survived fulfillment — PII leak: {content!r}")
        if RAW_CARD in content:
            _fail(f"raw card survived fulfillment — PII leak: {content!r}")
        if content == QUERY:
            _fail("fulfilled content is byte-identical to the unredacted query")

        # 3. decide_and_fulfill() one-call path yields the same masked content.
        verdict, one_call, _decision = await client.decide_and_fulfill(
            DecideRequest(
                stage="tool", query=QUERY, target={"type": "tool", "tool": "send_receipt"}
            )
        )
        print(f"decide_and_fulfill -> verdict={verdict} content={one_call!r}")
        if verdict != VERDICT_ALLOW:
            _fail(f"decide_and_fulfill verdict {verdict}, expected allow")
        if RAW_EMAIL in one_call or RAW_CARD in one_call:
            _fail(f"decide_and_fulfill leaked PII: {one_call!r}")

    # 4. Demo / wrong credentials are refused by the enterprise PDP.
    async with AxonFlow(
        endpoint=ENDPOINT, client_id="demo-org", client_secret="demo-license-not-real"
    ) as bad_client:
        try:
            await bad_client.decide(DecideRequest(stage="tool", query="hi"))
        except AuthenticationError:
            print("demo creds -> AuthenticationError (refused) OK")
        else:
            _fail("demo credentials were NOT refused by the PDP")

    print("PASS: decide -> fulfill -> forward verified against real agent")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except ObligationNotFulfillableError as e:
        _fail(f"obligation unexpectedly not fulfillable against real agent: {e}")
