"""Tests for the AuthZEN-native surface (ADR-065).

Every guard in ``axonflow/authzen.py`` has a case here that FAILS without it,
which is the only thing that makes a guard evidence rather than decoration. The
ones that would otherwise be silent are called out in the test's own docstring.

The transport is a recording stub. That is deliberate for this file: what it
pins is what the SDK does with a given set of bytes, and it must be able to
produce bytes a real server would only emit if it were broken (a decision whose
boolean and state disagree, an allow with no profile context). The other half —
that a real gateway actually behaves this way — is
``runtime-e2e/authzen_evaluation/test.py``, which drives a live agent.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from pydantic import ValidationError

from axonflow import (
    AUTHZEN_PROFILE_V1,
    AUTHZEN_UNKNOWN_RESOLUTION_FAILED,
    AuthZENAction,
    AuthZENAttribute,
    AuthZENBulk,
    AuthZENDecision,
    AuthZENEnvelope,
    AuthZENProtocolError,
    AuthZENRefusal,
    AuthZENRequest,
    AuthZENResource,
    AuthZENResponse,
    AuthZENSubject,
)
from axonflow.authzen import (
    AUTHZEN_PATH,
    AUTHZEN_PROFILE_HEADER,
    _assert_fully_resolved,
    build_envelope,
    evaluate_envelope,
    resolve_envelope,
    to_wire,
)
from axonflow.exceptions import AuthenticationError, AxonFlowError

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

ALLOW_CONTEXT = {
    "profile": AUTHZEN_PROFILE_V1,
    "state": "ALLOW",
    "category": "allowed",
    "reason": "permitted",
    "decision_id": "dec-1",
    "schema_version": "2026-08-29",
}
DENY_CONTEXT = {
    "profile": AUTHZEN_PROFILE_V1,
    "state": "DENY",
    "category": "not_permitted",
    "reason": "explicit_constraint",
    "decision_id": "dec-2",
    "schema_version": "2026-08-29",
}


class RecordingTransport:
    """Records what the SDK sent and replays a canned status + body."""

    def __init__(self, status: int = 200, body: Any = None) -> None:
        self.status = status
        self.body = body if body is not None else {"decision": True, "context": ALLOW_CONTEXT}
        self.calls: list[tuple[str, dict[str, Any], dict[str, str]]] = []

    async def __call__(
        self, path: str, body: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, bytes]:
        self.calls.append((path, body, headers))
        raw = self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode()
        return self.status, raw

    @property
    def sent(self) -> dict[str, Any]:
        assert self.calls, "the transport was never called"
        return self.calls[-1][1]


def singular(**overrides: Any) -> AuthZENRequest:
    fields: dict[str, Any] = {
        "subject": AuthZENSubject(type="gateway", id="llm-gateway-01"),
        "action": AuthZENAction(name="llm.completion"),
        "resource": AuthZENResource(type="llm", id="llm"),
        "context": {"args": {"query": "summarise yesterday's incident report"}},
    }
    fields.update(overrides)
    return AuthZENRequest(**fields)


async def evaluate(transport: RecordingTransport, request: AuthZENRequest) -> AuthZENDecision:
    # Through build_envelope, which is the path client.evaluate takes. Calling
    # AuthZENEnvelope directly here would test a construction sequence no
    # caller uses and would miss the typed refusals build_envelope produces.
    return await evaluate_envelope(transport, build_envelope(evaluation=request))


# --------------------------------------------------------------------------
# The tri-state
# --------------------------------------------------------------------------


class TestTriState:
    """known / absent / unknown are three states, and each has its own outcome."""

    async def test_known_absent_and_unknown_produce_three_different_outcomes(self) -> None:
        """The whole reason the type exists.

        Without the tri-state — with ``None`` standing in for "no value" —
        absent and unknown are the same object and these three cases collapse
        into two. Asserting all three together is what makes the collapse
        visible: delete the ``absent`` branch of ``_resolve_value`` and the
        second case starts raising; delete the ``unknown`` branch and the third
        case starts SENDING a request whose attribute nobody resolved.
        """
        known = RecordingTransport()
        await evaluate(
            known,
            singular(
                context={
                    "args": {"query": "q"},
                    "correlation": {"trace_id": AuthZENAttribute.known("t-1")},
                }
            ),
        )
        assert known.sent["evaluation"]["context"]["correlation"] == {"trace_id": "t-1"}

        absent = RecordingTransport()
        await evaluate(
            absent,
            singular(
                context={
                    "args": {"query": "q"},
                    "correlation": {"trace_id": AuthZENAttribute.absent()},
                }
            ),
        )
        # The member is GONE, and the request was still sent: absence is
        # resolved data, so there is a question to ask.
        assert absent.calls, "an absent attribute must not stop the request"
        assert absent.sent["evaluation"]["context"]["correlation"] == {}

        unknown = RecordingTransport()
        with pytest.raises(AuthZENRefusal) as caught:
            await evaluate(
                unknown,
                singular(
                    context={
                        "args": {"query": "q"},
                        "correlation": {
                            "trace_id": AuthZENAttribute.unknown(AUTHZEN_UNKNOWN_RESOLUTION_FAILED)
                        },
                    }
                ),
            )
        assert not unknown.calls, "an unknown attribute must never reach the network"
        assert caught.value.code == "unevaluable_attribute"
        assert caught.value.pointer == "/evaluation/context/correlation/trace_id"
        assert caught.value.refused_by == "client"
        assert AUTHZEN_UNKNOWN_RESOLUTION_FAILED in str(caught.value)

    async def test_absent_and_unknown_differ_on_a_required_member_too(self) -> None:
        """Not only on optional ones.

        An ABSENT query leaves the request evaluable-looking and lets the
        SERVER answer (it refuses with missing_evaluable_content, which is a
        deployment rule the SDK deliberately does not duplicate). An UNKNOWN
        query is refused here, by this SDK, before anything is sent. Same
        member, two different places, two different codes.
        """
        absent = RecordingTransport(
            status=422,
            body={
                "code": "missing_evaluable_content",
                "message": "the query must be a non-empty string",
                "pointer": "/evaluation/context/args/query",
            },
        )
        with pytest.raises(AuthZENRefusal) as from_server:
            await evaluate(absent, singular(context={"args": {"query": AuthZENAttribute.absent()}}))
        assert absent.calls, "the request must reach the server"
        assert from_server.value.refused_by == "gateway"
        assert from_server.value.code == "missing_evaluable_content"

        unknown = RecordingTransport()
        with pytest.raises(AuthZENRefusal) as from_client:
            await evaluate(
                unknown,
                singular(
                    context={"args": {"query": AuthZENAttribute.unknown("resolution_failed")}}
                ),
            )
        assert not unknown.calls
        assert from_client.value.refused_by == "client"
        assert from_client.value.code == "unevaluable_attribute"

    async def test_a_known_null_survives_and_is_not_confused_with_absence(self) -> None:
        """``known(None)`` is a JSON null on the wire; ``absent()`` is no member.

        Without the ``_DROP`` sentinel these are the same value and the first
        case silently becomes the second — the SDK rewriting a caller's null
        into a missing member.
        """
        transport = RecordingTransport()
        await evaluate(
            transport,
            singular(
                context={
                    "args": {"query": "q"},
                    "correlation": {
                        "explicit_null": AuthZENAttribute.known(None),
                        "gone": AuthZENAttribute.absent(),
                    },
                }
            ),
        )
        correlation = transport.sent["evaluation"]["context"]["correlation"]
        assert correlation == {"explicit_null": None}

    async def test_unknown_requires_a_reason(self) -> None:
        with pytest.raises(ValueError, match="must name why"):
            AuthZENAttribute.unknown("   ")

    async def test_a_pointer_escapes_json_pointer_metacharacters(self) -> None:
        """RFC 6901. Without escaping, a key containing ``/`` produces a pointer
        that resolves to nothing — on the refusal whose whole diagnostic value
        is the pointer.
        """
        transport = RecordingTransport()
        with pytest.raises(AuthZENRefusal) as caught:
            await evaluate(
                transport,
                singular(
                    context={
                        "args": {"query": "q"},
                        "correlation": {"a/b~c": AuthZENAttribute.unknown("stale")},
                    }
                ),
            )
        assert caught.value.pointer == "/evaluation/context/correlation/a~1b~0c"

    async def test_attributes_resolve_inside_properties_bags(self) -> None:
        transport = RecordingTransport()
        with pytest.raises(AuthZENRefusal) as caught:
            await evaluate(
                transport,
                singular(
                    subject=AuthZENSubject(
                        type="gateway",
                        id="g1",
                        properties={"clearance": AuthZENAttribute.unknown("stale")},
                    )
                ),
            )
        assert caught.value.pointer == "/evaluation/subject/properties/clearance"

    async def test_attributes_resolve_inside_a_plural_entry_at_its_own_pointer(self) -> None:
        transport = RecordingTransport()
        bulk = AuthZENBulk(
            subject=AuthZENSubject(type="gateway", id="g1"),
            action=AuthZENAction(name="tool.call"),
            context={"args": {"query": "q"}},
            evaluations=[
                AuthZENRequest(resource=AuthZENResource(type="tool", id="jira/move_issue")),
                AuthZENRequest(
                    resource=AuthZENResource(type="tool", id="jira/update_project"),
                    context={"correlation": {"k": AuthZENAttribute.unknown("stale")}},
                ),
            ],
        )
        with pytest.raises(AuthZENRefusal) as caught:
            await evaluate_envelope(transport, AuthZENEnvelope(evaluations=bulk))
        # Entry pointers mirror the server's: the base is /evaluations, its
        # entries live in that object's own `evaluations` array.
        assert caught.value.pointer == "/evaluations/evaluations/1/context/correlation/k"

    async def test_an_absent_element_is_dropped_from_a_list(self) -> None:
        transport = RecordingTransport()
        await evaluate(
            transport,
            singular(
                context={
                    "args": {"query": "q"},
                    "correlation": {
                        "tags": [
                            AuthZENAttribute.known("a"),
                            AuthZENAttribute.absent(),
                            "c",
                        ]
                    },
                }
            ),
        )
        assert transport.sent["evaluation"]["context"]["correlation"]["tags"] == ["a", "c"]

    async def test_absence_does_not_cascade_out_of_the_bag_the_caller_placed(self) -> None:
        """An emptied bag is sent empty, not deleted.

        The bag is the caller's structure; the attributes are the data in it.
        An SDK that deleted a container the caller wrote would be editing the
        question rather than resolving the answer — and the caller would have
        no way to express "send an empty correlation object".
        """
        transport = RecordingTransport(
            status=422,
            body={"code": "missing_evaluable_content", "message": "nothing to evaluate"},
        )
        with pytest.raises(AuthZENRefusal):
            await evaluate(
                transport,
                singular(context={"args": {"query": AuthZENAttribute.absent()}}),
            )
        assert transport.sent["evaluation"]["context"] == {"args": {}}

    async def test_the_lever_for_a_conditional_member_sits_inside_the_bag(self) -> None:
        """The whole ``correlation`` member drops, ``args`` survives untouched.

        This is why absence does not need to cascade out of the bag: the
        caller's conditional member IS an attribute, and dropping it is the
        ordinary rule doing its job one level in.
        """
        transport = RecordingTransport()
        await evaluate(
            transport,
            singular(
                context={
                    "args": {"query": "q"},
                    "correlation": AuthZENAttribute.absent(),
                }
            ),
        )
        assert transport.sent["evaluation"]["context"] == {"args": {"query": "q"}}


# --------------------------------------------------------------------------
# Round trips
# --------------------------------------------------------------------------


class TestRoundTrip:
    def test_request_round_trips_through_json(self) -> None:
        envelope = AuthZENEnvelope(evaluation=singular())
        wire = to_wire(envelope)
        decoded = AuthZENEnvelope.model_validate(json.loads(json.dumps(wire)))
        assert decoded == resolve_envelope(envelope)

    def test_response_round_trips_through_json(self) -> None:
        raw = json.dumps({"decision": True, "context": ALLOW_CONTEXT})
        response = AuthZENResponse.model_validate_json(raw)
        assert json.loads(response.model_dump_json(exclude_none=True)) == json.loads(raw)

    def test_an_unknown_member_in_a_response_is_refused_not_dropped(self) -> None:
        """``extra="forbid"``. Without it the member is silently dropped and the
        SDK acts on a partial reading of an authorization decision.
        """
        with pytest.raises(Exception, match=r"extra_forbidden|Extra inputs"):
            AuthZENResponse.model_validate(
                {"decision": True, "context": ALLOW_CONTEXT, "escalation": "x"}
            )


# --------------------------------------------------------------------------
# The response direction: what may and may not be read as an allow
# --------------------------------------------------------------------------


class TestDecisionValidation:
    async def test_a_200_with_no_profile_context_is_refused(self) -> None:
        """The SDK always negotiates, so a context-less 200 means the gateway
        did not honour it.

        Without this guard the response decodes cleanly, ``decision`` is true,
        ``obligations`` is an empty list indistinguishable from "no
        obligations", and the caller proceeds on an allow whose mandatory
        redaction it never saw.
        """
        transport = RecordingTransport(body={"decision": True})
        with pytest.raises(AuthZENProtocolError, match="without the profile context"):
            await evaluate(transport, singular())

    async def test_a_profile_this_build_cannot_read_is_refused(self) -> None:
        body = {"decision": True, "context": {**ALLOW_CONTEXT, "profile": "profile-2099-01-01"}}
        transport = RecordingTransport(body=body)
        with pytest.raises(AuthZENProtocolError, match="profile-2099-01-01"):
            await evaluate(transport, singular())

    async def test_a_state_this_build_does_not_know_is_refused(self) -> None:
        body = {"decision": False, "context": {**DENY_CONTEXT, "state": "QUARANTINE"}}
        transport = RecordingTransport(body=body)
        with pytest.raises(AuthZENProtocolError, match="QUARANTINE"):
            await evaluate(transport, singular())

    @pytest.mark.parametrize(
        ("decision", "state"),
        [
            (True, "DENY"),
            (True, "CHALLENGE"),
            (True, "ERROR"),
            (False, "ALLOW"),
        ],
    )
    async def test_a_decision_that_disagrees_with_its_state_is_refused(
        self, decision: bool, state: str
    ) -> None:
        """Both directions.

        A ``true`` boolean beside a DENY state is the dangerous one; a ``false``
        boolean beside ALLOW is the mirror, and a check written only against the
        first would pass it. Neither is actionable: one of the two renderings of
        the outcome is wrong and there is no safe way to pick.
        """
        body = {"decision": decision, "context": {**ALLOW_CONTEXT, "state": state}}
        transport = RecordingTransport(body=body)
        with pytest.raises(AuthZENProtocolError, match="disagree"):
            await evaluate(transport, singular())

    async def test_obligations_on_a_refusal_are_refused(self) -> None:
        """Obligations ride only on an executable decision. Attaching them to a
        denial invites an enforcement point to discharge them and proceed.
        """
        body = {
            "decision": False,
            "context": {
                **DENY_CONTEXT,
                "obligations": [
                    {
                        "type": "field_redact",
                        "target": "args.query",
                        "mandatory": True,
                        "source_policy": "legacy:redact_pii",
                        "schema_version": 1,
                    }
                ],
            },
        }
        transport = RecordingTransport(body=body)
        with pytest.raises(AuthZENProtocolError, match="attached obligations to a DENY"):
            await evaluate(transport, singular())

    async def test_a_body_that_is_not_json_is_refused(self) -> None:
        transport = RecordingTransport(body=b"<html>gateway timeout</html>")
        with pytest.raises(AuthZENProtocolError, match="could not be decoded"):
            await evaluate(transport, singular())

    async def test_the_happy_path_returns_a_decision(self) -> None:
        transport = RecordingTransport()
        decision = await evaluate(transport, singular())
        assert decision.allowed is True
        assert decision.state == "ALLOW"
        assert decision.decision_id == "dec-1"
        assert decision.reason == "permitted"
        assert decision.category == "allowed"
        assert decision.obligations == []
        assert decision.mandatory_obligations == []
        assert decision.approval is None

    async def test_a_denial_is_a_decision_not_an_error(self) -> None:
        transport = RecordingTransport(body={"decision": False, "context": DENY_CONTEXT})
        decision = await evaluate(transport, singular())
        assert decision.allowed is False
        assert decision.state == "DENY"

    async def test_mandatory_obligations_are_separable_from_advisory_ones(self) -> None:
        body = {
            "decision": True,
            "context": {
                **ALLOW_CONTEXT,
                "obligations": [
                    {
                        "type": "field_redact",
                        "target": "args.query",
                        "mandatory": True,
                        "source_policy": "legacy:redact_pii",
                        "schema_version": 1,
                    },
                    {
                        "type": "notification",
                        "target": "ops",
                        "mandatory": False,
                        "source_policy": "p2",
                        "schema_version": 1,
                    },
                ],
            },
        }
        transport = RecordingTransport(body=body)
        decision = await evaluate(transport, singular())
        assert len(decision.obligations) == 2
        assert [o.type for o in decision.mandatory_obligations] == ["field_redact"]


class TestAllowedIsNotABareBoolean:
    def test_allowed_requires_the_operational_state(self) -> None:
        """Constructed by hand, because ``evaluate`` refuses this body before it
        can be built.

        This is the guard test for :attr:`AuthZENDecision.allowed`: with
        ``return self.decision is True`` alone — which is what AuthZEN 1.0's
        boolean invites — a decision carrying DENY reads as permission.
        """
        decision = AuthZENDecision.model_validate(
            {"decision": True, "context": {**ALLOW_CONTEXT, "state": "DENY"}}
        )
        assert decision.allowed is False

    def test_allowed_is_false_without_a_context(self) -> None:
        decision = AuthZENDecision.model_validate({"decision": True})
        assert decision.allowed is False
        assert decision.state == "ERROR"


# --------------------------------------------------------------------------
# Refusals and their classification
# --------------------------------------------------------------------------


class TestRefusalClassification:
    async def test_a_structured_refusal_becomes_a_typed_error(self) -> None:
        body = {
            "code": "unevaluable_attribute",
            "pointer": "/evaluation/subject/properties",
            "message": "this surface cannot evaluate caller-supplied properties",
            "supported": ["args", "correlation"],
            "request_id": "req-9",
        }
        transport = RecordingTransport(status=422, body=body)
        with pytest.raises(AuthZENRefusal) as caught:
            await evaluate(transport, singular())
        refusal = caught.value
        assert refusal.code == "unevaluable_attribute"
        assert refusal.pointer == "/evaluation/subject/properties"
        assert refusal.supported == ["args", "correlation"]
        assert refusal.request_id == "req-9"
        assert refusal.refused_by == "gateway"
        assert refusal.retryable is False

    async def test_only_a_gateway_dependency_failure_is_retryable(self) -> None:
        transport = RecordingTransport(
            status=502,
            body={"code": "evaluation_unavailable", "message": "the evaluator did not answer"},
        )
        with pytest.raises(AuthZENRefusal) as caught:
            await evaluate(transport, singular())
        assert caught.value.retryable is True

    async def test_a_client_refusal_is_never_retryable(self) -> None:
        """Even were a client-side refusal ever given the retryable CODE.

        Retryability read off the code alone would tell a caller to retry an
        attribute its own resolver failed to produce — a retry loop with no
        possible exit.
        """
        refusal = AuthZENRefusal(
            "evaluation_unavailable", "unresolved locally", refused_by="client"
        )
        assert refusal.retryable is False

    async def test_a_401_is_an_authentication_error_not_a_refusal(self) -> None:
        """The gateway answers authentication before the route runs, so a 401
        never carries an AuthZEN refusal document. Surfacing it as this
        client's existing AuthenticationError keeps one exception for "your
        credentials are wrong" across every method.
        """
        transport = RecordingTransport(
            status=401, body={"error": {"code": 401, "message": "Invalid credentials"}}
        )
        with pytest.raises(AuthenticationError):
            await evaluate(transport, singular())

    async def test_a_non_refusal_error_body_is_not_read_as_a_decision(self) -> None:
        transport = RecordingTransport(status=500, body={"error": "boom"})
        with pytest.raises(AxonFlowError) as caught:
            await evaluate(transport, singular())
        assert not isinstance(caught.value, AuthZENRefusal)
        assert "HTTP 500" in str(caught.value)


# --------------------------------------------------------------------------
# Envelope shape
# --------------------------------------------------------------------------


class TestEnvelope:
    async def test_the_profile_is_negotiated_on_every_request(self) -> None:
        transport = RecordingTransport()
        await evaluate(transport, singular())
        _, _, headers = transport.calls[-1]
        assert headers[AUTHZEN_PROFILE_HEADER] == AUTHZEN_PROFILE_V1

    async def test_the_route_is_the_one_the_gateway_registers(self) -> None:
        transport = RecordingTransport()
        await evaluate(transport, singular())
        assert transport.calls[-1][0] == AUTHZEN_PATH

    async def test_a_bulk_envelope_returns_one_decision_not_a_list(self) -> None:
        transport = RecordingTransport(body={"decision": False, "context": DENY_CONTEXT})
        decision = await evaluate_envelope(
            transport,
            AuthZENEnvelope(
                evaluations=AuthZENBulk(
                    subject=AuthZENSubject(type="gateway", id="g1"),
                    action=AuthZENAction(name="tool.call"),
                    context={"args": {"query": "q"}},
                    evaluations=[
                        AuthZENRequest(resource=AuthZENResource(type="tool", id="jira/a")),
                        AuthZENRequest(resource=AuthZENResource(type="tool", id="jira/b")),
                    ],
                )
            ),
        )
        assert isinstance(decision, AuthZENDecision)
        assert decision.allowed is False

    def test_an_envelope_naming_both_members_is_refused(self) -> None:
        with pytest.raises(Exception, match=r"exactly one of evaluation or evaluations"):
            AuthZENEnvelope(
                evaluation=singular(),
                evaluations=AuthZENBulk(evaluations=[singular()]),
            )

    def test_an_envelope_naming_neither_member_is_refused(self) -> None:
        with pytest.raises(Exception, match=r"exactly one of evaluation or evaluations"):
            AuthZENEnvelope()

    def test_a_bulk_with_no_entries_is_refused(self) -> None:
        with pytest.raises(Exception, match="at least 1 entry"):
            AuthZENEnvelope(evaluations=AuthZENBulk(evaluations=[]))

    def test_a_singular_member_must_carry_its_own_subject_action_and_resource(self) -> None:
        with pytest.raises(Exception, match="no shared base to inherit one from"):
            AuthZENEnvelope(
                evaluation=AuthZENRequest(subject=AuthZENSubject(type="gateway", id="g1"))
            )

    async def test_an_incomplete_plural_entry_fails_before_the_round_trip(self) -> None:
        """A plural entry may omit what the base supplies — but not what NOBODY
        supplies. Without ``check_envelope_complete`` this reaches the server
        and comes back as a 422 the caller has to map onto its own request.
        """
        transport = RecordingTransport()
        with pytest.raises(AuthZENRefusal) as caught:
            await evaluate_envelope(
                transport,
                AuthZENEnvelope(
                    evaluations=AuthZENBulk(
                        subject=AuthZENSubject(type="gateway", id="g1"),
                        context={"args": {"query": "q"}},
                        evaluations=[
                            AuthZENRequest(resource=AuthZENResource(type="tool", id="jira/a"))
                        ],
                    )
                ),
            )
        assert not transport.calls
        assert caught.value.code == "incomplete_evaluation"
        assert caught.value.pointer == "/evaluations/evaluations/0"
        assert "action" in str(caught.value)

    async def test_a_plural_entry_inherits_the_base_and_is_accepted(self) -> None:
        """The control for the test above: without it, a completeness check that
        simply refused every entry would look equally green.
        """
        transport = RecordingTransport()
        await evaluate_envelope(
            transport,
            AuthZENEnvelope(
                evaluations=AuthZENBulk(
                    subject=AuthZENSubject(type="gateway", id="g1"),
                    action=AuthZENAction(name="tool.call"),
                    context={"args": {"query": "q"}},
                    evaluations=[
                        AuthZENRequest(resource=AuthZENResource(type="tool", id="jira/a"))
                    ],
                )
            ),
        )
        assert transport.calls

    async def test_a_blank_subject_id_fails_before_the_round_trip(self) -> None:
        transport = RecordingTransport()
        with pytest.raises(AuthZENRefusal) as caught:
            await evaluate(transport, singular(subject=AuthZENSubject(type="gateway", id="   ")))
        assert not transport.calls
        assert caught.value.pointer == "/evaluation/subject/id"

    async def test_the_client_does_not_second_guess_the_deployment(self) -> None:
        """An action name this SDK has never heard of is SENT, not refused.

        Which actions are evaluable is deployment state the SDK does not have.
        A client that guessed would refuse requests a newer gateway accepts,
        and the caller could not tell an out-of-date SDK from a wrong request.
        """
        transport = RecordingTransport()
        await evaluate(transport, singular(action=AuthZENAction(name="warehouse.pick")))
        assert transport.calls
        assert transport.sent["evaluation"]["action"]["name"] == "warehouse.pick"


# --------------------------------------------------------------------------
# The generated models, on the shapes only a CHALLENGE decision carries
# --------------------------------------------------------------------------


APPROVAL = {
    "all_of": [
        {
            "quorum": 2,
            "eligible": [
                {"kind": "principal", "type": "user", "local": "alice", "qualifier": "corp"},
                {"kind": "group", "type": "team", "local": "risk"},
            ],
        }
    ],
    "separation_of_duties": True,
    "expires_at": "2026-09-02T00:00:00Z",
}


class TestTheGeneratedModels:
    """The approval requirement, its clauses and their identifiers appear only on
    responses the fixtures above never produce.

    They are the deepest part of the contract and the part a caller acts on
    under the most pressure (a human approval is pending), so they get their own
    cases rather than being left to whichever fixture happens to reach them.
    """

    async def test_a_challenge_decision_carries_an_approval_requirement(self) -> None:
        body = {
            "decision": False,
            "context": {
                **DENY_CONTEXT,
                "state": "CHALLENGE",
                "category": "approval_required",
                "reason": "approval_required",
                "approval": APPROVAL,
            },
        }
        decision = await evaluate(RecordingTransport(body=body), singular())
        assert decision.allowed is False
        assert decision.state == "CHALLENGE"
        assert decision.approval is not None
        assert decision.approval.all_of[0].quorum == 2
        assert [i.local for i in decision.approval.all_of[0].eligible] == ["alice", "risk"]
        # An optional member the server omitted must not be invented.
        assert decision.approval.all_of[0].eligible[1].qualifier is None

    async def test_an_approval_clause_with_an_empty_eligible_set_is_refused(self) -> None:
        """min_items on a nested array. A quorum drawn from nobody is a challenge
        no one can satisfy, which an enforcement point would sit on forever.
        """
        body = {
            "decision": False,
            "context": {
                **DENY_CONTEXT,
                "state": "CHALLENGE",
                "approval": {**APPROVAL, "all_of": [{"quorum": 1, "eligible": []}]},
            },
        }
        with pytest.raises(AuthZENProtocolError, match="at least 1 entry"):
            await evaluate(RecordingTransport(body=body), singular())

    async def test_a_fractional_value_where_the_contract_declares_an_int_is_refused(self) -> None:
        body = {
            "decision": True,
            "context": {
                **ALLOW_CONTEXT,
                "obligations": [
                    {
                        "type": "field_redact",
                        "target": "args.query",
                        "mandatory": True,
                        "source_policy": "legacy:redact_pii",
                        "schema_version": 1.5,
                    }
                ],
            },
        }
        with pytest.raises(AuthZENProtocolError, match="could not be decoded"):
            await evaluate(RecordingTransport(body=body), singular())

    async def test_an_obligations_fulfillment_params_survive_as_strings(self) -> None:
        body = {
            "decision": True,
            "context": {
                **ALLOW_CONTEXT,
                "obligations": [
                    {
                        "type": "field_redact",
                        "target": "args.query",
                        "params": {
                            "fulfillment_endpoint": "/api/v1/mcp/check-input",
                            "fulfillment_method": "POST",
                            "fulfillment_phase": "request",
                        },
                        "mandatory": True,
                        "source_policy": "legacy:redact_pii",
                        "schema_version": 1,
                    }
                ],
            },
        }
        decision = await evaluate(RecordingTransport(body=body), singular())
        params = decision.mandatory_obligations[0].params
        assert params is not None
        assert params["fulfillment_method"] == "POST"

    async def test_an_optional_member_that_is_present_but_blank_is_refused(self) -> None:
        """min_length on an OPTIONAL member is the case a required-only check
        misses entirely: ``target`` may be omitted, but a blank one names no
        field, and the enforcement point would redact nothing while reporting
        that it had.
        """
        body = {
            "decision": True,
            "context": {
                **ALLOW_CONTEXT,
                "obligations": [
                    {
                        "type": "field_redact",
                        "target": "",
                        "mandatory": True,
                        "source_policy": "p",
                        "schema_version": 1,
                    }
                ],
            },
        }
        with pytest.raises(AuthZENProtocolError, match="present but too short"):
            await evaluate(RecordingTransport(body=body), singular())


# --------------------------------------------------------------------------
# Regression cases from the R3 review
# --------------------------------------------------------------------------


class TestTheBeltAgainstAnUnresolvedAttribute:
    """R3 round 1: the belt ran AFTER model_dump and could never fire.

    pydantic serialises AuthZENAttribute -- a dataclass -- into an ordinary
    ``{"state": ..., "value": ..., "reason": ...}`` object, so the isinstance
    check was handed a plain dict, and an attribute the resolver had not
    visited went out on the wire wearing that shape. It now runs on the MODEL,
    before serialisation.
    """

    def test_it_fires_on_an_attribute_the_resolver_did_not_visit(self) -> None:
        # Constructed by reaching past resolution, which is the only way to
        # produce the state the belt exists for: on today's contract the
        # resolver's bag coverage is total.
        envelope = AuthZENEnvelope(evaluation=singular())
        envelope.evaluation.context["smuggled"] = AuthZENAttribute.unknown("stale")  # type: ignore[index]
        with pytest.raises(AuthZENProtocolError, match="unresolved AuthZENAttribute"):
            _assert_fully_resolved(envelope)

    def test_it_names_the_member_it_found(self) -> None:
        envelope = AuthZENEnvelope(evaluation=singular())
        envelope.evaluation.context["args"]["smuggled"] = AuthZENAttribute.absent()  # type: ignore[index]
        with pytest.raises(AuthZENProtocolError, match=r"/evaluation/context/args/smuggled"):
            _assert_fully_resolved(envelope)

    def test_it_passes_a_fully_resolved_envelope(self) -> None:
        """The control: a belt that raised on everything would look as green."""
        _assert_fully_resolved(resolve_envelope(AuthZENEnvelope(evaluation=singular())))

    def test_to_wire_refuses_an_unresolved_attribute(self) -> None:
        """Named for what it asserts.

        The RESOLVER catches this one, not the belt -- a smuggled attribute
        inside ``context`` is in a bag the resolver walks. What this pins is
        that ``to_wire`` runs the same pipeline ``evaluate`` does, so the
        document it shows a support engineer is the document that would have
        been sent.
        """
        envelope = AuthZENEnvelope(evaluation=singular())
        envelope.evaluation.context["smuggled"] = AuthZENAttribute.unknown("stale")  # type: ignore[index]
        with pytest.raises(AuthZENRefusal) as caught:
            to_wire(envelope)
        assert caught.value.pointer == "/evaluation/context/smuggled"


class TestCyclicInput:
    async def test_a_self_referential_bag_is_a_typed_refusal(self) -> None:
        """Not a RecursionError.

        A caller that builds a cycle gets the same typed refusal every other
        malformed bag gets, rather than an exception type nothing documents and
        no enforcement point catches.
        """
        cycle: dict[str, Any] = {"query": "q"}
        cycle["self"] = cycle
        transport = RecordingTransport()
        with pytest.raises(AuthZENRefusal) as caught:
            await evaluate(transport, singular(context={"args": cycle}))
        assert not transport.calls
        assert caught.value.code == "unevaluable_attribute"
        assert "nests deeper" in str(caught.value)


class TestRefusalDecodingIsForwardCompatible:
    """R3 round 1: strict decoding of the REFUSAL envelope is a trap.

    Strictness on a DECISION is a safety control -- an unread member may be the
    one that constrains an allow. A refusal constrains nothing, so the same
    strictness buys no safety and costs the caller the typed refusal itself:
    one additive field would degrade every refusal into a bare error, losing
    the code, the pointer, the supported set and the retryable signal.
    """

    async def test_an_additive_member_does_not_destroy_the_typed_refusal(self) -> None:
        body = {
            "code": "evaluation_unavailable",
            "message": "the evaluator did not answer",
            "pointer": "/evaluation",
            "retry_after_seconds": 30,  # a member a future gateway might add
        }
        with pytest.raises(AuthZENRefusal) as caught:
            await evaluate(RecordingTransport(status=502, body=body), singular())
        assert caught.value.code == "evaluation_unavailable"
        assert caught.value.pointer == "/evaluation"
        assert caught.value.retryable is True

    async def test_a_body_with_no_code_is_not_read_as_a_refusal(self) -> None:
        """The control. Leniency must not invent a refusal the server never made."""
        with pytest.raises(AxonFlowError) as caught:
            await evaluate(RecordingTransport(status=500, body={"detail": "boom"}), singular())
        assert not isinstance(caught.value, AuthZENRefusal)


class TestTheTwoSDKsNameMistakesTheSameWay:
    """Cross-SDK parity cases. Each has a byte-for-byte sibling in
    ``tests/authzen.test.ts``; a divergence here is a divergence there.
    """

    async def test_an_incomplete_singular_envelope_is_a_typed_refusal(self) -> None:
        """Not a pydantic ValidationError.

        ``client.evaluate`` documents AuthZENRefusal, AuthZENProtocolError and
        AuthenticationError. An enforcement point that catches those would not
        catch pydantic's exception, so a mistyped request escaped a fail-closed
        handler as an error nothing on the path expects.
        """
        transport = RecordingTransport()
        with pytest.raises(AuthZENRefusal) as caught:
            await evaluate_envelope(
                transport,
                build_envelope(
                    evaluation=AuthZENRequest(subject=AuthZENSubject(type="gateway", id="g1"))
                ),
            )
        assert not transport.calls
        assert caught.value.code == "incomplete_evaluation"
        assert caught.value.pointer == "/evaluation"
        assert caught.value.refused_by == "client"

    async def test_an_envelope_naming_neither_member_is_a_malformed_envelope(self) -> None:
        transport = RecordingTransport()
        with pytest.raises(AuthZENRefusal) as caught:
            await evaluate_envelope(transport, build_envelope())
        assert not transport.calls
        assert caught.value.code == "malformed_envelope"

    def test_a_bulk_with_no_entries_is_refused_at_construction(self) -> None:
        """The one place the two SDKs cannot be made to agree, pinned so the
        difference is a decision rather than a surprise.

        A pydantic model validates when it is BUILT, so an empty bulk is
        refused at ``AuthZENBulk(...)`` -- before this SDK is called at all --
        with a message naming the rule. TypeScript has no equivalent moment: an
        object literal is inert until ``evaluateAll`` validates it, where the
        same mistake surfaces as ``AuthZENRefusal(malformed_envelope)``.

        Deferring here to match would mean giving up request models altogether,
        which is a worse surface for a worse reason. Both refuse; only the
        moment differs, and both READMEs say so.
        """
        with pytest.raises(ValidationError, match="at least 1 entry"):
            AuthZENBulk(evaluations=[])

    async def test_an_extra_member_on_a_subject_is_refused_not_dropped(self) -> None:
        """The whole surface's rule, applied client-side: mapped or refused,
        never silently ignored.
        """
        transport = RecordingTransport()
        with pytest.raises(Exception, match=r"extra_forbidden|Extra inputs"):
            AuthZENSubject(type="gateway", id="g1", department="finance")  # type: ignore[call-arg]
        assert not transport.calls
