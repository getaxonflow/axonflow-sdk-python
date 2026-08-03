"""Tests for audit log read methods."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
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
    async def test_search_handles_null_entries(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test wrapped response with null entries is normalized to empty list."""
        httpx_mock.add_response(
            json={
                "entries": None,
                "total": 0,
                "limit": 10,
                "offset": 0,
            }
        )

        result = await client.search_audit_logs()

        assert result.entries == []
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
    async def test_get_handles_null_entries(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """Test wrapped response with null entries is normalized to empty list."""
        httpx_mock.add_response(
            json={
                "entries": None,
                "total": 0,
                "limit": 50,
                "offset": 0,
            }
        )

        result = await client.get_audit_logs_by_tenant("tenant-abc")

        assert result.entries == []
        assert result.total == 0

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


# Real capture: captured 2026-08-03 from an isolated community v9.13.0 stack
# (getaxonflow/axonflow tag v9.13.0 = df027c788), session 3254. Raw
# POST /api/v1/audit/search response through the agent proxy, verbatim.
REAL_CAPTURE_PATH = Path(__file__).parent / "fixtures" / "audit_search_live_v9130.json"


class TestAuditRealWireShape:
    """#3254: the audit read model against the REAL 9.x wire shape.

    The seven fiction fields (query_summary, success, blocked, risk_score,
    latency_ms, policy_violations, metadata) have never been served on the
    9.x line; the real wire carries policy_decision, policy_details and
    response_time_ms. These tests pin the additive interim: real fields
    populate, fiction fields stay at defaults, nothing throws.
    """

    def test_real_capture_parses_with_new_fields_populated(self) -> None:
        """Deserialize the REAL captured payload, unmodified.

        Fixture provenance: captured 2026-08-03 from an isolated community
        v9.13.0 stack, session 3254 (see REAL_CAPTURE_PATH comment).
        """
        payload = json.loads(REAL_CAPTURE_PATH.read_text())
        response = AuditSearchResponse.model_validate(payload)

        assert response.total == 2
        assert len(response.entries) == 2

        error_entry = response.entries[0]
        allowed_entry = response.entries[1]

        # New real-wire fields are populated from the capture.
        assert error_entry.policy_decision == "error"
        assert error_entry.policy_details["tool_name"] == "s3254_blocked_probe"
        expected_error = "blocked by policy sys_sqli_or_true"
        assert error_entry.policy_details["error_message"] == expected_error
        assert allowed_entry.policy_decision == "allowed"
        assert allowed_entry.policy_details["success"] is True
        assert allowed_entry.response_time_ms == 0

        # The verdict set is OPEN: "error" is not in the code-documented
        # allowed/blocked/redacted set and must still parse as a plain string.
        assert isinstance(error_entry.policy_decision, str)

        # The seven fiction fields are ABSENT from the real wire and must
        # stay at their defaults, silently.
        for entry in response.entries:
            assert entry.query_summary == ""
            assert entry.success is True
            assert entry.blocked is False
            assert entry.risk_score == 0.0
            assert entry.latency_ms == 0
            assert entry.policy_violations == []
            assert entry.metadata == {}

        # Real fields that were already modeled keep parsing.
        assert error_entry.request_type == "tool_call_audit"
        assert error_entry.tenant_id == "community"

    def test_old_server_payload_without_new_fields_defaults(self) -> None:
        """Old-server tolerance: a payload WITHOUT the three new fields
        parses and the new fields default (absence-tolerant contract).

        Hand-modified capture: the real 2026-08-03 session-3254 capture with
        policy_decision, policy_details and response_time_ms removed.
        """
        payload = json.loads(REAL_CAPTURE_PATH.read_text())
        for raw in payload["entries"]:
            del raw["policy_decision"]
            del raw["policy_details"]
            del raw["response_time_ms"]

        response = AuditSearchResponse.model_validate(payload)

        assert len(response.entries) == 2
        for entry in response.entries:
            assert entry.policy_decision == ""
            assert entry.policy_details == {}
            assert entry.response_time_ms == 0

    def test_both_fiction_and_real_fields_in_one_payload(self) -> None:
        """Fictional AND real fields together parse with no collision.

        Hand-modified capture: the real 2026-08-03 session-3254 capture with
        the seven fiction fields injected alongside the real ones.
        """
        payload = json.loads(REAL_CAPTURE_PATH.read_text())
        fiction = {
            "query_summary": "legacy summary",
            "success": False,
            "blocked": True,
            "risk_score": 0.75,
            "latency_ms": 1234,
            "policy_violations": ["legacy-policy-1"],
            "metadata": {"legacy": True},
        }
        for raw in payload["entries"]:
            raw.update(fiction)

        response = AuditSearchResponse.model_validate(payload)

        for entry in response.entries:
            # Fiction fields parse when present (kept for compatibility).
            assert entry.query_summary == "legacy summary"
            assert entry.success is False
            assert entry.blocked is True
            assert entry.risk_score == 0.75
            assert entry.latency_ms == 1234
            assert entry.policy_violations == ["legacy-policy-1"]
            assert entry.metadata == {"legacy": True}
        # Real fields are untouched by the fiction fields' presence.
        assert response.entries[0].policy_decision == "error"
        assert response.entries[1].policy_decision == "allowed"
        assert response.entries[0].policy_details["tool_name"] == "s3254_blocked_probe"

    def test_null_policy_details_parses(self) -> None:
        """The orchestrator marshals a nil Go map/slice as JSON null -
        observed live on real /api/v1/audit/search rows (session 3254,
        runtime-e2e/audit_real_wire_fields). Null-tolerant, not merely
        absence-tolerant.

        Hand-modified capture: the real 2026-08-03 session-3254 capture
        with policy_details/metadata/policy_violations set to null.
        """
        payload = json.loads(REAL_CAPTURE_PATH.read_text())
        for raw in payload["entries"]:
            raw["policy_details"] = None
            raw["metadata"] = None
            raw["policy_violations"] = None

        response = AuditSearchResponse.model_validate(payload)

        for entry in response.entries:
            assert entry.policy_details == {}
            assert entry.metadata == {}
            assert entry.policy_violations == []
        assert response.entries[0].policy_decision == "error"

    def test_search_request_action_field(self) -> None:
        """AuditSearchRequest.action is optional and defaults to None."""
        request = AuditSearchRequest()
        assert request.action is None

        request = AuditSearchRequest(action="blocked")
        assert request.action == "blocked"

    @pytest.mark.asyncio
    async def test_search_sends_action_filter(
        self,
        client: AxonFlow,
        httpx_mock: HTTPXMock,
    ) -> None:
        """The action filter is sent on the wire; request_type is still
        sent when set (deprecated but harmless, #3254)."""
        httpx_mock.add_response(json={"entries": [], "total": 0, "limit": 100, "offset": 0})

        await client.search_audit_logs(
            AuditSearchRequest(action="error", request_type="legacy_filter")
        )

        sent = httpx_mock.get_requests()[-1]
        body = json.loads(sent.content)
        assert body["action"] == "error"
        assert body["request_type"] == "legacy_filter"
