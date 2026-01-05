"""Tests for audit log read methods."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow
from axonflow.exceptions import AxonFlowError
from axonflow.types import (
    AuditLogEntry,
    AuditQueryOptions,
    AuditSearchRequest,
    AuditSearchResponse,
)


@pytest.fixture
def mock_audit_entries() -> list[dict[str, Any]]:
    """Sample audit log entries."""
    return [
        {
            "id": "audit-1",
            "request_id": "req-1",
            "timestamp": "2026-01-05T10:00:00Z",
            "user_email": "user@example.com",
            "client_id": "client-1",
            "tenant_id": "tenant-1",
            "request_type": "llm_chat",
            "query_summary": "Test query",
            "success": True,
            "blocked": False,
            "risk_score": 0.1,
            "provider": "openai",
            "model": "gpt-4",
            "tokens_used": 150,
            "latency_ms": 250,
            "policy_violations": [],
            "metadata": {},
        },
        {
            "id": "audit-2",
            "request_id": "req-2",
            "timestamp": "2026-01-05T11:00:00Z",
            "user_email": "user@example.com",
            "client_id": "client-1",
            "tenant_id": "tenant-1",
            "request_type": "llm_chat",
            "query_summary": "Blocked query",
            "success": False,
            "blocked": True,
            "risk_score": 0.9,
            "provider": "openai",
            "model": "gpt-4",
            "tokens_used": 0,
            "latency_ms": 50,
            "policy_violations": ["policy-1"],
            "metadata": {"reason": "pii_detected"},
        },
    ]


class TestSearchAuditLogs:
    """Tests for search_audit_logs method."""

    @pytest.mark.asyncio
    async def test_search_with_all_filters(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
        mock_audit_entries: list[dict[str, Any]],
    ) -> None:
        """Test search with all filter parameters."""
        httpx_mock.add_response(json=mock_audit_entries)

        start_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end_time = datetime(2026, 1, 5, tzinfo=timezone.utc)
        request = AuditSearchRequest(
            user_email="user@example.com",
            client_id="client-1",
            start_time=start_time,
            end_time=end_time,
            request_type="llm_chat",
            limit=50,
            offset=10,
        )

        result = await client.search_audit_logs(request)

        # Verify response parsing
        assert len(result.entries) == 2
        assert result.entries[0].id == "audit-1"
        assert result.entries[1].blocked is True

    @pytest.mark.asyncio
    async def test_search_with_defaults(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
        mock_audit_entries: list[dict[str, Any]],
    ) -> None:
        """Test search with no parameters uses defaults."""
        httpx_mock.add_response(json=mock_audit_entries)

        result = await client.search_audit_logs()

        assert isinstance(result, AuditSearchResponse)
        assert len(result.entries) == 2
        assert result.limit == 100  # Default limit

    @pytest.mark.asyncio
    async def test_search_with_none_request(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
        mock_audit_entries: list[dict[str, Any]],
    ) -> None:
        """Test search with explicit None request."""
        httpx_mock.add_response(json=mock_audit_entries)

        result = await client.search_audit_logs(None)

        assert isinstance(result, AuditSearchResponse)
        assert result.limit == 100

    @pytest.mark.asyncio
    async def test_search_handles_wrapped_response(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test search handles wrapped response format."""
        httpx_mock.add_response(
            json={
                "entries": [
                    {
                        "id": "audit-1",
                        "timestamp": "2026-01-05T10:00:00Z",
                    }
                ],
                "total": 100,
                "limit": 10,
                "offset": 0,
            }
        )

        result = await client.search_audit_logs()

        assert result.total == 100
        assert result.limit == 10
        assert len(result.entries) == 1

    @pytest.mark.asyncio
    async def test_search_empty_result(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test search with no results."""
        httpx_mock.add_response(json=[])

        result = await client.search_audit_logs()

        assert len(result.entries) == 0
        assert result.total == 0

    @pytest.mark.asyncio
    async def test_search_400_error(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test search handles 400 error."""
        httpx_mock.add_response(
            status_code=400,
            json={"error": "invalid request"},
        )

        with pytest.raises(AxonFlowError):
            await client.search_audit_logs()

    @pytest.mark.asyncio
    async def test_search_500_error(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test search handles 500 error."""
        httpx_mock.add_response(
            status_code=500,
            json={"error": "internal server error"},
        )

        with pytest.raises(AxonFlowError):
            await client.search_audit_logs()


class TestGetAuditLogsByTenant:
    """Tests for get_audit_logs_by_tenant method."""

    @pytest.mark.asyncio
    async def test_get_with_defaults(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
        mock_audit_entries: list[dict[str, Any]],
    ) -> None:
        """Test get tenant logs with default options."""
        httpx_mock.add_response(json=mock_audit_entries)

        result = await client.get_audit_logs_by_tenant("tenant-abc")

        assert len(result.entries) == 2
        assert result.limit == 50  # Default limit

    @pytest.mark.asyncio
    async def test_get_with_custom_options(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
        mock_audit_entries: list[dict[str, Any]],
    ) -> None:
        """Test get tenant logs with custom options."""
        httpx_mock.add_response(json=mock_audit_entries)

        options = AuditQueryOptions(limit=100, offset=25)
        result = await client.get_audit_logs_by_tenant("tenant-abc", options)

        assert result.limit == 100
        assert result.offset == 25

    @pytest.mark.asyncio
    async def test_get_empty_tenant_id_raises(
        self,
        client: AxonFlow,
    ) -> None:
        """Test empty tenant ID raises ValueError."""
        with pytest.raises(ValueError, match="tenant_id is required"):
            await client.get_audit_logs_by_tenant("")

    @pytest.mark.asyncio
    async def test_get_handles_wrapped_response(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test get handles wrapped response format."""
        httpx_mock.add_response(
            json={
                "entries": [
                    {
                        "id": "audit-1",
                        "timestamp": "2026-01-05T10:00:00Z",
                        "tenant_id": "tenant-abc",
                    }
                ],
                "total": 50,
                "limit": 50,
                "offset": 0,
            }
        )

        result = await client.get_audit_logs_by_tenant("tenant-abc")

        assert result.total == 50
        assert len(result.entries) == 1

    @pytest.mark.asyncio
    async def test_get_empty_result(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test get with no results."""
        httpx_mock.add_response(json=[])

        result = await client.get_audit_logs_by_tenant("tenant-abc")

        assert len(result.entries) == 0

    @pytest.mark.asyncio
    async def test_get_404_error(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test get handles 404 error."""
        httpx_mock.add_response(
            status_code=404,
            json={"error": "tenant not found"},
        )

        with pytest.raises(AxonFlowError):
            await client.get_audit_logs_by_tenant("nonexistent")

    @pytest.mark.asyncio
    async def test_get_403_error(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test get handles 403 error."""
        httpx_mock.add_response(
            status_code=403,
            json={"error": "not authorized for this tenant"},
        )

        with pytest.raises(AxonFlowError):
            await client.get_audit_logs_by_tenant("other-tenant")


class TestAuditTypes:
    """Tests for audit type validation."""

    def test_audit_log_entry_parsing(self) -> None:
        """Test AuditLogEntry validates correctly."""
        entry = AuditLogEntry.model_validate(
            {
                "id": "audit-123",
                "timestamp": "2026-01-05T10:30:00Z",
                "user_email": "user@example.com",
                "risk_score": 0.25,
                "tokens_used": 500,
                "policy_violations": ["pol-1", "pol-2"],
            }
        )

        assert entry.id == "audit-123"
        assert entry.user_email == "user@example.com"
        assert entry.risk_score == 0.25
        assert entry.tokens_used == 500
        assert len(entry.policy_violations) == 2

    def test_audit_log_entry_defaults(self) -> None:
        """Test AuditLogEntry default values."""
        entry = AuditLogEntry.model_validate(
            {
                "id": "audit-123",
                "timestamp": "2026-01-05T10:30:00Z",
            }
        )

        assert entry.request_id == ""
        assert entry.user_email == ""
        assert entry.success is True
        assert entry.blocked is False
        assert entry.risk_score == 0.0
        assert entry.tokens_used == 0
        assert entry.policy_violations == []
        assert entry.metadata == {}

    def test_audit_search_request_validation(self) -> None:
        """Test AuditSearchRequest validation."""
        # Valid request
        request = AuditSearchRequest(
            user_email="test@example.com",
            limit=50,
        )
        assert request.limit == 50

        # Default limit
        request = AuditSearchRequest()
        assert request.limit == 100

    def test_audit_search_request_limit_bounds(self) -> None:
        """Test AuditSearchRequest limit validation."""
        # Max limit
        request = AuditSearchRequest(limit=1000)
        assert request.limit == 1000

        # Over max should fail
        with pytest.raises(ValueError):
            AuditSearchRequest(limit=1001)

        # Under min should fail
        with pytest.raises(ValueError):
            AuditSearchRequest(limit=0)

    def test_audit_query_options_defaults(self) -> None:
        """Test AuditQueryOptions default values."""
        options = AuditQueryOptions()
        assert options.limit == 50
        assert options.offset == 0

    def test_audit_search_response_structure(self) -> None:
        """Test AuditSearchResponse structure."""
        response = AuditSearchResponse(
            entries=[
                AuditLogEntry(id="1", timestamp=datetime.now(tz=timezone.utc)),
                AuditLogEntry(id="2", timestamp=datetime.now(tz=timezone.utc)),
            ],
            total=100,
            limit=10,
            offset=0,
        )

        assert len(response.entries) == 2
        assert response.total == 100
        assert response.limit == 10
