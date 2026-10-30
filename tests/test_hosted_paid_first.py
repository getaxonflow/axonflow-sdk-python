"""Synthetic Hosted Starter refusals for a Portal-linked Basic credential."""

from __future__ import annotations

import asyncio
import base64

import httpx
import pytest

from axonflow import AxonFlow
from axonflow.exceptions import AxonFlowError, BudgetExceededError, RateLimitError


@pytest.mark.parametrize("route", ["proxy", "gateway", "map"])
@pytest.mark.parametrize("status", [402, 429])
def test_linked_credential_preserves_hosted_refusal_without_fallback(
    monkeypatch: pytest.MonkeyPatch, route: str, status: int
) -> None:
    monkeypatch.setenv("AXONFLOW_TELEMETRY", "off")
    monkeypatch.delenv("AXONFLOW_TRY", raising=False)
    tenant = "cs_synthetic_linked"
    secret = "synthetic-device-secret"
    expected_auth = "Basic " + base64.b64encode(f"{tenant}:{secret}".encode()).decode()
    requests: list[str] = []
    payload = (
        {
            "error": "hosted_payment_required",
            "message": "Hosted governance requires an active paid plan.",
            "checkout_url": "https://agent.example.test/api/v1/billing/checkout",
            "checkout_method": "POST",
            "tenant_id": tenant,
        }
        if status == 402
        else {
            "error": "hosted_monthly_limit_reached",
            "message": "Monthly hosted governance allowance reached.",
            "limit_type": "monthly_governed_events",
            "higher_usage_path": "paid_enterprise_poc",
        }
    )

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == expected_auth
        assert request.headers["X-Client-ID"] == tenant
        assert "X-Tenant-ID" not in request.headers
        requests.append(request.url.path)
        return httpx.Response(status, json=payload)

    async def exercise() -> AxonFlowError:
        client = AxonFlow(
            endpoint="https://agent.example.test",
            client_id=tenant,
            client_secret=secret,
            cache_enabled=False,
        )
        if route == "map":
            client._map_http_client._transport = httpx.MockTransport(respond)
        else:
            client._http_client._transport = httpx.MockTransport(respond)
        async with client:
            with pytest.raises(AxonFlowError) as caught:
                if route == "proxy":
                    await client.proxy_llm_call("synthetic", "governed", "chat")
                elif route == "gateway":
                    await client.get_policy_approved_context("synthetic", "governed")
                else:
                    await client._map_request("POST", "/api/v1/plan/synthetic/run")
        return caught.value

    error = asyncio.run(exercise())
    expected_path = {
        "proxy": "/api/request",
        "gateway": "/api/policy/pre-check",
        "map": "/api/v1/plan/synthetic/run",
    }[route]
    assert requests == [expected_path]
    assert isinstance(error.__cause__, httpx.HTTPStatusError)
    assert error.__cause__.response.status_code == status
    assert error.details.items() >= payload.items()
    if status == 402:
        assert isinstance(error, BudgetExceededError)
    else:
        assert isinstance(error, RateLimitError)
        assert error.limit_type == "monthly_governed_events"
        assert error.limit == 0
        assert error.remaining == 0


def test_existing_budget_402_keeps_budget_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AXONFLOW_TELEMETRY", "off")
    monkeypatch.delenv("AXONFLOW_TRY", raising=False)

    async def exercise() -> BudgetExceededError:
        client = AxonFlow(endpoint="https://agent.example.test", cache_enabled=False)
        client._http_client._transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                402,
                json={
                    "message": "Budget exceeded",
                    "budget_info": {
                        "budget_id": "budget_synthetic",
                        "used_usd": 2.0,
                        "limit_usd": 1.0,
                    },
                },
            )
        )
        async with client:
            with pytest.raises(BudgetExceededError) as caught:
                await client.proxy_llm_call("synthetic", "governed", "chat")
        return caught.value

    error = asyncio.run(exercise())
    assert error.budget_id == "budget_synthetic"
    assert error.used_usd == 2.0
    assert error.limit_usd == 1.0
