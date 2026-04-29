"""Integration tests for AxonFlow Python SDK.

Run with: RUN_INTEGRATION_TESTS=1 pytest tests/test_integration.py -v

Set environment variables before running:
    RUN_INTEGRATION_TESTS=1
    AXONFLOW_AGENT_URL=http://localhost:8080
    AXONFLOW_CLIENT_ID=demo-client
    AXONFLOW_CLIENT_SECRET=demo-secret

These tests are designed to run against a bare community docker-compose
stack (no LLM provider, no planning engine, default PII_ACTION=redact).
They assert the SDK↔agent wire and the policy engine — not that a specific
LLM provider is configured.
"""

import os
from datetime import datetime, timedelta

import pytest

from axonflow import AxonFlow
from axonflow.exceptions import PolicyViolationError
from axonflow.types import TokenUsage

# Skip all tests in this module unless RUN_INTEGRATION_TESTS is set
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="Integration tests require RUN_INTEGRATION_TESTS=1",
)


def get_test_config():
    """Get test configuration from environment.

    ``AXONFLOW_TEST_TIMEOUT`` (seconds) overrides the default request
    timeout so tests can run against deployments where LLM tail latency
    is higher than a bare docker-compose stack. The hosted SaaS (e.g.
    ``try.getaxonflow.com``) routinely sees 60-90s LLM round-trips
    during traffic spikes; the docker-compose stack returns instantly
    because no real LLM is configured. Default 30s preserves the
    existing fast path; the nightly-try workflow bumps this to 120s.
    """
    return {
        "endpoint": os.getenv("AXONFLOW_AGENT_URL", "http://localhost:8080"),
        "client_id": os.getenv("AXONFLOW_CLIENT_ID", "demo-client"),
        "client_secret": os.getenv("AXONFLOW_CLIENT_SECRET", "demo-secret"),
        "debug": True,
        "timeout": float(os.getenv("AXONFLOW_TEST_TIMEOUT", "30.0")),
    }


@pytest.fixture
async def client():
    """Create test client."""
    config = get_test_config()
    async with AxonFlow(**config) as ax:
        yield ax


@pytest.mark.asyncio
async def test_health_check(client):
    """Test basic connectivity."""
    healthy = await client.health_check()
    assert healthy, "Health check failed"


@pytest.mark.asyncio
async def test_proxy_llm_call_simple(client):
    """Test the SDK↔agent wire for a basic query.

    On a bare community stack with no LLM provider configured, the agent
    returns success=False with error='LLM routing failed'. That is an
    acceptable outcome for this test — the goal is verifying the wire
    works, not that a specific LLM is available.
    """
    response = await client.proxy_llm_call(
        user_token="demo-user",
        query="What is 2+2?",
        request_type="chat",
    )
    llm_unavailable = "LLM routing" in (response.error or "")
    assert response.success or response.blocked or llm_unavailable, (
        f"Unexpected error: {response.error}"
    )


@pytest.mark.asyncio
async def test_proxy_llm_call_sql_injection(client):
    """Test that the policy engine engages on SQL injection.

    Different deployment modes configure SQLi system policies with different
    actions: bare docker-compose defaults to ``block``; the hosted community
    SaaS at try.getaxonflow.com currently runs them in alert / log mode and
    relies on the LLM as a secondary refusal layer. This test asserts that
    the engine *engaged* (saw the SQLi pattern) rather than that it *blocked*
    in any specific way — engagement is the contract, action is config.

    Accepts any of these as evidence the engine fired:
      - ``response.blocked`` with a SQL-related ``block_reason``
      - ``PolicyViolationError`` mentioning SQL / injection / drop
      - ``policies_evaluated`` containing a policy id that matches the
        canonical SQLi pattern (``sys_sqli_*``) or the literal substring
        ``injection``. Substring ``sql`` alone is too loose — it would
        match an unrelated ``sql_format_validator``-style id.

    When the SaaS-side fix in axonflow-enterprise#1747 lands and
    try.getaxonflow.com restores ``block`` actions, set
    ``AXONFLOW_STRICT_SQLI_BLOCK=1`` in the workflow env to require the
    strict ``response.blocked`` path. With that env set, this test fails
    if the engine fired but did not block — surfacing a regression where
    the SaaS quietly drops back to alert mode.
    """
    try:
        response = await client.proxy_llm_call(
            user_token="demo-user",
            query="SELECT * FROM users; DROP TABLE users;--",
            request_type="sql",
        )
        strict = os.environ.get("AXONFLOW_STRICT_SQLI_BLOCK") == "1"
        if response.blocked:
            reason = (response.block_reason or "").lower()
            assert "sql" in reason or "injection" in reason or "drop" in reason
            return
        if strict:
            pytest.fail(
                "AXONFLOW_STRICT_SQLI_BLOCK=1 requires response.blocked=True "
                f"on SQLi but got blocked={response.blocked} (engine may have "
                "fired in alert/log mode — see axonflow-enterprise#1747)."
            )
        # Engine fired in non-block action mode — at least one policy id in
        # policies_evaluated must match the canonical SQLi pattern.
        policies = response.policy_info.policies_evaluated if response.policy_info else []
        engine_engaged = any(
            p.lower().startswith("sys_sqli") or "injection" in p.lower() for p in policies
        )
        assert engine_engaged, (
            "Policy engine did not engage on SQL injection. "
            f"policies_evaluated={policies} blocked={response.blocked}"
        )
    except PolicyViolationError as e:
        msg = str(e).lower()
        assert "sql" in msg or "injection" in msg or "drop" in msg


@pytest.mark.asyncio
async def test_proxy_llm_call_pii_detection(client):
    """Test that PII is detected by the policy engine.

    Default PII_ACTION=redact means SSN is redacted and the request then
    proceeds to the LLM. On a bare community stack the LLM call fails, but
    the PII policy must have fired. With PII_ACTION=block the SDK raises
    PolicyViolationError. This test accepts any evidence the PII policy
    fired: exception, blocked=True, redacted=True, or a PII policy in
    policies_evaluated.
    """
    try:
        response = await client.proxy_llm_call(
            user_token="demo-user",
            query="My SSN is 123-45-6789",
            request_type="chat",
        )
        policies = response.policy_info.policies_evaluated if response.policy_info else []
        redacted = bool((response.data or {}).get("redacted"))
        pii_policy_fired = response.blocked or redacted or any("pii" in p.lower() for p in policies)
        assert pii_policy_fired, (
            f"Expected PII policy to fire. "
            f"policies_evaluated={policies}, blocked={response.blocked}, "
            f"error={response.error}"
        )
    except PolicyViolationError as e:
        msg = str(e).lower()
        assert "pii" in msg or "ssn" in msg or "social security" in msg


@pytest.mark.asyncio
async def test_gateway_mode_pre_check(client):
    """Test Gateway Mode pre-check."""
    result = await client.get_policy_approved_context(
        user_token="demo-user",
        query="Analyze this data",
    )

    assert result.context_id, "Expected non-empty context_id"
    assert result.expires_at is not None, "ExpiresAt was not parsed"
    now = datetime.now(result.expires_at.tzinfo)
    assert result.expires_at > now, "ExpiresAt should be in the future"


@pytest.mark.asyncio
async def test_gateway_mode_datetime_parsing(client):
    """Test datetime parsing with nanoseconds."""
    result = await client.get_policy_approved_context(
        user_token="demo-user",
        query="Test datetime parsing",
    )

    # ExpiresAt should be approximately 5 minutes from now
    now = datetime.now(result.expires_at.tzinfo)
    expected_expiry = now + timedelta(minutes=5)
    time_diff = abs((result.expires_at - expected_expiry).total_seconds())

    # Allow 30 second tolerance
    assert time_diff < 30, (
        f"ExpiresAt not within expected range. Got {result.expires_at}, expected ~{expected_expiry}"
    )


@pytest.mark.asyncio
async def test_gateway_mode_audit_llm_call(client):
    """Test Gateway Mode audit."""
    # First get a context
    pre_check = await client.get_policy_approved_context(
        user_token="demo-user",
        query="Test audit",
    )

    # Then audit an LLM call
    result = await client.audit_llm_call(
        context_id=pre_check.context_id,
        response_summary="Test response summary",
        provider="openai",
        model="gpt-4",
        token_usage=TokenUsage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        ),
        latency_ms=250,
    )

    assert result.success, "Expected audit to succeed"
    assert result.audit_id, "Expected non-empty audit_id"


@pytest.mark.asyncio
async def test_generate_plan(client):
    """Test multi-agent plan generation.

    Skipped on stacks without an LLM provider or planning engine.
    """
    # Capability gate: skip when the agent reports planning is not
    # available rather than string-matching error text. The previous
    # broad-marker skip ("LLM", "provider", ...) silently absorbed any
    # error containing those words — including unrelated SDK regressions.
    if os.environ.get("AXONFLOW_HAS_PLANNING") != "1":
        pytest.skip(
            "Plan generation skipped: set AXONFLOW_HAS_PLANNING=1 to run "
            "this test against a stack with the planning engine enabled."
        )

    plan = await client.generate_plan(
        query="Book a flight from NYC to LA",
        domain="travel",
    )
    assert plan.plan_id, "Expected non-empty plan_id"


@pytest.mark.asyncio
async def test_list_connectors(client):
    """Test listing MCP connectors."""
    connectors = await client.list_connectors()

    # Should have at least one connector
    assert isinstance(connectors, list)
    # Log connector names for debugging
    for c in connectors:
        print(f"  - {c.name} ({c.type})")
