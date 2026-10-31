"""The MCP approval retry ID stays on the one check-input request body."""

from __future__ import annotations

import json
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow
from axonflow.exceptions import ConnectorError

APPROVAL_ID = "00000000-0000-4000-8000-000000000001"
OTHER_ID = "00000000-0000-4000-8000-000000000002"
INPUT_URL = "https://test.axonflow.com/api/v1/mcp/check-input"


def body(request: Any) -> dict[str, Any]:
    return json.loads(request.content)


@pytest.mark.asyncio
async def test_async_retry_id_is_body_only_and_does_not_leak(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(url=INPUT_URL, json={"allowed": True}, is_reusable=True)

    await client.mcp_check_input("postgres", "SELECT 1", "query", {"x": 1}, approval_id=APPROVAL_ID)
    await client.mcp_check_input("postgres", "SELECT 1", "query", {"x": 1})

    first, second = httpx_mock.get_requests()
    assert body(first)["approval_id"] == APPROVAL_ID
    assert "X-Axonflow-Approval-Id" not in first.headers
    assert "approval_id" not in body(second)
    assert body(first) | {"approval_id": None} == body(second) | {"approval_id": None}
    assert "X-Axonflow-Approval-Id" not in client._http_client.headers


@pytest.mark.asyncio
async def test_async_alias_forwards_retry_id(client: AxonFlow, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=INPUT_URL, json={"allowed": True})
    await client.check_tool_input("postgres", "SELECT 1", approval_id=APPROVAL_ID)
    assert body(httpx_mock.get_requests()[0])["approval_id"] == APPROVAL_ID


def test_sync_methods_forward_retry_id(sync_client: Any, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=INPUT_URL, json={"allowed": True}, is_reusable=True)
    sync_client.mcp_check_input("postgres", "SELECT 1", approval_id=APPROVAL_ID)
    sync_client.check_tool_input("postgres", "SELECT 1", approval_id=OTHER_ID)
    first, second = httpx_mock.get_requests()
    assert body(first)["approval_id"] == APPROVAL_ID
    assert body(second)["approval_id"] == OTHER_ID


@pytest.mark.asyncio
async def test_conflicting_header_is_left_for_server_refusal(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url=INPUT_URL,
        status_code=403,
        json={"allowed": False, "block_reason": "approval_not_found"},
    )
    result = await client.mcp_check_input(
        "postgres",
        "SELECT 1",
        approval_id=APPROVAL_ID,
        extra_headers={"X-Axonflow-Approval-Id": OTHER_ID},
    )
    request = httpx_mock.get_requests()[0]
    assert body(request)["approval_id"] == APPROVAL_ID
    assert request.headers["X-Axonflow-Approval-Id"] == OTHER_ID
    assert not result.allowed
    assert result.block_reason == "approval_not_found"


@pytest.mark.asyncio
async def test_other_http_error_remains_connector_error(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(url=INPUT_URL, status_code=502, json={"error": "synthetic outage"})
    with pytest.raises(ConnectorError, match="synthetic outage"):
        await client.mcp_check_input("postgres", "SELECT 1", approval_id=APPROVAL_ID)
    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert body(requests[0])["approval_id"] == APPROVAL_ID
