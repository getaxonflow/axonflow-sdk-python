"""Regression tests for ``health_check_detailed()`` and ``SDKCompatibility``.

The platform ``/health`` endpoint returns ``min_sdk_version`` and
``recommended_sdk_version`` as per-language maps. An older SDK declared
both as ``str`` and crashed with ``AttributeError: 'dict' object has no
attribute 'split'`` whenever a caller invoked ``health_check_detailed()``.

These tests pin the dict-shape contract and the legacy-string defensive
fallback so the regression cannot recur.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow, SDKCompatibility


def _platform_health_payload(
    *,
    min_versions: Any,
    recommended_versions: Any,
) -> dict[str, Any]:
    return {
        "service": "axonflow-agent",
        "status": "healthy",
        "version": "7.4.4",
        "tier": "community",
        "capabilities": [
            {
                "name": "health_check",
                "since": "1.0.0",
                "description": "Basic health endpoint",
            }
        ],
        "sdk_compatibility": {
            "min_sdk_version": min_versions,
            "recommended_sdk_version": recommended_versions,
        },
    }


class TestSDKCompatibilityShape:
    def test_dict_shape_parses_correctly(self) -> None:
        compat = SDKCompatibility(
            min_sdk_version={
                "go": "5.0.0",
                "java": "5.0.0",
                "python": "6.0.0",
                "typescript": "5.0.0",
            },
            recommended_sdk_version={
                "go": "5.8.0",
                "java": "6.1.0",
                "python": "6.8.0",
                "typescript": "6.1.0",
            },
        )
        assert compat.min_sdk_version_for("python") == "6.0.0"
        assert compat.min_sdk_version_for("go") == "5.0.0"
        assert compat.recommended_sdk_version_for("typescript") == "6.1.0"

    def test_unknown_language_returns_empty_string(self) -> None:
        compat = SDKCompatibility(
            min_sdk_version={"python": "6.0.0"},
            recommended_sdk_version={"python": "6.8.0"},
        )
        assert compat.min_sdk_version_for("rust") == ""
        assert compat.recommended_sdk_version_for("rust") == ""


class TestHealthCheckDetailedDictResponse:
    """The platform's actual response shape — dict per language."""

    def test_does_not_crash_with_dict_min_sdk_version(
        self, httpx_mock: HTTPXMock, config_dict: dict[str, Any]
    ) -> None:
        httpx_mock.add_response(
            url="https://test.axonflow.com/health",
            json=_platform_health_payload(
                min_versions={
                    "go": "5.0.0",
                    "java": "5.0.0",
                    "python": "6.0.0",
                    "typescript": "5.0.0",
                },
                recommended_versions={
                    "go": "5.8.0",
                    "java": "6.1.0",
                    "python": "6.8.0",
                    "typescript": "6.1.0",
                },
            ),
        )
        client = AxonFlow.sync(**config_dict)
        try:
            health = client.health_check_detailed()
        finally:
            client.close()

        assert health.status == "healthy"
        assert health.sdk_compatibility is not None
        assert isinstance(health.sdk_compatibility.min_sdk_version, dict)
        assert health.sdk_compatibility.min_sdk_version_for("python") == "6.0.0"
        assert health.sdk_compatibility.recommended_sdk_version_for("python") == "6.8.0"


class TestHealthCheckDetailedLegacyString:
    """Older platforms (pre-version-discovery) sent a bare string. Don't crash."""

    def test_bare_string_min_sdk_version_normalised_to_dict(
        self, httpx_mock: HTTPXMock, config_dict: dict[str, Any]
    ) -> None:
        httpx_mock.add_response(
            url="https://test.axonflow.com/health",
            json=_platform_health_payload(
                min_versions="6.0.0",
                recommended_versions="6.8.0",
            ),
        )
        client = AxonFlow.sync(**config_dict)
        try:
            health = client.health_check_detailed()
        finally:
            client.close()

        assert health.sdk_compatibility is not None
        # Legacy bare string is normalised to a python-keyed dict so callers
        # don't need to care which platform version they're talking to.
        assert health.sdk_compatibility.min_sdk_version_for("python") == "6.0.0"
        assert health.sdk_compatibility.recommended_sdk_version_for("python") == "6.8.0"

    def test_empty_string_min_sdk_version_yields_empty_dict(
        self, httpx_mock: HTTPXMock, config_dict: dict[str, Any]
    ) -> None:
        httpx_mock.add_response(
            url="https://test.axonflow.com/health",
            json=_platform_health_payload(min_versions="", recommended_versions=""),
        )
        client = AxonFlow.sync(**config_dict)
        try:
            health = client.health_check_detailed()
        finally:
            client.close()

        assert health.sdk_compatibility is not None
        assert health.sdk_compatibility.min_sdk_version == {}
        assert health.sdk_compatibility.min_sdk_version_for("python") == ""
