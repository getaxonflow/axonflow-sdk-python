# Copyright 2026 AxonFlow
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for HITL Queue API methods."""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow, SyncAxonFlow
from axonflow.exceptions import (
    AuthenticationError,
    AxonFlowError,
)
from axonflow.exceptions import (
    ConnectionError as AxonFlowConnectionError,
)
from axonflow.hitl import (
    HITLApprovalRequest,
    HITLCreateInput,
    HITLQueueListOptions,
    HITLQueueListResponse,
    HITLReviewInput,
    HITLStats,
)

# =========================================================================
# HITL Queue List Tests
# =========================================================================


class TestListHITLQueue:
    """Test list_hitl_queue method."""

    @pytest.mark.asyncio
    async def test_list_hitl_queue(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test listing HITL queue with items."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/hitl/queue",
            json={
                "success": True,
                "data": [
                    {
                        "request_id": "hitl-req-001",
                        "org_id": "org-1",
                        "tenant_id": "tenant-1",
                        "client_id": "client-1",
                        "user_id": "user-1",
                        "original_query": "What is the CEO salary?",
                        "request_type": "chat",
                        "triggered_policy_id": "pol-pii-001",
                        "triggered_policy_name": "PII Salary Detection",
                        "trigger_reason": "Query requests sensitive compensation data",
                        "severity": "high",
                        "compliance_framework": "GDPR",
                        "status": "pending",
                        "expires_at": "2026-02-13T10:00:00Z",
                        "created_at": "2026-02-12T10:00:00Z",
                        "updated_at": "2026-02-12T10:00:00Z",
                    },
                    {
                        "request_id": "hitl-req-002",
                        "org_id": "org-1",
                        "tenant_id": "tenant-1",
                        "client_id": "client-2",
                        "original_query": "Delete all user records",
                        "request_type": "chat",
                        "triggered_policy_id": "pol-sec-001",
                        "triggered_policy_name": "Destructive Operations",
                        "trigger_reason": "Potential destructive database operation",
                        "severity": "critical",
                        "status": "pending",
                        "expires_at": "2026-02-13T11:00:00Z",
                        "created_at": "2026-02-12T11:00:00Z",
                        "updated_at": "2026-02-12T11:00:00Z",
                    },
                ],
                "meta": {"total": 2, "limit": 50, "offset": 0},
            },
        )

        result = await client.list_hitl_queue()
        assert isinstance(result, HITLQueueListResponse)
        assert result.total == 2
        assert result.has_more is False
        assert len(result.items) == 2
        assert result.items[0].request_id == "hitl-req-001"
        assert result.items[0].severity == "high"
        assert result.items[0].triggered_policy_name == "PII Salary Detection"
        assert result.items[0].compliance_framework == "GDPR"
        assert result.items[1].request_id == "hitl-req-002"
        assert result.items[1].severity == "critical"
        assert result.items[1].user_id is None

    @pytest.mark.asyncio
    async def test_list_hitl_queue_with_filters(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test listing HITL queue with status and severity filters."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/hitl/queue?status=pending&severity=critical&limit=5&offset=0",
            json={
                "success": True,
                "data": [
                    {
                        "request_id": "hitl-req-002",
                        "org_id": "org-1",
                        "tenant_id": "tenant-1",
                        "client_id": "client-2",
                        "original_query": "Delete all user records",
                        "request_type": "chat",
                        "triggered_policy_id": "pol-sec-001",
                        "triggered_policy_name": "Destructive Operations",
                        "trigger_reason": "Potential destructive database operation",
                        "severity": "critical",
                        "status": "pending",
                        "expires_at": "2026-02-13T11:00:00Z",
                        "created_at": "2026-02-12T11:00:00Z",
                        "updated_at": "2026-02-12T11:00:00Z",
                    },
                ],
                "meta": {"total": 1, "limit": 5, "offset": 0},
            },
        )

        opts = HITLQueueListOptions(
            status="pending",
            severity="critical",
            limit=5,
            offset=0,
        )
        result = await client.list_hitl_queue(opts)
        assert result.total == 1
        assert len(result.items) == 1
        assert result.items[0].severity == "critical"

    @pytest.mark.asyncio
    async def test_list_hitl_queue_empty(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test listing HITL queue when no items exist."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/hitl/queue",
            json={
                "success": True,
                "data": [],
                "meta": {"total": 0, "limit": 50, "offset": 0},
            },
        )

        result = await client.list_hitl_queue()
        assert result.total == 0
        assert result.items == []
        assert result.has_more is False


# =========================================================================
# HITL Get Request Tests
# =========================================================================


class TestGetHITLRequest:
    """Test get_hitl_request method."""

    @pytest.mark.asyncio
    async def test_get_hitl_request(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting a specific HITL approval request."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/hitl/queue/hitl-req-001",
            json={
                "success": True,
                "data": {
                    "request_id": "hitl-req-001",
                    "org_id": "org-1",
                    "tenant_id": "tenant-1",
                    "client_id": "client-1",
                    "user_id": "user-1",
                    "original_query": "What is the CEO salary?",
                    "request_type": "chat",
                    "request_context": {"session_id": "sess-abc"},
                    "triggered_policy_id": "pol-pii-001",
                    "triggered_policy_name": "PII Salary Detection",
                    "trigger_reason": "Query requests sensitive compensation data",
                    "severity": "high",
                    "eu_ai_act_article": "Article 14",
                    "compliance_framework": "GDPR",
                    "risk_classification": "high-risk",
                    "status": "pending",
                    "expires_at": "2026-02-13T10:00:00Z",
                    "created_at": "2026-02-12T10:00:00Z",
                    "updated_at": "2026-02-12T10:00:00Z",
                },
            },
        )

        result = await client.get_hitl_request("hitl-req-001")
        assert isinstance(result, HITLApprovalRequest)
        assert result.request_id == "hitl-req-001"
        assert result.org_id == "org-1"
        assert result.tenant_id == "tenant-1"
        assert result.user_id == "user-1"
        assert result.original_query == "What is the CEO salary?"
        assert result.request_context == {"session_id": "sess-abc"}
        assert result.triggered_policy_name == "PII Salary Detection"
        assert result.eu_ai_act_article == "Article 14"
        assert result.compliance_framework == "GDPR"
        assert result.risk_classification == "high-risk"
        assert result.status == "pending"
        assert result.reviewer_id is None
        assert result.reviewed_at is None

    @pytest.mark.asyncio
    async def test_get_hitl_request_reviewed(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting an HITL request that has been reviewed."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/hitl/queue/hitl-req-003",
            json={
                "success": True,
                "data": {
                    "request_id": "hitl-req-003",
                    "org_id": "org-1",
                    "tenant_id": "tenant-1",
                    "client_id": "client-1",
                    "original_query": "Show me patient records",
                    "request_type": "chat",
                    "triggered_policy_id": "pol-hipaa-001",
                    "triggered_policy_name": "HIPAA PHI Protection",
                    "trigger_reason": "Query accesses protected health information",
                    "severity": "critical",
                    "compliance_framework": "HIPAA",
                    "status": "approved",
                    "reviewer_id": "admin-1",
                    "reviewer_email": "admin@hospital.com",
                    "review_comment": "Authorized physician request",
                    "reviewed_at": "2026-02-12T12:00:00Z",
                    "expires_at": "2026-02-13T10:00:00Z",
                    "created_at": "2026-02-12T10:00:00Z",
                    "updated_at": "2026-02-12T12:00:00Z",
                },
            },
        )

        result = await client.get_hitl_request("hitl-req-003")
        assert result.status == "approved"
        assert result.reviewer_id == "admin-1"
        assert result.reviewer_email == "admin@hospital.com"
        assert result.review_comment == "Authorized physician request"
        assert result.reviewed_at == "2026-02-12T12:00:00Z"


# =========================================================================
# HITL Create Request Tests
# =========================================================================


class TestCreateHITLRequest:
    """Test create_hitl_request method (POST /api/v1/hitl/queue)."""

    @pytest.mark.asyncio
    async def test_create_hitl_request(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Posting a valid create-input enqueues a HITL row and returns the created record."""
        httpx_mock.add_response(
            method="POST",
            url="https://test.axonflow.com/api/v1/hitl/queue",
            status_code=201,
            json={
                "success": True,
                "data": {
                    "request_id": "hitl-req-new-001",
                    "org_id": "org-1",
                    "tenant_id": "tenant-1",
                    "client_id": "loan-desk",
                    "user_id": "cust-001",
                    "original_query": "disburse $50000 to cust-001",
                    "request_type": "adk-tool",
                    "request_context": {"tool_name": "disburse_payment"},
                    "triggered_policy_id": "loan-amount-cap",
                    "triggered_policy_name": "Loan amount cap",
                    "trigger_reason": "Disbursement above $10k requires manager approval",
                    "severity": "high",
                    "notify_url": "https://workflows.example.com/hooks/loan-approve",
                    "status": "pending",
                    "expires_at": "2026-05-23T11:00:00Z",
                    "created_at": "2026-05-23T10:00:00Z",
                    "updated_at": "2026-05-23T10:00:00Z",
                },
            },
        )

        result = await client.create_hitl_request(
            HITLCreateInput(
                client_id="loan-desk",
                user_id="cust-001",
                original_query="disburse $50000 to cust-001",
                request_type="adk-tool",
                request_context={"tool_name": "disburse_payment"},
                triggered_policy_id="loan-amount-cap",
                triggered_policy_name="Loan amount cap",
                trigger_reason="Disbursement above $10k requires manager approval",
                severity="high",
                notify_url="https://workflows.example.com/hooks/loan-approve",
            )
        )
        assert isinstance(result, HITLApprovalRequest)
        assert result.request_id == "hitl-req-new-001"
        assert result.status == "pending"
        assert result.triggered_policy_name == "Loan amount cap"
        assert result.notify_url == "https://workflows.example.com/hooks/loan-approve"

    @pytest.mark.asyncio
    async def test_create_hitl_request_minimal(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """The required-field minimum is client_id + original_query + request_type."""
        httpx_mock.add_response(
            method="POST",
            url="https://test.axonflow.com/api/v1/hitl/queue",
            status_code=201,
            json={
                "success": True,
                "data": {
                    "request_id": "hitl-req-minimal",
                    "org_id": "org-1",
                    "tenant_id": "tenant-1",
                    "client_id": "c1",
                    "original_query": "q",
                    "request_type": "chat",
                    "triggered_policy_id": "",
                    "triggered_policy_name": "",
                    "trigger_reason": "",
                    "severity": "medium",
                    "status": "pending",
                    "expires_at": "2026-05-23T11:00:00Z",
                    "created_at": "2026-05-23T10:00:00Z",
                    "updated_at": "2026-05-23T10:00:00Z",
                },
            },
        )

        result = await client.create_hitl_request(
            HITLCreateInput(
                client_id="c1",
                original_query="q",
                request_type="chat",
            )
        )
        assert result.request_id == "hitl-req-minimal"
        assert result.notify_url is None

    @pytest.mark.asyncio
    async def test_create_hitl_request_bad_notify_url_scheme_propagates_400(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Platform-side notify_url scheme rejection surfaces as AxonFlowError.

        Mirrors ``platform/agent/hitl/webhook.go:105 ValidateNotifyURL``
        in the companion sister-session work for
        getaxonflow/axonflow-enterprise#2419. The SDK is intentionally
        pass-through here so a tightened scheme allowlist on the
        platform does not require an SDK upgrade. Parity with TS/Go/
        Java/Rust sister tests in the same v8.2.0 release train.
        """
        httpx_mock.add_response(
            method="POST",
            url="https://test.axonflow.com/api/v1/hitl/queue",
            status_code=400,
            json={
                "success": False,
                "error": 'notify_url scheme "javascript" is not allowed (use https:// or http://)',
            },
        )

        with pytest.raises(AxonFlowError):
            await client.create_hitl_request(
                HITLCreateInput(
                    client_id="loan-desk",
                    original_query="disburse $50000",
                    request_type="adk-tool",
                    notify_url="javascript:alert(1)",
                )
            )

    @pytest.mark.asyncio
    async def test_create_hitl_request_auth_failure_propagates(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """401 from the platform surfaces as AuthenticationError, not a bare AxonFlowError.

        Exercises the same error-mapping path as every other SDK method.
        Important here because ADK / n8n callers configure credentials
        once at agent start and a rotated key shouldn't look like a
        validation problem when create_hitl_request is the first
        request after the rotation.
        """
        httpx_mock.add_response(
            method="POST",
            url="https://test.axonflow.com/api/v1/hitl/queue",
            status_code=401,
            json={"success": False, "error": "Invalid API key"},
        )

        with pytest.raises(AuthenticationError):
            await client.create_hitl_request(
                HITLCreateInput(
                    client_id="loan-desk",
                    original_query="disburse $50000",
                    request_type="adk-tool",
                )
            )

    @pytest.mark.asyncio
    async def test_create_hitl_request_network_failure_propagates(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Connect failure propagates as AxonFlow ConnectionError after retries are exhausted.

        ``_request`` wraps :class:`httpx.ConnectError` in
        :class:`axonflow.exceptions.ConnectionError`; this test guards
        the mapping so ADK callers can rely on it when implementing
        their own retry/backoff above us. The default ``RetryConfig``
        attempts the request three times before giving up — register
        one exception per attempt so the test deterministically reaches
        the wrapped-error path instead of getting a "no response
        registered" :class:`TimeoutError` on the retry.
        """
        import httpx

        for _ in range(3):
            httpx_mock.add_exception(httpx.ConnectError("connection refused"))

        with pytest.raises(AxonFlowConnectionError):
            await client.create_hitl_request(
                HITLCreateInput(
                    client_id="loan-desk",
                    original_query="disburse $50000",
                    request_type="adk-tool",
                )
            )


# =========================================================================
# HITL Approve Request Tests
# =========================================================================


class TestApproveHITLRequest:
    """Test approve_hitl_request method."""

    @pytest.mark.asyncio
    async def test_approve_hitl_request(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test approving an HITL request."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/hitl/queue/hitl-req-001/approve",
            json={"success": True, "data": None},
        )

        review = HITLReviewInput(
            reviewer_id="admin-1",
            reviewer_email="admin@company.com",
            reviewer_role="compliance_officer",
            comment="Reviewed and approved - query is legitimate",
        )
        # Should not raise
        await client.approve_hitl_request("hitl-req-001", review)

    @pytest.mark.asyncio
    async def test_approve_hitl_request_minimal(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test approving an HITL request with minimal review input."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/hitl/queue/hitl-req-002/approve",
            json={"success": True, "data": None},
        )

        review = HITLReviewInput(
            reviewer_id="admin-2",
            reviewer_email="admin2@company.com",
        )
        await client.approve_hitl_request("hitl-req-002", review)


# =========================================================================
# HITL Reject Request Tests
# =========================================================================


class TestRejectHITLRequest:
    """Test reject_hitl_request method."""

    @pytest.mark.asyncio
    async def test_reject_hitl_request(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test rejecting an HITL request."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/hitl/queue/hitl-req-001/reject",
            json={"success": True, "data": None},
        )

        review = HITLReviewInput(
            reviewer_id="admin-1",
            reviewer_email="admin@company.com",
            reviewer_role="security_admin",
            comment="Query contains sensitive PII - rejected",
        )
        # Should not raise
        await client.reject_hitl_request("hitl-req-001", review)

    @pytest.mark.asyncio
    async def test_reject_hitl_request_minimal(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test rejecting an HITL request with minimal review input."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/hitl/queue/hitl-req-003/reject",
            json={"success": True, "data": None},
        )

        review = HITLReviewInput(
            reviewer_id="admin-3",
            reviewer_email="admin3@company.com",
        )
        await client.reject_hitl_request("hitl-req-003", review)


# =========================================================================
# HITL Stats Tests
# =========================================================================


class TestGetHITLStats:
    """Test get_hitl_stats method."""

    @pytest.mark.asyncio
    async def test_get_hitl_stats(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting HITL queue statistics."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/hitl/stats",
            json={
                "success": True,
                "data": {
                    "total_pending": 15,
                    "high_priority": 8,
                    "critical_priority": 3,
                    "oldest_pending_hours": 4.5,
                },
            },
        )

        result = await client.get_hitl_stats()
        assert isinstance(result, HITLStats)
        assert result.total_pending == 15
        assert result.high_priority == 8
        assert result.critical_priority == 3
        assert result.oldest_pending_hours == 4.5

    @pytest.mark.asyncio
    async def test_get_hitl_stats_empty_queue(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test getting HITL stats when queue is empty."""
        httpx_mock.add_response(
            url="https://test.axonflow.com/api/v1/hitl/stats",
            json={
                "success": True,
                "data": {
                    "total_pending": 0,
                    "high_priority": 0,
                    "critical_priority": 0,
                    "oldest_pending_hours": None,
                },
            },
        )

        result = await client.get_hitl_stats()
        assert result.total_pending == 0
        assert result.high_priority == 0
        assert result.critical_priority == 0
        assert result.oldest_pending_hours is None


# =========================================================================
# Sync Wrapper Tests
# =========================================================================


class TestHITLSyncWrappers:
    """Test sync wrappers for all HITL methods."""

    def test_sync_list_hitl_queue(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync list_hitl_queue wrapper."""
        httpx_mock.add_response(
            json={
                "success": True,
                "data": [
                    {
                        "request_id": "hitl-req-001",
                        "org_id": "org-1",
                        "tenant_id": "tenant-1",
                        "client_id": "client-1",
                        "original_query": "What is the CEO salary?",
                        "request_type": "chat",
                        "triggered_policy_id": "pol-pii-001",
                        "triggered_policy_name": "PII Salary Detection",
                        "trigger_reason": "Sensitive data query",
                        "severity": "high",
                        "status": "pending",
                        "expires_at": "2026-02-13T10:00:00Z",
                        "created_at": "2026-02-12T10:00:00Z",
                        "updated_at": "2026-02-12T10:00:00Z",
                    },
                ],
                "meta": {"total": 1, "limit": 50, "offset": 0},
            },
        )

        result = sync_client.list_hitl_queue()
        assert isinstance(result, HITLQueueListResponse)
        assert result.total == 1
        assert len(result.items) == 1

    def test_sync_list_hitl_queue_with_opts(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync list_hitl_queue wrapper with filter options."""
        httpx_mock.add_response(
            json={
                "success": True,
                "data": [],
                "meta": {"total": 0, "limit": 5, "offset": 0},
            },
        )

        opts = HITLQueueListOptions(status="pending", limit=5)
        result = sync_client.list_hitl_queue(opts)
        assert result.total == 0
        assert result.items == []

    def test_sync_get_hitl_request(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync get_hitl_request wrapper."""
        httpx_mock.add_response(
            json={
                "success": True,
                "data": {
                    "request_id": "hitl-req-001",
                    "org_id": "org-1",
                    "tenant_id": "tenant-1",
                    "client_id": "client-1",
                    "original_query": "What is the CEO salary?",
                    "request_type": "chat",
                    "triggered_policy_id": "pol-pii-001",
                    "triggered_policy_name": "PII Salary Detection",
                    "trigger_reason": "Sensitive data query",
                    "severity": "high",
                    "status": "pending",
                    "expires_at": "2026-02-13T10:00:00Z",
                    "created_at": "2026-02-12T10:00:00Z",
                    "updated_at": "2026-02-12T10:00:00Z",
                },
            },
        )

        result = sync_client.get_hitl_request("hitl-req-001")
        assert isinstance(result, HITLApprovalRequest)
        assert result.request_id == "hitl-req-001"

    def test_sync_approve_hitl_request(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync approve_hitl_request wrapper."""
        httpx_mock.add_response(
            json={"success": True, "data": None},
        )

        review = HITLReviewInput(
            reviewer_id="admin-1",
            reviewer_email="admin@company.com",
            comment="Approved",
        )
        # Should not raise
        sync_client.approve_hitl_request("hitl-req-001", review)

    def test_sync_reject_hitl_request(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync reject_hitl_request wrapper."""
        httpx_mock.add_response(
            json={"success": True, "data": None},
        )

        review = HITLReviewInput(
            reviewer_id="admin-1",
            reviewer_email="admin@company.com",
            comment="Rejected - unsafe query",
        )
        # Should not raise
        sync_client.reject_hitl_request("hitl-req-001", review)

    def test_sync_get_hitl_stats(
        self,
        sync_client: SyncAxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test sync get_hitl_stats wrapper."""
        httpx_mock.add_response(
            json={
                "success": True,
                "data": {
                    "total_pending": 5,
                    "high_priority": 2,
                    "critical_priority": 1,
                    "oldest_pending_hours": 2.0,
                },
            },
        )

        result = sync_client.get_hitl_stats()
        assert isinstance(result, HITLStats)
        assert result.total_pending == 5
        assert result.critical_priority == 1


# =========================================================================
# Type Model Tests
# =========================================================================


class TestHITLTypeModels:
    """Test that all HITL types can be instantiated and validated."""

    def test_hitl_approval_request(self) -> None:
        """Test HITLApprovalRequest model."""
        req = HITLApprovalRequest(
            request_id="hitl-req-1",
            org_id="org-1",
            tenant_id="tenant-1",
            client_id="client-1",
            original_query="Test query",
            request_type="chat",
            triggered_policy_id="pol-1",
            triggered_policy_name="Test Policy",
            trigger_reason="Test reason",
            severity="high",
            status="pending",
            expires_at="2026-02-13T10:00:00Z",
            created_at="2026-02-12T10:00:00Z",
            updated_at="2026-02-12T10:00:00Z",
        )
        assert req.request_id == "hitl-req-1"
        assert req.severity == "high"
        assert req.user_id is None
        assert req.request_context is None
        assert req.eu_ai_act_article is None
        assert req.compliance_framework is None
        assert req.risk_classification is None
        assert req.reviewer_id is None
        assert req.reviewer_email is None
        assert req.review_comment is None
        assert req.reviewed_at is None

    def test_hitl_approval_request_full(self) -> None:
        """Test HITLApprovalRequest model with all optional fields."""
        req = HITLApprovalRequest(
            request_id="hitl-req-2",
            org_id="org-1",
            tenant_id="tenant-1",
            client_id="client-1",
            user_id="user-1",
            original_query="Show patient data",
            request_type="chat",
            request_context={"session": "abc"},
            triggered_policy_id="pol-hipaa-1",
            triggered_policy_name="HIPAA PHI",
            trigger_reason="PHI access",
            severity="critical",
            eu_ai_act_article="Article 14",
            compliance_framework="HIPAA",
            risk_classification="high-risk",
            status="approved",
            reviewer_id="admin-1",
            reviewer_email="admin@hospital.com",
            review_comment="OK",
            reviewed_at="2026-02-12T12:00:00Z",
            expires_at="2026-02-13T10:00:00Z",
            created_at="2026-02-12T10:00:00Z",
            updated_at="2026-02-12T12:00:00Z",
        )
        assert req.user_id == "user-1"
        assert req.request_context == {"session": "abc"}
        assert req.eu_ai_act_article == "Article 14"
        assert req.reviewer_id == "admin-1"

    def test_hitl_queue_list_options_defaults(self) -> None:
        """Test HITLQueueListOptions with defaults."""
        opts = HITLQueueListOptions()
        assert opts.status is None
        assert opts.severity is None
        assert opts.limit is None
        assert opts.offset is None

    def test_hitl_queue_list_response_defaults(self) -> None:
        """Test HITLQueueListResponse with defaults."""
        resp = HITLQueueListResponse()
        assert resp.items == []
        assert resp.total == 0
        assert resp.has_more is False

    def test_hitl_review_input(self) -> None:
        """Test HITLReviewInput model."""
        review = HITLReviewInput(
            reviewer_id="admin-1",
            reviewer_email="admin@company.com",
            reviewer_role="compliance_officer",
            comment="Approved after review",
        )
        assert review.reviewer_id == "admin-1"
        assert review.reviewer_email == "admin@company.com"
        assert review.reviewer_role == "compliance_officer"
        assert review.comment == "Approved after review"

    def test_hitl_review_input_minimal(self) -> None:
        """Test HITLReviewInput model with only required fields."""
        review = HITLReviewInput(
            reviewer_id="admin-1",
            reviewer_email="admin@company.com",
        )
        assert review.reviewer_role is None
        assert review.comment is None

    def test_hitl_stats(self) -> None:
        """Test HITLStats model."""
        stats = HITLStats(
            total_pending=10,
            high_priority=5,
            critical_priority=2,
            oldest_pending_hours=3.5,
        )
        assert stats.total_pending == 10
        assert stats.high_priority == 5
        assert stats.critical_priority == 2
        assert stats.oldest_pending_hours == 3.5

    def test_hitl_stats_defaults(self) -> None:
        """Test HITLStats model with defaults."""
        stats = HITLStats()
        assert stats.total_pending == 0
        assert stats.high_priority == 0
        assert stats.critical_priority == 0
        assert stats.oldest_pending_hours is None
