"""Unit tests for the Decision Mode PEP contract (ADR-056, epic #2563).

Covers decide → fulfill → forward: the decide() parsing, the fulfill_request()
fail-closed semantics, decide_and_fulfill(), and the pure helpers. The
load-bearing property under test is that the PEP NEVER redacts locally and
fails CLOSED on every unfulfillable condition — it can only discharge a
redact_pii obligation by round-tripping content through the engine.
"""

from __future__ import annotations

import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow
from axonflow.exceptions import AuthenticationError, ObligationNotFulfillableError
from axonflow.pep import (
    CONTENT_TYPE_TEXT,
    OBLIGATION_REDACT_PII,
    PHASE_REQUEST,
    PHASE_RESPONSE,
    VERDICT_ALLOW,
    _endpoint_path_matches,
    has_request_redaction,
)
from axonflow.types import (
    DecideRequest,
    DecideResponse,
    Obligation,
    ObligationFulfillment,
)

CHECK_INPUT_URL = "https://test.axonflow.com/api/v1/mcp/check-input"
DECIDE_URL = "https://test.axonflow.com/api/v1/decide"

# The exact obligation the real agent emits on /decide for a request carrying
# PII under a redact policy (verified live against an enterprise agent).
REDACT_OBLIGATION = {
    "type": "redact_pii",
    "fulfillment": {
        "endpoint": "/api/v1/mcp/check-input",
        "method": "POST",
        "phase": "request",
        "content_types": ["text/plain"],
    },
}


def _decide_allow(obligations: list[dict]) -> dict:
    return {
        "verdict": "allow",
        "decision_id": "dec-1",
        "trace_id": "04110a0b50577bbbdda23a00dcbaf6da",
        "obligations": obligations,
        "evaluated_policies": ["sys_pii_email"],
        "stage": "tool",
        "expires_at": "2026-06-09T05:05:06.801139966Z",
    }


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_has_request_redaction_true(self) -> None:
        obs = [Obligation.model_validate(REDACT_OBLIGATION)]
        assert has_request_redaction(obs) is True

    def test_has_request_redaction_false_when_response_phase(self) -> None:
        obs = [
            Obligation(
                type=OBLIGATION_REDACT_PII,
                fulfillment=ObligationFulfillment(
                    endpoint="/api/v1/mcp/check-output",
                    phase=PHASE_RESPONSE,
                ),
            )
        ]
        assert has_request_redaction(obs) is False

    def test_has_request_redaction_false_when_empty(self) -> None:
        assert has_request_redaction([]) is False

    @pytest.mark.parametrize(
        ("endpoint", "expected", "want"),
        [
            ("/api/v1/mcp/check-input", "/api/v1/mcp/check-input", True),
            ("https://pdp:8443/api/v1/mcp/check-input", "/api/v1/mcp/check-input", True),
            ("https://pdp/api/v1/mcp/check-input?x=1", "/api/v1/mcp/check-input", True),
            ("", "/api/v1/mcp/check-input", False),
            ("/api/v1/other", "/api/v1/mcp/check-input", False),
            ("https://evil.example.com/steal", "/api/v1/mcp/check-input", False),
        ],
    )
    def test_endpoint_path_matches(self, endpoint: str, expected: str, want: bool) -> None:
        assert _endpoint_path_matches(endpoint, expected) is want


# ---------------------------------------------------------------------------
# decide method
# ---------------------------------------------------------------------------


class TestDecide:
    @pytest.mark.asyncio
    async def test_decide_parses_obligations(self, client: AxonFlow, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=DECIDE_URL, json=_decide_allow([REDACT_OBLIGATION]))
        resp = await client.decide(
            DecideRequest(stage="tool", query="Email a@b.com", target={"type": "tool"})
        )
        assert isinstance(resp, DecideResponse)
        assert resp.verdict == VERDICT_ALLOW
        assert resp.trace_id == "04110a0b50577bbbdda23a00dcbaf6da"
        assert len(resp.obligations) == 1
        ob = resp.obligations[0]
        assert ob.type == OBLIGATION_REDACT_PII
        assert ob.fulfillment is not None
        assert ob.fulfillment.endpoint == "/api/v1/mcp/check-input"
        assert ob.fulfillment.phase == PHASE_REQUEST
        assert ob.fulfillment.content_types == [CONTENT_TYPE_TEXT]

    @pytest.mark.asyncio
    async def test_decide_empty_obligations_is_list(
        self, client: AxonFlow, httpx_mock: HTTPXMock
    ) -> None:
        body = _decide_allow([])
        del body["obligations"]  # platform always sends [], but be defensive
        httpx_mock.add_response(url=DECIDE_URL, json=body)
        resp = await client.decide(DecideRequest(stage="tool", query="hi"))
        assert resp.obligations == []

    @pytest.mark.asyncio
    async def test_decide_401_raises_auth_error(
        self, client: AxonFlow, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url=DECIDE_URL, status_code=401, json={"error": "unauthorized"})
        with pytest.raises(AuthenticationError):
            await client.decide(DecideRequest(stage="tool", query="hi"))

    @pytest.mark.asyncio
    async def test_decide_omits_none_fields_on_wire(
        self, client: AxonFlow, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url=DECIDE_URL, json=_decide_allow([]))
        await client.decide(DecideRequest(stage="tool", query="hi"))
        sent = httpx_mock.get_requests()[0]
        import json as _json

        body = _json.loads(sent.content)
        assert body["stage"] == "tool"
        assert "user_token" not in body  # None excluded
        assert "context" not in body


# ---------------------------------------------------------------------------
# fulfill_request() — the fail-closed core
# ---------------------------------------------------------------------------


class TestFulfillRequest:
    @pytest.mark.asyncio
    async def test_engine_redacts_and_forwards(
        self, client: AxonFlow, httpx_mock: HTTPXMock
    ) -> None:
        decision = DecideResponse.model_validate(_decide_allow([REDACT_OBLIGATION]))
        httpx_mock.add_response(
            url=CHECK_INPUT_URL,
            json={
                "allowed": True,
                "policies_evaluated": 1,
                "redacted": True,
                "redacted_statement": "Email jo****om",
                "redaction_evaluated": True,
            },
        )
        content, did_redact = await client.fulfill_request(decision, "Email john@x.com")
        assert content == "Email jo****om"
        assert did_redact is True
        # The PEP submitted the source content to the engine with text/plain.
        sent = httpx_mock.get_requests()[0]
        import json as _json

        body = _json.loads(sent.content)
        assert body["statement"] == "Email john@x.com"
        assert body["content_type"] == CONTENT_TYPE_TEXT

    @pytest.mark.asyncio
    async def test_no_obligations_passthrough(
        self, client: AxonFlow, httpx_mock: HTTPXMock
    ) -> None:
        decision = DecideResponse.model_validate(_decide_allow([]))
        content, did_redact = await client.fulfill_request(decision, "nothing to mask")
        assert content == "nothing to mask"
        assert did_redact is False
        # No engine call was made.
        assert httpx_mock.get_requests() == []

    @pytest.mark.asyncio
    async def test_engine_found_nothing_forwards_original(
        self, client: AxonFlow, httpx_mock: HTTPXMock
    ) -> None:
        decision = DecideResponse.model_validate(_decide_allow([REDACT_OBLIGATION]))
        httpx_mock.add_response(
            url=CHECK_INPUT_URL,
            json={"allowed": True, "redacted": False, "redaction_evaluated": True},
        )
        content, did_redact = await client.fulfill_request(decision, "clean text")
        assert content == "clean text"
        assert did_redact is False

    @pytest.mark.asyncio
    async def test_redaction_not_evaluated_fails_closed(
        self, client: AxonFlow, httpx_mock: HTTPXMock
    ) -> None:
        """redaction_evaluated=false ⇒ redactor disabled ⇒ MUST fail closed (#2563 B1)."""
        decision = DecideResponse.model_validate(_decide_allow([REDACT_OBLIGATION]))
        httpx_mock.add_response(
            url=CHECK_INPUT_URL,
            json={"allowed": True, "redacted": False, "redaction_evaluated": False},
        )
        with pytest.raises(ObligationNotFulfillableError, match="redactor did not run"):
            await client.fulfill_request(decision, "Email john@x.com")

    @pytest.mark.asyncio
    async def test_redaction_evaluated_absent_fails_closed(
        self, client: AxonFlow, httpx_mock: HTTPXMock
    ) -> None:
        """Absent redaction_evaluated defaults False (older platform) ⇒ fail closed."""
        decision = DecideResponse.model_validate(_decide_allow([REDACT_OBLIGATION]))
        httpx_mock.add_response(
            url=CHECK_INPUT_URL,
            json={"allowed": True, "redacted": True, "redacted_statement": "x"},
        )
        with pytest.raises(ObligationNotFulfillableError):
            await client.fulfill_request(decision, "Email john@x.com")

    @pytest.mark.asyncio
    async def test_missing_fulfillment_fails_closed(
        self, client: AxonFlow, httpx_mock: HTTPXMock
    ) -> None:
        decision = DecideResponse.model_validate(
            _decide_allow([{"type": "redact_pii"}])  # no fulfillment block
        )
        with pytest.raises(ObligationNotFulfillableError, match="missing request-phase"):
            await client.fulfill_request(decision, "Email john@x.com")
        assert httpx_mock.get_requests() == []

    @pytest.mark.asyncio
    async def test_response_phase_fulfillment_fails_closed(
        self, client: AxonFlow, httpx_mock: HTTPXMock
    ) -> None:
        decision = DecideResponse.model_validate(
            _decide_allow(
                [
                    {
                        "type": "redact_pii",
                        "fulfillment": {
                            "endpoint": "/api/v1/mcp/check-output",
                            "phase": "response",
                            "content_types": ["text/plain"],
                        },
                    }
                ]
            )
        )
        with pytest.raises(ObligationNotFulfillableError):
            await client.fulfill_request(decision, "Email john@x.com")

    @pytest.mark.asyncio
    async def test_unadvertised_content_type_fails_closed(
        self, client: AxonFlow, httpx_mock: HTTPXMock
    ) -> None:
        decision = DecideResponse.model_validate(
            _decide_allow(
                [
                    {
                        "type": "redact_pii",
                        "fulfillment": {
                            "endpoint": "/api/v1/mcp/check-input",
                            "phase": "request",
                            "content_types": ["image/png"],  # text/plain not advertised
                        },
                    }
                ]
            )
        )
        with pytest.raises(ObligationNotFulfillableError, match="text/plain"):
            await client.fulfill_request(decision, "Email john@x.com")
        assert httpx_mock.get_requests() == []

    @pytest.mark.asyncio
    async def test_foreign_endpoint_fails_closed(
        self, client: AxonFlow, httpx_mock: HTTPXMock
    ) -> None:
        """A malformed verdict must not steer the PEP into calling an arbitrary URL."""
        decision = DecideResponse.model_validate(
            _decide_allow(
                [
                    {
                        "type": "redact_pii",
                        "fulfillment": {
                            "endpoint": "https://evil.example.com/exfil",
                            "phase": "request",
                            "content_types": ["text/plain"],
                        },
                    }
                ]
            )
        )
        with pytest.raises(ObligationNotFulfillableError, match="not the request-redaction"):
            await client.fulfill_request(decision, "Email john@x.com")
        assert httpx_mock.get_requests() == []

    @pytest.mark.asyncio
    async def test_engine_error_fails_closed(self, client: AxonFlow, httpx_mock: HTTPXMock) -> None:
        decision = DecideResponse.model_validate(_decide_allow([REDACT_OBLIGATION]))
        httpx_mock.add_response(url=CHECK_INPUT_URL, status_code=500, json={"error": "boom"})
        with pytest.raises(ObligationNotFulfillableError):
            await client.fulfill_request(decision, "Email john@x.com")

    @pytest.mark.asyncio
    async def test_non_redact_obligation_is_passthrough(
        self, client: AxonFlow, httpx_mock: HTTPXMock
    ) -> None:
        decision = DecideResponse.model_validate(
            _decide_allow([{"type": "some_future_obligation"}])
        )
        content, did_redact = await client.fulfill_request(decision, "untouched")
        assert content == "untouched"
        assert did_redact is False
        assert httpx_mock.get_requests() == []


# ---------------------------------------------------------------------------
# decide_and_fulfill method
# ---------------------------------------------------------------------------


class TestDecideAndFulfill:
    @pytest.mark.asyncio
    async def test_allow_with_redaction(self, client: AxonFlow, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(url=DECIDE_URL, json=_decide_allow([REDACT_OBLIGATION]))
        httpx_mock.add_response(
            url=CHECK_INPUT_URL,
            json={
                "allowed": True,
                "redacted": True,
                "redacted_statement": "masked",
                "redaction_evaluated": True,
            },
        )
        verdict, content, decision = await client.decide_and_fulfill(
            DecideRequest(stage="tool", query="Email john@x.com")
        )
        assert verdict == VERDICT_ALLOW
        assert content == "masked"
        assert decision.decision_id == "dec-1"

    @pytest.mark.asyncio
    async def test_deny_does_not_fulfill(self, client: AxonFlow, httpx_mock: HTTPXMock) -> None:
        httpx_mock.add_response(
            url=DECIDE_URL,
            json={
                "verdict": "deny",
                "decision_id": "d2",
                "obligations": [],
                "evaluated_policies": ["sys_secret_block"],
                "reasons": ["blocked: secret"],
            },
        )
        verdict, content, _decision = await client.decide_and_fulfill(
            DecideRequest(stage="tool", query="leak the api key sk-123")
        )
        assert verdict == "deny"
        # On a non-allow verdict the original query is returned unchanged and the
        # caller blocks; no engine fulfillment is attempted.
        assert content == "leak the api key sk-123"
        assert httpx_mock.get_requests(url=CHECK_INPUT_URL) == []

    @pytest.mark.asyncio
    async def test_allow_unfulfillable_raises(
        self, client: AxonFlow, httpx_mock: HTTPXMock
    ) -> None:
        httpx_mock.add_response(url=DECIDE_URL, json=_decide_allow([{"type": "redact_pii"}]))
        with pytest.raises(ObligationNotFulfillableError):
            await client.decide_and_fulfill(DecideRequest(stage="tool", query="Email a@b.com"))
