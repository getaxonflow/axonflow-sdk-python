"""Tests for the SDK telemetry module."""

from __future__ import annotations

import json
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from axonflow.telemetry import (
    _DEFAULT_CHECKPOINT_URL,
    _build_payload,
    _is_telemetry_enabled,
    _normalize_arch,
    send_telemetry_ping,
)

# ---------------------------------------------------------------------------
# _is_telemetry_enabled tests
# ---------------------------------------------------------------------------


class TestIsTelemetryEnabled:
    """Tests for the telemetry opt-in / opt-out logic."""

    def test_disabled_by_env_do_not_track(self) -> None:
        """DO_NOT_TRACK=1 disables telemetry regardless of other settings."""
        with patch.dict("os.environ", {"DO_NOT_TRACK": "1"}):
            assert _is_telemetry_enabled("production", None, True) is False
            assert _is_telemetry_enabled("production", True, True) is False

    def test_disabled_by_env_axonflow(self) -> None:
        """AXONFLOW_TELEMETRY=off disables telemetry."""
        with patch.dict("os.environ", {"AXONFLOW_TELEMETRY": "off"}):
            assert _is_telemetry_enabled("production", None, True) is False

    def test_disabled_by_env_axonflow_case_insensitive(self) -> None:
        """AXONFLOW_TELEMETRY=OFF (uppercase) also disables."""
        with patch.dict("os.environ", {"AXONFLOW_TELEMETRY": "OFF"}):
            assert _is_telemetry_enabled("production", None, True) is False

    def test_disabled_sandbox_mode(self) -> None:
        """Default OFF for sandbox mode when no explicit config."""
        with patch.dict("os.environ", {}, clear=True):
            assert _is_telemetry_enabled("sandbox", None, True) is False

    def test_enabled_production_with_credentials(self) -> None:
        """Default ON for production mode with credentials."""
        with patch.dict("os.environ", {}, clear=True):
            assert _is_telemetry_enabled("production", None, True) is True

    def test_enabled_production_without_credentials(self) -> None:
        """Default ON for production mode even without credentials."""
        with patch.dict("os.environ", {}, clear=True):
            assert _is_telemetry_enabled("production", None, False) is True

    def test_config_override_true(self) -> None:
        """Explicit True enables even in sandbox mode."""
        with patch.dict("os.environ", {}, clear=True):
            assert _is_telemetry_enabled("sandbox", True, False) is True

    def test_config_override_false(self) -> None:
        """Explicit False disables even in production mode."""
        with patch.dict("os.environ", {}, clear=True):
            assert _is_telemetry_enabled("production", False, True) is False

    def test_env_do_not_track_beats_config_true(self) -> None:
        """Environment opt-out always wins over config=True."""
        with patch.dict("os.environ", {"DO_NOT_TRACK": "1"}):
            assert _is_telemetry_enabled("production", True, True) is False

    def test_env_axonflow_telemetry_beats_config_true(self) -> None:
        """AXONFLOW_TELEMETRY=off beats config=True."""
        with patch.dict("os.environ", {"AXONFLOW_TELEMETRY": "off"}):
            assert _is_telemetry_enabled("production", True, True) is False


# ---------------------------------------------------------------------------
# _build_payload tests
# ---------------------------------------------------------------------------


class TestBuildPayload:
    """Tests for payload construction."""

    def test_payload_format(self) -> None:
        """Verify all expected fields are present and correctly typed."""
        payload = _build_payload("production")

        assert payload["sdk"] == "python"
        assert isinstance(payload["sdk_version"], str)
        assert payload["platform_version"] is None
        assert isinstance(payload["os"], str)
        assert isinstance(payload["arch"], str)
        assert isinstance(payload["runtime_version"], str)
        assert payload["deployment_mode"] == "production"
        assert payload["features"] == []
        assert isinstance(payload["instance_id"], str)
        # Should be a valid UUID
        assert len(payload["instance_id"]) == 36  # UUID v4 string length

    def test_payload_mode_propagated(self) -> None:
        """deployment_mode reflects the supplied mode."""
        assert _build_payload("sandbox")["deployment_mode"] == "sandbox"
        assert _build_payload("production")["deployment_mode"] == "production"

    def test_payload_instance_id_unique(self) -> None:
        """Each call generates a new instance_id."""
        p1 = _build_payload("production")
        p2 = _build_payload("production")
        assert p1["instance_id"] != p2["instance_id"]


# ---------------------------------------------------------------------------
# send_telemetry_ping integration tests
# ---------------------------------------------------------------------------


class TestSendTelemetryPing:
    """End-to-end tests for send_telemetry_ping."""

    @patch("axonflow.telemetry.httpx")
    def test_payload_posted_correctly(self, mock_httpx: MagicMock) -> None:
        """Verify the HTTP POST is made with correct JSON payload."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"latest_version": None, "alerts": []}
        mock_httpx.post.return_value = mock_response

        with patch.dict("os.environ", {}, clear=True):
            send_telemetry_ping(
                mode="production",
                endpoint="https://agent.axonflow.com",
                telemetry_enabled=None,
                has_credentials=True,
            )
            # Wait for daemon thread to complete.
            _wait_for_threads()

        mock_httpx.post.assert_called_once()
        call_args = mock_httpx.post.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert url == _DEFAULT_CHECKPOINT_URL

        payload = call_args[1].get("json") or call_args[0][1]
        assert payload["sdk"] == "python"
        assert payload["deployment_mode"] == "production"
        assert "instance_id" in payload

    @patch("axonflow.telemetry.httpx")
    def test_disabled_skips_post(self, mock_httpx: MagicMock) -> None:
        """When telemetry is disabled, no HTTP call is made."""
        with patch.dict("os.environ", {"DO_NOT_TRACK": "1"}):
            send_telemetry_ping(
                mode="production",
                endpoint="https://agent.axonflow.com",
                telemetry_enabled=None,
            )
        _wait_for_threads()
        mock_httpx.post.assert_not_called()

    @patch("axonflow.telemetry.httpx")
    def test_sandbox_default_skips_post(self, mock_httpx: MagicMock) -> None:
        """Sandbox mode with no config override skips telemetry."""
        with patch.dict("os.environ", {}, clear=True):
            send_telemetry_ping(
                mode="sandbox",
                endpoint="https://agent.axonflow.com",
                telemetry_enabled=None,
            )
        _wait_for_threads()
        mock_httpx.post.assert_not_called()

    @patch("axonflow.telemetry.httpx")
    def test_config_override_true_in_sandbox(self, mock_httpx: MagicMock) -> None:
        """Config override=True enables ping even in sandbox mode."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_httpx.post.return_value = mock_response

        with patch.dict("os.environ", {}, clear=True):
            send_telemetry_ping(
                mode="sandbox",
                endpoint="https://agent.axonflow.com",
                telemetry_enabled=True,
            )
        _wait_for_threads()
        mock_httpx.post.assert_called_once()

    @patch("axonflow.telemetry.httpx")
    def test_config_override_false_in_production(self, mock_httpx: MagicMock) -> None:
        """Config override=False disables ping even in production mode."""
        with patch.dict("os.environ", {}, clear=True):
            send_telemetry_ping(
                mode="production",
                endpoint="https://agent.axonflow.com",
                telemetry_enabled=False,
            )
        _wait_for_threads()
        mock_httpx.post.assert_not_called()

    @patch("axonflow.telemetry.httpx")
    def test_silent_failure_on_connection_error(self, mock_httpx: MagicMock) -> None:
        """Connection errors are swallowed silently."""
        mock_httpx.post.side_effect = httpx.ConnectError("connection refused")

        with patch.dict("os.environ", {}, clear=True):
            # Should not raise.
            send_telemetry_ping(
                mode="production",
                endpoint="https://agent.axonflow.com",
                telemetry_enabled=None,
                has_credentials=True,
            )
        _wait_for_threads()
        # No exception = pass.

    @patch("axonflow.telemetry.httpx")
    def test_silent_failure_on_timeout(self, mock_httpx: MagicMock) -> None:
        """Timeout errors are swallowed silently."""
        mock_httpx.post.side_effect = httpx.TimeoutException("timed out")

        with patch.dict("os.environ", {}, clear=True):
            send_telemetry_ping(
                mode="production",
                endpoint="https://agent.axonflow.com",
                telemetry_enabled=None,
                has_credentials=True,
            )
        _wait_for_threads()

    @patch("axonflow.telemetry.httpx")
    def test_custom_endpoint_via_env(self, mock_httpx: MagicMock) -> None:
        """AXONFLOW_CHECKPOINT_URL overrides the default endpoint."""
        custom_url = "https://custom-checkpoint.example.com/v1/ping"
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_httpx.post.return_value = mock_response

        with patch.dict(
            "os.environ",
            {"AXONFLOW_CHECKPOINT_URL": custom_url},
            clear=True,
        ):
            send_telemetry_ping(
                mode="production",
                endpoint="https://agent.axonflow.com",
                telemetry_enabled=True,
            )
        _wait_for_threads()

        call_args = mock_httpx.post.call_args
        url = call_args[0][0] if call_args[0] else call_args[1]["url"]
        assert url == custom_url

    @patch("axonflow.telemetry.httpx")
    def test_outdated_version_warning(self, mock_httpx: MagicMock) -> None:
        """When server reports a newer version, a warning is logged."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"latest_version": "99.0.0", "alerts": []}
        mock_httpx.post.return_value = mock_response

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("axonflow.telemetry.logger") as mock_logger,
        ):
            send_telemetry_ping(
                mode="production",
                endpoint="https://agent.axonflow.com",
                telemetry_enabled=None,
                has_credentials=True,
            )
            _wait_for_threads()

        mock_logger.warning.assert_called_once()
        warning_msg = mock_logger.warning.call_args[0][0]
        assert "newer" in warning_msg.lower() or "available" in warning_msg.lower()

    @patch("axonflow.telemetry.httpx")
    def test_timeout_passed_to_post(self, mock_httpx: MagicMock) -> None:
        """Verify the 3-second timeout is passed to httpx.post."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_httpx.post.return_value = mock_response

        with patch.dict("os.environ", {}, clear=True):
            send_telemetry_ping(
                mode="production",
                endpoint="https://agent.axonflow.com",
                telemetry_enabled=None,
                has_credentials=True,
            )
        _wait_for_threads()

        call_kwargs = mock_httpx.post.call_args[1]
        assert call_kwargs["timeout"] == 3

    @patch("axonflow.telemetry.httpx")
    def test_non_200_response_no_crash(self, mock_httpx: MagicMock) -> None:
        """Non-200 responses are handled gracefully."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_httpx.post.return_value = mock_response

        with patch.dict("os.environ", {}, clear=True):
            send_telemetry_ping(
                mode="production",
                endpoint="https://agent.axonflow.com",
                telemetry_enabled=None,
                has_credentials=True,
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
