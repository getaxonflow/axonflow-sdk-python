"""Tests for the SDK telemetry module."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from axonflow.telemetry import (
    _DEFAULT_CHECKPOINT_URL,
    ORG_ID_LOCAL_DEV_SENTINEL,
    _build_payload,
    _is_telemetry_enabled,
    _telemetry_org_id,
    send_telemetry_ping,
)

# Snapshot the real httpx.get / httpx.post before the conftest autouse
# fixture replaces them with the egress-blocked stub. The functional
# E2E test in this file (TestSendTelemetryPingOrgIDOnWire) is the one
# legitimate exception that needs to drive real bytes through a local
# socket — it reinstalls these via monkeypatch within the test only.
_REAL_HTTPX_GET = httpx.get
_REAL_HTTPX_POST = httpx.post

# ---------------------------------------------------------------------------
# _is_telemetry_enabled tests
# ---------------------------------------------------------------------------


class TestIsTelemetryEnabled:
    """Tests for the v8.0 telemetry opt-in / opt-out logic.

    v8 contract: AXONFLOW_TELEMETRY=off is the SOLE opt-out lever.
    Telemetry is otherwise ON for every mode (sandbox and production fire
    on the same schedule; sandbox is tagged stream="sandbox" in payload).
    The v7.x ``telemetry_enabled`` config override and ``has_credentials``
    parameter were removed — see CHANGELOG v8.0.0.
    """

    def test_enabled_by_default_no_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no AXONFLOW_TELEMETRY env var, telemetry is ON."""
        monkeypatch.setenv("AXONFLOW_TELEMETRY", "")
        assert _is_telemetry_enabled() is True

    def test_disabled_by_env_axonflow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AXONFLOW_TELEMETRY=off disables telemetry."""
        monkeypatch.setenv("AXONFLOW_TELEMETRY", "off")
        assert _is_telemetry_enabled() is False

    def test_disabled_by_env_axonflow_case_insensitive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AXONFLOW_TELEMETRY=OFF (uppercase) also disables."""
        monkeypatch.setenv("AXONFLOW_TELEMETRY", "OFF")
        assert _is_telemetry_enabled() is False

    def test_disabled_by_env_with_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AXONFLOW_TELEMETRY=' off ' (padded) also disables."""
        monkeypatch.setenv("AXONFLOW_TELEMETRY", "  off  ")
        assert _is_telemetry_enabled() is False

    def test_other_env_values_do_not_disable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Any value other than 'off' (case-insensitive) leaves telemetry ON.

        Specifically, '0' / 'false' / 'no' DO NOT disable — only the literal
        token 'off' is the opt-out. This matches the cross-SDK contract.
        """
        for val in ("0", "false", "no", "true", "on", "anything"):
            monkeypatch.setenv("AXONFLOW_TELEMETRY", val)
            assert _is_telemetry_enabled() is True, f"value {val!r} should NOT disable"

    def test_do_not_track_alone_does_NOT_disable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DO_NOT_TRACK=1 alone is no longer honored as an AxonFlow opt-out.

        Regression guard: host CLIs like Codex and Claude Code inject DNT=1
        unconditionally, so honoring it would prevent telemetry from any
        plugin/SDK running inside those hosts regardless of user intent.
        """
        monkeypatch.setenv("DO_NOT_TRACK", "1")
        monkeypatch.setenv("AXONFLOW_TELEMETRY", "")
        assert _is_telemetry_enabled() is True

    def test_axonflow_off_still_disables_with_dnt_also_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AXONFLOW_TELEMETRY=off is the canonical opt-out and wins regardless of DNT."""
        monkeypatch.setenv("DO_NOT_TRACK", "1")
        monkeypatch.setenv("AXONFLOW_TELEMETRY", "off")
        assert _is_telemetry_enabled() is False


# ---------------------------------------------------------------------------
# _build_payload tests
# ---------------------------------------------------------------------------


class TestBuildPayload:
    """Tests for payload construction."""

    def test_payload_format(self) -> None:
        """Verify all expected fields are present and correctly typed."""
        payload = _build_payload("production", deployment_mode="self_hosted")

        assert payload["telemetry_type"] == "sdk"
        assert payload["sdk"] == "python"
        assert isinstance(payload["sdk_version"], str)
        assert payload["platform_version"] is None
        assert isinstance(payload["os"], str)
        assert isinstance(payload["arch"], str)
        assert isinstance(payload["runtime_version"], str)
        assert payload["deployment_mode"] == "self_hosted"
        assert payload["features"] == []
        assert isinstance(payload["instance_id"], str)
        # Should be a valid UUID
        assert len(payload["instance_id"]) == 36  # UUID v4 string length
        # v1 telemetry-schema field removed in 8.0.1: profile is no longer in payload
        assert "profile" not in payload

    def test_payload_deployment_mode_propagated(self) -> None:
        """deployment_mode reflects the supplied v1 schema value."""
        sh = _build_payload("sandbox", deployment_mode="self_hosted")
        cs = _build_payload("production", deployment_mode="community_saas")
        un = _build_payload("production", deployment_mode="unknown")
        assert sh["deployment_mode"] == "self_hosted"
        assert cs["deployment_mode"] == "community_saas"
        assert un["deployment_mode"] == "unknown"

    def test_payload_instance_id_unique(self) -> None:
        """Each call generates a new instance_id."""
        p1 = _build_payload("production")
        p2 = _build_payload("production")
        assert p1["instance_id"] != p2["instance_id"]

    def test_sandbox_mode_emits_stream_tag(self) -> None:
        """v8 contract: sandbox-mode payload carries stream="sandbox" so
        analytics can distinguish dev/test pings server-side without
        conflating them with production heartbeat.
        """
        payload = _build_payload("sandbox")
        assert payload.get("stream") == "sandbox"

    def test_production_mode_omits_stream_tag(self) -> None:
        """Production-mode payload omits the stream field entirely; the
        server defaults absent stream to "heartbeat". Keeps wire shape
        byte-identical with v7.x for the production path.
        """
        payload = _build_payload("production")
        assert "stream" not in payload

    def test_other_modes_omit_stream_tag(self) -> None:
        """Any non-sandbox mode (empty / staging / unknown) omits stream."""
        for mode in ("", "staging", "unknown", "development"):
            payload = _build_payload(mode)
            assert "stream" not in payload, f"mode={mode!r} should not emit stream"


# ---------------------------------------------------------------------------
# send_telemetry_ping integration tests
# ---------------------------------------------------------------------------


class TestSendTelemetryPing:
    """End-to-end tests for send_telemetry_ping under the v8 contract."""

    @patch("axonflow.telemetry.httpx")
    def test_payload_posted_correctly(
        self, mock_httpx: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the HTTP POST is made with correct JSON payload."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"latest_version": None, "alerts": []}
        mock_httpx.post.return_value = mock_response

        monkeypatch.setenv("AXONFLOW_TELEMETRY", "")
        send_telemetry_ping(
            mode="production",
            endpoint="https://agent.axonflow.com",
        )
        # Wait for daemon thread to complete.
        _wait_for_threads()

        mock_httpx.post.assert_called_once()
        call_args = mock_httpx.post.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert url == _DEFAULT_CHECKPOINT_URL

        payload = call_args[1].get("json") or call_args[0][1]
        assert payload["telemetry_type"] == "sdk"
        assert payload["sdk"] == "python"
        # v1 schema: deployment_mode classifies from endpoint host.
        assert payload["deployment_mode"] == "self_hosted"
        # 8.0.1: profile field removed (collided with governance env var).
        assert "profile" not in payload
        assert "instance_id" in payload
        # Production-mode payload omits stream (server defaults to heartbeat).
        assert "stream" not in payload

    @patch("axonflow.telemetry.httpx")
    def test_disabled_skips_post(
        self, mock_httpx: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When telemetry is disabled (AXONFLOW_TELEMETRY=off), no HTTP call is made."""
        monkeypatch.setenv("AXONFLOW_TELEMETRY", "off")
        send_telemetry_ping(
            mode="production",
            endpoint="https://agent.axonflow.com",
        )
        _wait_for_threads()
        mock_httpx.post.assert_not_called()

    @patch("axonflow.telemetry.httpx")
    def test_sandbox_mode_fires_with_stream_tag(
        self, mock_httpx: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """v8 contract: sandbox-mode pings FIRE (no longer suppressed) and
        carry stream="sandbox" in the payload. Pre-v8 this test would have
        asserted no ping was sent — see CHANGELOG v8.0.0 for the rationale.
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_httpx.post.return_value = mock_response

        monkeypatch.setenv("AXONFLOW_TELEMETRY", "")
        send_telemetry_ping(
            mode="sandbox",
            endpoint="https://agent.axonflow.com",
        )
        _wait_for_threads()

        mock_httpx.post.assert_called_once()
        payload = mock_httpx.post.call_args[1].get("json") or mock_httpx.post.call_args[0][1]
        # v1 schema: deployment_mode classifies from endpoint host (self_hosted),
        # NOT from config.Mode. The sandbox marker lives on `stream`.
        assert payload["deployment_mode"] == "self_hosted"
        assert payload.get("stream") == "sandbox"

    @patch("axonflow.telemetry.httpx")
    def test_silent_failure_on_connection_error(
        self, mock_httpx: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Connection errors are swallowed silently."""
        mock_httpx.post.side_effect = httpx.ConnectError("connection refused")

        monkeypatch.setenv("AXONFLOW_TELEMETRY", "")
        # Should not raise.
        send_telemetry_ping(
            mode="production",
            endpoint="https://agent.axonflow.com",
        )
        _wait_for_threads()
        # No exception = pass.

    @patch("axonflow.telemetry.httpx")
    def test_silent_failure_on_timeout(
        self, mock_httpx: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Timeout errors are swallowed silently."""
        mock_httpx.post.side_effect = httpx.TimeoutException("timed out")

        monkeypatch.setenv("AXONFLOW_TELEMETRY", "")
        send_telemetry_ping(
            mode="production",
            endpoint="https://agent.axonflow.com",
        )
        _wait_for_threads()

    @patch("axonflow.telemetry.httpx")
    def test_custom_endpoint_via_env(
        self, mock_httpx: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AXONFLOW_CHECKPOINT_URL overrides the default endpoint."""
        custom_url = "https://custom-checkpoint.example.com/v1/ping"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_httpx.post.return_value = mock_response

        monkeypatch.setenv("AXONFLOW_TELEMETRY", "")
        monkeypatch.setenv("AXONFLOW_CHECKPOINT_URL", custom_url)
        send_telemetry_ping(
            mode="production",
            endpoint="https://agent.axonflow.com",
        )
        _wait_for_threads()

        call_args = mock_httpx.post.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert url == custom_url

    @patch("axonflow.telemetry.httpx")
    def test_outdated_version_warning(
        self, mock_httpx: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When server reports a newer version, a warning is logged."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"latest_version": "99.0.0", "alerts": []}
        mock_httpx.post.return_value = mock_response

        monkeypatch.setenv("AXONFLOW_TELEMETRY", "")
        with patch("axonflow.telemetry.logger") as mock_logger:
            send_telemetry_ping(
                mode="production",
                endpoint="https://agent.axonflow.com",
            )
            _wait_for_threads()

        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "newer" in warning_msg.lower() or "available" in warning_msg.lower()

    @patch("axonflow.telemetry.httpx")
    def test_timeout_passed_to_post(
        self, mock_httpx: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """POST timeout is derived from the shared telemetry deadline, so it
        is bounded by the total budget (``_TIMEOUT_SECONDS``) and comfortably
        positive after the health probe. Under mocks the health probe returns
        instantly, so the remaining budget stays close to the full 3s.
        """
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_httpx.post.return_value = mock_response

        monkeypatch.setenv("AXONFLOW_TELEMETRY", "")
        send_telemetry_ping(
            mode="production",
            endpoint="https://agent.axonflow.com",
        )
        _wait_for_threads()

        call_kwargs = mock_httpx.post.call_args[1]
        # Total budget is 3s; health probe is mocked and returns instantly,
        # so the POST should get essentially all of it. Allow slack for
        # scheduler jitter.
        assert 2.0 < call_kwargs["timeout"] <= 3.0

    @patch("axonflow.telemetry.httpx")
    def test_non_200_response_no_crash(
        self, mock_httpx: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Non-200 responses are handled gracefully."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_httpx.post.return_value = mock_response

        monkeypatch.setenv("AXONFLOW_TELEMETRY", "")
        send_telemetry_ping(
            mode="production",
            endpoint="https://agent.axonflow.com",
        )
        _wait_for_threads()
        # No exception = pass.


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wait_for_threads(timeout: float = 5.0) -> None:
    """Wait for all non-main daemon threads to finish (up to *timeout* seconds).

    This is needed because send_telemetry_ping spawns a daemon thread.
    """
    deadline = time.monotonic() + timeout
    for t in threading.enumerate():
        if t is threading.current_thread():
            continue
        if t.daemon:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                t.join(timeout=remaining)


# ---------------------------------------------------------------------------
# org_id tests — v9.1 preflight, issue #2277
# ---------------------------------------------------------------------------


class TestTelemetryOrgIDHelperUnit:
    """``_telemetry_org_id`` env → sentinel fallback. Issue #2277."""

    def test_org_id_set_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORG_ID", "acme-corp")
        assert _telemetry_org_id() == "acme-corp"

    def test_org_id_unset_returns_local_dev_org_sentinel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ORG_ID", raising=False)
        assert _telemetry_org_id() == ORG_ID_LOCAL_DEV_SENTINEL
        assert ORG_ID_LOCAL_DEV_SENTINEL == "local-dev-org"  # wire-value lock

    def test_org_id_empty_string_falls_through_to_sentinel(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty string is treated as unset — same as the Go SDK."""
        monkeypatch.setenv("ORG_ID", "")
        assert _telemetry_org_id() == ORG_ID_LOCAL_DEV_SENTINEL

    def test_org_id_cs_prefixed_passes_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """cs_<uuid> Community SaaS tenant identifier passes through verbatim."""
        cs_id = "cs_abc12345-6789-4def-9012-345678901234"
        monkeypatch.setenv("ORG_ID", cs_id)
        assert _telemetry_org_id() == cs_id


class TestBuildPayloadIncludesOrgID:
    """``_build_payload`` always includes ``org_id``. Issue #2277."""

    def test_payload_includes_org_id_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ORG_ID", "acme-corp")
        payload = _build_payload("production", deployment_mode="self_hosted")
        assert payload["org_id"] == "acme-corp"

    def test_payload_includes_sentinel_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ORG_ID", raising=False)
        payload = _build_payload("production")
        assert payload["org_id"] == ORG_ID_LOCAL_DEV_SENTINEL

    def test_payload_includes_cs_uuid_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cs_id = "cs_e3a4b5c6-d7e8-4f90-a1b2-c3d4e5f6a7b8"
        monkeypatch.setenv("ORG_ID", cs_id)
        payload = _build_payload("production")
        assert payload["org_id"] == cs_id


class TestSendTelemetryPingOrgIDOnWire:
    """Functional E2E: org_id arrives on the wire at the receiver.

    Uses a real ``http.server.HTTPServer`` in a daemon thread (no mocks
    of httpx) so this proof exercises the full net stack between the
    SDK and the receiver. Captures the raw request body and asserts the
    literal JSON contains ``"org_id":"<value>"`` — defends against tag-
    removal mutations and unintended omitempty behavior.
    """

    def _run_with_capture(
        self,
        monkeypatch: pytest.MonkeyPatch,
        org_id_env: str | None,
    ) -> bytes:
        import http.server
        import json
        import socketserver

        # The autouse _disable_telemetry conftest fixture replaces
        # httpx.get / httpx.post with a RuntimeError-raising stub to
        # block real egress. This test is the legitimate exception:
        # it stands up a localhost listener and wants real bytes on
        # the wire. Restore the real callables for this test only;
        # pytest monkeypatch undoes the override at function teardown.
        monkeypatch.setattr(httpx, "get", _REAL_HTTPX_GET)
        monkeypatch.setattr(httpx, "post", _REAL_HTTPX_POST)

        captured: dict[str, bytes] = {}

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                captured["body"] = self.rfile.read(length)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"latest_version": None, "alerts": []}).encode())

            def do_GET(self) -> None:  # noqa: N802
                # SDK probes /health before posting telemetry; return a
                # synthetic platform-version so the path doesn't fall
                # back to None.
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"version": "8.0.0-test"}).encode())

            def log_message(self, *_args: object) -> None:  # silence stderr
                return

        with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as srv:
            port = srv.server_address[1]
            # Handle health probe + telemetry POST in sequence — two requests.
            srv_thread = threading.Thread(
                target=lambda: [srv.handle_request(), srv.handle_request()],
                daemon=True,
            )
            srv_thread.start()

            monkeypatch.setenv("AXONFLOW_CHECKPOINT_URL", f"http://127.0.0.1:{port}/v1/ping")
            monkeypatch.setenv("AXONFLOW_TELEMETRY", "")
            if org_id_env is None:
                monkeypatch.delenv("ORG_ID", raising=False)
            else:
                monkeypatch.setenv("ORG_ID", org_id_env)

            send_telemetry_ping(
                mode="production",
                endpoint=f"http://127.0.0.1:{port}",
            )
            _wait_for_threads()
            srv_thread.join(timeout=2.0)

        assert "body" in captured, "telemetry ping never reached the local server"
        return captured["body"]

    def test_org_id_acme_corp_on_wire(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = self._run_with_capture(monkeypatch, "acme-corp")
        assert b'"org_id": "acme-corp"' in body or b'"org_id":"acme-corp"' in body, (
            f"wire body missing org_id=acme-corp: {body!r}"
        )

    def test_org_id_sentinel_on_wire(self, monkeypatch: pytest.MonkeyPatch) -> None:
        body = self._run_with_capture(monkeypatch, None)
        assert b'"org_id": "local-dev-org"' in body or b'"org_id":"local-dev-org"' in body, (
            f"wire body missing sentinel org_id: {body!r}"
        )

    def test_org_id_cs_prefixed_on_wire(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cs_id = "cs_f29e9c5c-5c5b-4e0d-8e0d-aabbccddeeff"
        body = self._run_with_capture(monkeypatch, cs_id)
        expected_a = f'"org_id": "{cs_id}"'.encode()
        expected_b = f'"org_id":"{cs_id}"'.encode()
        assert expected_a in body or expected_b in body, (
            f"wire body missing cs_-prefixed org_id: {body!r}"
        )
