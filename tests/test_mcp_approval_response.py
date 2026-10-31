"""Typed MCP held and spent approval responses from the pinned Agent contract."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow
from axonflow.types import DecisionPendingApproval, MCPCheckInputResponse

APPROVAL_ID = "00000000-0000-4000-8000-000000000001"
INPUT_URL = "https://test.axonflow.com/api/v1/mcp/check-input"


@pytest.mark.asyncio
async def test_held_refusal_to_spent_retry_round_trip(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url=INPUT_URL,
        status_code=403,
        json={
            "allowed": False,
            "block_reason": "approval_required",
            "pending_approval": {
                "approval_id": APPROVAL_ID,
                "status": "pending",
                "plane": "mcp:request",
                "expires_at": "2030-01-01T00:00:00Z",
                "retry": {
                    "header": "X-Axonflow-Approval-Id",
                    "argument": "approval_id",
                },
            },
        },
    )
    httpx_mock.add_response(
        url=INPUT_URL,
        json={"allowed": True, "approval_id": APPROVAL_ID},
    )

    held = await client.mcp_check_input("postgres", "SELECT 1", operation="query")
    assert not held.allowed
    assert held.approval_id is None
    assert isinstance(held.pending_approval, DecisionPendingApproval)
    assert held.pending_approval.approval_id == APPROVAL_ID
    assert held.pending_approval.plane == "mcp:request"
    assert held.pending_approval.retry.argument == "approval_id"
    assert held.pending_approval.expires_at == datetime(2030, 1, 1, tzinfo=timezone.utc)

    # The fixture represents the separate human approval before this exact retry.
    spent = await client.mcp_check_input(
        "postgres",
        "SELECT 1",
        operation="query",
        approval_id=held.pending_approval.approval_id,
    )
    assert spent.allowed
    assert spent.pending_approval is None
    assert spent.approval_id == APPROVAL_ID

    first, second = httpx_mock.get_requests()
    initial_body = json.loads(first.content)
    retry_body = json.loads(second.content)
    assert "approval_id" not in initial_body
    assert retry_body.pop("approval_id") == APPROVAL_ID
    assert retry_body == initial_body
    assert "X-Axonflow-Approval-Id" not in second.headers


def test_legacy_response_defaults_and_malformed_hold_is_refused() -> None:
    legacy = MCPCheckInputResponse.model_validate({"allowed": True})
    assert legacy.pending_approval is None
    assert legacy.approval_id is None

    with pytest.raises(ValidationError):
        MCPCheckInputResponse.model_validate(
            {
                "allowed": False,
                "pending_approval": {
                    "approval_id": APPROVAL_ID,
                    "status": "pending",
                    "plane": "mcp:request",
                    # The pinned PendingApproval contract requires retry.
                },
            }
        )
