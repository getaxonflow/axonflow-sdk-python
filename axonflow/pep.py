"""Decision Mode PEP (Policy Enforcement Point) contract constants and helpers.

A PEP follows one path: **decide → fulfill → forward** (ADR-056, epic #2563).

  - decide:  ask the PDP (``POST /api/v1/decide``) for a verdict on a request.
  - fulfill: for every obligation the verdict carries, call the ENGINE endpoint
    named in the obligation's ``fulfillment`` block to obtain engine-redacted
    content.
  - forward: forward the (possibly redacted) content, or block, per verdict.

The structural guarantee #2563 demands: a PEP built on this SDK contains NO
redaction logic of its own. The ONLY way it discharges a ``redact_pii``
obligation is by POSTing the source content to the engine endpoint the
obligation names (``client.fulfill_request`` / ``client.decide_and_fulfill``)
and forwarding what the engine returns. If an obligation arrives without a
fulfillable engine endpoint — or the engine reports the redactor did not run —
the helper raises :class:`~axonflow.exceptions.ObligationNotFulfillableError`
and the caller MUST fail closed (block), never forward unredacted.

This mirrors ``platform/shared/pep`` (the Go reference PEP) so the SDK PEP
cannot reimplement redaction the way a hand-rolled regex would.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from axonflow.types import Obligation

# --- Obligation contract constants (mirror platform/agent/decision_handler.go) ---

#: The obligation a PEP discharges by replacing request content with
#: engine-redacted content before forwarding.
OBLIGATION_REDACT_PII = "redact_pii"

#: Fulfillment phases. ``/decide`` runs pre-call so it only emits request-phase
#: obligations; the response-phase value is part of the contract for PEP helpers
#: that fan out to the response-redaction endpoint after the backend call.
PHASE_REQUEST = "request"
PHASE_RESPONSE = "response"

#: The only redaction content-type wired today. The contract is content-type
#: agnostic — a PEP holding content of a type not advertised by an obligation's
#: ``content_types`` must fail closed rather than forward it unredacted.
CONTENT_TYPE_TEXT = "text/plain"

# --- Verdict values returned by the PDP ---
VERDICT_ALLOW = "allow"
VERDICT_DENY = "deny"
VERDICT_NEEDS_APPROVAL = "needs_approval"

# --- Engine endpoints a PEP will POST content to for fulfillment ---
# An obligation whose fulfillment endpoint is not one of these is rejected — a
# PEP must not be steered into calling an arbitrary URL by a malformed verdict.
REQUEST_REDACTION_PATH = "/api/v1/mcp/check-input"
RESPONSE_REDACTION_PATH = "/api/v1/mcp/check-output"

DECIDE_PATH = "/api/v1/decide"


def has_request_redaction(obligations: list[Obligation]) -> bool:
    """Report whether any obligation requires request-phase PII redaction.

    Exposed so a PEP can branch ("does this verdict carry work for me?") before
    calling ``client.fulfill_request``.
    """
    return any(
        o.type == OBLIGATION_REDACT_PII
        and o.fulfillment is not None
        and o.fulfillment.phase == PHASE_REQUEST
        for o in obligations
    )


def _endpoint_path_matches(endpoint: str, expected: str) -> bool:
    """Report whether ``endpoint`` is the expected engine path.

    Tolerates an absolute URL whose path component matches (some PDPs return a
    fully-qualified obligation endpoint); a blank endpoint never matches.
    """
    e = (endpoint or "").strip()
    if e == expected:
        return True
    marker = "://"
    idx = e.find(marker)
    if idx >= 0:
        rest = e[idx + len(marker) :]
        slash = rest.find("/")
        if slash >= 0:
            path = rest[slash:]
            q = path.find("?")
            if q >= 0:
                path = path[:q]
            return path == expected
    return False
