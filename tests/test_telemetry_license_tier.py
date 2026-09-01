"""license_tier telemetry field (#3619).

Contract under test: the platform's licence tier rides along on the ``/health``
response the SDK ALREADY fetches for ``platform_version``, is forwarded to the
checkpoint receiver verbatim, and is OMITTED — never defaulted — whenever it
could not be learned.

These tests stand up real ``http.server`` listeners on both sides rather than
mocking ``httpx``, so the assertions are about bytes that actually crossed a
socket. A mocked transport would certify the payload dict; only the wire body
proves what the receiver sees.
"""

from __future__ import annotations

import http.server
import json
import socketserver
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import pytest

from axonflow.telemetry import (
    _build_payload,
    _probe_platform_health,
    _send_telemetry_ping_now,
)

# Snapshot the real callables before the conftest autouse fixture swaps in the
# egress-blocking stub. These tests are a legitimate exception: they bind a
# localhost listener and want real bytes on the wire.
_REAL_HTTPX_GET = httpx.get
_REAL_HTTPX_POST = httpx.post

# Endpoints that raise from inside httpx rather than returning a response.
# "http://[::1" (an unclosed IPv6 bracket) is the one that matters: it raises
# httpx.InvalidURL, which does NOT subclass httpx.HTTPError.
MALFORMED_ENDPOINTS = [
    "http://[::1",
    "http://\x7f",  # control character in host
    "http://",  # no host
    "not a url",
    "",
]

# Exactly the values platform/agent/run.go currentLicenseTier() can return,
# plus the csaas "Plus" alias its health serializer emits.
PLATFORM_EMITTED_TIERS = ["community", "evaluation", "Enterprise", "Plus", "starting"]


@contextmanager
def _stand_in_platform(status: int, body: str) -> Iterator[str]:
    """A stand-in platform whose /health returns a fixed status and raw body."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return
            payload = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            return

    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as srv:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            yield f"http://127.0.0.1:{srv.server_address[1]}"
        finally:
            srv.shutdown()


@contextmanager
def _checkpoint() -> Iterator[dict[str, bytes]]:
    """A stand-in checkpoint receiver recording the raw POST body."""
    captured: dict[str, bytes] = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            captured["body"] = self.rfile.read(length)
            resp = json.dumps({"latest_version": None, "alerts": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

        def log_message(self, *_args: object) -> None:
            return

    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as srv:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        captured["url"] = f"http://127.0.0.1:{srv.server_address[1]}/v1/ping".encode()
        try:
            yield captured
        finally:
            srv.shutdown()


def _capture_wire(monkeypatch: pytest.MonkeyPatch, platform_endpoint: str) -> bytes:
    """Run one real ping against ``platform_endpoint``; return the raw wire body."""
    monkeypatch.setattr(httpx, "get", _REAL_HTTPX_GET)
    monkeypatch.setattr(httpx, "post", _REAL_HTTPX_POST)
    monkeypatch.setenv("AXONFLOW_TELEMETRY", "")

    with _checkpoint() as cap:
        _send_telemetry_ping_now(
            cap["url"].decode(),
            "production",
            platform_endpoint,
            debug=False,
        )
        return cap.get("body", b"")


class TestTierReachesTheWireVerbatim:
    @pytest.mark.parametrize("tier", PLATFORM_EMITTED_TIERS)
    def test_every_platform_emitted_value_is_forwarded_unchanged(
        self, monkeypatch: pytest.MonkeyPatch, tier: str
    ) -> None:
        body = json.dumps({"status": "healthy", "version": "10.3.0", "tier": tier})
        with _stand_in_platform(200, body) as endpoint:
            wire = _capture_wire(monkeypatch, endpoint)

        # Assert on the literal JSON rather than a parsed dict: a mutation
        # renaming the key would still round-trip through a decode.
        assert f'"license_tier": "{tier}"'.encode() in wire or (
            f'"license_tier":"{tier}"'.encode() in wire
        ), wire


class TestTierIsOmittedWhenNotLearned:
    """The load-bearing fail-open half.

    For every way the health probe can fail, the ping must still be delivered
    and the field must be ABSENT from the JSON — never ``""`` and never a
    substituted default. Emitting ``"community"`` for a platform we could not
    reach would be a false claim about a customer's deployment.
    """

    @pytest.mark.parametrize(
        ("name", "status", "body"),
        [
            ("health returns 500", 500, json.dumps({"tier": "Enterprise"})),
            ("health returns malformed JSON", 200, '{"tier":"Enterprise"'),
            ("health has no tier key", 200, json.dumps({"status": "healthy", "version": "10.3.0"})),
            ("health has an empty tier", 200, json.dumps({"version": "10.3.0", "tier": ""})),
            ("health has a non-string tier", 200, json.dumps({"version": "10.3.0", "tier": 42})),
            ("health returns a JSON array", 200, "[1,2,3]"),
        ],
    )
    def test_ping_still_delivered_and_field_absent(
        self, monkeypatch: pytest.MonkeyPatch, name: str, status: int, body: str
    ) -> None:
        with _stand_in_platform(status, body) as endpoint:
            wire = _capture_wire(monkeypatch, endpoint)

        assert wire, f"{name}: the ping was SUPPRESSED — telemetry must degrade, not stop"
        assert b'"telemetry_type"' in wire, f"{name}: not a well-formed sdk ping: {wire!r}"
        assert b"license_tier" not in wire, f"{name}: field must be omitted, got {wire!r}"

    def test_platform_unreachable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Bind then release a port so nothing is listening on it.
        with _stand_in_platform(200, "{}") as endpoint:
            dead = endpoint

        wire = _capture_wire(monkeypatch, dead)
        assert wire, "the ping was SUPPRESSED — telemetry must degrade, not stop"
        assert b"license_tier" not in wire, wire

    def test_endpoint_not_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        wire = _capture_wire(monkeypatch, "")
        assert wire, "the ping was SUPPRESSED — telemetry must degrade, not stop"
        assert b"license_tier" not in wire, wire


class TestVersionAndTierAreLearnedIndependently:
    """One field's absence must never discard the other.

    The pre-#3619 probe returned early when ``version`` was empty; had the tier
    been read after that guard, a platform answering with a tier but no version
    would have reported no tier at all.
    """

    @pytest.mark.parametrize(
        ("body", "want_version", "want_tier"),
        [
            (json.dumps({"version": "10.3.0", "tier": "Enterprise"}), "10.3.0", "Enterprise"),
            (json.dumps({"tier": "Enterprise"}), None, "Enterprise"),
            (json.dumps({"version": "10.3.0"}), "10.3.0", None),
            (json.dumps({"status": "healthy"}), None, None),
        ],
    )
    def test_each_field_promoted_on_its_own(
        self,
        monkeypatch: pytest.MonkeyPatch,
        body: str,
        want_version: str | None,
        want_tier: str | None,
    ) -> None:
        monkeypatch.setattr(httpx, "get", _REAL_HTTPX_GET)
        with _stand_in_platform(200, body) as endpoint:
            probe = _probe_platform_health(endpoint, timeout=2.0)

        assert probe.platform_version == want_version
        assert probe.license_tier == want_tier


class TestBuildPayloadOmitsRatherThanNulls:
    def test_absent_tier_omits_the_key_entirely(self) -> None:
        payload = _build_payload("production", "10.3.0", "remote", "self_hosted", None)
        # None must not serialize as JSON null: null is a claim ("the tier is
        # nothing"), omission is the absence this wire uses for "not known".
        assert "license_tier" not in payload
        assert "license_tier" not in json.dumps(payload)

    def test_present_tier_is_carried_unchanged(self) -> None:
        payload = _build_payload("production", "10.3.0", "remote", "self_hosted", "Plus")
        assert payload["license_tier"] == "Plus"

    def test_platform_version_keeps_its_explicit_null_wire_shape(self) -> None:
        # platform_version has always been sent as an explicit null when
        # unknown. That long-standing shape is deliberately NOT changed to
        # match license_tier's omission.
        payload = _build_payload("production", None, "remote", "self_hosted", None)
        assert payload["platform_version"] is None
        assert '"platform_version": null' in json.dumps(payload, indent=None) or (
            '"platform_version":null' in json.dumps(payload, separators=(",", ":"))
        )


class TestProbeSharesTheTelemetryDeadline:
    def test_a_stalled_health_does_not_stack_a_second_timeout(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Guards issue #1692: the probe and the POST share ONE budget.

        Reading the tier must not introduce a second request or a second
        timeout, so a stalled /health is bounded by the budget it was handed.
        """
        monkeypatch.setattr(httpx, "get", _REAL_HTTPX_GET)

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                time.sleep(5)

            def log_message(self, *_args: object) -> None:
                return

        with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as srv:
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            endpoint = f"http://127.0.0.1:{srv.server_address[1]}"
            try:
                started = time.monotonic()
                probe = _probe_platform_health(endpoint, timeout=0.4)
                elapsed = time.monotonic() - started
            finally:
                srv.shutdown()

        assert probe.platform_version is None
        assert probe.license_tier is None
        # Bounded by the supplied budget, not an independent per-probe timeout.
        assert elapsed < 0.4 + 1.0, f"probe took {elapsed}s — a second timeout is stacking"


class TestTheProbeNeverRaisesIntoTheCaller:
    """Telemetry must never disrupt the caller — including on a malformed endpoint.

    ``httpx.InvalidURL`` does NOT subclass ``httpx.HTTPError``, so the explicit
    exception tuple these functions used to carry let it escape: an endpoint
    with an unclosed IPv6 bracket raised straight out of
    ``_send_telemetry_ping_now``, which documents that it returns ``False`` on
    any failure. Both functions now catch broadly.
    """

    @pytest.mark.parametrize("endpoint", MALFORMED_ENDPOINTS)
    def test_probe_returns_empty_instead_of_raising(
        self, monkeypatch: pytest.MonkeyPatch, endpoint: str
    ) -> None:
        monkeypatch.setattr(httpx, "get", _REAL_HTTPX_GET)

        probe = _probe_platform_health(endpoint, timeout=0.5)

        assert probe.platform_version is None
        assert probe.license_tier is None

    @pytest.mark.parametrize("endpoint", MALFORMED_ENDPOINTS)
    def test_the_whole_ping_returns_false_instead_of_raising(
        self, monkeypatch: pytest.MonkeyPatch, endpoint: str
    ) -> None:
        monkeypatch.setattr(httpx, "get", _REAL_HTTPX_GET)
        monkeypatch.setattr(httpx, "post", _REAL_HTTPX_POST)
        monkeypatch.setenv("AXONFLOW_TELEMETRY", "")

        # Nothing is listening on the checkpoint URL either; the point is that
        # the call RETURNS rather than propagating an exception.
        assert (
            _send_telemetry_ping_now(
                "http://127.0.0.1:1/v1/ping", "production", endpoint, debug=False
            )
            is False
        )

    def test_a_malformed_checkpoint_url_also_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The POST leg has the same exposure as the probe leg.
        monkeypatch.setattr(httpx, "get", _REAL_HTTPX_GET)
        monkeypatch.setattr(httpx, "post", _REAL_HTTPX_POST)
        monkeypatch.setenv("AXONFLOW_TELEMETRY", "")

        assert _send_telemetry_ping_now("http://[::1", "production", "", debug=False) is False


class TestTheWholePingHonoursOneDeadline:
    """Exercises the CALL SITE, not just the probe.

    ``test_a_stalled_health_does_not_stack_a_second_timeout`` hands
    ``_probe_platform_health`` a budget directly, so it stays green even if
    ``_send_telemetry_ping_now`` stops passing ``timeout=health_budget`` — the
    exact mutation that restores issue #1692's ~5s worst case. Testing the
    predicate is not testing the wiring.
    """

    def test_a_stalled_health_does_not_blow_the_shared_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(httpx, "get", _REAL_HTTPX_GET)
        monkeypatch.setattr(httpx, "post", _REAL_HTTPX_POST)
        monkeypatch.setenv("AXONFLOW_TELEMETRY", "")

        class _Stalling(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                time.sleep(10)

            def log_message(self, *_args: object) -> None:
                return

        with socketserver.TCPServer(("127.0.0.1", 0), _Stalling) as platform_srv:
            threading.Thread(target=platform_srv.serve_forever, daemon=True).start()
            endpoint = f"http://127.0.0.1:{platform_srv.server_address[1]}"
            try:
                with _checkpoint() as cap:
                    started = time.monotonic()
                    _send_telemetry_ping_now(
                        cap["url"].decode(), "production", endpoint, debug=False
                    )
                    elapsed = time.monotonic() - started
            finally:
                platform_srv.shutdown()

        # The health probe is capped at 1s out of the shared _TIMEOUT_SECONDS
        # budget. A call site that dropped the derived timeout would fall back
        # to the 2.0s default and push the total well past this bound.
        assert elapsed < 2.0, (
            f"the whole ping took {elapsed:.2f}s — the call site is not passing "
            f"the derived health budget (issue #1692)"
        )


class TestDeploymentModeIsUnaffected:
    """The three similarly-named concepts stay separate.

    The SDK's endpoint-derived TOPOLOGY dimension must be identical whether or
    not the platform reported an edition.
    """

    @pytest.mark.parametrize(
        "body",
        [
            json.dumps({"version": "10.3.0", "tier": "Enterprise"}),
            json.dumps({"version": "10.3.0"}),
        ],
    )
    def test_topology_dimension_unchanged_by_the_tier(
        self, monkeypatch: pytest.MonkeyPatch, body: str
    ) -> None:
        with _stand_in_platform(200, body) as endpoint:
            wire = _capture_wire(monkeypatch, endpoint)

        assert json.loads(wire)["deployment_mode"] == "self_hosted"
