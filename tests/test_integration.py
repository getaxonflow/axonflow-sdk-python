"""Integration tests for AxonFlow Python SDK.

Run with: RUN_INTEGRATION_TESTS=1 pytest tests/test_integration.py -v

Set environment variables before running:
    RUN_INTEGRATION_TESTS=1
    AXONFLOW_AGENT_URL=http://localhost:8080
    AXONFLOW_CLIENT_ID=demo-client
    AXONFLOW_CLIENT_SECRET=demo-secret
"""

import os
from datetime import datetime, timedelta

import pytest

from axonflow import AxonFlow
from axonflow.types import TokenUsage

# Skip all tests in this module unless RUN_INTEGRATION_TESTS is set
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="Integration tests require RUN_INTEGRATION_TESTS=1",
)


def get_test_config():
    """Get test configuration from environment."""
    return {
        "agent_url": os.getenv("AXONFLOW_AGENT_URL", "http://localhost:8080"),
        "client_id": os.getenv("AXONFLOW_CLIENT_ID", "demo-client"),
        "client_secret": os.getenv("AXONFLOW_CLIENT_SECRET", "demo-secret"),
        "debug": True,
        "timeout": 30.0,
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
async def test_execute_query_simple(client):
    """Test a basic query."""
    response = await client.execute_query(
        user_token="demo-user",
        query="What is 2+2?",
        request_type="chat",
    )
    assert response.success or response.blocked, f"Unexpected error: {response.error}"


@pytest.mark.asyncio
async def test_execute_query_sql_injection(client):
    """Test that SQL injection is blocked."""
    response = await client.execute_query(
        user_token="demo-user",
        query="SELECT * FROM users; DROP TABLE users;--",
        request_type="sql",
    )
    assert response.blocked, "Expected SQL injection to be blocked"
    reason = response.block_reason.lower() if response.block_reason else ""
    assert "sql" in reason or "injection" in reason


@pytest.mark.asyncio
async def test_execute_query_pii_detection(client):
    """Test that PII is blocked."""
    response = await client.execute_query(
        user_token="demo-user",
        query="My SSN is 123-45-6789",
        request_type="chat",
    )
    assert response.blocked, "Expected PII to be blocked"
    reason = response.block_reason.lower() if response.block_reason else ""
    assert "ssn" in reason or "social security" in reason


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
    """Test multi-agent plan generation."""
    try:
        plan = await client.generate_plan(
            query="Book a flight from NYC to LA",
            domain="travel",
        )

        assert plan.plan_id, "Expected non-empty plan_id"
    except Exception as e:
        # Plan generation may fail if orchestrator doesn't have LLM configured
        if "LLM" in str(e) or "provider" in str(e):
            pytest.skip(f"Plan generation skipped (LLM not configured): {e}")
        raise


@pytest.mark.asyncio
async def test_list_connectors(client):
    """Test listing MCP connectors."""
    connectors = await client.list_connectors()

    # Should have at least one connector
    assert isinstance(connectors, list)
    # Log connector names for debugging
    for c in connectors:
        print(f"  - {c.name} ({c.type})")
