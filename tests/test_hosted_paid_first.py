"""Synthetic Hosted Starter refusals for a Portal-linked Basic credential."""

from __future__ import annotations

import asyncio
import base64

import httpx
import pytest

from axonflow import AxonFlow
from axonflow.exceptions import AxonFlowError, BudgetExceededError, RateLimitError
from axonflow.types import DecideRequest


@pytest.mark.parametrize(
    ("route", "status"),
    [
        (route, status)
        for route in ("proxy", "gateway", "map", "mcp-input", "mcp-output", "decide")
        for status in (402, 429)
    ]
    + [
        (route, 402)
        for route in ("mcp-query", "decisions", "authzen", "orchestrator", "workflow-step")
    ],
)
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
            calls = {
                "proxy": lambda: client.proxy_llm_call("synthetic", "governed", "chat"),
                "gateway": lambda: client.get_policy_approved_context("synthetic", "governed"),
                "mcp-input": lambda: client.mcp_check_input("postgres", "SELECT 1"),
                "mcp-output": lambda: client.mcp_check_output(
                    "postgres", response_data=[{"value": 1}]
                ),
                "mcp-query": lambda: client.mcp_query("postgres", "SELECT 1"),
                "decide": lambda: client.decide(DecideRequest(stage="tool", query="synthetic")),
                "decisions": client.list_decisions,
                "authzen": lambda: client._send_authzen("/api/v1/access/evaluation", {}, {}),
                "orchestrator": lambda: client._orchestrator_request("GET", "/api/v1/plans"),
                "workflow-step": lambda: client.mark_step_completed(
                    "wf_synthetic", "step_synthetic"
                ),
                "map": lambda: client._map_request("POST", "/api/v1/plan/synthetic/run"),
            }
            with pytest.raises(AxonFlowError) as caught:
                await calls[route]()
        return caught.value

    error = asyncio.run(exercise())
    expected_path = {
        "proxy": "/api/request",
        "gateway": "/api/policy/pre-check",
        "map": "/api/v1/plan/synthetic/run",
        "mcp-input": "/api/v1/mcp/check-input",
        "mcp-output": "/api/v1/mcp/check-output",
        "mcp-query": "/mcp/resources/query",
        "decide": "/api/v1/decide",
        "decisions": "/api/v1/decisions",
        "authzen": "/api/v1/access/evaluation",
        "orchestrator": "/api/v1/plans",
        "workflow-step": "/api/v1/workflows/wf_synthetic/steps/step_synthetic/complete",
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
