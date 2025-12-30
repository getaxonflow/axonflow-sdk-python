"""Self-Hosted Zero-Config Mode Tests for AxonFlow Python SDK.

Tests for the zero-configuration self-hosted mode where users can run
AxonFlow without any API keys, license keys, or credentials.

This tests the scenario where a first-time user:
1. Starts the agent with SELF_HOSTED_MODE=true
   and SELF_HOSTED_MODE_ACKNOWLEDGED=I_UNDERSTAND_NO_AUTH
2. Connects the SDK with no credentials
3. Makes requests that should succeed without authentication

Community Features (no credentials required):
- Health check
- Execute query (Proxy Mode)

Enterprise Features (credentials required):
- Gateway Mode (get_policy_approved_context, audit_llm_call)
- Policy enforcement

Run with:
    AXONFLOW_AGENT_URL=http://localhost:8080 \
    RUN_INTEGRATION_TESTS=1 pytest tests/test_selfhosted_zero_config.py -v
"""

import os

import pytest

from axonflow import AxonFlow
from axonflow.exceptions import AuthenticationError
from axonflow.types import TokenUsage


def is_localhost() -> bool:
    """Check if we're testing against localhost."""
    agent_url = os.getenv("AXONFLOW_AGENT_URL", "http://localhost:8080")
    return "localhost" in agent_url or "127.0.0.1" in agent_url


def get_community_config():
    """Get community/self-hosted configuration (no credentials)."""
    return {
        "agent_url": os.getenv("AXONFLOW_AGENT_URL", "http://localhost:8080"),
        "client_id": "default",  # Can be any value for self-hosted
        # No credentials - community mode
        "debug": True,
        "timeout": 30.0,
    }


def get_enterprise_config():
    """Get enterprise configuration (with credentials for Gateway Mode)."""
    return {
        "agent_url": os.getenv("AXONFLOW_AGENT_URL", "http://localhost:8080"),
        "client_id": "default",
        "client_secret": "test-secret",  # Enterprise - credentials required
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
    """Test that SDK can be initialized with minimal/no credentials for any endpoint."""

    def test_create_client_without_credentials_for_localhost(self):
        """SDK should accept no credentials for localhost (community mode)."""
        config = get_community_config()
        # This should not raise an error
        client = AxonFlow(**config)
        assert client is not None
        assert not client._has_credentials()
        print("✅ Client created without credentials for localhost (community mode)")

    def test_create_client_without_credentials_for_any_endpoint(self):
        """SDK should accept no credentials for any endpoint (community mode)."""
        client = AxonFlow(
            agent_url="https://my-custom-domain.local",
            client_id="default",
            # No credentials - community mode works for any endpoint
            debug=True,
        )
        assert client is not None
        assert not client._has_credentials()
        print("✅ Client created without credentials for any endpoint (community mode)")

    def test_has_credentials_with_license_key(self):
        """SDK should detect credentials when license_key is set."""
        client = AxonFlow(
            agent_url="http://localhost:8080",
            license_key="test-license-key",
            debug=True,
        )
        assert client._has_credentials()
        print("✅ Client detected credentials with license_key")

    def test_has_credentials_with_client_secret(self):
        """SDK should detect credentials when client_secret is set."""
        client = AxonFlow(
            agent_url="http://localhost:8080",
            client_id="test-client",
            client_secret="test-secret",
            debug=True,
        )
        assert client._has_credentials()
        print("✅ Client detected credentials with client_secret")


# ============================================================
# 2. GATEWAY MODE (Enterprise Feature - requires credentials)
# ============================================================
class TestGatewayModeEnterprise:
    """Test Gateway Mode requires credentials (enterprise feature)."""

    @pytest.fixture
    async def enterprise_client(self):
        """Create test client with credentials for enterprise features."""
        config = get_enterprise_config()
        async with AxonFlow(**config) as ax:
            yield ax

    @pytest.fixture
    async def community_client(self):
        """Create test client without credentials (community mode)."""
        config = get_community_config()
        async with AxonFlow(**config) as ax:
            yield ax

    @pytest.mark.asyncio
    async def test_pre_check_requires_credentials(self, community_client):
        """get_policy_approved_context should raise AuthenticationError without credentials."""
        with pytest.raises(AuthenticationError) as exc_info:
            await community_client.get_policy_approved_context(
                user_token="",
                query="What is the weather in Paris?",
            )

        assert "requires credentials" in str(exc_info.value)
        assert "Gateway Mode" in str(exc_info.value)
        print("✅ get_policy_approved_context requires credentials")

    @pytest.mark.asyncio
    async def test_audit_requires_credentials(self, community_client):
        """audit_llm_call should raise AuthenticationError without credentials."""
        with pytest.raises(AuthenticationError) as exc_info:
            await community_client.audit_llm_call(
                context_id="ctx_mock_123",
                response_summary="Test response",
                provider="openai",
                model="gpt-4",
                token_usage=TokenUsage(
                    prompt_tokens=100,
                    completion_tokens=50,
                    total_tokens=150,
                ),
                latency_ms=250,
            )

        assert "requires credentials" in str(exc_info.value)
        assert "Gateway Mode" in str(exc_info.value)
        print("✅ audit_llm_call requires credentials")

    @pytest.mark.asyncio
    async def test_pre_check_with_credentials(self, enterprise_client):
        """Pre-check should work with credentials."""
        result = await enterprise_client.get_policy_approved_context(
            user_token="",  # Empty token - but has enterprise credentials
            query="What is the weather in Paris?",
        )

        assert result.context_id, "Expected non-empty context_id"
        assert result.expires_at is not None, "Expected expires_at to be set"

        print(f"✅ Pre-check succeeded with credentials: {result.context_id}")

    @pytest.mark.asyncio
    async def test_full_gateway_flow_with_credentials(self, enterprise_client):
        """Complete Gateway Mode flow should work with credentials."""
        # Step 1: Pre-check
        pre_check = await enterprise_client.get_policy_approved_context(
            user_token="",
            query="Analyze quarterly sales data",
        )

        assert pre_check.context_id, "Expected context_id from pre-check"

        # Step 2: Audit (simulating direct LLM call completion)
        audit = await enterprise_client.audit_llm_call(
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

        print(f"✅ Full Gateway Mode flow completed with credentials: {audit.audit_id}")


# ============================================================
# 3. PROXY MODE WITHOUT AUTHENTICATION (Community Feature)
# ============================================================
class TestProxyModeZeroConfig:
    """Test Proxy Mode works without credentials (community feature)."""

    @pytest.fixture
    async def client(self):
        """Create test client without credentials (community mode)."""
        config = get_community_config()
        async with AxonFlow(**config) as ax:
            yield ax

    @pytest.mark.asyncio
    async def test_execute_query_without_credentials(self, client):
        """Execute query should work without credentials."""
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
            print("✅ Query executed without credentials")


# ============================================================
# 4. POLICY ENFORCEMENT (Enterprise Feature - requires credentials)
# ============================================================
class TestPolicyEnforcementEnterprise:
    """Verify policies are enforced with credentials."""

    @pytest.fixture
    async def client(self):
        """Create test client with credentials for enterprise features."""
        config = get_enterprise_config()
        async with AxonFlow(**config) as ax:
            yield ax

    @pytest.mark.asyncio
    async def test_sql_injection_blocked_with_credentials(self, client):
        """SQL injection should be blocked with credentials."""
        result = await client.get_policy_approved_context(
            user_token="",
            query="SELECT * FROM users WHERE id=1; DROP TABLE users;--",
        )

        assert not result.approved, "SQL injection should be blocked"
        assert result.block_reason, "Expected block_reason to be set"

        print(f"✅ SQL injection blocked: {result.block_reason}")

    @pytest.mark.asyncio
    async def test_pii_blocked_with_credentials(self, client):
        """PII should be blocked with credentials."""
        result = await client.get_policy_approved_context(
            user_token="",
            query="My social security number is 123-45-6789",
        )

        assert not result.approved, "PII should be blocked"
        print("✅ PII blocked with credentials")


# ============================================================
# 5. HEALTH CHECK WITHOUT AUTH (Community Feature)
# ============================================================
class TestHealthCheckZeroConfig:
    """Test health check works without authentication."""

    @pytest.mark.asyncio
    async def test_health_check_no_credentials(self):
        """Health check should work without any credentials."""
        config = get_community_config()
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
    async def test_first_time_user_community_features(self):
        """Simulate a brand new user using community features."""
        # First-time user configuration - minimal setup (no credentials)
        client = AxonFlow(
            agent_url=os.getenv("AXONFLOW_AGENT_URL", "http://localhost:8080"),
            client_id="first-time-user",
            # No credentials - community mode
            debug=True,
        )

        async with client:
            # Step 1: Health check should work
            healthy = await client.health_check()
            assert healthy, "Health check should pass"

            # Step 2: Execute query should work (community feature)
            response = await client.execute_query(
                user_token="",
                query="Hello, this is my first query!",
                request_type="chat",
            )
            # May succeed or be blocked by policy, but not auth error
            assert response is not None

        print("✅ First-time user experience validated (community mode)")
        print("   - Client creation: OK")
        print("   - Health check: OK")
        print("   - Execute query: OK")

    @pytest.mark.asyncio
    async def test_first_time_user_enterprise_features_require_credentials(self):
        """Verify enterprise features require credentials for first-time users."""
        # First-time user tries Gateway Mode without credentials
        client = AxonFlow(
            agent_url=os.getenv("AXONFLOW_AGENT_URL", "http://localhost:8080"),
            client_id="first-time-user",
            # No credentials
            debug=True,
        )

        async with client:
            # Trying Gateway Mode should fail with clear error
            with pytest.raises(AuthenticationError) as exc_info:
                await client.get_policy_approved_context(
                    user_token="",
                    query="Hello, this is my first query!",
                )

            assert "requires credentials" in str(exc_info.value)

        print("✅ Enterprise features require credentials with clear error message")


# Note: Section 7 (Auth Headers) tests are in test_auth_headers.py
# They are separated because they use mocking and don't require a running agent
