"""Regression tests for ``client.list_providers()``.

Pins the wire-shape contract for ``GET /api/v1/llm-providers``: response
is shaped ``{"providers": [...], "pagination": {...}}`` with each
provider carrying an embedded health snapshot. Adding a regression test
because the example examples/llm-routing/e2e-tests was silently
swallowing AttributeError when the method didn't exist on the SDK.
"""

from __future__ import annotations

from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow, LLMProvider, LLMProviderHealth


def _provider_response(*, providers: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "providers": providers,
        "pagination": {"page": 1, "page_size": 20, "total": len(providers), "has_more": False},
    }


class TestListProviders:
    def test_returns_typed_providers(
        self, httpx_mock: HTTPXMock, config_dict: dict[str, Any]
    ) -> None:
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/llm-providers",
            json=_provider_response(
                providers=[
                    {
                        "name": "anthropic",
                        "type": "anthropic",
                        "enabled": True,
                        "priority": 0,
                        "weight": 0,
                        "has_api_key": True,
                        "health": {
                            "status": "healthy",
                            "message": "provider is operational",
                            "last_checked": "2026-04-28T08:45:12Z",
                        },
                    },
                    {
                        "name": "openai",
                        "type": "openai",
                        "enabled": True,
                        "priority": 1,
                        "weight": 0,
                        "has_api_key": True,
                        "health": {"status": "unhealthy", "message": "billing exceeded"},
                    },
                ]
            ),
        )
        client = AxonFlow.sync(**config_dict)
        try:
            providers = client.list_providers()
        finally:
            client.close()

        assert len(providers) == 2
        assert all(isinstance(p, LLMProvider) for p in providers)

        anthropic = providers[0]
        assert anthropic.name == "anthropic"
        assert anthropic.type == "anthropic"
        assert anthropic.has_api_key is True
        assert isinstance(anthropic.health, LLMProviderHealth)
        assert anthropic.health.status == "healthy"

        openai = providers[1]
        assert openai.health is not None
        assert openai.health.status == "unhealthy"
        assert openai.health.message == "billing exceeded"

    def test_empty_providers_list(
        self, httpx_mock: HTTPXMock, config_dict: dict[str, Any]
    ) -> None:
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/llm-providers",
            json=_provider_response(providers=[]),
        )
        client = AxonFlow.sync(**config_dict)
        try:
            providers = client.list_providers()
        finally:
            client.close()

        assert providers == []

    def test_filters_by_type_via_query_string(
        self, httpx_mock: HTTPXMock, config_dict: dict[str, Any]
    ) -> None:
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/llm-providers?type=anthropic",
            json=_provider_response(
                providers=[
                    {
                        "name": "anthropic",
                        "type": "anthropic",
                        "enabled": True,
                        "has_api_key": True,
                    }
                ]
            ),
        )
        client = AxonFlow.sync(**config_dict)
        try:
            providers = client.list_providers(provider_type="anthropic")
        finally:
            client.close()

        assert len(providers) == 1
        assert providers[0].type == "anthropic"

    def test_filters_by_enabled_false(
        self, httpx_mock: HTTPXMock, config_dict: dict[str, Any]
    ) -> None:
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/llm-providers?enabled=false",
            json=_provider_response(providers=[]),
        )
        client = AxonFlow.sync(**config_dict)
        try:
            providers = client.list_providers(enabled=False)
        finally:
            client.close()

        assert providers == []

    def test_provider_without_health_field(
        self, httpx_mock: HTTPXMock, config_dict: dict[str, Any]
    ) -> None:
        # Older platforms or never-checked providers may omit health entirely.
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/llm-providers",
            json=_provider_response(
                providers=[{"name": "ollama", "type": "ollama", "enabled": True}]
            ),
        )
        client = AxonFlow.sync(**config_dict)
        try:
            providers = client.list_providers()
        finally:
            client.close()

        assert len(providers) == 1
        assert providers[0].health is None
