"""Regression tests for review-feedback fixes on `list_providers()`.

1. Wire-shape: `LLMProvider` surfaces ``endpoint``, ``model``, ``region``,
   ``rate_limit``, ``timeout_seconds``, and ``settings``.
2. Pagination: `list_providers_paged` returns `LLMProviderListResponse`;
   `list_all_providers` walks every page.
3. Defensive: malformed health on one provider does NOT crash the listing.
4. Empty version string in `min_sdk_version_for(language)` does NOT log
   the upgrade-warning.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from axonflow import (
    AxonFlow,
    LLMProviderListResponse,
    PaginationMeta,
)


def _full_provider() -> dict[str, Any]:
    return {
        "name": "anthropic",
        "type": "anthropic",
        "enabled": True,
        "priority": 0,
        "weight": 100,
        "has_api_key": True,
        "endpoint": "https://api.anthropic.com",
        "model": "claude-haiku-4-5",
        "region": "us-east-1",
        "rate_limit": 60,
        "timeout_seconds": 30,
        "settings": {"temperature_default": 0.2},
        "health": {"status": "healthy"},
    }


def _wrapped(
    providers: list[dict[str, Any]], pagination: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "providers": providers,
        "pagination": pagination
        or {"page": 1, "page_size": 20, "total_items": len(providers), "total_pages": 1},
    }


class TestLLMProviderFullShape:
    def test_all_optional_fields_surface_when_present(
        self, httpx_mock: HTTPXMock, config_dict: dict[str, Any]
    ) -> None:
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/llm-providers",
            json=_wrapped([_full_provider()]),
        )
        client = AxonFlow.sync(**config_dict)
        try:
            providers = client.list_providers()
        finally:
            client.close()

        assert len(providers) == 1
        p = providers[0]
        assert p.endpoint == "https://api.anthropic.com"
        assert p.model == "claude-haiku-4-5"
        assert p.region == "us-east-1"
        assert p.rate_limit == 60
        assert p.timeout_seconds == 30
        assert p.settings == {"temperature_default": 0.2}

    def test_optional_fields_default_to_none_when_absent(
        self, httpx_mock: HTTPXMock, config_dict: dict[str, Any]
    ) -> None:
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/llm-providers",
            json=_wrapped([{"name": "ollama", "type": "ollama", "enabled": True}]),
        )
        client = AxonFlow.sync(**config_dict)
        try:
            providers = client.list_providers()
        finally:
            client.close()

        p = providers[0]
        assert p.endpoint is None
        assert p.model is None
        assert p.region is None
        assert p.rate_limit is None
        assert p.timeout_seconds is None
        assert p.settings is None


class TestPagination:
    def test_list_providers_paged_returns_pagination_meta(
        self, httpx_mock: HTTPXMock, config_dict: dict[str, Any]
    ) -> None:
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/llm-providers?page=2&page_size=5",
            json=_wrapped(
                [_full_provider()],
                pagination={
                    "page": 2,
                    "page_size": 5,
                    "total_items": 7,
                    "total_pages": 2,
                },
            ),
        )
        client = AxonFlow.sync(**config_dict)
        try:
            result = client.list_providers_paged(page=2, page_size=5)
        finally:
            client.close()

        assert isinstance(result, LLMProviderListResponse)
        assert isinstance(result.pagination, PaginationMeta)
        assert result.pagination.page == 2
        assert result.pagination.total_items == 7
        assert result.pagination.total_pages == 2
        assert len(result.providers) == 1

    def test_list_all_providers_walks_every_page(
        self, httpx_mock: HTTPXMock, config_dict: dict[str, Any]
    ) -> None:
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/llm-providers?page=1&page_size=2",
            json=_wrapped(
                [
                    {"name": "a", "type": "openai", "enabled": True, "has_api_key": True},
                    {"name": "b", "type": "openai", "enabled": True, "has_api_key": True},
                ],
                pagination={
                    "page": 1,
                    "page_size": 2,
                    "total_items": 3,
                    "total_pages": 2,
                },
            ),
        )
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/llm-providers?page=2&page_size=2",
            json=_wrapped(
                [{"name": "c", "type": "anthropic", "enabled": True, "has_api_key": True}],
                pagination={
                    "page": 2,
                    "page_size": 2,
                    "total_items": 3,
                    "total_pages": 2,
                },
            ),
        )
        client = AxonFlow.sync(**config_dict)
        try:
            providers = client.list_all_providers(page_size=2)
        finally:
            client.close()

        assert [p.name for p in providers] == ["a", "b", "c"]


class TestDefensiveHealthParsing:
    def test_malformed_health_does_not_crash_listing(
        self,
        httpx_mock: HTTPXMock,
        config_dict: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/llm-providers",
            json=_wrapped(
                [
                    {
                        "name": "wonky",
                        "type": "openai",
                        "enabled": True,
                        "has_api_key": True,
                        "health": {"status": 1234, "message": True},
                    },
                    _full_provider(),
                ]
            ),
        )
        client = AxonFlow.sync(**config_dict)
        try:
            with caplog.at_level(logging.WARNING):
                providers = client.list_providers()
        finally:
            client.close()

        assert len(providers) == 2
        assert providers[0].name == "wonky"
        assert providers[1].name == "anthropic"
        assert providers[1].health is not None
        assert providers[1].health.status == "healthy"


class TestHealthCheckEmptyString:
    def test_empty_string_version_does_not_trigger_upgrade_warning(
        self,
        httpx_mock: HTTPXMock,
        config_dict: dict[str, Any],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        httpx_mock.add_response(
            url="https://test.axonflow.com/health",
            json={
                "service": "axonflow-agent",
                "status": "healthy",
                "version": "7.4.4",
                "capabilities": [
                    {
                        "name": "health_check",
                        "since": "1.0.0",
                        "description": "Basic health endpoint",
                    }
                ],
                "sdk_compatibility": {
                    "min_sdk_version": {"python": ""},
                    "recommended_sdk_version": {"python": ""},
                },
            },
        )
        client = AxonFlow.sync(**config_dict)
        try:
            with caplog.at_level(logging.WARNING, logger="axonflow"):
                health = client.health_check_detailed()
        finally:
            client.close()

        warnings = [r for r in caplog.records if "below minimum" in (r.message or "")]
        assert warnings == [], f"expected no upgrade warnings, got: {[r.message for r in warnings]}"
        assert health.sdk_compatibility is not None
        assert health.sdk_compatibility.min_sdk_version_for("python") == ""
