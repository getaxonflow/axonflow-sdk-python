"""Unit tests for Cost Controls API methods."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow
from axonflow.types import (
    BudgetCheckRequest,
    BudgetOnExceed,
    BudgetPeriod,
    BudgetScope,
    CreateBudgetRequest,
    ListBudgetsOptions,
    ListUsageRecordsOptions,
    UpdateBudgetRequest,
)


class TestBudgetManagement:
    """Test budget management methods."""

    @pytest.mark.asyncio
    async def test_create_budget(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test creating a budget."""
        httpx_mock.add_response(
            json={
                "id": "budget-123",
                "name": "Monthly Budget",
                "scope": "organization",
                "organization_id": "org-1",
                "limit_usd": 100.0,
                "period": "monthly",
                "on_exceed": "warn",
                "alert_thresholds": [50, 80, 100],
                "enabled": True,
                "created_at": "2025-12-01T00:00:00Z",
                "updated_at": "2025-12-01T00:00:00Z",
            }
        )

        request = CreateBudgetRequest(
            id="budget-123",
            name="Monthly Budget",
            scope=BudgetScope.ORGANIZATION,
            organization_id="org-1",
            limit_usd=100.0,
            period=BudgetPeriod.MONTHLY,
            on_exceed=BudgetOnExceed.WARN,
            alert_thresholds=[50, 80, 100],
        )

        result = await client.create_budget(request)
        assert result.id == "budget-123"
        assert result.name == "Monthly Budget"
        assert result.limit_usd == 100.0

    @pytest.mark.asyncio
    async def test_get_budget(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting a budget by ID."""
        httpx_mock.add_response(
            json={
                "id": "budget-123",
                "name": "Test Budget",
                "scope": "team",
                "team_id": "team-1",
                "limit_usd": 50.0,
                "period": "weekly",
                "on_exceed": "block",
                "alert_thresholds": [80, 100],
                "enabled": True,
                "created_at": "2025-12-01T00:00:00Z",
                "updated_at": "2025-12-01T00:00:00Z",
            }
        )

        result = await client.get_budget("budget-123")
        assert result.id == "budget-123"
        assert result.scope == BudgetScope.TEAM

    @pytest.mark.asyncio
    async def test_list_budgets(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test listing budgets."""
        httpx_mock.add_response(
            json={
                "budgets": [
                    {
                        "id": "budget-1",
                        "name": "Budget 1",
                        "scope": "organization",
                        "limit_usd": 100.0,
                        "period": "monthly",
                        "on_exceed": "warn",
                        "enabled": True,
                        "created_at": "2025-12-01T00:00:00Z",
                        "updated_at": "2025-12-01T00:00:00Z",
                    },
                    {
                        "id": "budget-2",
                        "name": "Budget 2",
                        "scope": "team",
                        "limit_usd": 50.0,
                        "period": "weekly",
                        "on_exceed": "block",
                        "enabled": True,
                        "created_at": "2025-12-01T00:00:00Z",
                        "updated_at": "2025-12-01T00:00:00Z",
                    },
                ],
                "total": 2,
            }
        )

        result = await client.list_budgets()
        assert len(result.budgets) == 2
        assert result.total == 2

    @pytest.mark.asyncio
    async def test_list_budgets_with_options(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test listing budgets with filtering options."""
        httpx_mock.add_response(
            json={
                "budgets": [
                    {
                        "id": "budget-1",
                        "name": "Team Budget",
                        "scope": "team",
                        "limit_usd": 50.0,
                        "period": "monthly",
                        "on_exceed": "warn",
                        "enabled": True,
                        "created_at": "2025-12-01T00:00:00Z",
                        "updated_at": "2025-12-01T00:00:00Z",
                    },
                ],
                "total": 1,
            }
        )

        options = ListBudgetsOptions(scope=BudgetScope.TEAM, limit=10, offset=0)
        result = await client.list_budgets(options)
        assert len(result.budgets) == 1

    @pytest.mark.asyncio
    async def test_update_budget(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test updating a budget."""
        httpx_mock.add_response(
            json={
                "id": "budget-123",
                "name": "Updated Budget",
                "scope": "organization",
                "limit_usd": 200.0,
                "period": "monthly",
                "on_exceed": "block",
                "enabled": True,
                "created_at": "2025-12-01T00:00:00Z",
                "updated_at": "2025-12-02T00:00:00Z",
            }
        )

        request = UpdateBudgetRequest(
            name="Updated Budget",
            limit_usd=200.0,
            on_exceed=BudgetOnExceed.BLOCK,
        )

        result = await client.update_budget("budget-123", request)
        assert result.name == "Updated Budget"
        assert result.limit_usd == 200.0

    @pytest.mark.asyncio
    async def test_delete_budget(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test deleting a budget."""
        httpx_mock.add_response(status_code=204)

        await client.delete_budget("budget-123")
        # No exception = success


class TestBudgetStatusAndAlerts:
    """Test budget status and alerts methods."""

    @pytest.mark.asyncio
    async def test_get_budget_status(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting budget status."""
        httpx_mock.add_response(
            json={
                "budget": {
                    "id": "budget-123",
                    "name": "Test Budget",
                    "scope": "organization",
                    "limit_usd": 100.0,
                    "period": "monthly",
                    "on_exceed": "warn",
                    "enabled": True,
                    "created_at": "2025-12-01T00:00:00Z",
                    "updated_at": "2025-12-01T00:00:00Z",
                },
                "used_usd": 45.50,
                "remaining_usd": 54.50,
                "percentage": 45.5,
                "is_exceeded": False,
                "is_blocked": False,
                "period_start": "2025-12-01T00:00:00Z",
                "period_end": "2025-12-31T23:59:59Z",
            }
        )

        result = await client.get_budget_status("budget-123")
        assert result.budget.id == "budget-123"
        assert result.used_usd == 45.50
        assert result.remaining_usd == 54.50

    @pytest.mark.asyncio
    async def test_get_budget_alerts(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting budget alerts."""
        httpx_mock.add_response(
            json={
                "alerts": [
                    {
                        "id": "alert-1",
                        "budget_id": "budget-123",
                        "alert_type": "threshold",
                        "threshold": 50,
                        "percentage_reached": 51.2,
                        "amount_usd": 51.20,
                        "message": "Budget threshold 50% reached",
                        "created_at": "2025-12-15T10:00:00Z",
                    },
                ],
                "count": 1,
            }
        )

        result = await client.get_budget_alerts("budget-123")
        assert len(result.alerts) == 1
        assert result.alerts[0].threshold == 50

    @pytest.mark.asyncio
    async def test_check_budget(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test pre-flight budget check."""
        httpx_mock.add_response(
            json={
                "allowed": True,
                "action": "allow",
                "message": "Within budget",
            }
        )

        request = BudgetCheckRequest(
            org_id="org-1",
            team_id="team-1",
        )

        result = await client.check_budget(request)
        assert result.allowed is True


class TestUsageTracking:
    """Test usage tracking methods."""

    @pytest.mark.asyncio
    async def test_get_usage_summary(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting usage summary."""
        httpx_mock.add_response(
            json={
                "total_cost_usd": 150.75,
                "total_requests": 5000,
                "total_tokens_in": 1000000,
                "total_tokens_out": 500000,
                "average_cost_per_request": 0.03,
                "period": "monthly",
                "period_start": "2025-12-01T00:00:00Z",
                "period_end": "2025-12-31T23:59:59Z",
            }
        )

        result = await client.get_usage_summary()
        assert result.total_cost_usd == 150.75
        assert result.total_requests == 5000

    @pytest.mark.asyncio
    async def test_get_usage_summary_with_period(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting usage summary for specific period."""
        httpx_mock.add_response(
            json={
                "total_cost_usd": 35.25,
                "total_requests": 1200,
                "total_tokens_in": 240000,
                "total_tokens_out": 120000,
                "average_cost_per_request": 0.029,
                "period": "weekly",
                "period_start": "2025-12-09T00:00:00Z",
                "period_end": "2025-12-15T23:59:59Z",
            }
        )

        result = await client.get_usage_summary(period="weekly")
        assert result.period == "weekly"
        assert result.total_cost_usd == 35.25

    @pytest.mark.asyncio
    async def test_get_usage_breakdown(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting usage breakdown."""
        httpx_mock.add_response(
            json={
                "group_by": "provider",
                "total_cost_usd": 150.75,
                "items": [
                    {
                        "group_value": "openai",
                        "cost_usd": 100.0,
                        "percentage": 66.7,
                        "request_count": 3000,
                        "tokens_in": 600000,
                        "tokens_out": 300000,
                    },
                    {
                        "group_value": "anthropic",
                        "cost_usd": 50.75,
                        "percentage": 33.3,
                        "request_count": 2000,
                        "tokens_in": 400000,
                        "tokens_out": 200000,
                    },
                ],
                "period": "monthly",
            }
        )

        result = await client.get_usage_breakdown("provider")
        assert result.group_by == "provider"
        assert len(result.items) == 2

    @pytest.mark.asyncio
    async def test_get_usage_breakdown_with_period(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting usage breakdown with period."""
        httpx_mock.add_response(
            json={
                "group_by": "model",
                "total_cost_usd": 80.0,
                "items": [
                    {
                        "group_value": "gpt-4",
                        "cost_usd": 80.0,
                        "percentage": 100.0,
                        "request_count": 1000,
                        "tokens_in": 200000,
                        "tokens_out": 100000,
                    },
                ],
                "period": "daily",
            }
        )

        result = await client.get_usage_breakdown("model", period="daily")
        assert result.period == "daily"

    @pytest.mark.asyncio
    async def test_list_usage_records(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test listing usage records."""
        httpx_mock.add_response(
            json={
                "records": [
                    {
                        "id": "record-1",
                        "provider": "openai",
                        "model": "gpt-4",
                        "tokens_in": 100,
                        "tokens_out": 50,
                        "cost_usd": 0.0045,
                        "timestamp": "2025-12-15T10:00:00Z",
                    },
                ],
                "total": 1,
            }
        )

        result = await client.list_usage_records()
        assert len(result.records) == 1

    @pytest.mark.asyncio
    async def test_list_usage_records_with_options(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test listing usage records with filtering."""
        httpx_mock.add_response(
            json={
                "records": [
                    {
                        "id": "record-1",
                        "provider": "anthropic",
                        "model": "claude-3",
                        "tokens_in": 200,
                        "tokens_out": 100,
                        "cost_usd": 0.009,
                        "timestamp": "2025-12-15T10:00:00Z",
                    },
                ],
                "total": 1,
            }
        )

        options = ListUsageRecordsOptions(
            provider="anthropic",
            model="claude-3",
            limit=10,
            offset=0,
        )
        result = await client.list_usage_records(options)
        assert len(result.records) == 1
        assert result.records[0].provider == "anthropic"


class TestPricing:
    """Test pricing information methods."""

    @pytest.mark.asyncio
    async def test_get_pricing(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting pricing information."""
        httpx_mock.add_response(
            json={
                "pricing": [
                    {
                        "provider": "openai",
                        "model": "gpt-4",
                        "pricing": {
                            "input_per_1k": 0.03,
                            "output_per_1k": 0.06,
                        },
                    },
                    {
                        "provider": "anthropic",
                        "model": "claude-3-sonnet",
                        "pricing": {
                            "input_per_1k": 0.003,
                            "output_per_1k": 0.015,
                        },
                    },
                ],
            }
        )

        result = await client.get_pricing()
        assert len(result.pricing) == 2

    @pytest.mark.asyncio
    async def test_get_pricing_with_provider_filter(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting pricing filtered by provider."""
        httpx_mock.add_response(
            json={
                "pricing": [
                    {
                        "provider": "openai",
                        "model": "gpt-4",
                        "pricing": {
                            "input_per_1k": 0.03,
                            "output_per_1k": 0.06,
                        },
                    },
                ],
            }
        )

        result = await client.get_pricing(provider="openai")
        assert len(result.pricing) == 1
        assert result.pricing[0].provider == "openai"

    @pytest.mark.asyncio
    async def test_get_pricing_single_model(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting pricing for a single model returns wrapped response."""
        httpx_mock.add_response(
            json={
                "provider": "openai",
                "model": "gpt-4",
                "pricing": {
                    "input_per_1k": 0.03,
                    "output_per_1k": 0.06,
                },
            }
        )

        result = await client.get_pricing(provider="openai", model="gpt-4")
        assert len(result.pricing) == 1
        assert result.pricing[0].model == "gpt-4"
