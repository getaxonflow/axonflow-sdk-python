"""Unit tests for WCP retry_context + idempotency_key (#1673 Phase 1 + 2).

Mirrors the six shapes from §6.8 of WCP_RETRY_IDEMPOTENCY_WIRE_CONTRACT.md.
"""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow, IdempotencyKeyMismatchError
from axonflow.workflow import (
    MarkStepCompletedRequest,
    PriorCompletionStatus,
    StepGateRequest,
    StepType,
)


GATE_URL = "https://test.axonflow.com/api/v1/workflows/wf_1/steps/step_1/gate"
COMPLETE_URL = "https://test.axonflow.com/api/v1/workflows/wf_1/steps/step_1/complete"


# --- Test a: first-call shape ------------------------------------------------


@pytest.mark.asyncio
async def test_first_call_retry_context_shape(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    now = "2026-04-21T15:30:45.123Z"
    httpx_mock.add_response(
        url=GATE_URL,
        json={
            "decision": "allow",
            "step_id": "step_1",
            "cached": False,
            "decision_source": "fresh",
            "retry_context": {
                "gate_count": 1,
                "completion_count": 0,
                "prior_completion_status": "none",
                "prior_output_available": False,
                "prior_output": None,
                "prior_completion_at": None,
                "first_attempt_at": now,
                "last_attempt_at": now,
                "last_decision": "allow",
                "idempotency_key": "",
            },
        },
    )

    gate = await client.step_gate(
        "wf_1", "step_1", StepGateRequest(step_type=StepType.LLM_CALL)
    )
    rc = gate.retry_context
    assert rc is not None
    assert rc.gate_count == 1
    assert rc.completion_count == 0
    assert rc.prior_completion_status is PriorCompletionStatus.NONE
    assert rc.prior_output_available is False
    assert rc.prior_output is None
    assert rc.prior_completion_at is None
    assert rc.first_attempt_at == rc.last_attempt_at
    assert rc.last_decision.value == gate.decision.value
    assert rc.idempotency_key == ""


# --- Test b: second-call after completion ------------------------------------


@pytest.mark.asyncio
async def test_second_call_after_completion(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url=GATE_URL,
        json={
            "decision": "allow",
            "step_id": "step_1",
            "retry_context": {
                "gate_count": 2,
                "completion_count": 1,
                "prior_completion_status": "completed",
                "prior_output_available": True,
                "prior_output": None,
                "prior_completion_at": "2026-04-21T15:30:30.000Z",
                "first_attempt_at": "2026-04-21T15:30:00.000Z",
                "last_attempt_at": "2026-04-21T15:31:00.000Z",
                "last_decision": "allow",
                "idempotency_key": "",
            },
        },
    )

    gate = await client.step_gate(
        "wf_1", "step_1", StepGateRequest(step_type=StepType.LLM_CALL)
    )
    rc = gate.retry_context
    assert rc is not None
    assert rc.gate_count == 2
    assert rc.completion_count == 1
    assert rc.prior_completion_status is PriorCompletionStatus.COMPLETED
    assert rc.prior_output_available is True
    assert rc.prior_completion_at is not None
    assert rc.first_attempt_at != rc.last_attempt_at


# --- Test c: second-call without completion ----------------------------------


@pytest.mark.asyncio
async def test_second_call_without_completion(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url=GATE_URL,
        json={
            "decision": "allow",
            "step_id": "step_1",
            "retry_context": {
                "gate_count": 2,
                "completion_count": 0,
                "prior_completion_status": "gated_not_completed",
                "prior_output_available": False,
                "prior_output": None,
                "prior_completion_at": None,
                "first_attempt_at": "2026-04-21T15:30:00.000Z",
                "last_attempt_at": "2026-04-21T15:31:00.000Z",
                "last_decision": "allow",
                "idempotency_key": "",
            },
        },
    )

    gate = await client.step_gate(
        "wf_1", "step_1", StepGateRequest(step_type=StepType.LLM_CALL)
    )
    rc = gate.retry_context
    assert rc is not None
    assert rc.gate_count == 2
    assert rc.completion_count == 0
    assert rc.prior_completion_status is PriorCompletionStatus.GATED_NOT_COMPLETED
    assert rc.prior_output_available is False
    assert rc.prior_completion_at is None


# --- Test d: include_prior_output=True ---------------------------------------


@pytest.mark.asyncio
async def test_include_prior_output_sends_query_param_and_populates_output(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    prior_output = {"result": "ok", "score": 0.92}
    httpx_mock.add_response(
        url=GATE_URL + "?include_prior_output=true",
        json={
            "decision": "allow",
            "step_id": "step_1",
            "retry_context": {
                "gate_count": 2,
                "completion_count": 1,
                "prior_completion_status": "completed",
                "prior_output_available": True,
                "prior_output": prior_output,
                "prior_completion_at": "2026-04-21T15:30:30.000Z",
                "first_attempt_at": "2026-04-21T15:30:00.000Z",
                "last_attempt_at": "2026-04-21T15:31:00.000Z",
                "last_decision": "allow",
                "idempotency_key": "",
            },
        },
    )

    gate = await client.step_gate(
        "wf_1",
        "step_1",
        StepGateRequest(step_type=StepType.LLM_CALL),
        include_prior_output=True,
    )
    assert gate.retry_context is not None
    assert gate.retry_context.prior_output == prior_output

    # Confirm the query param was actually sent
    requests = httpx_mock.get_requests()
    assert any(
        "include_prior_output=true" in str(r.url) for r in requests
    ), f"Expected include_prior_output=true on query string, got {[str(r.url) for r in requests]}"


# --- Test e: idempotency_key round-trip --------------------------------------


@pytest.mark.asyncio
async def test_idempotency_key_round_trip(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    key = "payment:wire:acct4471:invoice-7721"
    httpx_mock.add_response(
        url=GATE_URL,
        json={
            "decision": "allow",
            "step_id": "step_1",
            "retry_context": {
                "gate_count": 1,
                "completion_count": 0,
                "prior_completion_status": "none",
                "prior_output_available": False,
                "prior_output": None,
                "prior_completion_at": None,
                "first_attempt_at": "2026-04-21T15:30:00.000Z",
                "last_attempt_at": "2026-04-21T15:30:00.000Z",
                "last_decision": "allow",
                "idempotency_key": key,
            },
        },
    )
    httpx_mock.add_response(
        url=COMPLETE_URL,
        status_code=204,
    )

    gate = await client.step_gate(
        "wf_1",
        "step_1",
        StepGateRequest(step_type=StepType.LLM_CALL, idempotency_key=key),
    )
    assert gate.retry_context is not None
    assert gate.retry_context.idempotency_key == key

    await client.mark_step_completed(
        "wf_1",
        "step_1",
        MarkStepCompletedRequest(output={"ok": True}, idempotency_key=key),
    )

    requests = httpx_mock.get_requests()
    import json as _json

    gate_body = _json.loads(requests[0].content)
    complete_body = _json.loads(requests[1].content)
    assert gate_body["idempotency_key"] == key
    assert complete_body["idempotency_key"] == key


# --- Test f: 409 IDEMPOTENCY_KEY_MISMATCH ------------------------------------


@pytest.mark.asyncio
async def test_mark_step_completed_409_raises_typed_error(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url=COMPLETE_URL,
        status_code=409,
        json={
            "error": {
                "code": "IDEMPOTENCY_KEY_MISMATCH",
                "message": "idempotency_key on complete does not match the key recorded on gate",
                "details": {
                    "workflow_id": "wf_1",
                    "step_id": "step_1",
                    "expected_idempotency_key": "a",
                    "received_idempotency_key": "b",
                },
            }
        },
    )

    with pytest.raises(IdempotencyKeyMismatchError) as excinfo:
        await client.mark_step_completed(
            "wf_1",
            "step_1",
            MarkStepCompletedRequest(idempotency_key="b"),
        )

    err = excinfo.value
    assert err.workflow_id == "wf_1"
    assert err.step_id == "step_1"
    assert err.expected_idempotency_key == "a"
    assert err.received_idempotency_key == "b"


@pytest.mark.asyncio
async def test_step_gate_409_raises_typed_error(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url=GATE_URL,
        status_code=409,
        json={
            "error": {
                "code": "IDEMPOTENCY_KEY_MISMATCH",
                "message": "mismatch",
                "details": {
                    "workflow_id": "wf_1",
                    "step_id": "step_1",
                    "expected_idempotency_key": "a",
                    "received_idempotency_key": "b",
                },
            }
        },
    )

    with pytest.raises(IdempotencyKeyMismatchError) as excinfo:
        await client.step_gate(
            "wf_1",
            "step_1",
            StepGateRequest(step_type=StepType.LLM_CALL, idempotency_key="b"),
        )
    assert excinfo.value.expected_idempotency_key == "a"
    assert excinfo.value.received_idempotency_key == "b"
