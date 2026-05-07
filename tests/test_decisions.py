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


# ============================================================================
# list_decisions — Session γ contract tests (#1982)
# ============================================================================

import pytest_httpx  # noqa: F401, E402

from axonflow.decisions import DecisionSummary, ListDecisionsOptions  # noqa: E402
from axonflow.exceptions import RateLimitError  # noqa: E402


class TestDecisionSummaryShape:
    """Slim 5-field type — pre-α1 + dynamic-only blocks may omit policy_id
    + tool_signature; SDK must accept and round-trip cleanly."""

    def test_minimum_fields_parse(self) -> None:
        d = DecisionSummary(
            decision_id="dec-1",
            timestamp=datetime(2026, 5, 7, 12, 0, 0, tzinfo=timezone.utc),
            decision="deny",
        )
        assert d.policy_id is None
        assert d.tool_signature is None

    def test_full_fields_round_trip(self) -> None:
        raw = {
            "decision_id": "dec-x",
            "timestamp": "2026-05-07T12:00:00Z",
            "decision": "allow",
            "policy_id": "pol-default",
            "tool_signature": "github.status",
        }
        d = DecisionSummary.model_validate(raw)
        assert d.decision == "allow"
        assert d.policy_id == "pol-default"
        # extra='ignore' accepts arbitrary unknown fields
        raw_extra = {**raw, "policy_version": 7, "future_field": "shrug"}
        d2 = DecisionSummary.model_validate(raw_extra)
        assert d2.decision_id == "dec-x"


class TestListDecisions:
    """Tests for AxonFlowClient.list_decisions."""

    @pytest.mark.asyncio
    async def test_happy_path(self, httpx_mock) -> None:
        from axonflow.client import AxonFlow

        httpx_mock.add_response(
            method="GET",
            url="http://localhost:8080/api/v1/decisions",
            json={
                "decisions": [
                    {
                        "decision_id": "dec-1",
                        "timestamp": "2026-05-07T12:00:00Z",
                        "decision": "deny",
                        "policy_id": "pol-sqli",
                        "tool_signature": "postgres.query",
                    },
                    {
                        "decision_id": "dec-2",
                        "timestamp": "2026-05-07T11:00:00Z",
                        "decision": "allow",
                        "policy_id": "pol-default",
                        "tool_signature": "github.status",
                    },
                    {
                        "decision_id": "dec-3",
                        "timestamp": "2026-05-07T10:00:00Z",
                        "decision": "require_approval",
                        "policy_id": "pol-amount",
                        "tool_signature": "stripe.charge",
                    },
                ]
            },
        )
        client = AxonFlow(endpoint="http://localhost:8080")
        got = await client.list_decisions()
        assert len(got) == 3
        assert got[0].decision_id == "dec-1"
        assert got[2].decision == "require_approval"

    @pytest.mark.asyncio
    async def test_filter_serialization(self, httpx_mock) -> None:
        from axonflow.client import AxonFlow

        # Mock matches the EXACT URL — if we forget to register a field
        # in the URL builder, no mock matches and httpx_mock raises.
        httpx_mock.add_response(
            method="GET",
            url=(
                "http://localhost:8080/api/v1/decisions?"
                "since=2026-05-07T00%3A00%3A00Z&"
                "decision=deny&"
                "policy_id=pol-sqli&"
                "tool_signature=postgres.query&"
                "limit=25"
            ),
            json={"decisions": []},
        )
        client = AxonFlow(endpoint="http://localhost:8080")
        opts = ListDecisionsOptions(
            since=datetime(2026, 5, 7, 0, 0, 0, tzinfo=timezone.utc),
            decision="deny",
            policy_id="pol-sqli",
            tool_signature="postgres.query",
            limit=25,
        )
        got = await client.list_decisions(opts)
        assert got == []

    @pytest.mark.asyncio
    async def test_omits_unset_filters(self, httpx_mock) -> None:
        from axonflow.client import AxonFlow

        # Only decision is set; URL must omit the others entirely.
        httpx_mock.add_response(
            method="GET",
            url="http://localhost:8080/api/v1/decisions?decision=deny",
            json={"decisions": []},
        )
        client = AxonFlow(endpoint="http://localhost:8080")
        await client.list_decisions(ListDecisionsOptions(decision="deny"))

    @pytest.mark.asyncio
    async def test_429_upgrade_envelope(self, httpx_mock) -> None:
        from axonflow.client import AxonFlow

        httpx_mock.add_response(
            method="GET",
            url="http://localhost:8080/api/v1/decisions?limit=10",
            status_code=429,
            json={
                "error": "Free tier shows the last 5 decisions in 24h. Pro raises this to 100 decisions in the last 30 days.",
                "limit_type": "decision_list_size",
                "tier": "Community",
                "limit": 5,
                "remaining": 0,
                "upgrade": {
                    "tier": "Pro",
                    "wording": "Free tier shows the last 5 decisions in 24h. Pro raises this to 100 decisions in the last 30 days.",
                    "compare_url": "https://getaxonflow.com/pricing/",
                    "buy_url": "https://buy.stripe.com/bJe28qbztcdVchjdkw8k800",
                },
            },
        )
        client = AxonFlow(endpoint="http://localhost:8080")
        with pytest.raises(RateLimitError) as excinfo:
            await client.list_decisions(ListDecisionsOptions(limit=10))
        rle = excinfo.value
        assert rle.tier == "Community"
        assert rle.limit_type == "decision_list_size"
        assert rle.limit == 5
        assert rle.upgrade is not None
        assert rle.upgrade.tier == "Pro"
        assert rle.upgrade.compare_url == "https://getaxonflow.com/pricing/"
        assert rle.upgrade.buy_url == "https://buy.stripe.com/bJe28qbztcdVchjdkw8k800"

    @pytest.mark.asyncio
    async def test_429_malformed_body(self, httpx_mock) -> None:
        """Malformed 429 body must NOT silently succeed — surfaces RateLimitError
        without upgrade context."""
        from axonflow.client import AxonFlow

        httpx_mock.add_response(
            method="GET",
            url="http://localhost:8080/api/v1/decisions",
            status_code=429,
            text="not a json envelope",
        )
        client = AxonFlow(endpoint="http://localhost:8080")
        with pytest.raises(RateLimitError) as excinfo:
            await client.list_decisions()
        # Upgrade is None when we can't parse the body, but the error is
        # still typed so callers can branch on it.
        assert excinfo.value.upgrade is None
        assert excinfo.value.limit_type is None

    @pytest.mark.asyncio
    async def test_401(self, httpx_mock) -> None:
        from axonflow.client import AxonFlow
        from axonflow.exceptions import AxonFlowError

        httpx_mock.add_response(
            method="GET",
            url="http://localhost:8080/api/v1/decisions",
            status_code=401,
            json={"error": "X-Tenant-ID header is required"},
        )
        client = AxonFlow(endpoint="http://localhost:8080")
        with pytest.raises(AxonFlowError) as excinfo:
            await client.list_decisions()
        assert "401" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_forward_compat_unknown_fields(self, httpx_mock) -> None:
        """Additive unknown fields on summaries + outer envelope parse cleanly."""
        from axonflow.client import AxonFlow

        httpx_mock.add_response(
            method="GET",
            url="http://localhost:8080/api/v1/decisions",
            json={
                "decisions": [
                    {
                        "decision_id": "dec-fwd",
                        "timestamp": "2026-05-07T12:00:00Z",
                        "decision": "deny",
                        "policy_id": "pol-x",
                        "tool_signature": "tool-x",
                        "policy_version": 7,
                        "latest_policy_version": 9,
                        "arbitrary_unknown": "ignored",
                    }
                ],
                "next_cursor": "future_cursor_pagination",
            },
        )
        client = AxonFlow(endpoint="http://localhost:8080")
        got = await client.list_decisions()
        assert len(got) == 1
        assert got[0].decision_id == "dec-fwd"


class TestBuildListDecisionsQuery:
    def test_none_options_returns_empty(self) -> None:
        from axonflow.client import _build_list_decisions_query

        assert _build_list_decisions_query(None) == ""

    def test_empty_options_returns_empty(self) -> None:
        from axonflow.client import _build_list_decisions_query

        assert _build_list_decisions_query(ListDecisionsOptions()) == ""

    def test_partial_options_omit_none_fields(self) -> None:
        from axonflow.client import _build_list_decisions_query

        opts = ListDecisionsOptions(decision="deny", limit=7)
        qs = _build_list_decisions_query(opts)
        assert qs == "decision=deny&limit=7"
