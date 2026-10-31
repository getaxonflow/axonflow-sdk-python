"""MCP input request wire fields used when retrying an approved hold."""

from __future__ import annotations

from axonflow.types import MCPCheckInputRequest


def test_approval_id_is_optional_and_serializes_for_exact_retry() -> None:
    initial = MCPCheckInputRequest(connector_type="postgres", statement="select 1")
    assert "approval_id" not in initial.model_dump(exclude_none=True)

    approval_id = "00000000-0000-4000-8000-000000000001"
    retry = initial.model_copy(update={"approval_id": approval_id})
    wire = retry.model_dump(exclude_none=True)
    assert wire["approval_id"] == approval_id
    assert wire["connector_type"] == "postgres"
    assert wire["statement"] == "select 1"

    round_trip = MCPCheckInputRequest.model_validate_json(retry.model_dump_json())
    assert round_trip.approval_id == approval_id
    assert round_trip.model_dump(exclude_none=True) == wire
