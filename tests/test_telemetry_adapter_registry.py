"""Adapter registry, relay caps, redirect refusal and heartbeat cadence.

Covers axonflow-enterprise#3682 items 1-3 for the Python SDK.

WHAT THESE TESTS CAN AND CANNOT VARY. Every HTTP case here runs the SDK's real
``httpx`` call path against a recording ``MockTransport``, and reads the bytes
the SDK actually serialized. The axes under test — what reaches ``features``,
what reaches the relayed fields, and what happens on a 3xx — are all varied.
Redirect handling in particular is genuinely exercised, because httpx
implements it in the ``Client``, ABOVE the transport.

The repo blocks real socket egress from the telemetry path in unit tests
(``tests/conftest.py``, so a deleted opt-out cannot fire a ping at the
PRODUCTION checkpoint) and directs tests here to ``MockTransport``. The real
two-socket proof lives in ``runtime-e2e/adapter_telemetry/``, where real
endpoints are required.

They CANNOT vary three axes, stated rather than left implied:

* The **socket**. Nothing here proves the SDK can talk to a real listener;
  that is the runtime-e2e driver's job.

* The **receiver**. ``NormalizeAdapterFeature`` folds an unrecognised adapter
  name into ``adapter:unknown`` at READ time, in another repo, and is asserted
  there. That separation is the point of item 1: this SDK sends the caller's
  name and takes no view on the vocabulary.
* The **scheme**. Both listeners are local ``http``, so an ``https -> http``
  downgrade is not exercised. This is the same blind spot that hid the Go
  per-user-credential leak in #3651. It does not apply here — the telemetry
  path sends no credential and no ``Authorization`` header — but a future
  change that added one would not be caught by these fixtures.
"""

from __future__ import annotations

import json

import httpx
import pytest

from axonflow import telemetry as tel
from axonflow.telemetry import register_adapter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _Route:
    """One stand-in endpoint: what it answers, and what it was asked.

    NOT a mock of the system under test — it is the PEER. httpx's own
    ``Client`` machinery runs in full above it, which is the part that
    matters here: redirect handling lives in the Client, ABOVE the transport,
    so ``follow_redirects`` is genuinely exercised.

    The repo blocks real socket egress from the telemetry path in unit tests
    (see ``tests/conftest.py``) and directs tests here to ``MockTransport``.
    The real two-listener socket proof lives in
    ``runtime-e2e/adapter_telemetry/``, where real endpoints are required.
    """

    def __init__(self, url: str, status: int = 200, body: str = "{}", location: str | None = None):
        self.url = url
        self.status = status
        self.body = body
        self.location = location
        self.requests: list[httpx.Request] = []

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        headers = {"Content-Type": "application/json"}
        if self.location is not None:
            headers["Location"] = self.location
            return httpx.Response(self.status, headers=headers, content=b"")
        return httpx.Response(self.status, headers=headers, content=self.body.encode())


@pytest.fixture
def route_http(monkeypatch):
    """Install ``httpx.get`` / ``httpx.post`` backed by a recording
    MockTransport, replacing the conftest egress guard for the duration of
    one test.

    The guard exists so a test that deletes ``AXONFLOW_TELEMETRY`` cannot fire
    a real ping at the PRODUCTION checkpoint. Routing to an in-process
    transport honours that completely: nothing leaves the process.
    """

    def install(*routes: _Route):
        table = {route.url: route for route in routes}

        def handler(request: httpx.Request) -> httpx.Response:
            route = table.get(str(request.url))
            if route is None:
                # An unrouted URL is a fixture bug, and it must be loud: a
                # ConnectError here would be swallowed by the telemetry
                # path's broad except and read as an ordinary failure.
                msg = f"unrouted request to {request.url}"
                raise AssertionError(msg)
            return route.respond(request)

        transport = httpx.MockTransport(handler)

        def _get(url, **kwargs):
            follow = kwargs.pop("follow_redirects", False)
            kwargs.pop("timeout", None)
            with httpx.Client(transport=transport, follow_redirects=follow) as client:
                return client.get(url)

        def _post(url, **kwargs):
            follow = kwargs.pop("follow_redirects", False)
            kwargs.pop("timeout", None)
            json_body = kwargs.pop("json", None)
            with httpx.Client(transport=transport, follow_redirects=follow) as client:
                return client.post(url, json=json_body)

        monkeypatch.setattr(httpx, "get", _get)
        monkeypatch.setattr(httpx, "post", _post)

    return install


# ---------------------------------------------------------------------------
# Item 1 — the registry
# ---------------------------------------------------------------------------


def test_features_is_present_and_empty_by_default():
    """POSITIVE CONTROL for every absence assertion below.

    "features did not contain adapter:x" is only evidence if the field exists
    at all. An absent key and an empty array are different facts.
    """
    payload = tel._build_payload("production")  # noqa: SLF001
    assert "features" in payload, "the features key must always be present"
    assert payload["features"] == []


def test_registered_adapter_reaches_the_payload():
    """MUTATION GATE: change ``"features": _registered_features()`` back to
    ``"features": []`` in ``_build_payload`` and this fails.
    """
    register_adapter("langchain")
    assert tel._build_payload("production")["features"] == ["adapter:langchain"]  # noqa: SLF001


def test_unregistered_adapter_does_not():
    """Paired with the test above, which is its positive control."""
    register_adapter("langchain")
    features = tel._build_payload("production")["features"]  # noqa: SLF001
    assert "adapter:langgraph" not in features
    # Without this line the assertion above is satisfied by an empty array.
    assert features == ["adapter:langchain"]


@pytest.mark.parametrize(
    ("names", "expected", "about"),
    [
        (["LangChain"], ["adapter:langchain"], "lowercased, as the receiver folds before matching"),
        (["  langgraph\t\n"], ["adapter:langgraph"], "stripped; whitespace is not part of a name"),
        (
            ["litellm", "LITELLM", " litellm "],
            ["adapter:litellm"],
            "deduplicated: a per-request constructor declares itself once",
        ),
        (
            ["langgraph", "langchain"],
            ["adapter:langchain", "adapter:langgraph"],
            "sorted: registration order must not change the bytes",
        ),
        (
            ["some-framework-we-have-never-heard-of"],
            ["adapter:some-framework-we-have-never-heard-of"],
            "NOT filtered: an SDK-side allowlist would be a second vocabulary that drifts",
        ),
    ],
)
def test_register_adapter_normalises(names, expected, about):
    for name in names:
        register_adapter(name)
    assert tel._registered_features() == expected, about  # noqa: SLF001


@pytest.mark.parametrize("name", ["", "   ", "\t\n", None, 42, b"langchain"])
def test_register_adapter_refuses_unusable_names(name):
    """``adapter:`` alone is not an identifier, and a non-string argument must
    be refused rather than coerced or raised — this is a fire-and-forget
    telemetry declaration, not a validated API.
    """
    register_adapter(name)
    assert tel._registered_features() == []  # noqa: SLF001


# ---------------------------------------------------------------------------
# Item 2 — the caps
# ---------------------------------------------------------------------------


def test_adapter_name_cap_is_bytes_and_drops_whole():
    """The item-2 boundary, asserted at exactly 64/65 rather than at a round
    number far from the edge.

    TWO MUTATION GATES failing in opposite directions:
      1. ``_MAX_RELAYED_VALUE_BYTES = 65`` -> the 65-byte case is admitted.
      2. truncating instead of returning -> a 64-byte name appears on the wire.
    """
    register_adapter("a" * 64)
    assert tel._registered_features() == ["adapter:" + "a" * 64], (  # noqa: SLF001
        "a 64-byte name is within the cap and must be kept"
    )

    tel._reset_adapter_registry_for_test()  # noqa: SLF001
    register_adapter("a" * 65)
    assert tel._registered_features() == [], (  # noqa: SLF001
        "a 65-byte name must be DROPPED WHOLE. A truncated adapter name is a name "
        "nothing is running, and the receiver records it as a real value"
    )


def test_the_cap_counts_bytes_not_characters():
    """33 x U+00E9 is 33 CHARACTERS and 66 BYTES.

    This is the case a cap written as ``len(s)`` admits — and in Python
    ``len()`` on a str counts code points, so that mistake is the natural one
    to make.
    """
    name = "é" * 33
    assert len(name) <= tel._MAX_RELAYED_VALUE_BYTES, (  # noqa: SLF001
        "fixture is wrong: it must be under the cap by CHARACTERS"
    )
    assert len(name.encode()) > tel._MAX_RELAYED_VALUE_BYTES, (  # noqa: SLF001
        "fixture is wrong: it must be over the cap by BYTES"
    )
    register_adapter(name)
    assert tel._registered_features() == []  # noqa: SLF001


def test_features_array_is_bounded_to_32_entries():
    """MUTATION GATE: ``_MAX_FEATURES = 33`` and the length assertion fails."""
    for i in range(40):
        register_adapter(f"{i:02d}")
    features = tel._registered_features()  # noqa: SLF001
    assert len(features) == tel._MAX_FEATURES  # noqa: SLF001
    # Sorted-then-truncated, so "which 32 survive" is a defined answer rather
    # than a set-iteration accident.
    assert features[0] == "adapter:00"
    assert features[-1] == "adapter:31"


def test_bound_features_drops_an_overlong_entry():
    """Tested DIRECTLY on ``_bound_features``, and here is why.

    ``register_adapter`` already refuses a name over 64 bytes, so the longest
    entry it can emit is ``len("adapter:") + 64 == 72`` — well under 128. A
    test driven through the registry could not express this defect and would
    read as disproof of a bound that was never exercised.
    """
    longest_possible = len(tel._FEATURE_ADAPTER_PREFIX) + tel._MAX_RELAYED_VALUE_BYTES  # noqa: SLF001
    assert longest_possible <= tel._MAX_FEATURE_BYTES, (  # noqa: SLF001
        "the premise changed: register_adapter can now emit an entry that exceeds "
        "_MAX_FEATURE_BYTES, so this bound is reachable through the registry and MUST "
        "be tested through it as well"
    )

    within = "adapter:" + "b" * (tel._MAX_FEATURE_BYTES - len("adapter:"))  # noqa: SLF001
    over = within + "b"
    assert tel._bound_features([within, over]) == [within], (  # noqa: SLF001
        "the over-long entry must be DROPPED WHOLE, not truncated"
    )


# ---------------------------------------------------------------------------
# Item 2 (relay) — edition and platform_deployment_mode
# ---------------------------------------------------------------------------


def test_relayed_fields_are_learned_from_one_health_response(route_http):
    """``edition`` and ``platform_deployment_mode`` ride the SAME ``/health``
    the version and tier already come from — no new request.

    The request COUNT is the assertion that makes "no new request" real.
    """
    health = _Route(
        "http://platform.test/health",
        body=json.dumps(
            {
                "status": "healthy",
                "version": "10.4.0",
                "tier": "Enterprise",
                "edition": "enterprise",
                "deployment_mode": "in-vpc-enterprise",
            }
        ),
    )
    route_http(health)
    probe = tel._probe_platform_health("http://platform.test", timeout=2.0)  # noqa: SLF001

    assert probe.platform_version == "10.4.0"
    assert probe.license_tier == "Enterprise"
    assert probe.edition == "enterprise"
    assert probe.platform_deployment_mode == "in-vpc-enterprise"
    assert len(health.requests) == 1, (
        f"the probe made {len(health.requests)} requests; every relayed dimension must "
        "ride ONE /health fetch"
    )
    assert health.requests[0].url.path == "/health"


def test_platform_deployment_mode_never_overwrites_the_sdks_own():
    """The trap this contract is most likely to be got wrong on.

    ``/health``'s member is ``deployment_mode`` (the platform describing
    itself). The ping's ``deployment_mode`` is the TOPOLOGY this SDK derives
    from the endpoint URL. They are different dimensions travelling under
    different names.
    """
    payload = tel._build_payload(  # noqa: SLF001
        "production",
        platform_version="10.4.0",
        endpoint_type="localhost",
        deployment_mode="self_hosted",
        edition="enterprise",
        platform_deployment_mode="in-vpc-enterprise",
    )
    assert payload["deployment_mode"] == "self_hosted", (
        "the SDK's own topology must survive; overwriting it would change a value "
        "every existing dashboard reads"
    )
    assert payload["platform_deployment_mode"] == "in-vpc-enterprise"


@pytest.mark.parametrize(
    ("body", "about"),
    [
        ('{"version":"10.4.0"}', "keys absent entirely"),
        ('{"version":"10.4.0","edition":"","deployment_mode":""}', "explicit empty strings"),
        ('{"version":"10.4.0","edition":42,"deployment_mode":true}', "non-string values"),
    ],
)
def test_unlearned_relay_fields_are_omitted_never_empty(route_http, body, about):
    """ABSENT is not EMPTY. A value not learned is omitted, never sent as ``""``
    or ``null`` — and presence is ``has(key)``, not truthiness.

    An oversized value is covered separately below.
    """
    health = _Route("http://platform.test/health", body=body)
    route_http(health)
    probe = tel._probe_platform_health("http://platform.test", timeout=2.0)  # noqa: SLF001

    assert probe.edition is None, about
    assert probe.platform_deployment_mode is None, about
    # Positive control: the run happened and the INDEPENDENT field survived,
    # so the two Nones above are real absences and not a dead probe.
    assert probe.platform_version == "10.4.0", (
        "a badly-typed or missing new dimension must not regress an existing one"
    )

    payload = tel._build_payload(  # noqa: SLF001
        "production",
        edition=probe.edition,
        platform_deployment_mode=probe.platform_deployment_mode,
    )
    assert "edition" not in payload
    assert "platform_deployment_mode" not in payload


def test_an_oversized_relayed_value_is_dropped_alone(route_http):
    """A hostile or broken /health must cost the ping ONE dimension, not all
    of them — and the ping must still be built.
    """
    health = _Route(
        "http://platform.test/health",
        body=json.dumps({"version": "10.4.0", "edition": "e" * 65}),
    )
    route_http(health)
    probe = tel._probe_platform_health("http://platform.test", timeout=2.0)  # noqa: SLF001

    assert probe.edition is None, "a 65-byte edition must be dropped whole"
    assert probe.platform_version == "10.4.0", (
        "the oversized value must be dropped ALONE; taking the whole probe with it "
        "would lose every other dimension"
    )


# ---------------------------------------------------------------------------
# Item 3 — redirect refusal, on both legs, with two listeners
# ---------------------------------------------------------------------------


def test_health_redirect_is_refused_and_the_target_is_never_read(route_http):
    """TWO listeners, and the second one records.

    A single-listener fixture cannot express this defect: if the redirector
    and the target are the same process, a followed redirect and a refused one
    are indistinguishable. The target serves a complete, plausible /health
    with DIFFERENT values so that following would be visible in the result.

    MUTATION GATE: ``follow_redirects=True`` on the probe and this fails —
    the target records a hit and the relayed version becomes the target's.
    """
    target = _Route(
        "http://elsewhere.test/health",
        body=json.dumps({"version": "6.6.6-REDIRECT-TARGET", "tier": "Plus"}),
    )
    redirector = _Route(
        "http://platform.test/health", status=302, location="http://elsewhere.test/health"
    )
    route_http(redirector, target)
    probe = tel._probe_platform_health("http://platform.test", timeout=2.0)  # noqa: SLF001

    # POSITIVE CONTROL: the first listener was actually asked. Without it,
    # "the target saw nothing" is equally true of a run that never happened.
    assert len(redirector.requests) == 1, (
        "the redirector was never contacted, so the assertions below prove nothing"
    )
    assert target.requests == [], (
        "the redirect TARGET was fetched: the 30x was followed, and every relayed value "
        "would describe a platform the caller never pointed at"
    )
    assert probe == tel._EMPTY_HEALTH_PROBE, (  # noqa: SLF001
        "a refused redirect must yield 'not learned', not the target's values"
    )


def test_checkpoint_redirect_is_not_treated_as_delivery(route_http):
    """The more dangerous half.

    An HTTP client that follows a 301/302/303 does not re-POST: it converts
    the request to a bodyless GET. So a followed redirect yields a 200 for a
    request that carried NO PAYLOAD, the SDK reads that 200 as delivery, and
    the caller advances the 7-day stamp — the installation goes silent for a
    week on a ping that was never sent.

    MUTATION GATE: ``follow_redirects=True`` on the POST and this fails —
    ``_send_telemetry_ping_now`` returns True and the target records a
    bodyless GET.
    """
    target = _Route("http://elsewhere.test/v1/ping", body='{"latest_version":"0.0.0"}')
    redirector = _Route(
        "http://checkpoint.test/v1/ping", status=302, location="http://elsewhere.test/v1/ping"
    )
    route_http(redirector, target)
    delivered = tel._send_telemetry_ping_now(  # noqa: SLF001
        "http://checkpoint.test/v1/ping", "production", "", debug=False
    )

    assert len(redirector.requests) == 1, "the redirector was never contacted"
    assert target.requests == [], (
        "the redirect TARGET received the request. A followed redirect reports DELIVERY "
        "for a ping that was never sent, and the 7-day stamp advances on it"
    )
    assert delivered is False, (
        "a 3xx must NOT be reported as delivery, or the stamp advances on nothing"
    )


def test_a_2xx_that_is_not_200_counts_as_delivery(route_http):
    """Python alone compared the checkpoint's status against 200 exactly, so a
    202 read as a failure and the same ping was retried at every gate run
    forever with the stamp never advancing. Every sibling SDK accepts any 2xx.
    """
    checkpoint = _Route("http://checkpoint.test/v1/ping", status=202)
    route_http(checkpoint)
    delivered = tel._send_telemetry_ping_now(  # noqa: SLF001
        "http://checkpoint.test/v1/ping", "production", "", debug=False
    )

    assert len(checkpoint.requests) == 1, "the ping never left, so nothing is measured"
    assert delivered is True, "a 202 is a delivered ping"


def test_the_adapter_reaches_the_wire_through_the_real_post(route_http):
    """End-to-end through the SDK's own httpx POST: register, ping, and read
    the bytes the listener received.

    The unit tests above assert on the payload dict; this one asserts on what
    was actually serialized and sent.
    """
    register_adapter("litellm")
    checkpoint = _Route("http://checkpoint.test/v1/ping")
    route_http(checkpoint)
    delivered = tel._send_telemetry_ping_now(  # noqa: SLF001
        "http://checkpoint.test/v1/ping", "production", "", debug=False
    )

    assert delivered is True
    assert len(checkpoint.requests) == 1
    body = json.loads(checkpoint.requests[0].content)
    assert body["features"] == ["adapter:litellm"]
    assert body["telemetry_type"] == "sdk"


# ---------------------------------------------------------------------------
# Item 3 — cadence
# ---------------------------------------------------------------------------


def test_guard_interval_doubles_and_caps():
    from axonflow.heartbeat import (
        HEARTBEAT_GUARD_INTERVAL_S,
        HEARTBEAT_INTERVAL_S,
        _guard_interval_for,
    )

    assert _guard_interval_for(0) == HEARTBEAT_GUARD_INTERVAL_S
    assert _guard_interval_for(1) == 2 * HEARTBEAT_GUARD_INTERVAL_S
    assert _guard_interval_for(2) == 4 * HEARTBEAT_GUARD_INTERVAL_S
    assert _guard_interval_for(7) == 128 * HEARTBEAT_GUARD_INTERVAL_S
    assert _guard_interval_for(8) == HEARTBEAT_INTERVAL_S, "256h exceeds 7 days, so it caps"
    assert _guard_interval_for(10**6) == HEARTBEAT_INTERVAL_S, (
        "an unbounded counter must cap, not produce an absurd interval"
    )


# ---------------------------------------------------------------------------
# The FIRST-PARTY adapters declare themselves
# ---------------------------------------------------------------------------


def _fake_chat_model() -> object:
    """A stand-in that satisfies ``AxonFlowChatModel``'s isinstance guard.

    Mirrors ``tests/test_langchain_adapter.py::_make_wrapped_model``:
    ``MagicMock(spec=BaseChatModel)`` passes ``isinstance``. Falls back to a
    bare mock when langchain_core is absent so the registration assertion still
    runs — the guard would then raise, which the caller sees.
    """
    from unittest.mock import MagicMock

    try:
        from langchain_core.language_models import BaseChatModel

        base: type = BaseChatModel
    except ImportError:  # pragma: no cover - langchain_core is a dev dep
        base = object

    model = MagicMock(spec=base)
    model.__class__.__name__ = "ChatAnthropic"
    model.__class__.__module__ = "langchain_anthropic"
    model.model_name = "claude-sonnet-4-6"
    return model


def test_langgraph_adapter_declares_itself():
    """The census correction.

    The first version of this change grepped for the wire string ``adapter:``
    and concluded the SDK declared no adapters. That grep answers "does any
    code build the string", NOT "does this SDK ship an adapter" — a census is
    bounded by the shape you search for. Asked of the EXPORTED TYPE, this SDK
    ships FOUR adapter entry points across two frameworks.

    Registration is in the CONSTRUCTOR, not at module import: importing says
    the adapter is installed, constructing says it is in use.

    MUTATION GATE: delete the ``register_adapter`` call from
    ``AxonFlowLangGraphAdapter.__init__`` and this fails.
    """
    from axonflow.adapters.langgraph import AxonFlowLangGraphAdapter

    # Positive control: nothing is registered by the IMPORT alone. Without
    # this, the assertion below would also pass for import-time registration,
    # which is a different (over-reporting) contract.
    assert tel._registered_features() == [], (  # noqa: SLF001
        "importing the adapter module registered something; import is not use"
    )

    AxonFlowLangGraphAdapter(client=object(), workflow_name="wf")
    assert tel._registered_features() == ["adapter:langgraph"]  # noqa: SLF001


def test_langchain_adapters_declare_themselves():
    """Python ships a LangChain adapter too — two entry points sharing one
    governance mixin — and both must declare the same name.

    A test covering only one of them would leave the other free to drift.
    """
    from axonflow.adapters.langchain import AxonFlowChatModel, AxonFlowRunnableBinding

    assert tel._registered_features() == []  # noqa: SLF001

    AxonFlowRunnableBinding(bound=object(), axonflow=object())
    assert tel._registered_features() == ["adapter:langchain"]  # noqa: SLF001

    # The second entry point declares the SAME name, and the set deduplicates —
    # so two adapter objects do not become two wire entries.
    AxonFlowRunnableBinding(bound=object(), axonflow=object())
    assert tel._registered_features() == ["adapter:langchain"]  # noqa: SLF001

    # AxonFlowChatModel is a SEPARATE entry point and needs its own
    # construction, not a "not None" import check.
    #
    # An earlier version of this test ended at `assert AxonFlowChatModel is not
    # None`, which is satisfied by the import alone — so deleting
    # `register_adapter` from that constructor SURVIVED the mutant. A test that
    # cannot reach the code it names is not covering it. Its isinstance guard
    # needs a real BaseChatModel, so build the same MagicMock(spec=...) the
    # LangChain adapter suite uses.
    tel._reset_adapter_registry_for_test()  # noqa: SLF001
    AxonFlowChatModel(wrapped=_fake_chat_model(), axonflow=object())
    assert tel._registered_features() == ["adapter:langchain"]  # noqa: SLF001


def test_both_frameworks_ride_one_ping():
    """An application using both adapters declares both on ONE ping, sorted.

    This is the shape that proves the array is built from the registry rather
    than from whichever adapter happened to construct last.
    """
    from axonflow.adapters.langchain import AxonFlowRunnableBinding
    from axonflow.adapters.langgraph import AxonFlowLangGraphAdapter

    AxonFlowLangGraphAdapter(client=object(), workflow_name="wf")
    AxonFlowRunnableBinding(bound=object(), axonflow=object())

    assert tel._build_payload("production")["features"] == [  # noqa: SLF001
        "adapter:langchain",
        "adapter:langgraph",
    ]


def test_governed_graph_declares_itself():
    """``wrap_langgraph`` / ``GovernedGraph`` is a THIRD entry point.

    Deleting ``register_adapter`` from ``GovernedGraph.__init__`` survived every
    other test in this file, because nothing constructed one. Four adapter entry
    points need four constructions; testing one and asserting the others by
    import is how three of them stay uncovered.
    """
    from axonflow.adapters.langgraph_wrapper import GovernedGraph

    assert tel._registered_features() == []  # noqa: SLF001
    GovernedGraph(object(), client=object(), workflow_name="wf")
    assert tel._registered_features() == ["adapter:langgraph"]  # noqa: SLF001


# ---------------------------------------------------------------------------
# The refusal must be OBSERVABLE, not merely correct
# ---------------------------------------------------------------------------


def test_a_refused_health_redirect_is_logged(route_http, caplog):
    """A refused redirect is the one failure on this path that would otherwise
    look like an ordinary non-2xx, so the diagnostic naming it is part of the
    contract — and an unasserted log line is a claim, not a behaviour.

    The Location value must NOT appear: it is remote-controlled text.
    """
    import logging

    target = _Route("http://elsewhere.test/health", body='{"version":"6.6.6"}')
    redirector = _Route(
        "http://platform.test/health", status=302, location="http://elsewhere.test/health"
    )
    route_http(redirector, target)

    with caplog.at_level(logging.DEBUG, logger="axonflow.telemetry"):
        tel._probe_platform_health("http://platform.test", timeout=2.0)  # noqa: SLF001

    messages = [r.getMessage() for r in caplog.records]
    assert any("redirect" in m and "302" in m for m in messages), (
        f"no diagnostic named the refused redirect; records were {messages}"
    )
    assert not any("elsewhere.test" in m for m in messages), (
        "the Location value reached the log. It is remote-controlled text and the "
        f"diagnostic only needs to say what was refused. Records: {messages}"
    )


def test_a_refused_checkpoint_redirect_is_logged(route_http, caplog):
    """Same, on the leg where a followed redirect would be reported as
    DELIVERY — which is the more dangerous half.
    """
    import logging

    target = _Route("http://elsewhere.test/v1/ping", body="{}")
    redirector = _Route(
        "http://checkpoint.test/v1/ping", status=302, location="http://elsewhere.test/v1/ping"
    )
    route_http(redirector, target)

    with caplog.at_level(logging.DEBUG, logger="axonflow.telemetry"):
        delivered = tel._send_telemetry_ping_now(  # noqa: SLF001
            "http://checkpoint.test/v1/ping", "production", "", debug=False
        )

    assert delivered is False
    messages = [r.getMessage() for r in caplog.records]
    assert any("redirect" in m and "302" in m for m in messages), (
        f"no diagnostic named the refused redirect; records were {messages}"
    )
