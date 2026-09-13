"""The PEP capability handshake: the declaration, its bytes, and where it goes.

Three claims, each asserted rather than argued:

1. PARITY. A declaration encodes to exactly the bytes the platform's own
   reference encoder produces for it, and a declaration the platform would
   refuse fails here, at construction, naming the same member. The golden
   values below came from the platform's encoder and were round-tripped
   through its decoder; the refusal cases follow its validator rule by rule.
2. PLACEMENT. The declaration reaches the wire on every method whose route
   reads it (decide, AuthZEN evaluation, the MCP check routes, the gateway
   pre-check), on every request those methods make, and on no other route.
   It is never a default header.
3. PRECEDENCE. A per-call declaration replaces the client's on that call
   only. A header put in ``extra_headers`` by hand is sent as given. A call
   carrying both is refused before anything is sent.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import inspect
import json

import pytest

from axonflow import (
    AUTHZEN_OBLIGATION_TYPE_VALUES,
    AUTHZEN_PROFILE_V1,
    PEP_HANDSHAKE_HEADER,
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
    PEPHandshakeError,
    SyncAxonFlow,
)
from axonflow.pep_handshake import MAX_PEP_HANDSHAKE_CAPABILITIES

BASE = "http://localhost:8080"


def _caps(*pairs: tuple[str, int]) -> list[PEPCapability]:
    return [PEPCapability(kind, version) for kind, version in pairs]


def _client(**kwargs: object) -> AxonFlow:
    return AxonFlow(
        endpoint=BASE,
        client_id="test-client",
        client_secret="test-secret",
        **kwargs,  # type: ignore[arg-type]
    )


# Produced by the platform's reference encoder for the declarations beside
# them, and accepted by its decoder. The capabilities are listed in the order
# they were GIVEN; for "unsorted" that is not the canonical order.
GOLDEN = [
    pytest.param(
        "sdk-python",
        "https://pep.example.test",
        (),
        "eyJwcm9maWxlX3ZlcnNpb24iOjEsInBlcF9pZCI6InNkay1weXRob24iLCJhdWRpZW5jZSI6Imh0dHBz"
        "Oi8vcGVwLmV4YW1wbGUudGVzdCIsImNhcGFiaWxpdGllcyI6W119",
        id="empty",
    ),
    pytest.param(
        "gateway.request-1",
        "urn:example:aud",
        (("field_redact", 2), ("approval_challenge", 1), ("field_redact", 1), ("notification", 3)),
        "eyJwcm9maWxlX3ZlcnNpb24iOjEsInBlcF9pZCI6ImdhdGV3YXkucmVxdWVzdC0xIiwiYXVkaWVuY2Ui"
        "OiJ1cm46ZXhhbXBsZTphdWQiLCJjYXBhYmlsaXRpZXMiOlt7InR5cGUiOiJhcHByb3ZhbF9jaGFsbGVu"
        "Z2UiLCJ2ZXJzaW9uIjoxfSx7InR5cGUiOiJmaWVsZF9yZWRhY3QiLCJ2ZXJzaW9uIjoxfSx7InR5cGUi"
        "OiJmaWVsZF9yZWRhY3QiLCJ2ZXJzaW9uIjoyfSx7InR5cGUiOiJub3RpZmljYXRpb24iLCJ2ZXJzaW9u"
        "IjozfV19",
        id="unsorted",
    ),
    pytest.param(
        "a",
        "A",
        (("step_up_authentication", 1),),
        "eyJwcm9maWxlX3ZlcnNpb24iOjEsInBlcF9pZCI6ImEiLCJhdWRpZW5jZSI6IkEiLCJjYXBhYmlsaXRp"
        "ZXMiOlt7InR5cGUiOiJzdGVwX3VwX2F1dGhlbnRpY2F0aW9uIiwidmVyc2lvbiI6MX1dfQ",
        id="minimal",
    ),
]

# The 64-capability vector, pinned by length and digest rather than inline.
SIXTY_FOUR_LEN = 3364
SIXTY_FOUR_SHA256 = "cdb2b368348bceaed99ca92647afeecd70981604157b60ad66067953a371edbf"

DECLARED = PEPHandshake(
    pep_id="request-path",
    audience="https://pep.example.test",
    capabilities=_caps(("field_redact", 1)),
)
OTHER = PEPHandshake(
    pep_id="response-path",
    audience="https://pep.example.test",
    capabilities=_caps(("field_mask", 1)),
)


# --------------------------------------------------------------------------
# 1. Parity with the platform
# --------------------------------------------------------------------------


class TestTheEncodingIsThePlatforms:
    @pytest.mark.parametrize(("pep_id", "audience", "pairs", "header"), GOLDEN)
    def test_the_bytes_are_the_platform_encoders(self, pep_id, audience, pairs, header):
        declared = PEPHandshake(pep_id=pep_id, audience=audience, capabilities=_caps(*pairs))
        assert declared.header_value == header

    def test_sixty_four_capabilities_encode_under_the_byte_cap(self):
        # The platform's construction: every declared type in canonical order,
        # at version 1, then 2, and so on, stopping at the count cap.
        kinds = sorted(AUTHZEN_OBLIGATION_TYPE_VALUES)
        pairs = [(kind, v) for v in range(1, 6) for kind in kinds][:MAX_PEP_HANDSHAKE_CAPABILITIES]
        declared = PEPHandshake(pep_id="p", audience="a", capabilities=_caps(*pairs))
        assert len(declared.header_value) == SIXTY_FOUR_LEN
        assert hashlib.sha256(declared.header_value.encode()).hexdigest() == SIXTY_FOUR_SHA256

    def test_a_declaration_past_the_byte_cap_is_refused_as_a_whole(self):
        pairs = [("step_up_authentication", 1_000_000_000 + i) for i in range(64)]
        with pytest.raises(PEPHandshakeError) as caught:
            PEPHandshake(pep_id="p", audience="a", capabilities=_caps(*pairs))
        # The document is at fault, not a member, exactly as the platform reports it.
        assert caught.value.pointer == ""

    def test_the_order_of_declaration_does_not_change_the_bytes(self):
        given = [("notification", 3), ("field_redact", 2), ("approval_challenge", 1)]
        one = PEPHandshake(pep_id="gw", audience="a", capabilities=_caps(*given))
        other = PEPHandshake(pep_id="gw", audience="a", capabilities=_caps(*reversed(given)))
        assert one.header_value == other.header_value
        assert one == other
        assert one.capabilities == tuple(sorted(_caps(*given)))

    def test_the_header_is_unpadded_base64url_of_the_canonical_document(self):
        declared = PEPHandshake(
            pep_id="gw",
            audience="https://pep.example.test",
            capabilities=_caps(("field_redact", 2), ("field_redact", 1)),
        )
        value = declared.header_value
        assert not set(value) & {"=", "+", "/"}
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        assert raw == (
            b'{"profile_version":1,"pep_id":"gw","audience":"https://pep.example.test",'
            b'"capabilities":[{"type":"field_redact","version":1},'
            b'{"type":"field_redact","version":2}]}'
        )

    def test_an_empty_declaration_is_a_declaration(self):
        declared = PEPHandshake(pep_id="gw", audience="a", capabilities=())
        raw = base64.urlsafe_b64decode(
            declared.header_value + "=" * (-len(declared.header_value) % 4)
        )
        assert json.loads(raw)["capabilities"] == []

    def test_the_callers_sequence_is_copied(self):
        given = _caps(("field_redact", 1))
        declared = PEPHandshake(pep_id="gw", audience="a", capabilities=given)
        before = declared.header_value
        given.append(PEPCapability("field_mask", 1))
        assert declared.capabilities == (PEPCapability("field_redact", 1),)
        assert declared.header_value == before

    def test_it_is_immutable(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            DECLARED.pep_id = "other"  # type: ignore[misc]


class TestTheRefusalsAreThePlatforms:
    @pytest.mark.parametrize(
        ("pep_id", "audience", "capabilities", "pointer"),
        [
            pytest.param("", "a", (), "/pep_id", id="pep_id-empty"),
            pytest.param("Gateway", "a", (), "/pep_id", id="pep_id-upper-case"),
            pytest.param("client:gw", "a", (), "/pep_id", id="pep_id-colon"),
            pytest.param("-gw", "a", (), "/pep_id", id="pep_id-leading-dash"),
            pytest.param("gw\n", "a", (), "/pep_id", id="pep_id-trailing-newline"),
            pytest.param("g" * 129, "a", (), "/pep_id", id="pep_id-129-bytes"),
            pytest.param(7, "a", (), "/pep_id", id="pep_id-not-a-string"),
            pytest.param("gw", "", (), "/audience", id="audience-empty"),
            pytest.param("gw", "/aud", (), "/audience", id="audience-leading-slash"),
            pytest.param("gw", "a b", (), "/audience", id="audience-space"),
            pytest.param("gw", "aud\n", (), "/audience", id="audience-trailing-newline"),
            pytest.param("gw", "a" * 129, (), "/audience", id="audience-129-bytes"),
            pytest.param("gw", "a", None, "/capabilities", id="capabilities-absent"),
            pytest.param("gw", "a", "field_redact", "/capabilities", id="capabilities-a-string"),
            pytest.param("gw", "a", {"field_redact": 1}, "/capabilities", id="capabilities-a-map"),
            pytest.param(
                "gw", "a", [("field_redact", 1)], "/capabilities", id="capabilities-a-pair"
            ),
            pytest.param(
                "gw",
                "a",
                _caps(("field_redact", 1), ("field_redact", 1)),
                "/capabilities",
                id="capabilities-repeated",
            ),
            pytest.param(
                "gw",
                "a",
                _caps(*[("field_redact", v) for v in range(1, 66)]),
                "/capabilities",
                id="capabilities-65",
            ),
        ],
    )
    def test_the_member_at_fault_is_named(self, pep_id, audience, capabilities, pointer):
        with pytest.raises(PEPHandshakeError) as caught:
            PEPHandshake(pep_id=pep_id, audience=audience, capabilities=capabilities)
        assert caught.value.pointer == pointer
        assert str(caught.value).startswith(f"{PEP_HANDSHAKE_HEADER}: {pointer}: ")

    @pytest.mark.parametrize(
        ("kind", "version"),
        [
            pytest.param("redact_pii", 1, id="a-legacy-obligation-name"),
            pytest.param("Field_Redact", 1, id="wrong-case"),
            pytest.param(None, 1, id="no-type"),
            pytest.param("field_redact", 0, id="version-zero"),
            pytest.param("field_redact", -1, id="version-negative"),
            pytest.param("field_redact", True, id="version-a-bool"),
            pytest.param("field_redact", 1.0, id="version-a-float"),
            pytest.param("field_redact", "1", id="version-a-string"),
        ],
    )
    def test_a_capability_the_platform_cannot_match_is_refused(self, kind, version):
        with pytest.raises(PEPHandshakeError) as caught:
            PEPCapability(kind, version)
        assert caught.value.pointer == "/capabilities"

    def test_the_longest_identifiers_the_platform_reads_are_accepted(self):
        PEPHandshake(pep_id="g" * 128, audience="A" * 128, capabilities=())

    def test_a_uri_audience_is_accepted(self):
        PEPHandshake(pep_id="gw.request-1", audience="https://api.example.com/v1", capabilities=())

    def test_every_obligation_type_this_build_declares_is_accepted(self):
        for kind in AUTHZEN_OBLIGATION_TYPE_VALUES:
            PEPCapability(kind, 1)

    def test_the_error_is_an_axonflow_error_and_a_value_error(self):
        with pytest.raises(AxonFlowError):
            PEPCapability("field_redact", 0)
        with pytest.raises(ValueError, match="/capabilities"):
            PEPCapability("field_redact", 0)


# --------------------------------------------------------------------------
# 2. Placement: the four planes, every request they make, and nothing else
# --------------------------------------------------------------------------

REDACT_OBLIGATION = {
    "type": "redact_pii",
    "fulfillment": {
        "endpoint": "/api/v1/mcp/check-input",
        "method": "POST",
        "phase": "request",
        "content_types": ["text/plain"],
    },
}
DECIDE_BODY = {
    "verdict": "allow",
    "decision_id": "dec-1",
    "obligations": [],
    "evaluated_policies": [],
    "stage": "tool",
}
REDACTING_DECIDE_BODY = {**DECIDE_BODY, "obligations": [REDACT_OBLIGATION]}
AUTHZEN_BODY = {
    "decision": True,
    "context": {
        "profile": AUTHZEN_PROFILE_V1,
        "state": "ALLOW",
        "category": "allowed",
        "reason": "permitted",
        "decision_id": "dec-1",
        "schema_version": "2026-08-29",
    },
}
CHECK_BODY = {"allowed": True, "policies_evaluated": 1}
FULFIL_BODY = {**CHECK_BODY, "redaction_evaluated": True, "redacted": False}
PRE_CHECK_BODY = {"context_id": "ctx-1", "approved": True, "expires_at": "2030-01-01T00:00:00Z"}

DECIDE = "/api/v1/decide"
EVALUATION = "/api/v1/access/evaluation"
CHECK_INPUT = "/api/v1/mcp/check-input"
CHECK_OUTPUT = "/api/v1/mcp/check-output"
PRE_CHECK = "/api/policy/pre-check"

_REQUEST = AuthZENRequest(
    subject=AuthZENSubject(type="gateway", id="gw"),
    action=AuthZENAction(name="llm.completion"),
    resource=AuthZENResource(type="llm", id="llm"),
    context={"args": {"query": "hi"}},
)
_BULK = AuthZENBulk(
    subject=AuthZENSubject(type="gateway", id="gw"),
    action=AuthZENAction(name="tool.call"),
    context={"args": {"query": "hi"}},
    evaluations=[AuthZENRequest(resource=AuthZENResource(type="tool", id="jira/a"))],
)

# (responses in the order the method makes its requests, the call). Each call
# takes the client and the per-call keywords the test passes through.
PLANE_CALLS = [
    pytest.param(
        [(DECIDE, DECIDE_BODY)],
        lambda c, **kw: c.decide(DecideRequest(stage="tool", query="hi"), **kw),
        id="decide",
    ),
    pytest.param(
        [(DECIDE, REDACTING_DECIDE_BODY), (CHECK_INPUT, FULFIL_BODY)],
        lambda c, **kw: c.decide_and_fulfill(DecideRequest(stage="tool", query="hi"), **kw),
        id="decide_and_fulfill",
    ),
    pytest.param(
        [(CHECK_INPUT, FULFIL_BODY)],
        lambda c, **kw: c.fulfill_request(
            DecideResponse.model_validate(REDACTING_DECIDE_BODY), "hi", **kw
        ),
        id="fulfill_request",
    ),
    pytest.param(
        [(EVALUATION, AUTHZEN_BODY)],
        lambda c, **kw: c.evaluate(_REQUEST, **kw),
        id="evaluate",
    ),
    pytest.param(
        [(EVALUATION, AUTHZEN_BODY)],
        lambda c, **kw: c.evaluate_all(_BULK, **kw),
        id="evaluate_all",
    ),
    pytest.param(
        [(CHECK_INPUT, CHECK_BODY)],
        lambda c, **kw: c.mcp_check_input("postgres", "select 1", **kw),
        id="mcp_check_input",
    ),
    pytest.param(
        [(CHECK_INPUT, CHECK_BODY)],
        lambda c, **kw: c.check_tool_input("postgres", "select 1", **kw),
        id="check_tool_input",
    ),
    pytest.param(
        [(CHECK_OUTPUT, CHECK_BODY)],
        lambda c, **kw: c.mcp_check_output("postgres", message="hi", **kw),
        id="mcp_check_output",
    ),
    pytest.param(
        [(CHECK_OUTPUT, CHECK_BODY)],
        lambda c, **kw: c.check_tool_output("postgres", message="hi", **kw),
        id="check_tool_output",
    ),
    pytest.param(
        [(PRE_CHECK, PRE_CHECK_BODY)],
        lambda c, **kw: c.get_policy_approved_context("tok", "hi", **kw),
        id="get_policy_approved_context",
    ),
    pytest.param(
        [(PRE_CHECK, PRE_CHECK_BODY)],
        lambda c, **kw: c.pre_check("tok", "hi", **kw),
        id="pre_check",
    ),
]

#: The methods whose requests reach a plane that reads the declaration.
PLANE_METHODS = frozenset(p.id for p in PLANE_CALLS)


def _respond(httpx_mock, responses):
    for path, body in responses:
        httpx_mock.add_response(url=f"{BASE}{path}", method="POST", json=body)


class TestTheDeclarationReachesEveryPlaneRequest:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("responses", "call"), PLANE_CALLS)
    async def test_the_clients_declaration(self, httpx_mock, responses, call):
        _respond(httpx_mock, responses)
        await call(_client(pep_handshake=DECLARED))
        sent = httpx_mock.get_requests()
        assert [r.url.path for r in sent] == [path for path, _ in responses]
        for request in sent:
            assert request.headers.get_list(PEP_HANDSHAKE_HEADER) == [DECLARED.header_value]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("responses", "call"), PLANE_CALLS)
    async def test_a_per_call_declaration_is_forwarded_through_every_delegation(
        self, httpx_mock, responses, call
    ):
        _respond(httpx_mock, responses)
        await call(_client(pep_handshake=DECLARED), pep_handshake=OTHER)
        for request in httpx_mock.get_requests():
            assert request.headers.get_list(PEP_HANDSHAKE_HEADER) == [OTHER.header_value]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("responses", "call"), PLANE_CALLS)
    async def test_no_declaration_sends_no_header(self, httpx_mock, responses, call):
        # There is no default: a client that declared nothing sends exactly
        # what it sent before the handshake existed.
        _respond(httpx_mock, responses)
        await call(_client())
        for request in httpx_mock.get_requests():
            assert PEP_HANDSHAKE_HEADER not in request.headers


class TestNoOtherRouteReceivesIt:
    @pytest.mark.asyncio
    async def test_not_the_proxy_route(self, httpx_mock):
        httpx_mock.add_response(
            url=f"{BASE}/api/request",
            json={"success": True, "data": {"answer": "4"}, "blocked": False},
        )
        await _client(pep_handshake=DECLARED).proxy_llm_call("", "What is 2+2?", "chat")
        assert PEP_HANDSHAKE_HEADER not in httpx_mock.get_requests()[-1].headers

    @pytest.mark.asyncio
    async def test_not_the_health_route(self, httpx_mock):
        httpx_mock.add_response(url=f"{BASE}/health", json={"status": "healthy"})
        await _client(pep_handshake=DECLARED).health_check()
        assert PEP_HANDSHAKE_HEADER not in httpx_mock.get_requests()[-1].headers

    def test_it_is_never_a_default_header(self):
        client = _client(pep_handshake=DECLARED)
        assert PEP_HANDSHAKE_HEADER not in client._http_client.headers
        assert PEP_HANDSHAKE_HEADER not in client._map_http_client.headers


# --------------------------------------------------------------------------
# 3. Precedence, and the values that are not declarations
# --------------------------------------------------------------------------


class TestPrecedence:
    @pytest.mark.asyncio
    async def test_a_per_call_declaration_replaces_the_clients_on_that_call_only(self, httpx_mock):
        httpx_mock.add_response(url=f"{BASE}{CHECK_INPUT}", json=CHECK_BODY, is_reusable=True)
        client = _client(pep_handshake=DECLARED)

        await client.mcp_check_input("postgres", "a", pep_handshake=OTHER)
        await client.mcp_check_input("postgres", "b")

        first, second = httpx_mock.get_requests()
        assert first.headers.get_list(PEP_HANDSHAKE_HEADER) == [OTHER.header_value]
        assert second.headers.get_list(PEP_HANDSHAKE_HEADER) == [DECLARED.header_value]

    @pytest.mark.asyncio
    async def test_a_per_call_declaration_does_not_stick_to_a_client_without_one(self, httpx_mock):
        httpx_mock.add_response(url=f"{BASE}{DECIDE}", json=DECIDE_BODY, is_reusable=True)
        client = _client()

        await client.decide(DecideRequest(stage="tool", query="a"), pep_handshake=OTHER)
        await client.decide(DecideRequest(stage="tool", query="b"))

        first, second = httpx_mock.get_requests()
        assert first.headers.get_list(PEP_HANDSHAKE_HEADER) == [OTHER.header_value]
        assert PEP_HANDSHAKE_HEADER not in second.headers

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", [PEP_HANDSHAKE_HEADER, PEP_HANDSHAKE_HEADER.lower()])
    async def test_a_hand_set_header_is_sent_as_given_and_only_once(self, httpx_mock, name):
        httpx_mock.add_response(url=f"{BASE}{CHECK_INPUT}", json=CHECK_BODY)
        client = _client(pep_handshake=DECLARED)

        await client.mcp_check_input("postgres", "a", extra_headers={name: "hand-set"})

        # One header line: the platform refuses a repeated handshake header.
        sent = httpx_mock.get_requests()[-1]
        assert sent.headers.get_list(PEP_HANDSHAKE_HEADER) == ["hand-set"]

    @pytest.mark.asyncio
    async def test_both_on_one_call_is_refused_before_anything_is_sent(self, httpx_mock):
        client = _client(pep_handshake=DECLARED)
        with pytest.raises(ValueError, match="pass one"):
            await client.mcp_check_input(
                "postgres",
                "a",
                extra_headers={PEP_HANDSHAKE_HEADER.lower(): "hand-set"},
                pep_handshake=OTHER,
            )
        assert httpx_mock.get_requests() == []

    @pytest.mark.parametrize(
        "value",
        [{"pep_id": "gw"}, DECLARED.header_value],
        ids=["a-dict", "an-encoded-string"],
    )
    def test_a_value_that_is_not_a_declaration_is_refused_at_construction(self, value):
        with pytest.raises(TypeError, match="PEPHandshake"):
            _client(pep_handshake=value)

    @pytest.mark.asyncio
    async def test_a_value_that_is_not_a_declaration_is_refused_per_call(self, httpx_mock):
        with pytest.raises(TypeError, match="PEPHandshake"):
            await _client().decide(
                DecideRequest(stage="tool", query="a"),
                pep_handshake=DECLARED.header_value,  # type: ignore[arg-type]
            )
        assert httpx_mock.get_requests() == []

    @pytest.mark.asyncio
    async def test_a_derived_client_keeps_the_declaration(self, httpx_mock):
        httpx_mock.add_response(url=f"{BASE}{DECIDE}", json=DECIDE_BODY)
        derived = _client(pep_handshake=DECLARED).as_user("user-token")

        await derived.decide(DecideRequest(stage="tool", query="a"))

        sent = httpx_mock.get_requests()[-1]
        assert sent.headers.get_list(PEP_HANDSHAKE_HEADER) == [DECLARED.header_value]


class TestTheSyncClient:
    def test_the_clients_declaration(self, httpx_mock):
        httpx_mock.add_response(url=f"{BASE}{DECIDE}", json=DECIDE_BODY)
        client = AxonFlow.sync(BASE, "test-client", "test-secret", pep_handshake=DECLARED)

        client.decide(DecideRequest(stage="tool", query="a"))

        sent = httpx_mock.get_requests()[-1]
        assert sent.headers.get_list(PEP_HANDSHAKE_HEADER) == [DECLARED.header_value]

    def test_a_per_call_declaration(self, httpx_mock):
        httpx_mock.add_response(url=f"{BASE}{EVALUATION}", json=AUTHZEN_BODY)
        client = AxonFlow.sync(BASE, "test-client", "test-secret", pep_handshake=DECLARED)

        client.evaluate(_REQUEST, pep_handshake=OTHER)

        sent = httpx_mock.get_requests()[-1]
        assert sent.headers.get_list(PEP_HANDSHAKE_HEADER) == [OTHER.header_value]


class TestTheParameterCensus:
    """The keyword sits on exactly the methods whose requests reach a plane.

    A method added later that reaches a plane without the keyword, or one that
    takes it without reaching a plane, fails here; the placement tests above
    then prove each listed method sends it.
    """

    @pytest.mark.parametrize("cls", [AxonFlow, SyncAxonFlow])
    def test_exactly_the_plane_methods_take_it(self, cls):
        takers = {
            name
            for name, member in inspect.getmembers(cls, inspect.isfunction)
            if not name.startswith("_") and "pep_handshake" in inspect.signature(member).parameters
        }
        assert takers == PLANE_METHODS

    @pytest.mark.parametrize("name", sorted(PLANE_METHODS))
    def test_keyword_only_defaulting_to_none_and_the_same_on_both_clients(self, name):
        signatures = [inspect.signature(getattr(cls, name)) for cls in (AxonFlow, SyncAxonFlow)]
        for signature in signatures:
            param = signature.parameters["pep_handshake"]
            assert param.kind is inspect.Parameter.KEYWORD_ONLY
            assert param.default is None
        assert list(signatures[0].parameters) == list(signatures[1].parameters)

    def test_the_constructor_takes_it_with_no_default_declaration(self):
        param = inspect.signature(AxonFlow).parameters["pep_handshake"]
        assert param.kind is inspect.Parameter.KEYWORD_ONLY
        assert param.default is None
