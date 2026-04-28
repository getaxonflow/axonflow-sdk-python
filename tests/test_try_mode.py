"""Tests for AXONFLOW_TRY=1 try-mode auto-endpoint resolution.

These cover the env-var-driven endpoint override at axonflow/client.py:480-491
which previously had zero coverage.
"""

from __future__ import annotations

import pytest

from axonflow import AxonFlow


def test_try_mode_resolves_to_try_endpoint(monkeypatch):
    """AXONFLOW_TRY=1 should ignore endpoint+AXONFLOW_AGENT_URL and route to try.getaxonflow.com."""
    monkeypatch.setenv("AXONFLOW_TRY", "1")
    monkeypatch.delenv("AXONFLOW_AGENT_URL", raising=False)

    client = AxonFlow(client_id="test-client", client_secret="test-secret")

    assert client._config.endpoint == "https://try.getaxonflow.com"


def test_try_mode_overrides_explicit_endpoint(monkeypatch):
    """AXONFLOW_TRY=1 takes precedence over an explicitly-passed endpoint."""
    monkeypatch.setenv("AXONFLOW_TRY", "1")

    client = AxonFlow(
        endpoint="http://localhost:8080",
        client_id="test-client",
        client_secret="test-secret",
    )

    assert client._config.endpoint == "https://try.getaxonflow.com"


def test_try_mode_overrides_axonflow_agent_url_env(monkeypatch):
    """AXONFLOW_TRY=1 takes precedence over AXONFLOW_AGENT_URL env var."""
    monkeypatch.setenv("AXONFLOW_TRY", "1")
    monkeypatch.setenv("AXONFLOW_AGENT_URL", "http://localhost:9999")

    client = AxonFlow(client_id="test-client", client_secret="test-secret")

    assert client._config.endpoint == "https://try.getaxonflow.com"


def test_try_mode_requires_client_id(monkeypatch):
    """AXONFLOW_TRY=1 without client_id raises TypeError with a clear message."""
    monkeypatch.setenv("AXONFLOW_TRY", "1")

    with pytest.raises(TypeError, match="client_id is required in try mode"):
        AxonFlow()


def test_try_mode_requires_client_id_even_with_endpoint(monkeypatch):
    """An explicit endpoint doesn't satisfy try mode's client_id requirement."""
    monkeypatch.setenv("AXONFLOW_TRY", "1")

    with pytest.raises(TypeError, match="client_id is required in try mode"):
        AxonFlow(endpoint="https://something.example.com")


def test_try_mode_off_uses_normal_endpoint_resolution(monkeypatch):
    """Without AXONFLOW_TRY=1 the SDK uses the explicit endpoint."""
    monkeypatch.delenv("AXONFLOW_TRY", raising=False)
    monkeypatch.delenv("AXONFLOW_AGENT_URL", raising=False)

    client = AxonFlow(
        endpoint="http://localhost:8080",
        client_id="test-client",
        client_secret="test-secret",
    )

    assert client._config.endpoint == "http://localhost:8080"


def test_try_mode_unset_falls_back_to_agent_url_env(monkeypatch):
    """Without AXONFLOW_TRY=1, AXONFLOW_AGENT_URL is the fallback when no endpoint is passed."""
    monkeypatch.delenv("AXONFLOW_TRY", raising=False)
    monkeypatch.setenv("AXONFLOW_AGENT_URL", "http://localhost:9999")

    client = AxonFlow(client_id="test-client", client_secret="test-secret")

    assert client._config.endpoint == "http://localhost:9999"


def test_try_mode_disabled_with_other_value(monkeypatch):
    """AXONFLOW_TRY=other-value is treated as off (only the literal "1" enables it)."""
    monkeypatch.setenv("AXONFLOW_TRY", "true")
    monkeypatch.delenv("AXONFLOW_AGENT_URL", raising=False)

    client = AxonFlow(
        endpoint="http://localhost:8080",
        client_id="test-client",
        client_secret="test-secret",
    )

    assert client._config.endpoint == "http://localhost:8080"
