"""Unit tests for WCP Approval, Plan Rollback, and Webhook CRUD methods."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow, SyncAxonFlow
from axonflow.types import (
    ListWebhooksResponse,
    RollbackPlanResponse,
    WebhookSubscription,
)
from axonflow.workflow import (
    ApproveStepResponse,
    PendingApproval,
    PendingApprovalsResponse,
    RejectStepResponse,
)

# =========================================================================
# WCP Approval Tests (Feature 5)
# =========================================================================


class TestApproveStep:
    """Test approve_step method."""

    @pytest.mark.asyncio
    async def test_approve_step(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test approving a workflow step."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflow-control/wf-123/steps/step-1/approve",
            json={
                "workflow_id": "wf-123",
                "step_id": "step-1",
                "status": "approved",
            },
        )

        result = await client.approve_step("wf-123", "step-1")
        assert isinstance(result, ApproveStepResponse)
        assert result.workflow_id == "wf-123"
        assert result.step_id == "step-1"
        assert result.status == "approved"

    @pytest.mark.asyncio
    async def test_approve_step_defaults_ids_from_args(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test that workflow_id and step_id default from args if not in response."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflow-control/wf-456/steps/step-2/approve",
            json={
                "status": "approved",
            },
        )

        result = await client.approve_step("wf-456", "step-2")
        assert result.workflow_id == "wf-456"
        assert result.step_id == "step-2"
        assert result.status == "approved"


class TestRejectStep:
    """Test reject_step method."""

    @pytest.mark.asyncio
    async def test_reject_step_with_reason(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test rejecting a workflow step with a reason."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflow-control/wf-123/steps/step-1/reject",
            json={
                "workflow_id": "wf-123",
                "step_id": "step-1",
                "status": "rejected",
            },
        )

        result = await client.reject_step("wf-123", "step-1", reason="Unsafe operation")
        assert isinstance(result, RejectStepResponse)
        assert result.workflow_id == "wf-123"
        assert result.step_id == "step-1"
        assert result.status == "rejected"

    @pytest.mark.asyncio
    async def test_reject_step_without_reason(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test rejecting a workflow step without a reason."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflow-control/wf-123/steps/step-1/reject",
            json={
                "workflow_id": "wf-123",
                "step_id": "step-1",
                "status": "rejected",
            },
        )

        result = await client.reject_step("wf-123", "step-1")
        assert result.status == "rejected"

    @pytest.mark.asyncio
    async def test_reject_step_defaults_ids_from_args(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test that workflow_id and step_id default from args if not in response."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflow-control/wf-789/steps/step-3/reject",
            json={
                "status": "rejected",
            },
        )

        result = await client.reject_step("wf-789", "step-3")
        assert result.workflow_id == "wf-789"
        assert result.step_id == "step-3"


class TestGetPendingApprovals:
    """Test get_pending_approvals method."""

    @pytest.mark.asyncio
    async def test_get_pending_approvals(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting pending approvals."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflow-control/pending-approvals?limit=20",
            json={
                "approvals": [
                    {
                        "workflow_id": "wf-123",
                        "workflow_name": "customer-support",
                        "step_id": "step-1",
                        "step_name": "Generate Response",
                        "step_type": "llm_call",
                        "created_at": "2026-02-07T10:00:00Z",
                    },
                    {
                        "workflow_id": "wf-456",
                        "workflow_name": "data-pipeline",
                        "step_id": "step-3",
                        "step_name": "Delete Records",
                        "step_type": "tool_call",
                        "created_at": "2026-02-07T11:00:00Z",
                    },
                ],
                "total": 2,
            },
        )

        result = await client.get_pending_approvals()
        assert isinstance(result, PendingApprovalsResponse)
        assert result.total == 2
        assert len(result.approvals) == 2
        assert result.approvals[0].workflow_id == "wf-123"
        assert result.approvals[0].workflow_name == "customer-support"
        assert result.approvals[0].step_name == "Generate Response"
        assert result.approvals[1].step_type == "tool_call"

    @pytest.mark.asyncio
    async def test_get_pending_approvals_with_limit(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting pending approvals with custom limit."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflow-control/pending-approvals?limit=5",
            json={
                "approvals": [],
                "total": 0,
            },
        )

        result = await client.get_pending_approvals(limit=5)
        assert result.total == 0
        assert result.approvals == []

    @pytest.mark.asyncio
    async def test_get_pending_approvals_empty(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting pending approvals when none exist."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflow-control/pending-approvals?limit=20",
            json={
                "approvals": [],
                "total": 0,
            },
        )

        result = await client.get_pending_approvals()
        assert result.total == 0
        assert result.approvals == []


# =========================================================================
# Plan Rollback Tests (Feature 7)
# =========================================================================


class TestRollbackPlan:
    """Test rollback_plan method."""

    @pytest.mark.asyncio
    async def test_rollback_plan(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test rolling back a plan to a previous version."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/plan/plan-123/rollback/2",
            json={
                "plan_id": "plan-123",
                "version": 2,
                "previous_version": 5,
                "status": "rolled_back",
            },
        )

        result = await client.rollback_plan("plan-123", target_version=2)
        assert isinstance(result, RollbackPlanResponse)
        assert result.plan_id == "plan-123"
        assert result.version == 2
        assert result.previous_version == 5
        assert result.status == "rolled_back"

    @pytest.mark.asyncio
    async def test_rollback_plan_to_version_1(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test rolling back a plan to version 1."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/plan/plan-456/rollback/1",
            json={
                "plan_id": "plan-456",
                "version": 1,
                "previous_version": 3,
                "status": "rolled_back",
            },
        )

        result = await client.rollback_plan("plan-456", target_version=1)
        assert result.plan_id == "plan-456"
        assert result.version == 1
        assert result.previous_version == 3


# =========================================================================
# Webhook CRUD Tests (Feature 7)
# =========================================================================


class TestCreateWebhook:
    """Test create_webhook method."""

    @pytest.mark.asyncio
    async def test_create_webhook(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test creating a webhook subscription."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/webhooks",
            json={
                "id": "wh-123",
                "url": "https://example.com/webhooks",
                "events": ["workflow.completed", "step.approval_required"],
                "active": True,
                "created_at": "2026-02-07T10:00:00Z",
                "updated_at": "2026-02-07T10:00:00Z",
            },
        )

        result = await client.create_webhook(
            url="https://example.com/webhooks",
            events=["workflow.completed", "step.approval_required"],
            secret="my-secret",
        )
        assert isinstance(result, WebhookSubscription)
        assert result.id == "wh-123"
        assert result.url == "https://example.com/webhooks"
        assert len(result.events) == 2
        assert result.active is True

    @pytest.mark.asyncio
    async def test_create_webhook_without_secret(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test creating a webhook without a secret."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/webhooks",
            json={
                "id": "wh-456",
                "url": "https://example.com/hooks",
                "events": ["workflow.failed"],
                "active": True,
                "created_at": "2026-02-07T10:00:00Z",
                "updated_at": "2026-02-07T10:00:00Z",
            },
        )

        result = await client.create_webhook(
            url="https://example.com/hooks",
            events=["workflow.failed"],
        )
        assert result.id == "wh-456"
        assert result.active is True

    @pytest.mark.asyncio
    async def test_create_webhook_inactive(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test creating an inactive webhook."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/webhooks",
            json={
                "id": "wh-789",
                "url": "https://example.com/hooks",
                "events": ["step.completed"],
                "active": False,
                "created_at": "2026-02-07T10:00:00Z",
                "updated_at": "2026-02-07T10:00:00Z",
            },
        )

        result = await client.create_webhook(
            url="https://example.com/hooks",
            events=["step.completed"],
            active=False,
        )
        assert result.active is False


class TestGetWebhook:
    """Test get_webhook method."""

    @pytest.mark.asyncio
    async def test_get_webhook(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting a webhook by ID."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/webhooks/wh-123",
            json={
                "id": "wh-123",
                "url": "https://example.com/webhooks",
                "events": ["workflow.completed"],
                "active": True,
                "created_at": "2026-02-07T10:00:00Z",
                "updated_at": "2026-02-07T10:00:00Z",
            },
        )

        result = await client.get_webhook("wh-123")
        assert isinstance(result, WebhookSubscription)
        assert result.id == "wh-123"
        assert result.url == "https://example.com/webhooks"


class TestUpdateWebhook:
    """Test update_webhook method."""

    @pytest.mark.asyncio
    async def test_update_webhook_deactivate(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test deactivating a webhook."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/webhooks/wh-123",
            json={
                "id": "wh-123",
                "url": "https://example.com/webhooks",
                "events": ["workflow.completed"],
                "active": False,
                "created_at": "2026-02-07T10:00:00Z",
                "updated_at": "2026-02-07T12:00:00Z",
            },
        )

        result = await client.update_webhook("wh-123", active=False)
        assert isinstance(result, WebhookSubscription)
        assert result.active is False

    @pytest.mark.asyncio
    async def test_update_webhook_url_and_events(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test updating webhook URL and events."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/webhooks/wh-123",
            json={
                "id": "wh-123",
                "url": "https://new-url.com/hooks",
                "events": ["workflow.completed", "workflow.failed"],
                "active": True,
                "created_at": "2026-02-07T10:00:00Z",
                "updated_at": "2026-02-07T12:00:00Z",
            },
        )

        result = await client.update_webhook(
            "wh-123",
            url="https://new-url.com/hooks",
            events=["workflow.completed", "workflow.failed"],
        )
        assert result.url == "https://new-url.com/hooks"
        assert len(result.events) == 2


class TestDeleteWebhook:
    """Test delete_webhook method."""

    @pytest.mark.asyncio
    async def test_delete_webhook(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test deleting a webhook."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/webhooks/wh-123",
            status_code=204,
        )

        # Should not raise
        await client.delete_webhook("wh-123")


class TestListWebhooks:
    """Test list_webhooks method."""

    @pytest.mark.asyncio
    async def test_list_webhooks(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test listing webhooks."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/webhooks",
            json={
                "webhooks": [
                    {
                        "id": "wh-123",
                        "url": "https://example.com/hooks",
                        "events": ["workflow.completed"],
                        "active": True,
                        "created_at": "2026-02-07T10:00:00Z",
                        "updated_at": "2026-02-07T10:00:00Z",
                    },
                    {
                        "id": "wh-456",
                        "url": "https://other.com/hooks",
                        "events": ["step.approval_required", "workflow.failed"],
                        "active": False,
                        "created_at": "2026-02-06T10:00:00Z",
                        "updated_at": "2026-02-07T08:00:00Z",
                    },
                ],
                "total": 2,
            },
        )

        result = await client.list_webhooks()
        assert isinstance(result, ListWebhooksResponse)
        assert result.total == 2
        assert len(result.webhooks) == 2
        assert result.webhooks[0].id == "wh-123"
        assert result.webhooks[0].active is True
        assert result.webhooks[1].id == "wh-456"
        assert result.webhooks[1].active is False

    @pytest.mark.asyncio
    async def test_list_webhooks_empty(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test listing webhooks when none exist."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/webhooks",
            json={
                "webhooks": [],
                "total": 0,
            },
        )

        result = await client.list_webhooks()
        assert result.total == 0
        assert result.webhooks == []


# =========================================================================
# Sync Wrapper Tests
# =========================================================================


class TestSyncWrappers:
    """Test sync wrappers for all new methods."""

    def test_sync_approve_step(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync approve_step wrapper."""
        httpx_mock.add_response(
            json={
                "workflow_id": "wf-123",
                "step_id": "step-1",
                "status": "approved",
            },
        )

        result = sync_client.approve_step("wf-123", "step-1")
        assert isinstance(result, ApproveStepResponse)
        assert result.status == "approved"

    def test_sync_reject_step(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync reject_step wrapper."""
        httpx_mock.add_response(
            json={
                "workflow_id": "wf-123",
                "step_id": "step-1",
                "status": "rejected",
            },
        )

        result = sync_client.reject_step("wf-123", "step-1", reason="Not safe")
        assert isinstance(result, RejectStepResponse)
        assert result.status == "rejected"

    def test_sync_get_pending_approvals(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync get_pending_approvals wrapper."""
        httpx_mock.add_response(
            json={
                "approvals": [
                    {
                        "workflow_id": "wf-123",
                        "workflow_name": "test-wf",
                        "step_id": "step-1",
                        "step_name": "Test Step",
                        "step_type": "llm_call",
                        "created_at": "2026-02-07T10:00:00Z",
                    },
                ],
                "total": 1,
            },
        )

        result = sync_client.get_pending_approvals(limit=10)
        assert isinstance(result, PendingApprovalsResponse)
        assert result.total == 1

    def test_sync_rollback_plan(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync rollback_plan wrapper."""
        httpx_mock.add_response(
            json={
                "plan_id": "plan-123",
                "version": 2,
                "previous_version": 4,
                "status": "rolled_back",
            },
        )

        result = sync_client.rollback_plan("plan-123", target_version=2)
        assert isinstance(result, RollbackPlanResponse)
        assert result.version == 2

    def test_sync_create_webhook(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync create_webhook wrapper."""
        httpx_mock.add_response(
            json={
                "id": "wh-123",
                "url": "https://example.com/hooks",
                "events": ["workflow.completed"],
                "active": True,
                "created_at": "2026-02-07T10:00:00Z",
                "updated_at": "2026-02-07T10:00:00Z",
            },
        )

        result = sync_client.create_webhook(
            url="https://example.com/hooks",
            events=["workflow.completed"],
        )
        assert isinstance(result, WebhookSubscription)
        assert result.id == "wh-123"

    def test_sync_get_webhook(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync get_webhook wrapper."""
        httpx_mock.add_response(
            json={
                "id": "wh-123",
                "url": "https://example.com/hooks",
                "events": ["workflow.completed"],
                "active": True,
                "created_at": "2026-02-07T10:00:00Z",
                "updated_at": "2026-02-07T10:00:00Z",
            },
        )

        result = sync_client.get_webhook("wh-123")
        assert isinstance(result, WebhookSubscription)

    def test_sync_update_webhook(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync update_webhook wrapper."""
        httpx_mock.add_response(
            json={
                "id": "wh-123",
                "url": "https://example.com/hooks",
                "events": ["workflow.completed"],
                "active": False,
                "created_at": "2026-02-07T10:00:00Z",
                "updated_at": "2026-02-07T12:00:00Z",
            },
        )

        result = sync_client.update_webhook("wh-123", active=False)
        assert isinstance(result, WebhookSubscription)
        assert result.active is False

    def test_sync_delete_webhook(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync delete_webhook wrapper."""
        httpx_mock.add_response(status_code=204)

        # Should not raise
        sync_client.delete_webhook("wh-123")

    def test_sync_list_webhooks(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync list_webhooks wrapper."""
        httpx_mock.add_response(
            json={
                "webhooks": [
                    {
                        "id": "wh-123",
                        "url": "https://example.com/hooks",
                        "events": ["workflow.completed"],
                        "active": True,
                        "created_at": "2026-02-07T10:00:00Z",
                        "updated_at": "2026-02-07T10:00:00Z",
                    },
                ],
                "total": 1,
            },
        )

        result = sync_client.list_webhooks()
        assert isinstance(result, ListWebhooksResponse)
        assert result.total == 1


# =========================================================================
# Type Model Tests
# =========================================================================


class TestTypeModels:
    """Test that all new types can be instantiated and validated."""

    def test_approve_step_response(self) -> None:
        """Test ApproveStepResponse model."""
        resp = ApproveStepResponse(
            workflow_id="wf-1", step_id="step-1", status="approved"
        )
        assert resp.workflow_id == "wf-1"
        assert resp.step_id == "step-1"
        assert resp.status == "approved"

    def test_reject_step_response(self) -> None:
        """Test RejectStepResponse model."""
        resp = RejectStepResponse(
            workflow_id="wf-1", step_id="step-1", status="rejected"
        )
        assert resp.workflow_id == "wf-1"
        assert resp.status == "rejected"

    def test_pending_approval(self) -> None:
        """Test PendingApproval model."""
        approval = PendingApproval(
            workflow_id="wf-1",
            workflow_name="test-wf",
            step_id="step-1",
            step_name="Test",
            step_type="llm_call",
            created_at="2026-02-07T10:00:00Z",
        )
        assert approval.workflow_id == "wf-1"
        assert approval.step_type == "llm_call"

    def test_pending_approvals_response_defaults(self) -> None:
        """Test PendingApprovalsResponse with defaults."""
        resp = PendingApprovalsResponse()
        assert resp.approvals == []
        assert resp.total == 0

    def test_rollback_plan_response(self) -> None:
        """Test RollbackPlanResponse model."""
        resp = RollbackPlanResponse(
            plan_id="plan-1", version=2, previous_version=5, status="rolled_back"
        )
        assert resp.plan_id == "plan-1"
        assert resp.version == 2
        assert resp.previous_version == 5
        assert resp.status == "rolled_back"

    def test_webhook_subscription(self) -> None:
        """Test WebhookSubscription model."""
        wh = WebhookSubscription(
            id="wh-1",
            url="https://example.com",
            events=["workflow.completed"],
            active=True,
            created_at="2026-02-07T10:00:00Z",
            updated_at="2026-02-07T10:00:00Z",
        )
        assert wh.id == "wh-1"
        assert wh.url == "https://example.com"
        assert wh.events == ["workflow.completed"]
        assert wh.active is True

    def test_webhook_subscription_defaults(self) -> None:
        """Test WebhookSubscription with defaults."""
        wh = WebhookSubscription(
            id="wh-1",
            url="https://example.com",
            created_at="2026-02-07T10:00:00Z",
            updated_at="2026-02-07T10:00:00Z",
        )
        assert wh.events == []
        assert wh.active is True

    def test_list_webhooks_response_defaults(self) -> None:
        """Test ListWebhooksResponse with defaults."""
        resp = ListWebhooksResponse()
        assert resp.webhooks == []
        assert resp.total == 0

    def test_list_webhooks_response_with_data(self) -> None:
        """Test ListWebhooksResponse with data."""
        wh = WebhookSubscription(
            id="wh-1",
            url="https://example.com",
            events=["workflow.completed"],
            active=True,
            created_at="2026-02-07T10:00:00Z",
            updated_at="2026-02-07T10:00:00Z",
        )
        resp = ListWebhooksResponse(webhooks=[wh], total=1)
        assert len(resp.webhooks) == 1
        assert resp.total == 1
