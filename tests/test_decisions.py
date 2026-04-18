"""Tests for axonflow.decisions (ADR-043 explainability)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from axonflow.decisions import DecisionExplanation, ExplainPolicy, ExplainRule


class TestDecisionExplanationShape:
    """Frozen shape per ADR-043 — these tests pin the contract."""

    def test_minimum_fields_parse(self) -> None:
        exp = DecisionExplanation(
            decision_id="dec-1",
            timestamp=datetime(2026, 4, 17, tzinfo=timezone.utc),
            decision="deny",
        )
        assert exp.decision_id == "dec-1"
        assert exp.decision == "deny"
        assert exp.policy_matches == []
        assert exp.override_available is False
        assert exp.historical_hit_count_session == 0

    def test_full_fields_round_trip(self) -> None:
        raw = {
            "decision_id": "dec_wf1_step2",
            "timestamp": "2026-04-17T12:00:00Z",
            "decision": "deny",
            "reason": "SQL injection detected",
            "risk_level": "high",
            "policy_matches": [
                {
                    "policy_id": "pol-sqli",
                    "policy_name": "SQL Injection Detector",
                    "action": "deny",
                    "risk_level": "high",
                    "allow_override": True,
                    "policy_description": "Blocks SQL injection",
                }
            ],
            "matched_rules": [
                {
                    "policy_id": "pol-sqli",
                    "rule_id": "rule-1",
                    "rule_text": "Contains UNION SELECT",
                    "matched_on": "query.sql",
                }
            ],
            "override_available": True,
            "override_existing_id": "ov-abc",
            "historical_hit_count_session": 3,
            "policy_source_link": "https://policies.axonflow/sqli",
            "tool_signature": "Bash",
        }
        exp = DecisionExplanation.model_validate(raw)
        assert exp.decision == "deny"
        assert len(exp.policy_matches) == 1
        assert exp.policy_matches[0].policy_id == "pol-sqli"
        assert exp.policy_matches[0].allow_override is True
        assert len(exp.matched_rules or []) == 1
        assert exp.override_existing_id == "ov-abc"
        assert exp.historical_hit_count_session == 3
        assert exp.tool_signature == "Bash"

    def test_extra_fields_are_ignored_for_forward_compat(self) -> None:
        # ADR-043: additive fields must not break existing clients.
        raw = {
            "decision_id": "dec-1",
            "timestamp": "2026-04-17T12:00:00Z",
            "decision": "allow",
            "future_field_we_dont_know_yet": {"nested": True},
        }
        exp = DecisionExplanation.model_validate(raw)
        assert exp.decision == "allow"


class TestExplainPolicy:
    def test_defaults(self) -> None:
        p = ExplainPolicy(policy_id="p-1")
        assert p.policy_id == "p-1"
        assert p.allow_override is False
        assert p.policy_name is None


class TestExplainRule:
    def test_minimum(self) -> None:
        r = ExplainRule(policy_id="p-1")
        assert r.policy_id == "p-1"
        assert r.rule_id is None


class TestClientExplainDecision:
    """Tests for AxonFlowClient.explain_decision."""

    @pytest.mark.asyncio
    async def test_rejects_empty_decision_id(self) -> None:
        from axonflow.client import AxonFlow

        client = AxonFlow(endpoint="http://localhost:8080")
        with pytest.raises(ValueError, match="decision_id is required"):
            await client.explain_decision("")

    @pytest.mark.asyncio
    async def test_happy_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from axonflow.client import AxonFlow

        client = AxonFlow(endpoint="http://localhost:8080")

        captured_args: list[tuple[str, str]] = []

        async def fake_request(
            self: AxonFlow, method: str, path: str, **kwargs: object
        ) -> dict[str, object]:
            captured_args.append((method, path))
            return {
                "decision_id": "dec-1",
                "timestamp": "2026-04-17T12:00:00Z",
                "decision": "deny",
                "reason": "blocked",
                "policy_matches": [
                    {"policy_id": "p-1", "policy_name": "Test", "allow_override": True}
                ],
                "override_available": True,
            }

        monkeypatch.setattr(AxonFlow, "_orchestrator_request", fake_request)

        exp = await client.explain_decision("dec-1")
        assert exp.decision_id == "dec-1"
        assert exp.override_available is True
        assert exp.policy_matches[0].policy_id == "p-1"
        assert captured_args == [("GET", "/api/v1/decisions/dec-1/explain")]

    @pytest.mark.asyncio
    async def test_url_encodes_decision_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from axonflow.client import AxonFlow

        client = AxonFlow(endpoint="http://localhost:8080")

        captured_paths: list[str] = []

        async def fake_request(
            self: AxonFlow, method: str, path: str, **kwargs: object
        ) -> dict[str, object]:
            captured_paths.append(path)
            return {
                "decision_id": "a/b",
                "timestamp": "2026-04-17T12:00:00Z",
                "decision": "allow",
                "reason": "",
                "policy_matches": [],
                "override_available": False,
                "historical_hit_count_session": 0,
            }

        monkeypatch.setattr(AxonFlow, "_orchestrator_request", fake_request)

        await client.explain_decision("a/b")
        assert len(captured_paths) == 1
        assert "a%2Fb" in captured_paths[0]
        assert "a/b/explain" not in captured_paths[0]

    @pytest.mark.asyncio
    async def test_handles_empty_response(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from axonflow.client import AxonFlow

        client = AxonFlow(endpoint="http://localhost:8080")

        async def fake_request(self: AxonFlow, method: str, path: str, **kwargs: object) -> None:
            return None

        monkeypatch.setattr(AxonFlow, "_orchestrator_request", fake_request)

        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            await client.explain_decision("dec-1")


class TestAuditSearchRequestNewFilters:
    """The three new filters added per ADR-042/ADR-043 must serialize correctly."""

    def test_filters_included_when_set(self) -> None:
        from axonflow.types import AuditSearchRequest

        req = AuditSearchRequest(
            decision_id="dec-1",
            policy_name="SQL Injection Detector",
            override_id="ov-abc",
        )
        dumped = req.model_dump(exclude_none=True)
        assert dumped["decision_id"] == "dec-1"
        assert dumped["policy_name"] == "SQL Injection Detector"
        assert dumped["override_id"] == "ov-abc"

    def test_filters_absent_when_unset(self) -> None:
        from axonflow.types import AuditSearchRequest

        req = AuditSearchRequest()
        dumped = req.model_dump(exclude_none=True)
        assert "decision_id" not in dumped
        assert "policy_name" not in dumped
        assert "override_id" not in dumped
