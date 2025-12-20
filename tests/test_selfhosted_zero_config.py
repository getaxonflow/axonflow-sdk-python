"""Self-Hosted Zero-Config Mode Tests for AxonFlow Python SDK.

Tests for the zero-configuration self-hosted mode where users can run
AxonFlow without any API keys, license keys, or credentials.

This tests the scenario where a first-time user:
1. Starts the agent with SELF_HOSTED_MODE=true
   and SELF_HOSTED_MODE_ACKNOWLEDGED=I_UNDERSTAND_NO_AUTH
2. Connects the SDK with no credentials
3. Makes requests that should succeed without authentication

Run with:
    AXONFLOW_AGENT_URL=http://localhost:8080 \\
    RUN_INTEGRATION_TESTS=1 pytest tests/test_selfhosted_zero_config.py -v
"""

import os

import pytest

from axonflow import AxonFlow
from axonflow.types import TokenUsage


def is_localhost() -> bool:
    """Check if we're testing against localhost."""
    agent_url = os.getenv("AXONFLOW_AGENT_URL", "http://localhost:8080")
    return "localhost" in agent_url or "127.0.0.1" in agent_url


def get_selfhosted_config():
    """Get self-hosted configuration (minimal credentials for localhost)."""
    return {
        "agent_url": os.getenv("AXONFLOW_AGENT_URL", "http://localhost:8080"),
        "client_id": "default",  # Can be any value for self-hosted
        "client_secret": "",  # Empty - zero-config mode
        "debug": True,
        "timeout": 30.0,
    }


# Skip if not localhost (self-hosted mode requires localhost)
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION_TESTS") != "1",
    reason="Integration tests require RUN_INTEGRATION_TESTS=1",
)


# ============================================================
# 1. CLIENT INITIALIZATION WITHOUT CREDENTIALS
# ============================================================
class TestClientInitializationZeroConfig:
    """Test that SDK can be initialized with minimal/no credentials for localhost."""

    def test_create_client_with_empty_secret_for_localhost(self):
        """SDK should accept empty client_secret for localhost."""
        config = get_selfhosted_config()
        # This should not raise an error
        client = AxonFlow(**config)
        assert client is not None
        print("✅ Client created with empty secret for localhost")

    def test_create_client_with_whitespace_secret_for_localhost(self):
        """SDK should accept whitespace-only client_secret for localhost."""
        client = AxonFlow(
            agent_url="http://localhost:8080",
            client_id="default",
            client_secret="   ",  # Whitespace only
            debug=True,
        )
        assert client is not None
        print("✅ Client created with whitespace secret for localhost")


# ============================================================
# 2. GATEWAY MODE WITHOUT AUTHENTICATION
# ============================================================
class TestGatewayModeZeroConfig:
    """Test Gateway Mode works without real credentials."""

    @pytest.fixture
    async def client(self):
        """Create test client with zero-config."""
        config = get_selfhosted_config()
        async with AxonFlow(**config) as ax:
            yield ax

    @pytest.mark.asyncio
    async def test_pre_check_with_empty_token(self, client):
        """Pre-check should work with empty user token."""
        result = await client.get_policy_approved_context(
            user_token="",  # Empty token - zero-config scenario
            query="What is the weather in Paris?",
        )

        assert result.context_id, "Expected non-empty context_id"
        assert result.expires_at is not None, "Expected expires_at to be set"

        print(f"✅ Pre-check succeeded with empty token: {result.context_id}")

    @pytest.mark.asyncio
    async def test_pre_check_with_whitespace_token(self, client):
        """Pre-check should work with whitespace-only token."""
        result = await client.get_policy_approved_context(
            user_token="   ",  # Whitespace only
            query="Simple test query",
        )

        assert result.context_id, "Expected non-empty context_id"
        print("✅ Pre-check succeeded with whitespace token")

    @pytest.mark.asyncio
    async def test_full_gateway_flow_zero_config(self, client):
        """Complete Gateway Mode flow should work without credentials."""
        # Step 1: Pre-check
        pre_check = await client.get_policy_approved_context(
            user_token="",
            query="Analyze quarterly sales data",
        )

        assert pre_check.context_id, "Expected context_id from pre-check"

        # Step 2: Audit (simulating direct LLM call completion)
        audit = await client.audit_llm_call(
            context_id=pre_check.context_id,
            response_summary="Generated sales analysis report",
            provider="openai",
            model="gpt-4",
            token_usage=TokenUsage(
                prompt_tokens=100,
                completion_tokens=75,
                total_tokens=175,
            ),
            latency_ms=350,
        )

        assert audit.success, "Expected audit to succeed"
        assert audit.audit_id, "Expected audit_id to be set"

        print(f"✅ Full Gateway Mode flow completed: {audit.audit_id}")


# ============================================================
# 3. PROXY MODE WITHOUT AUTHENTICATION
# ============================================================
class TestProxyModeZeroConfig:
    """Test Proxy Mode works without real credentials."""

    @pytest.fixture
    async def client(self):
        """Create test client with zero-config."""
        config = get_selfhosted_config()
        async with AxonFlow(**config) as ax:
            yield ax

    @pytest.mark.asyncio
    async def test_execute_query_empty_token(self, client):
        """Execute query should work with empty user token."""
        response = await client.execute_query(
            user_token="",  # Empty token
            query="What is 2 + 2?",
            request_type="chat",
        )

        # Should either succeed or be blocked by policy (but not auth error)
        assert response is not None

        if response.blocked:
            print(f"⚠️ Query blocked by policy: {response.block_reason}")
        else:
            assert response.success, f"Expected success, got error: {response.error}"
            print("✅ Query executed with empty token")


# ============================================================
# 4. POLICY ENFORCEMENT STILL WORKS
# ============================================================
class TestPolicyEnforcementZeroConfig:
    """Verify policies are enforced even without authentication."""

    @pytest.fixture
    async def client(self):
        """Create test client with zero-config."""
        config = get_selfhosted_config()
        async with AxonFlow(**config) as ax:
            yield ax

    @pytest.mark.asyncio
    async def test_sql_injection_blocked_without_auth(self, client):
        """SQL injection should be blocked even without credentials."""
        result = await client.get_policy_approved_context(
            user_token="",
            query="SELECT * FROM users WHERE id=1; DROP TABLE users;--",
        )

        assert not result.approved, "SQL injection should be blocked"
        assert result.block_reason, "Expected block_reason to be set"

        print(f"✅ SQL injection blocked: {result.block_reason}")

    @pytest.mark.asyncio
    async def test_pii_blocked_without_auth(self, client):
        """PII should be blocked even without credentials."""
        result = await client.get_policy_approved_context(
            user_token="",
            query="My social security number is 123-45-6789",
        )

        assert not result.approved, "PII should be blocked"
        print("✅ PII blocked without credentials")


# ============================================================
# 5. HEALTH CHECK WITHOUT AUTH
# ============================================================
class TestHealthCheckZeroConfig:
    """Test health check works without authentication."""

    @pytest.mark.asyncio
    async def test_health_check_no_credentials(self):
        """Health check should work without any credentials."""
        config = get_selfhosted_config()
        async with AxonFlow(**config) as client:
            healthy = await client.health_check()
            assert healthy, "Expected health check to pass"
            print("✅ Health check succeeded without credentials")


# ============================================================
# 6. FIRST-TIME USER EXPERIENCE
# ============================================================
class TestFirstTimeUserZeroConfig:
    """Test the first-time user experience with zero configuration."""

    @pytest.mark.asyncio
    async def test_first_time_user_flow(self):
        """Simulate a brand new user with minimal configuration."""
        # First-time user configuration - minimal setup
        client = AxonFlow(
            agent_url=os.getenv("AXONFLOW_AGENT_URL", "http://localhost:8080"),
            client_id="first-time-user",
            client_secret="",  # Empty - zero-config
            debug=True,
        )

        async with client:
            # Step 1: Health check should work
            healthy = await client.health_check()
            assert healthy, "Health check should pass"

            # Step 2: Pre-check should work with empty token
            result = await client.get_policy_approved_context(
                user_token="",
                query="Hello, this is my first query!",
            )
            assert result.context_id, "Expected context_id"

        print("✅ First-time user experience validated")
        print("   - Client creation: OK")
        print("   - Health check: OK")
        print("   - Pre-check: OK")


# Note: Section 7 (Auth Headers) tests are in test_auth_headers.py
# They are separated because they use mocking and don't require a running agent
