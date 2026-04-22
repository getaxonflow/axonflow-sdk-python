"""Unit tests for WCP Approval, Plan Rollback, Webhook CRUD, WCP Workflow Lifecycle,
and Unified Execution Tracking methods."""

from __future__ import annotations

from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow, SyncAxonFlow
from axonflow.execution import (
    ExecutionStatus,
    ExecutionStatusValue,
    ExecutionType,
    UnifiedListExecutionsRequest,
    UnifiedListExecutionsResponse,
)
from axonflow.types import (
    ListWebhooksResponse,
    RollbackPlanResponse,
    WebhookSubscription,
)
from axonflow.workflow import (
    ApproveStepResponse,
    CreateWorkflowRequest,
    CreateWorkflowResponse,
    GateDecision,
    ListWorkflowsOptions,
    ListWorkflowsResponse,
    MarkStepCompletedRequest,
    PendingApproval,
    PendingApprovalsResponse,
    RejectStepResponse,
    StepGateRequest,
    StepGateResponse,
    StepType,
    WorkflowSource,
    WorkflowStatus,
    WorkflowStatusResponse,
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
            url="https://test.axonflow.com/api/v1/workflows/wf-123/steps/step-1/approve",
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
            url="https://test.axonflow.com/api/v1/workflows/wf-456/steps/step-2/approve",
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
            url="https://test.axonflow.com/api/v1/workflows/wf-123/steps/step-1/reject",
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
            url="https://test.axonflow.com/api/v1/workflows/wf-123/steps/step-1/reject",
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
            url="https://test.axonflow.com/api/v1/workflows/wf-789/steps/step-3/reject",
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
            url="https://test.axonflow.com/api/v1/workflows/approvals/pending?limit=20",
            json={
                "pending_approvals": [
                    {
                        "workflow_id": "wf-123",
                        "workflow_name": "customer-support",
                        "step_id": "step-1",
                        "step_index": 0,
                        "step_name": "Generate Response",
                        "step_type": "llm_call",
                        "decision": "require_approval",
                        "created_at": "2026-02-07T10:00:00Z",
                    },
                    {
                        "workflow_id": "wf-456",
                        "workflow_name": "data-pipeline",
                        "step_id": "step-3",
                        "step_index": 2,
                        "step_name": "Delete Records",
                        "step_type": "tool_call",
                        "decision": "require_approval",
                        "created_at": "2026-02-07T11:00:00Z",
                    },
                ],
                "count": 2,
            },
        )

        result = await client.get_pending_approvals()
        assert isinstance(result, PendingApprovalsResponse)
        assert result.count == 2
        assert len(result.pending_approvals) == 2
        assert result.pending_approvals[0].workflow_id == "wf-123"
        assert result.pending_approvals[0].workflow_name == "customer-support"
        assert result.pending_approvals[0].step_name == "Generate Response"
        assert result.pending_approvals[1].step_type == "tool_call"
        # WCP entries must not carry plan_id
        assert result.pending_approvals[0].plan_id is None

    @pytest.mark.asyncio
    async def test_get_pending_approvals_with_limit(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting pending approvals with custom limit."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflows/approvals/pending?limit=5",
            json={
                "pending_approvals": [],
                "count": 0,
            },
        )

        result = await client.get_pending_approvals(limit=5)
        assert result.count == 0
        assert result.pending_approvals == []

    @pytest.mark.asyncio
    async def test_get_pending_approvals_empty(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting pending approvals when none exist."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflows/approvals/pending?limit=20",
            json={
                "pending_approvals": [],
                "count": 0,
            },
        )

        result = await client.get_pending_approvals()
        assert result.count == 0
        assert result.pending_approvals == []


class TestGetPendingPlanApprovals:
    """Test get_pending_plan_approvals method — the MAP-plane listing (#1680)."""

    @pytest.mark.asyncio
    async def test_get_pending_plan_approvals(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """MAP-plane entries carry plan_id through the round trip."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/plans/approvals/pending?limit=20",
            json={
                "pending_approvals": [
                    {
                        "workflow_id": "wf_map_abc",
                        "workflow_name": "map-confirm-plan-abc",
                        "plan_id": "plan-abc",
                        "step_id": "step_0_analyze",
                        "step_index": 0,
                        "step_name": "Analyze transaction",
                        "step_type": "tool_call",
                        "decision": "require_approval",
                        "decision_reason": "High-value transaction",
                        "created_at": "2026-04-22T10:00:00Z",
                    },
                ],
                "count": 1,
            },
        )

        result = await client.get_pending_plan_approvals()
        assert isinstance(result, PendingApprovalsResponse)
        assert result.count == 1
        assert len(result.pending_approvals) == 1
        assert result.pending_approvals[0].plan_id == "plan-abc"
        assert result.pending_approvals[0].decision_reason == "High-value transaction"

    @pytest.mark.asyncio
    async def test_get_pending_plan_approvals_plan_id_filter(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """plan_id argument propagates to the query string."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/plans/approvals/pending?limit=20&plan_id=plan-abc",
            json={"pending_approvals": [], "count": 0},
        )

        await client.get_pending_plan_approvals(plan_id="plan-abc")

    @pytest.mark.asyncio
    async def test_get_pending_plan_approvals_limit_and_plan_id(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Both knobs propagate together."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/plans/approvals/pending?limit=3&plan_id=plan-x",
            json={"pending_approvals": [], "count": 0},
        )

        await client.get_pending_plan_approvals(limit=3, plan_id="plan-x")

    @pytest.mark.asyncio
    async def test_get_pending_plan_approvals_empty(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Empty list deserializes to an empty list (not None)."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/plans/approvals/pending?limit=20",
            json={"pending_approvals": [], "count": 0},
        )

        result = await client.get_pending_plan_approvals()
        assert result.count == 0
        assert result.pending_approvals == []


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
                "pending_approvals": [
                    {
                        "workflow_id": "wf-123",
                        "workflow_name": "test-wf",
                        "step_id": "step-1",
                        "step_index": 0,
                        "step_name": "Test Step",
                        "step_type": "llm_call",
                        "decision": "require_approval",
                        "created_at": "2026-02-07T10:00:00Z",
                    },
                ],
                "count": 1,
            },
        )

        result = sync_client.get_pending_approvals(limit=10)
        assert isinstance(result, PendingApprovalsResponse)
        assert result.count == 1

    def test_sync_get_pending_plan_approvals(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync get_pending_plan_approvals wrapper (MAP plane)."""
        httpx_mock.add_response(
            json={
                "pending_approvals": [
                    {
                        "workflow_id": "wf-map",
                        "workflow_name": "map-plan-1",
                        "plan_id": "plan-1",
                        "step_id": "step-1",
                        "step_index": 0,
                        "step_name": "Test",
                        "step_type": "tool_call",
                        "decision": "require_approval",
                        "created_at": "2026-04-22T10:00:00Z",
                    },
                ],
                "count": 1,
            },
        )

        result = sync_client.get_pending_plan_approvals(limit=10, plan_id="plan-1")
        assert isinstance(result, PendingApprovalsResponse)
        assert result.count == 1
        assert result.pending_approvals[0].plan_id == "plan-1"

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
        resp = ApproveStepResponse(workflow_id="wf-1", step_id="step-1", status="approved")
        assert resp.workflow_id == "wf-1"
        assert resp.step_id == "step-1"
        assert resp.status == "approved"

    def test_reject_step_response(self) -> None:
        """Test RejectStepResponse model."""
        resp = RejectStepResponse(workflow_id="wf-1", step_id="step-1", status="rejected")
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
        # plan_id defaults to None (WCP-plane entry shape)
        assert approval.plan_id is None

    def test_pending_approval_with_plan_id(self) -> None:
        """Test PendingApproval carries plan_id through on MAP-plane entries (#1680)."""
        approval = PendingApproval(
            workflow_id="wf-map",
            workflow_name="map-plan-1",
            plan_id="plan-1",
            step_id="step-1",
            step_index=0,
            decision="require_approval",
            created_at="2026-04-22T10:00:00Z",
        )
        assert approval.plan_id == "plan-1"
        # Ensures model_dump preserves plan_id so callers can serialize back
        dumped = approval.model_dump()
        assert dumped["plan_id"] == "plan-1"

    def test_pending_approvals_response_defaults(self) -> None:
        """Test PendingApprovalsResponse with defaults."""
        resp = PendingApprovalsResponse()
        assert resp.pending_approvals == []
        assert resp.count == 0

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


# =========================================================================
# WCP Workflow Lifecycle Tests
# =========================================================================


class TestCreateWorkflow:
    """Test create_workflow method."""

    @pytest.mark.asyncio
    async def test_create_workflow(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test creating a new workflow."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflows",
            json={
                "workflow_id": "wf-abc",
                "workflow_name": "customer-support",
                "source": "langgraph",
                "status": "in_progress",
                "created_at": "2026-02-07T10:00:00Z",
            },
        )

        request = CreateWorkflowRequest(
            workflow_name="customer-support",
            source=WorkflowSource.LANGGRAPH,
            metadata={"customer_id": "cust-1"},
        )
        result = await client.create_workflow(request)
        assert isinstance(result, CreateWorkflowResponse)
        assert result.workflow_id == "wf-abc"
        assert result.workflow_name == "customer-support"
        assert result.source == WorkflowSource.LANGGRAPH
        assert result.status == WorkflowStatus.IN_PROGRESS

    @pytest.mark.asyncio
    async def test_create_workflow_no_source(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test creating a workflow without specifying source."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflows",
            json={
                "workflow_id": "wf-xyz",
                "workflow_name": "data-pipeline",
                "source": "external",
                "status": "in_progress",
                "created_at": "2026-02-07T11:00:00Z",
            },
        )

        request = CreateWorkflowRequest(workflow_name="data-pipeline")
        result = await client.create_workflow(request)
        assert result.workflow_id == "wf-xyz"
        assert result.source == WorkflowSource.EXTERNAL


class TestGetWorkflow:
    """Test get_workflow method."""

    @pytest.mark.asyncio
    async def test_get_workflow(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting workflow status."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflows/wf-abc",
            json={
                "workflow_id": "wf-abc",
                "workflow_name": "customer-support",
                "source": "langgraph",
                "status": "in_progress",
                "current_step_index": 1,
                "total_steps": 3,
                "started_at": "2026-02-07T10:00:00Z",
                "steps": [
                    {
                        "step_id": "step-1",
                        "step_index": 0,
                        "step_name": "Fetch Data",
                        "step_type": "tool_call",
                        "decision": "allow",
                        "gate_checked_at": "2026-02-07T10:01:00Z",
                        "completed_at": "2026-02-07T10:02:00Z",
                    },
                ],
            },
        )

        result = await client.get_workflow("wf-abc")
        assert isinstance(result, WorkflowStatusResponse)
        assert result.workflow_id == "wf-abc"
        assert result.status == WorkflowStatus.IN_PROGRESS
        assert result.current_step_index == 1
        assert len(result.steps) == 1
        assert result.steps[0].step_id == "step-1"
        assert result.steps[0].decision == GateDecision.ALLOW

    @pytest.mark.asyncio
    async def test_get_workflow_no_steps(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting workflow status with no steps yet."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflows/wf-new",
            json={
                "workflow_id": "wf-new",
                "workflow_name": "new-workflow",
                "source": "external",
                "status": "in_progress",
                "current_step_index": 0,
                "started_at": "2026-02-07T10:00:00Z",
            },
        )

        result = await client.get_workflow("wf-new")
        assert result.steps == []
        assert result.current_step_index == 0


class TestStepGate:
    """Test step_gate method."""

    @pytest.mark.asyncio
    async def test_step_gate_allow(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test step gate returning allow decision."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflows/wf-abc/steps/step-1/gate",
            json={
                "decision": "allow",
                "step_id": "step-1",
                "reason": None,
                "policy_ids": [],
            },
        )

        request = StepGateRequest(
            step_name="Generate Response",
            step_type=StepType.LLM_CALL,
            model="gpt-4",
            provider="openai",
        )
        result = await client.step_gate("wf-abc", "step-1", request)
        assert isinstance(result, StepGateResponse)
        assert result.decision == GateDecision.ALLOW
        assert result.step_id == "step-1"
        assert result.is_allowed() is True
        assert result.is_blocked() is False

    @pytest.mark.asyncio
    async def test_step_gate_block(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test step gate returning block decision."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflows/wf-abc/steps/step-2/gate",
            json={
                "decision": "block",
                "step_id": "step-2",
                "reason": "PII detected in step input",
                "policy_ids": ["pii-ssn", "pii-email"],
            },
        )

        request = StepGateRequest(
            step_name="Delete Records",
            step_type=StepType.TOOL_CALL,
        )
        result = await client.step_gate("wf-abc", "step-2", request)
        assert result.decision == GateDecision.BLOCK
        assert result.reason == "PII detected in step input"
        assert len(result.policy_ids) == 2
        assert result.is_blocked() is True

    @pytest.mark.asyncio
    async def test_step_gate_require_approval(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test step gate returning require_approval decision."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflows/wf-abc/steps/step-3/gate",
            json={
                "decision": "require_approval",
                "step_id": "step-3",
                "reason": "High-risk operation requires approval",
                "policy_ids": ["high-risk-ops"],
                "approval_url": "https://portal.axonflow.com/approve/step-3",
            },
        )

        request = StepGateRequest(
            step_name="Execute SQL",
            step_type=StepType.CONNECTOR_CALL,
        )
        result = await client.step_gate("wf-abc", "step-3", request)
        assert result.decision == GateDecision.REQUIRE_APPROVAL
        assert result.requires_approval() is True
        assert result.approval_url == "https://portal.axonflow.com/approve/step-3"


class TestMarkStepCompleted:
    """Test mark_step_completed method."""

    @pytest.mark.asyncio
    async def test_mark_step_completed(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test marking a step as completed with output."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflows/wf-abc/steps/step-1/complete",
            status_code=204,
        )

        request = MarkStepCompletedRequest(
            output={"result": "Data fetched successfully"},
            metadata={"duration_ms": 150},
        )
        # Should not raise
        await client.mark_step_completed("wf-abc", "step-1", request)

    @pytest.mark.asyncio
    async def test_mark_step_completed_no_request(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test marking a step as completed without output."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflows/wf-abc/steps/step-2/complete",
            status_code=204,
        )

        await client.mark_step_completed("wf-abc", "step-2")


class TestCompleteWorkflow:
    """Test complete_workflow method."""

    @pytest.mark.asyncio
    async def test_complete_workflow(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test completing a workflow."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflows/wf-abc/complete",
            status_code=204,
        )

        await client.complete_workflow("wf-abc")


class TestAbortWorkflow:
    """Test abort_workflow method."""

    @pytest.mark.asyncio
    async def test_abort_workflow_with_reason(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test aborting a workflow with a reason."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflows/wf-abc/abort",
            status_code=204,
        )

        await client.abort_workflow("wf-abc", reason="User cancelled")

    @pytest.mark.asyncio
    async def test_abort_workflow_no_reason(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test aborting a workflow without a reason."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflows/wf-abc/abort",
            status_code=204,
        )

        await client.abort_workflow("wf-abc")


class TestResumeWorkflow:
    """Test resume_workflow method."""

    @pytest.mark.asyncio
    async def test_resume_workflow(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test resuming a workflow after approval."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflows/wf-abc/resume",
            status_code=204,
        )

        await client.resume_workflow("wf-abc")


class TestListWorkflows:
    """Test list_workflows method."""

    @pytest.mark.asyncio
    async def test_list_workflows(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test listing workflows with no filters."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflows",
            json={
                "workflows": [
                    {
                        "workflow_id": "wf-1",
                        "workflow_name": "wf-one",
                        "source": "langgraph",
                        "status": "in_progress",
                        "current_step_index": 0,
                        "started_at": "2026-02-07T10:00:00Z",
                    },
                ],
                "total": 1,
            },
        )

        result = await client.list_workflows()
        assert isinstance(result, ListWorkflowsResponse)
        assert result.total == 1
        assert len(result.workflows) == 1
        assert result.workflows[0].workflow_id == "wf-1"

    @pytest.mark.asyncio
    async def test_list_workflows_with_filters(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test listing workflows with status and source filters."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/workflows?status=in_progress&source=langgraph&limit=10&offset=5",
            json={
                "workflows": [],
                "total": 0,
            },
        )

        options = ListWorkflowsOptions(
            status=WorkflowStatus.IN_PROGRESS,
            source=WorkflowSource.LANGGRAPH,
            limit=10,
            offset=5,
        )
        result = await client.list_workflows(options)
        assert result.total == 0
        assert result.workflows == []


# =========================================================================
# Unified Execution Tracking Tests
# =========================================================================


class TestGetExecutionStatus:
    """Test get_execution_status method."""

    @pytest.mark.asyncio
    async def test_get_execution_status(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting unified execution status."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/unified/executions/exec-123",
            json={
                "execution_id": "exec-123",
                "execution_type": "wcp_workflow",
                "name": "customer-support",
                "source": "langgraph",
                "status": "running",
                "current_step_index": 1,
                "total_steps": 3,
                "progress_percent": 33.3,
                "started_at": "2026-02-07T10:00:00Z",
                "duration": "2m30s",
                "steps": [
                    {
                        "step_id": "step-1",
                        "step_index": 0,
                        "step_name": "Fetch Data",
                        "step_type": "tool_call",
                        "status": "completed",
                        "started_at": "2026-02-07T10:00:00Z",
                        "ended_at": "2026-02-07T10:01:00Z",
                        "duration": "1m0s",
                        "decision": "allow",
                        "policies_matched": [],
                        "model": "gpt-4",
                        "provider": "openai",
                        "cost_usd": 0.05,
                        "output": {"result": "ok"},
                    },
                    {
                        "step_id": "step-2",
                        "step_index": 1,
                        "step_name": "Generate Response",
                        "step_type": "llm_call",
                        "status": "running",
                        "started_at": "2026-02-07T10:01:00Z",
                        "decision": "allow",
                        "policies_matched": [],
                    },
                ],
                "tenant_id": "tenant-1",
                "org_id": "org-1",
                "user_id": "user-1",
                "client_id": "client-1",
                "metadata": {"key": "value"},
                "created_at": "2026-02-07T10:00:00Z",
                "updated_at": "2026-02-07T10:02:30Z",
            },
        )

        result = await client.get_execution_status("exec-123")
        assert isinstance(result, ExecutionStatus)
        assert result.execution_id == "exec-123"
        assert result.execution_type == ExecutionType.WCP_WORKFLOW
        assert result.name == "customer-support"
        assert result.status == ExecutionStatusValue.RUNNING
        assert result.progress_percent == 33.3
        assert len(result.steps) == 2
        assert result.steps[0].step_name == "Fetch Data"
        assert result.steps[0].cost_usd == 0.05
        assert result.steps[1].step_name == "Generate Response"
        assert result.tenant_id == "tenant-1"
        assert result.is_wcp_workflow() is True
        assert result.is_map_plan() is False

    @pytest.mark.asyncio
    async def test_get_execution_status_completed(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting completed execution status with approval fields."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/unified/executions/exec-456",
            json={
                "execution_id": "exec-456",
                "execution_type": "map_plan",
                "name": "data-analysis",
                "status": "completed",
                "current_step_index": 2,
                "total_steps": 2,
                "progress_percent": 100.0,
                "started_at": "2026-02-07T09:00:00Z",
                "completed_at": "2026-02-07T09:30:00Z",
                "duration": "30m0s",
                "estimated_cost_usd": 0.50,
                "actual_cost_usd": 0.45,
                "steps": [
                    {
                        "step_id": "s-1",
                        "step_index": 0,
                        "step_name": "Query DB",
                        "step_type": "connector_call",
                        "status": "completed",
                        "started_at": "2026-02-07T09:00:00Z",
                        "ended_at": "2026-02-07T09:10:00Z",
                        "decision": "require_approval",
                        "decision_reason": "High-risk query",
                        "approval_status": "approved",
                        "approved_by": "admin@example.com",
                        "approved_at": "2026-02-07T09:05:00Z",
                        "result_summary": "Fetched 1000 rows",
                    },
                ],
                "metadata": {},
                "created_at": "2026-02-07T09:00:00Z",
                "updated_at": "2026-02-07T09:30:00Z",
            },
        )

        result = await client.get_execution_status("exec-456")
        assert result.execution_type == ExecutionType.MAP_PLAN
        assert result.status == ExecutionStatusValue.COMPLETED
        assert result.actual_cost_usd == 0.45
        assert result.is_map_plan() is True
        assert result.steps[0].approval_status is not None
        assert result.steps[0].approved_by == "admin@example.com"
        assert result.steps[0].result_summary == "Fetched 1000 rows"

    @pytest.mark.asyncio
    async def test_get_execution_status_empty_id(
        self,
        client: AxonFlow,
    ) -> None:
        """Test get_execution_status with empty ID raises ValueError."""
        with pytest.raises(ValueError, match="Execution ID is required"):
            await client.get_execution_status("")


class TestListUnifiedExecutions:
    """Test list_unified_executions method."""

    @pytest.mark.asyncio
    async def test_list_unified_executions(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test listing unified executions with no filters."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/unified/executions",
            json={
                "executions": [
                    {
                        "execution_id": "exec-1",
                        "execution_type": "wcp_workflow",
                        "name": "wf-one",
                        "status": "running",
                        "started_at": "2026-02-07T10:00:00Z",
                        "created_at": "2026-02-07T10:00:00Z",
                        "updated_at": "2026-02-07T10:05:00Z",
                    },
                ],
                "total": 1,
                "limit": 50,
                "offset": 0,
                "has_more": False,
            },
        )

        result = await client.list_unified_executions()
        assert isinstance(result, UnifiedListExecutionsResponse)
        assert result.total == 1
        assert len(result.executions) == 1
        assert result.executions[0].execution_id == "exec-1"
        assert result.has_more is False

    @pytest.mark.asyncio
    async def test_list_unified_executions_with_filters(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test listing unified executions with filters."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/unified/executions?execution_type=wcp_workflow&status=running&tenant_id=t1&org_id=o1&limit=10&offset=5",
            json={
                "executions": [],
                "total": 0,
                "limit": 10,
                "offset": 5,
                "has_more": False,
            },
        )

        options = UnifiedListExecutionsRequest(
            execution_type=ExecutionType.WCP_WORKFLOW,
            status=ExecutionStatusValue.RUNNING,
            tenant_id="t1",
            org_id="o1",
            limit=10,
            offset=5,
        )
        result = await client.list_unified_executions(options)
        assert result.total == 0
        assert result.limit == 10
        assert result.offset == 5


class TestCancelExecution:
    """Test cancel_execution method."""

    @pytest.mark.asyncio
    async def test_cancel_execution_with_reason(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test cancelling an execution with a reason."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/unified/executions/exec-123/cancel",
            status_code=204,
        )

        await client.cancel_execution("exec-123", reason="No longer needed")

    @pytest.mark.asyncio
    async def test_cancel_execution_no_reason(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test cancelling an execution without a reason."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/unified/executions/exec-456/cancel",
            status_code=204,
        )

        await client.cancel_execution("exec-456")

    @pytest.mark.asyncio
    async def test_cancel_execution_empty_id(
        self,
        client: AxonFlow,
    ) -> None:
        """Test cancel_execution with empty ID raises ValueError."""
        with pytest.raises(ValueError, match="Execution ID is required"):
            await client.cancel_execution("")


# =========================================================================
# Sync Wrapper Tests for WCP Workflow + Unified Execution
# =========================================================================


class TestSyncWorkflowWrappers:
    """Test sync wrappers for WCP workflow lifecycle methods."""

    def test_sync_create_workflow(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync create_workflow wrapper."""
        httpx_mock.add_response(
            json={
                "workflow_id": "wf-sync",
                "workflow_name": "sync-wf",
                "source": "external",
                "status": "in_progress",
                "created_at": "2026-02-07T10:00:00Z",
            },
        )

        request = CreateWorkflowRequest(workflow_name="sync-wf")
        result = sync_client.create_workflow(request)
        assert isinstance(result, CreateWorkflowResponse)
        assert result.workflow_id == "wf-sync"

    def test_sync_get_workflow(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync get_workflow wrapper."""
        httpx_mock.add_response(
            json={
                "workflow_id": "wf-sync",
                "workflow_name": "sync-wf",
                "source": "external",
                "status": "in_progress",
                "current_step_index": 0,
                "started_at": "2026-02-07T10:00:00Z",
            },
        )

        result = sync_client.get_workflow("wf-sync")
        assert isinstance(result, WorkflowStatusResponse)
        assert result.workflow_id == "wf-sync"

    def test_sync_step_gate(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync step_gate wrapper."""
        httpx_mock.add_response(
            json={
                "decision": "allow",
                "step_id": "step-1",
                "policy_ids": [],
            },
        )

        request = StepGateRequest(
            step_name="Test Step",
            step_type=StepType.LLM_CALL,
        )
        result = sync_client.step_gate("wf-sync", "step-1", request)
        assert isinstance(result, StepGateResponse)
        assert result.decision == GateDecision.ALLOW

    def test_sync_mark_step_completed(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync mark_step_completed wrapper."""
        httpx_mock.add_response(status_code=204)

        sync_client.mark_step_completed("wf-sync", "step-1")

    def test_sync_complete_workflow(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync complete_workflow wrapper."""
        httpx_mock.add_response(status_code=204)

        sync_client.complete_workflow("wf-sync")

    def test_sync_abort_workflow(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync abort_workflow wrapper."""
        httpx_mock.add_response(status_code=204)

        sync_client.abort_workflow("wf-sync", reason="Cancelled")

    def test_sync_resume_workflow(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync resume_workflow wrapper."""
        httpx_mock.add_response(status_code=204)

        sync_client.resume_workflow("wf-sync")

    def test_sync_list_workflows(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync list_workflows wrapper."""
        httpx_mock.add_response(
            json={
                "workflows": [],
                "total": 0,
            },
        )

        result = sync_client.list_workflows()
        assert isinstance(result, ListWorkflowsResponse)
        assert result.total == 0


class TestSyncUnifiedExecutionWrappers:
    """Test sync wrappers for unified execution methods."""

    def test_sync_get_execution_status(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync get_execution_status wrapper."""
        httpx_mock.add_response(
            json={
                "execution_id": "exec-sync",
                "execution_type": "wcp_workflow",
                "name": "sync-exec",
                "status": "running",
                "started_at": "2026-02-07T10:00:00Z",
                "created_at": "2026-02-07T10:00:00Z",
                "updated_at": "2026-02-07T10:05:00Z",
            },
        )

        result = sync_client.get_execution_status("exec-sync")
        assert isinstance(result, ExecutionStatus)
        assert result.execution_id == "exec-sync"

    def test_sync_list_unified_executions(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync list_unified_executions wrapper."""
        httpx_mock.add_response(
            json={
                "executions": [],
                "total": 0,
                "limit": 50,
                "offset": 0,
                "has_more": False,
            },
        )

        result = sync_client.list_unified_executions()
        assert isinstance(result, UnifiedListExecutionsResponse)
        assert result.total == 0

    def test_sync_cancel_execution(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync cancel_execution wrapper."""
        httpx_mock.add_response(status_code=204)

        sync_client.cancel_execution("exec-sync", reason="Done")
