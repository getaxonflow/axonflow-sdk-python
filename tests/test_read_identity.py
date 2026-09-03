"""Read-path per-user identity (X-User-Token) and the read-scope contract.

Companion to axonflow/read_identity.py. Also carries the #234 fail-open tests:
a marker document whose state this build does not recognise must be REFUSED,
not walked as ordinary data and sent on the wire.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

import httpx
import pytest

from axonflow import AxonFlow
from axonflow.authzen import (
    AUTHZEN_ATTRIBUTE_MARKER,
    AuthZENAction,
    AuthZENAttribute,
    AuthZENRequest,
    AuthZENResource,
    AuthZENSubject,
    build_envelope,
    to_wire,
)
from axonflow.decisions import ListDecisionsOptions
from axonflow.exceptions import AxonFlowError
from axonflow.read_identity import (
    HEADER_READ_SCOPE,
    HEADER_USER_TOKEN,
    ReadScope,
    ReadScopeError,
    stamp_read_identity,
    use_read_identity,
)

# Distinctive on purpose: the leak tests grep whole captured streams for it,
# and a value like "tok" would match by accident.
TEST_TOKEN = "eyJhbGciOiJIUzI1NiJ9.SENTINEL-USER-TOKEN-a7f3c91e.sig"  # noqa: S105

# A complete DecisionSummary row. Deliberately not a stub with only
# decision_id: a page the model cannot validate would fail these tests for
# a reason that has nothing to do with identity.
ROW = {
    "decision_id": "d1",
    "timestamp": "2026-04-17T12:00:00Z",
    "decision": "blocked",
}
ROW_PAGE = {"decisions": [ROW]}
# A complete DecisionExplanation, same reasoning as ROW.
EXPLANATION = {
    "decision_id": "d1",
    "timestamp": "2026-04-17T12:00:00Z",
    "decision": "blocked",
}


def _client(**kwargs) -> AxonFlow:
    return AxonFlow(
        endpoint="http://localhost:8080",
        client_id="org",
        client_secret="secret",
        **kwargs,
    )


# ==========================================================================
# Option plumbing: present when configured, absent when not, exactly once
# ==========================================================================


@pytest.mark.asyncio
async def test_header_absent_when_not_configured(httpx_mock) -> None:
    httpx_mock.add_response(
        url=re.compile(r".*/api/v1/decisions.*"),
        json=ROW_PAGE,
    )
    await _client().list_decisions()

    request = httpx_mock.get_requests()[-1]
    assert HEADER_USER_TOKEN not in request.headers, (
        "a client with no identity configured must send no identity header at all, not an empty one"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "body", "url"),
    [
        ("explain", EXPLANATION, r".*/api/v1/decisions/d1/explain"),
        ("list", ROW_PAGE, r".*/api/v1/decisions\?.*|.*/api/v1/decisions$"),
    ],
)
async def test_client_level_token_travels_on_every_read(httpx_mock, name, body, url) -> None:
    """One client-wide identity, both read methods, header asserted once each.

    A per-method sprinkle would show up here as a method that silently omits it.
    """
    httpx_mock.add_response(url=re.compile(url), json=body)
    client = _client(user_token=TEST_TOKEN)

    if name == "explain":
        await client.explain_decision("d1")
    else:
        await client.list_decisions()

    request = httpx_mock.get_requests()[-1]
    values = request.headers.get_list(HEADER_USER_TOKEN)
    assert values == [TEST_TOKEN], f"{HEADER_USER_TOKEN} must appear exactly once, got {values!r}"


@pytest.mark.asyncio
async def test_per_call_overrides_client_level(httpx_mock) -> None:
    httpx_mock.add_response(url=re.compile(r".*/explain"), json=EXPLANATION)
    await _client(user_token="client-level-token").explain_decision("d1", user_token=TEST_TOKEN)

    assert httpx_mock.get_requests()[-1].headers[HEADER_USER_TOKEN] == TEST_TOKEN


@pytest.mark.asyncio
async def test_per_call_empty_token_clears_client_level(httpx_mock) -> None:
    """An explicitly empty per-call identity must NOT fall back.

    Falling back would make the option unable to express the very state the
    platform treats as distinct (ReadScope.NONE).
    """
    httpx_mock.add_response(
        url=re.compile(r".*/api/v1/decisions.*"),
        json={"decisions": []},
        headers={HEADER_READ_SCOPE: ReadScope.OWN_ROWS},
    )
    await _client(user_token=TEST_TOKEN).list_decisions(user_token="   ")

    assert HEADER_USER_TOKEN not in httpx_mock.get_requests()[-1].headers


@pytest.mark.asyncio
async def test_per_call_does_not_leak_into_the_next_call(httpx_mock) -> None:
    httpx_mock.add_response(url=re.compile(r".*/explain"), json=EXPLANATION, is_reusable=True)
    client = _client()

    await client.explain_decision("d1", user_token=TEST_TOKEN)
    await client.explain_decision("d1")

    assert HEADER_USER_TOKEN not in httpx_mock.get_requests()[-1].headers, (
        "a per-call identity must not become client state"
    )


@pytest.mark.asyncio
async def test_concurrent_reads_do_not_see_each_others_identity(httpx_mock) -> None:
    """Two users' reads, one client, in flight together.

    The per-call identity rides a ContextVar; a module-global would hand one
    user's read the other user's identity, which is the worst possible bug in
    an identity mechanism.
    """
    httpx_mock.add_response(
        url=re.compile(r".*/api/v1/decisions/.*"),
        json=EXPLANATION,
        is_reusable=True,
    )
    client = _client(user_token="client-level")

    async def read(token: str, decision: str) -> None:
        await client.explain_decision(decision, user_token=token)

    await asyncio.gather(
        *[read(f"token-user-{i}", f"dec-{i}") for i in range(12)],
    )

    seen = {
        req.url.path.split("/")[-2]: req.headers.get(HEADER_USER_TOKEN)
        for req in httpx_mock.get_requests()
    }
    for i in range(12):
        assert seen[f"dec-{i}"] == f"token-user-{i}", (
            f"dec-{i} carried {seen[f'dec-{i}']!r}; a per-call identity crossed between "
            f"concurrent reads"
        )


@pytest.mark.asyncio
async def test_token_is_trimmed(httpx_mock) -> None:
    httpx_mock.add_response(url=re.compile(r".*/explain"), json=EXPLANATION)
    await _client(user_token=f"  {TEST_TOKEN}\n").explain_decision("d1")

    assert httpx_mock.get_requests()[-1].headers[HEADER_USER_TOKEN] == TEST_TOKEN


def test_one_transport_site() -> None:
    """The structural half of "do not build a second identity plumbing".

    Exactly one site may write the header, and the literal may be spelled once.
    """
    root = Path(__file__).resolve().parent.parent / "axonflow"
    setter = re.compile(r"headers\[HEADER_USER_TOKEN\]\s*=")
    literal = re.compile(r'"X-User-Token"')

    setters: list[str] = []
    literals: list[str] = []
    for path in sorted(root.rglob("*.py")):
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if setter.search(line):
                setters.append(f"{path.name}:{number}")
            if literal.search(line):
                literals.append(f"{path.name}:{number}")

    assert len(setters) == 1, (
        f"{HEADER_USER_TOKEN} is set at {len(setters)} sites ({setters}); it must be set at "
        f"exactly one — the platform reads it once in its proxy middleware, not per route, so "
        f"a per-method sprinkle here is a second copy of a decision made in one place on both sides"
    )
    assert len(literals) == 1, (
        f"the literal is spelled at {len(literals)} sites ({literals}); it belongs in the "
        f"HEADER_USER_TOKEN constant alone, so a rename cannot leave a stale spelling behind"
    )


# ==========================================================================
# The token is a credential: never logged, never in an error, never in telemetry
# ==========================================================================


@pytest.mark.asyncio
async def test_token_never_leaves(httpx_mock, caplog) -> None:
    """The token reaches the header and NOTHING else.

    The 404 body echoes the token back — the strongest form of the mistake,
    since the natural implementation puts the response body into the error
    message verbatim.
    """
    httpx_mock.add_response(
        url=re.compile(r".*/explain"),
        status_code=404,
        json={"error": "not found", "echo": TEST_TOKEN},
        headers={HEADER_READ_SCOPE: ReadScope.OWN_ROWS},
    )
    client = _client(user_token=TEST_TOKEN, debug=True)

    with caplog.at_level(logging.DEBUG), pytest.raises(ReadScopeError) as excinfo:
        await client.explain_decision("d1")

    # The header DID carry it — otherwise the assertions below are vacuous.
    assert httpx_mock.get_requests()[-1].headers[HEADER_USER_TOKEN] == TEST_TOKEN

    assert TEST_TOKEN not in str(excinfo.value), "the exception message carries the user token"
    assert TEST_TOKEN not in caplog.text, "the debug log carries the user token"


def test_telemetry_carries_no_identity(monkeypatch) -> None:
    """The heartbeat goes to a THIRD PARTY; a customer's employee's credential must not.

    Drives the REAL ping — both requests it makes, the ``/health`` probe
    against the caller's own endpoint and the POST to the checkpoint host — and
    records what each was actually called with, rather than asserting that
    today's code path happens not to stamp the header. That is a property one
    refactor can remove silently.

    The telemetry module calls the MODULE-LEVEL ``httpx.get``/``httpx.post``,
    not the client's transports, which is why the request event hook cannot
    reach it; recording at those two functions is therefore the whole egress
    surface of this path.
    """
    import httpx

    from axonflow import telemetry

    calls: list[tuple[str, tuple, dict]] = []

    class _Recorded:
        status_code = 200
        text = '{"version":"10.4.0","license_tier":"enterprise"}'

        @staticmethod
        def json() -> dict:
            return {"version": "10.4.0", "license_tier": "enterprise"}

        @staticmethod
        def raise_for_status() -> None:
            return None

    def _record(name: str):
        def _call(*args, **kwargs):
            calls.append((name, args, kwargs))
            return _Recorded()

        return _call

    monkeypatch.setenv("AXONFLOW_TELEMETRY", "on")
    monkeypatch.setattr(httpx, "get", _record("get"))
    monkeypatch.setattr(httpx, "post", _record("post"))

    # A client exists and holds the identity, so the leak this test looks for
    # is reachable at all.
    _client(user_token=TEST_TOKEN)
    telemetry._send_telemetry_ping_now(  # noqa: SLF001
        "http://checkpoint.invalid/telemetry",
        "production",
        "http://localhost:8080",
        debug=True,
    )

    assert len(calls) >= 2, (
        f"captured {len(calls)} telemetry-path requests, want at least 2 (the /health probe "
        f"and the checkpoint POST); with fewer, the assertions below are vacuous"
    )
    for name, args, kwargs in calls:
        blob = repr(args) + repr(kwargs)
        assert HEADER_USER_TOKEN not in blob, f"{name}() carried the identity header"
        assert TEST_TOKEN not in blob, f"{name}() leaked the user token"


# ==========================================================================
# The three read outcomes
# ==========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("scope", "status", "want_typed", "want_missing"),
    [
        ("none", 404, True, True),
        ("own-rows", 404, True, False),
        ("tenant", 404, False, False),
        (None, 404, False, False),  # pre-#2922 platform states no scope
        ("segment-rows", 404, False, False),  # a scope this build does not know
        ("none", 500, False, False),  # a server fault under a scoped read
    ],
)
async def test_explain_scope_surfacing(httpx_mock, scope, status, want_typed, want_missing) -> None:
    headers = {HEADER_READ_SCOPE: scope} if scope is not None else {}
    httpx_mock.add_response(
        url=re.compile(r".*/explain"),
        status_code=status,
        json={"error": "Decision not found or past retention window"},
        headers=headers,
    )

    with pytest.raises(AxonFlowError) as excinfo:
        await _client().explain_decision("dec-1")

    is_typed = isinstance(excinfo.value, ReadScopeError)
    assert is_typed is want_typed, f"got {type(excinfo.value).__name__}: {excinfo.value}"
    if not want_typed:
        return
    assert excinfo.value.identity_missing is want_missing
    assert excinfo.value.scope == scope
    assert excinfo.value.identifier == "dec-1"
    assert excinfo.value.resource == "decision"


@pytest.mark.asyncio
async def test_list_empty_under_scope_none_is_refused(httpx_mock) -> None:
    httpx_mock.add_response(
        url=re.compile(r".*/api/v1/decisions.*"),
        json={"decisions": []},
        headers={HEADER_READ_SCOPE: "none"},
    )

    with pytest.raises(ReadScopeError) as excinfo:
        await _client().list_decisions()

    assert excinfo.value.identity_missing
    assert excinfo.value.status_code == 200, (
        "the platform answered successfully; it is the SCOPE that makes the page meaningless"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["own-rows", "tenant", None, "segment-rows"])
async def test_list_legitimate_empty_is_not_an_error(httpx_mock, scope) -> None:
    """The ways a read can HONESTLY return nothing.

    Refusing either would replace one wrong report with another.
    """
    headers = {HEADER_READ_SCOPE: scope} if scope is not None else {}
    httpx_mock.add_response(
        url=re.compile(r".*/api/v1/decisions.*"), json={"decisions": []}, headers=headers
    )

    assert await _client().list_decisions() == []


@pytest.mark.asyncio
async def test_list_non_empty_is_never_refused(httpx_mock) -> None:
    """Even a self-contradicting platform must not cost the caller its rows."""
    httpx_mock.add_response(
        url=re.compile(r".*/api/v1/decisions.*"),
        json=ROW_PAGE,
        headers={HEADER_READ_SCOPE: "none"},
    )

    assert len(await _client().list_decisions()) == 1


def test_read_scope_error_message_names_the_remedy_not_the_credential() -> None:
    missing = ReadScopeError(
        scope=ReadScope.NONE, status_code=404, resource="decision", identifier="d1"
    )
    assert "user_token" in str(missing), "the no-identity message does not name the remedy"
    assert "@axonflow.local" in str(missing), (
        "the message must name the reserved-domain cause too: a VALID token minted there "
        "resolves to nobody, and diagnosing it as 'you presented nothing' is a wrong diagnosis"
    )

    not_yours = ReadScopeError(scope=ReadScope.OWN_ROWS, status_code=404, resource="decision")
    assert not not_yours.identity_missing
    assert "resolved no per-user identity" not in str(not_yours)


def test_read_scope_absent_is_not_none() -> None:
    assert ReadScope.ABSENT != ReadScope.NONE
    assert ReadScope.ABSENT == ""


# ==========================================================================
# python#234 — a marker document with an unrecognised state must be REFUSED
# ==========================================================================


def _envelope_with_context(context: dict) -> AuthZENRequest:
    # A COMPLETE evaluation (subject + action + resource). An incomplete one is
    # refused before the attribute walk ever runs, which would make every
    # assertion below pass for the wrong reason.
    return AuthZENRequest(
        subject=AuthZENSubject(type="user", id="u1"),
        action=AuthZENAction(name="read"),
        resource=AuthZENResource(type="doc", id="r1"),
        context=context,
    )


@pytest.mark.parametrize(
    "state",
    ["future-state", "", "KNOWN", "Absent", "resolved"],
)
def test_marker_with_unrecognised_state_is_refused(state) -> None:
    """The fail-open: marker True + a state this build does not know.

    Before the fix these were reported as NOT attributes, walked as ordinary
    data, and SENT — marker key and all — so an attribute nobody resolved was
    recorded as one that was weighed.
    """
    document = {AUTHZEN_ATTRIBUTE_MARKER: True, "state": state, "value": "leaked"}

    with pytest.raises(Exception) as excinfo:  # noqa: PT011 - the SDK's typed refusal
        to_wire(build_envelope(evaluation=_envelope_with_context({"clearance": document})))

    assert "clearance" in str(excinfo.value), (
        f"the refusal must point at the member; got: {excinfo.value}"
    )


def test_marker_with_unrecognised_state_never_reaches_the_wire() -> None:
    """The other half of the same defect: the marker convention leaking out.

    Asserted on the serialized document rather than on the exception, because
    "it raised" and "it did not send" are different properties and only the
    second one is what the gateway sees.
    """
    document = {AUTHZEN_ATTRIBUTE_MARKER: True, "state": "future-state", "value": "leaked"}
    try:
        wire = json.dumps(
            to_wire(build_envelope(evaluation=_envelope_with_context({"clearance": document})))
        )
    except Exception:  # noqa: BLE001 - refusing is the correct outcome
        return
    pytest.fail(f"the marker document reached the wire: {wire}")


@pytest.mark.parametrize("marker", [False, "true", 1, None, "yes"])
def test_marker_that_is_not_boolean_true_stays_data(marker) -> None:
    """The mirror failure direction.

    A caller's own bag carrying a similarly-named key must NOT be turned into
    a refusal — only a positive self-identification counts.
    """
    document = {AUTHZEN_ATTRIBUTE_MARKER: marker, "state": "future-state", "value": "ordinary"}
    wire = to_wire(build_envelope(evaluation=_envelope_with_context({"bag": document})))
    assert wire["evaluation"]["context"]["bag"]["value"] == "ordinary"


def test_recognised_states_still_behave() -> None:
    """The fix must not break the three states that always worked."""
    envelope = build_envelope(
        evaluation=_envelope_with_context(
            {
                "known": AuthZENAttribute.known("v"),
                "absent": AuthZENAttribute.absent(),
            }
        )
    )
    wire = to_wire(envelope)
    assert wire["evaluation"]["context"]["known"] == "v"
    assert "absent" not in wire["evaluation"]["context"], "an absent attribute is dropped, not sent"


def test_mutant_restoring_the_state_pair_is_caught() -> None:
    """Mutation gate for #234.

    Reconstructs the pre-fix recogniser and shows it disagrees with the shipped
    one on exactly the payload that matters, so a weakened recogniser cannot
    pass silently.
    """
    from axonflow.authzen import _is_attribute

    document = {AUTHZEN_ATTRIBUTE_MARKER: True, "state": "future-state"}

    def pre_fix(value: object) -> bool:
        return (
            isinstance(value, dict)
            and value.get(AUTHZEN_ATTRIBUTE_MARKER) is True
            and value.get("state") in {"known", "absent", "unknown"}
        )

    assert pre_fix(document) is False, "the fixture does not reproduce the defect"
    assert _is_attribute(document) is True, (
        "the shipped recogniser agrees with the pre-fix one; the fail-open is not closed"
    )


# ==========================================================================
# Round-2 required properties: redirect, non-read routes, audit reads, as_user
# ==========================================================================


@pytest.mark.asyncio
async def test_identity_is_only_ever_sent_to_the_configured_endpoint(httpx_mock) -> None:
    """The identity is stamped by ORIGIN, not unconditionally.

    Why this matters even though httpx does not follow redirects by default
    (pinned separately below): httpx RE-RUNS request event hooks on a
    redirected request, and its sensitive-header list — which drops
    ``Authorization`` cross-origin — is fixed and does not include
    ``X-User-Token``. So a hook that stamped unconditionally would RE-ADD the
    per-user credential to a host the caller never named, on exactly the hop
    where the tenant credential is dropped. Measured in the Go sibling, which
    does follow redirects, and reproduced here at the hook.

    Asserted on the hook rather than through a redirect, because the SDK does
    not follow one — testing it through a redirect would need a configuration
    the SDK never uses, and would prove something about that configuration
    instead of about this code.
    """
    same = httpx.Request("GET", "http://localhost:8080/api/v1/decisions")
    other_host = httpx.Request("GET", "http://elsewhere.invalid/api/v1/decisions")
    other_port = httpx.Request("GET", "http://localhost:9999/api/v1/decisions")
    other_scheme = httpx.Request("GET", "https://localhost:8080/api/v1/decisions")

    for request, expected in (
        (same, TEST_TOKEN),
        (other_host, None),
        (other_port, None),
        (other_scheme, None),
    ):
        stamp_read_identity(TEST_TOKEN, request, endpoint="http://localhost:8080")
        got = request.headers.get(HEADER_USER_TOKEN)
        assert got == expected, (
            f"{request.url} got {got!r}, want {expected!r} — the per-user identity must reach "
            f"the configured endpoint and nowhere else"
        )


@pytest.mark.asyncio
async def test_client_does_not_follow_redirects(httpx_mock) -> None:
    """Pins the default the test above leans on.

    If this ever becomes True, the origin guard is the only thing standing
    between a 301 and a credential leak — so it is stated here rather than
    assumed.
    """
    client = _client(user_token=TEST_TOKEN)
    assert client._http_client.follow_redirects is False  # noqa: SLF001
    assert client._map_http_client.follow_redirects is False  # noqa: SLF001


@pytest.mark.asyncio
async def test_identity_travels_on_non_read_methods_too(httpx_mock) -> None:
    """The header is NOT a read-only convenience.

    The agent validates X-User-Token in proxyAuthMiddleware, in front of every
    route it proxies, and 401s a present-but-invalid one there. So a
    client-wide ``user_token`` reaches list_connectors and policy CRUD, and a
    stale one breaks them. Correct direction, real consequence — pinned rather
    than left as prose.
    """
    httpx_mock.add_response(url=re.compile(r".*/api/v1/connectors.*"), json={"connectors": []})
    await _client(user_token=TEST_TOKEN).list_connectors()

    assert httpx_mock.get_requests()[-1].headers[HEADER_USER_TOKEN] == TEST_TOKEN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("array shape", []),
        ("wrapped shape", {"entries": [], "total": 0}),
    ],
)
async def test_audit_reads_empty_under_scope_none_are_refused(httpx_mock, name, body) -> None:
    """The audit reads are in the same role-scoped family and used to answer
    the same vacuous empty page.

    Both decode shapes are exercised: the rule must not hold on whichever
    branch the server happened to take.
    """
    httpx_mock.add_response(
        url=re.compile(r".*/api/v1/audit/.*"),
        json=body,
        headers={HEADER_READ_SCOPE: "none"},
        is_reusable=True,
    )
    client = _client()

    with pytest.raises(ReadScopeError) as search_err:
        await client.search_audit_logs()
    assert search_err.value.identity_missing

    with pytest.raises(ReadScopeError) as tenant_err:
        await client.get_audit_logs_by_tenant("t1")
    assert tenant_err.value.identity_missing


@pytest.mark.asyncio
async def test_audit_reads_legitimate_empty_and_populated_are_not_refused(httpx_mock) -> None:
    httpx_mock.add_response(
        url=re.compile(r".*/api/v1/audit/.*"),
        json=[],
        headers={HEADER_READ_SCOPE: "own-rows"},
    )
    assert (await _client().search_audit_logs()).entries == []

    httpx_mock.add_response(
        url=re.compile(r".*/api/v1/audit/.*"),
        json=[
            {
                "id": "a1",
                "request_id": "r1",
                "timestamp": "2026-04-17T12:00:00Z",
                "user_email": "dev@example.com",
                "client_id": "org",
                "tenant_id": "t1",
                "request_type": "llm_chat",
                "policy_decision": "allowed",
            }
        ],
        headers={HEADER_READ_SCOPE: "none"},
    )
    populated = await _client().search_audit_logs()
    assert len(populated.entries) == 1, "a populated page was discarded on the strength of a header"


@pytest.mark.asyncio
async def test_as_user_reaches_every_method(httpx_mock) -> None:
    """The multi-tenant shape: unlike the per-call keyword, which only the read
    methods accept, a derived client reaches every method with no carve-out.
    """
    httpx_mock.add_response(
        url=re.compile(r".*/api/v1/connectors.*"), json={"connectors": []}, is_reusable=True
    )
    admin = _client(user_token="ADMIN-TOKEN")

    await admin.list_connectors()
    assert httpx_mock.get_requests()[-1].headers[HEADER_USER_TOKEN] == "ADMIN-TOKEN"

    await admin.as_user("ALICE-TOKEN").list_connectors()
    assert httpx_mock.get_requests()[-1].headers[HEADER_USER_TOKEN] == "ALICE-TOKEN", (
        "as_user did not reach a method that takes no per-call user_token"
    )

    # The derived client must not mutate the one it came from...
    assert admin._config.user_token == "ADMIN-TOKEN"  # noqa: SLF001
    # ...and it shares the connection POOL rather than building a new one.
    # (It gets its own thin httpx client, because the identity hook closes over
    # a client's own config — sharing the httpx object outright is exactly the
    # bug this test caught: as_user silently had no effect.)
    derived = admin.as_user("X")
    assert derived._http_client._transport is admin._http_client._transport  # noqa: SLF001
    assert derived._http_client is not admin._http_client  # noqa: SLF001


@pytest.mark.asyncio
async def test_as_user_with_no_token_presents_no_identity(httpx_mock) -> None:
    httpx_mock.add_response(url=re.compile(r".*/api/v1/connectors.*"), json={"connectors": []})
    await _client(user_token=TEST_TOKEN).as_user("").list_connectors()

    assert HEADER_USER_TOKEN not in httpx_mock.get_requests()[-1].headers


@pytest.mark.asyncio
@pytest.mark.parametrize("spelling", ["none", "None", "NONE", " none "])
async def test_read_scope_is_matched_case_insensitively(httpx_mock, spelling) -> None:
    """A scope spelled ``None`` degrading to "no opinion" would restore the
    vacuous empty list — too quiet a failure to leave to a constant staying put.
    """
    httpx_mock.add_response(
        url=re.compile(r".*/api/v1/decisions.*"),
        json={"decisions": []},
        headers={HEADER_READ_SCOPE: spelling},
    )
    with pytest.raises(ReadScopeError):
        await _client().list_decisions()


def test_generated_authzen_models_still_refuse_unknown_members() -> None:
    """The strict-decode property, as a REQUIRED sibling of the Go finding.

    In Go, giving the generated types an UnmarshalJSON silently disarmed the
    enclosing decoder's DisallowUnknownFields. Python's equivalent guard is
    ``ConfigDict(extra="forbid")`` on the generated base, and nothing in this
    change touches decoding — but "we did not touch it" is not evidence, and
    every client library has its own decoder defaults. This is the evidence.
    """
    from axonflow.authzen_types_gen import AuthZENObligation, AuthZENResponse

    with pytest.raises(Exception) as response_err:  # noqa: PT011 - pydantic ValidationError
        AuthZENResponse.model_validate({"decision": True, "advice": "proceed"})
    assert "advice" in str(response_err.value)

    with pytest.raises(Exception) as nested_err:  # noqa: PT011
        AuthZENObligation.model_validate(
            {
                "type": "redact",
                "mandatory": True,
                "source_policy": "p1",
                "schema_version": 1,
                "severity": "high",
            }
        )
    assert "severity" in str(nested_err.value)


def test_env_proxies_survive_on_both_the_client_and_a_derived_one(monkeypatch) -> None:
    """httpx builds its environment proxy map ONLY when it constructs the
    transport itself (``allow_env_proxies = trust_env and transport is None``).

    Passing an explicit transport therefore leaves ``_mounts`` empty and every
    customer behind an egress proxy loses connectivity — a total outage on
    upgrade, caused by a change about read scoping. This asserts the map is
    populated on the client AND carried across to a derived one, which does
    pass a transport (to share the pool) and so has to copy the mounts by hand.
    """
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")

    parent = _client(user_token=TEST_TOKEN)
    assert parent._http_client._mounts, (  # noqa: SLF001
        "the client built no proxy map from the environment; an explicit transport= was "
        "almost certainly passed, which silently disables env proxies for every caller"
    )
    assert parent._map_http_client._mounts  # noqa: SLF001

    derived = parent.as_user("ALICE")
    assert derived._http_client._mounts == parent._http_client._mounts, (  # noqa: SLF001
        "the derived client lost the proxy map: it passes an explicit transport to share the "
        "pool, so the mounts have to be carried across rather than left to httpx"
    )
    assert derived._map_http_client._mounts == parent._map_http_client._mounts  # noqa: SLF001


def test_as_user_resets_lazily_bound_namespaces() -> None:
    """A derived client must not inherit a sub-namespace bound to the PARENT.

    Each lazy namespace caches the client that created it, so a copied
    reference calls through the parent — with the parent's identity. The bug
    only appears when the parent touched the namespace BEFORE deriving, which
    is exactly the ordering a long-lived gateway has, so the test does that
    deliberately.
    """
    parent = _client(user_token="ADMIN-TOKEN")
    parent_namespace = parent.masfeat  # bind it on the PARENT first

    derived = parent.as_user("ALICE-TOKEN")
    assert derived.masfeat is not parent_namespace, (
        "the derived client inherited the parent's lazily-bound namespace, which still calls "
        "through the parent and therefore under the PARENT's identity"
    )

    # And the namespace it does get is bound to the DERIVED client, so its
    # requests carry Alice — the property the identity check is really about.
    assert derived.masfeat._client is derived  # noqa: SLF001
    assert parent.masfeat._client is parent  # noqa: SLF001


def test_credentials_do_not_appear_in_the_config_repr() -> None:
    """A config object reaches log lines, exception ``__repr__``s, debugger
    frames and crash reporters; a credential that rides along has left the
    process in every one of those.

    Both credentials are asserted together on purpose — the read-path identity
    is a per-user credential of the same class as ``client_secret``, and marking
    one while forgetting the other is the likely failure.
    """
    from axonflow.types import AxonFlowConfig

    config = AxonFlowConfig(
        endpoint="http://localhost:8080",
        client_id="org",
        client_secret="SECRET-VALUE",
        user_token="TOKEN-VALUE",
    )
    assert "SECRET-VALUE" not in repr(config)
    assert "TOKEN-VALUE" not in repr(config)
    # The non-secret fields must still be there, or this passes by rendering
    # nothing at all.
    assert "localhost:8080" in repr(config)


@pytest.mark.parametrize(
    ("payload", "member"),
    [
        # A DECISION arriving as the string "false" was read as the boolean
        # False; an obligation whose `mandatory` arrived as 1 was read as True.
        # Those are type errors on the wire being silently repaired into a
        # reading nobody sent — on exactly the members that decide whether an
        # unsupported obligation must DENY.
        ({"decision": "false"}, "decision"),
        ({"decision": 1}, "decision"),
    ],
)
def test_generated_models_do_not_coerce_wire_types(payload, member) -> None:
    from axonflow.authzen_types_gen import AuthZENResponse

    with pytest.raises(Exception) as excinfo:  # noqa: PT011 - pydantic ValidationError
        AuthZENResponse.model_validate(payload)
    assert member in str(excinfo.value)


def test_generated_models_do_not_coerce_an_obligations_mandatory() -> None:
    from axonflow.authzen_types_gen import AuthZENObligation

    with pytest.raises(Exception) as excinfo:  # noqa: PT011
        AuthZENObligation.model_validate(
            {"type": "redact", "mandatory": 1, "source_policy": "p", "schema_version": 1}
        )
    assert "mandatory" in str(excinfo.value)


def test_generated_models_still_accept_the_correct_types() -> None:
    """The other failure direction: strictness that refuses valid payloads is
    an outage, not a guard."""
    from axonflow.authzen_types_gen import AuthZENObligation, AuthZENResponse

    assert AuthZENResponse.model_validate({"decision": False}).decision is False
    obligation = AuthZENObligation.model_validate(
        {"type": "redact", "mandatory": True, "source_policy": "p", "schema_version": 1}
    )
    assert obligation.mandatory is True
    assert obligation.schema_version == 1


# ==========================================================================
# A derived client shares the cache — so the key must know the identity
# ==========================================================================


@pytest.mark.asyncio
async def test_two_derived_clients_do_not_share_a_cached_response(httpx_mock) -> None:
    """Two identities asking one question must produce TWO governed requests.

    ``as_user`` shares this client's cache deliberately — deriving one per
    request must not cost a cache — and its own docstring says so. The key was
    ``request_type:query:user_token``, where ``user_token`` is the write-path
    BODY field, and carried nothing about the identity the request would
    actually PRESENT.

    Measured before the fix: one request reached the server, identities
    ``['ALICE']``. The second caller was handed the first caller's governed
    response with nothing evaluated on their behalf. Two INDEPENDENT clients
    each own a cache and send two, which is why nothing outside the
    derived-client path could see it.
    """
    httpx_mock.add_response(
        url=re.compile(r".*/api/request$"),
        json={"success": True, "data": {"content": "answer"}},
        is_reusable=True,
    )

    base = _client(cache_enabled=True)
    query = "the same question from two people"
    for token in ("ALICE-TOKEN", "BOB-TOKEN"):
        await base.as_user(token).proxy_llm_call(
            user_token="", query=query, request_type="mcp-query"
        )

    requests = [r for r in httpx_mock.get_requests() if r.url.path == "/api/request"]
    identities = [r.headers.get(HEADER_USER_TOKEN, "NO IDENTITY") for r in requests]
    assert len(requests) == 2, (
        "two identities asking the same question must produce TWO governed requests. One "
        "means the second caller was served the FIRST caller's response out of a shared "
        f"cache, without the platform evaluating anything on their behalf. Seen: {identities}"
    )
    assert set(identities) == {"ALICE-TOKEN", "BOB-TOKEN"}, identities


@pytest.mark.asyncio
async def test_one_identity_asking_twice_still_hits_the_cache(httpx_mock) -> None:
    """The other failure direction.

    Without this, the test above is satisfied by a key that never matches — a
    disabled cache wearing a fix's name, costing every caller a round trip.
    """
    httpx_mock.add_response(
        url=re.compile(r".*/api/request$"),
        json={"success": True, "data": {"content": "answer"}},
        is_reusable=True,
    )

    alice = _client(cache_enabled=True).as_user("ALICE-TOKEN")
    for _ in range(2):
        await alice.proxy_llm_call(user_token="", query="one question", request_type="mcp-query")

    requests = [r for r in httpx_mock.get_requests() if r.url.path == "/api/request"]
    assert len(requests) == 1, (
        "the same identity asking the same question twice must be served from the cache; "
        "a key that never matches is a disabled cache wearing a fix's name"
    )


@pytest.mark.asyncio
async def test_a_per_call_override_is_not_served_the_client_wide_cached_response(
    httpx_mock,
) -> None:
    """The cache key must resolve the identity the way the HEADER does.

    The first fix put ``self._config.user_token`` in the key while the stamp
    resolved the per-call override, so the two disagreed on exactly the calls
    that HAVE an override: a call made inside ``use_read_identity("BOB")``
    presented BOB on the wire and was served the client-wide identity's cached
    answer. Same leak as two derived clients, one level further in, and no test
    over derived clients could see it.
    """
    httpx_mock.add_response(
        url=re.compile(r".*/api/request$"),
        json={"success": True, "data": {"content": "answer"}},
        is_reusable=True,
    )

    client = _client(cache_enabled=True, user_token="CLIENT-WIDE-TOKEN")
    query = "the same question, two identities"

    await client.proxy_llm_call(user_token="", query=query, request_type="mcp-query")
    with use_read_identity("BOB-TOKEN"):
        await client.proxy_llm_call(user_token="", query=query, request_type="mcp-query")

    requests = [r for r in httpx_mock.get_requests() if r.url.path == "/api/request"]
    identities = [r.headers.get(HEADER_USER_TOKEN, "NO IDENTITY") for r in requests]
    assert len(requests) == 2, (
        "a per-call override presents a different identity on the wire, so it must not be "
        f"served the client-wide identity's cached response. Seen: {identities}"
    )
    assert identities == ["CLIENT-WIDE-TOKEN", "BOB-TOKEN"], identities


@pytest.mark.asyncio
async def test_an_explicitly_unidentified_call_is_not_served_the_identified_response(
    httpx_mock,
) -> None:
    """An explicit empty override is a deliberately UNIDENTIFIED call.

    It drops the header, so the platform scopes it to nothing. If the key still
    resolved to the client-wide identity, that call would be handed the
    IDENTIFIED response out of the cache — the opposite of what the caller
    asked for, and the more dangerous direction of the two.
    """
    httpx_mock.add_response(
        url=re.compile(r".*/api/request$"),
        json={"success": True, "data": {"content": "answer"}},
        is_reusable=True,
    )

    client = _client(cache_enabled=True, user_token="CLIENT-WIDE-TOKEN")
    query = "identified, then deliberately not"

    await client.proxy_llm_call(user_token="", query=query, request_type="mcp-query")
    with use_read_identity(""):
        await client.proxy_llm_call(user_token="", query=query, request_type="mcp-query")

    requests = [r for r in httpx_mock.get_requests() if r.url.path == "/api/request"]
    identities = [r.headers.get(HEADER_USER_TOKEN, "NO IDENTITY") for r in requests]
    assert len(requests) == 2, (
        "an explicitly unidentified call must not be served the identified identity's cached "
        f"response. Seen: {identities}"
    )
    assert identities == ["CLIENT-WIDE-TOKEN", "NO IDENTITY"], identities


@pytest.mark.asyncio
async def test_the_same_per_call_override_twice_still_hits_the_cache(httpx_mock) -> None:
    """The control for both tests above.

    Without it, they are satisfied by a key that never matches — a disabled
    cache wearing a fix's name.
    """
    httpx_mock.add_response(
        url=re.compile(r".*/api/request$"),
        json={"success": True, "data": {"content": "answer"}},
        is_reusable=True,
    )

    client = _client(cache_enabled=True, user_token="CLIENT-WIDE-TOKEN")
    for _ in range(2):
        with use_read_identity("BOB-TOKEN"):
            await client.proxy_llm_call(
                user_token="", query="one question", request_type="mcp-query"
            )

    requests = [r for r in httpx_mock.get_requests() if r.url.path == "/api/request"]
    assert len(requests) == 1, (
        "the same override asking the same question twice must be served from the cache"
    )
