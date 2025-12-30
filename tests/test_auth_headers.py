"""Auth Header Verification Tests for AxonFlow Python SDK.

These tests verify that auth headers are correctly handled based on credentials,
not on localhost vs non-localhost URLs.

Key behavior:
- When credentials are provided (client_secret or license_key), headers are sent
- When no credentials are provided, headers are not sent
- This works for any endpoint (localhost or remote)
"""

import pytest

from axonflow import AxonFlow
from axonflow.exceptions import AuthenticationError
from axonflow.types import TokenUsage


# ============================================================
# AUTH HEADERS BASED ON CREDENTIALS (Unit Tests)
# ============================================================
class TestAuthHeadersWithCredentials:
    """Verify auth headers are sent when credentials are provided."""

    @pytest.mark.asyncio
    async def test_auth_headers_sent_with_client_secret(self, httpx_mock):
        """Auth headers should be sent when client_secret is provided."""
        httpx_mock.add_response(
            url="http://localhost:8080/api/request",
            json={"success": True, "data": {"answer": "4"}, "blocked": False},
        )

        client = AxonFlow(
            agent_url="http://localhost:8080",
            client_id="test-client",
            client_secret="test-secret",
            debug=True,
        )

        async with client:
            await client.execute_query(
                user_token="",
                query="What is 2+2?",
                request_type="chat",
            )

        requests = httpx_mock.get_requests()
        assert len(requests) == 1

        request = requests[0]
        headers = dict(request.headers)

        assert headers.get("x-client-secret") == "test-secret"
        assert headers.get("x-tenant-id") == "test-client"
        print("✅ Auth headers sent with client_secret")

    @pytest.mark.asyncio
    async def test_auth_headers_sent_with_license_key(self, httpx_mock):
        """Auth headers should be sent when license_key is provided."""
        httpx_mock.add_response(
            url="http://localhost:8080/api/request",
            json={"success": True, "data": {"answer": "4"}, "blocked": False},
        )

        client = AxonFlow(
            agent_url="http://localhost:8080",
            license_key="test-license-key",
            debug=True,
        )

        async with client:
            await client.execute_query(
                user_token="",
                query="What is 2+2?",
                request_type="chat",
            )

        requests = httpx_mock.get_requests()
        assert len(requests) == 1

        request = requests[0]
        headers = dict(request.headers)

        assert headers.get("x-license-key") == "test-license-key"
        print("✅ Auth headers sent with license_key")


class TestAuthHeadersWithoutCredentials:
    """Verify auth headers are NOT sent when no credentials are provided."""

    @pytest.mark.asyncio
    async def test_no_auth_headers_without_credentials(self, httpx_mock):
        """Auth headers should NOT be sent when no credentials are provided."""
        httpx_mock.add_response(
            url="http://localhost:8080/api/request",
            json={"success": True, "data": {"answer": "4"}, "blocked": False},
        )

        client = AxonFlow(
            agent_url="http://localhost:8080",
            client_id="test-client",
            # No client_secret, no license_key - community mode
            debug=True,
        )

        async with client:
            await client.execute_query(
                user_token="",
                query="What is 2+2?",
                request_type="chat",
            )

        requests = httpx_mock.get_requests()
        assert len(requests) == 1

        request = requests[0]
        headers = dict(request.headers)

        # X-Client-Secret should not be in headers
        assert "x-client-secret" not in headers
        # X-License-Key should not be in headers
        assert "x-license-key" not in headers
        # X-Tenant-ID is set from client_id (optional, not sensitive)
        assert headers.get("x-tenant-id") == "test-client"

        print("✅ No auth headers sent without credentials")

    @pytest.mark.asyncio
    async def test_no_auth_headers_for_health_check(self, httpx_mock):
        """Health check should not require auth headers."""
        httpx_mock.add_response(
            url="http://localhost:8080/health",
            json={"status": "healthy"},
        )

        client = AxonFlow(
            agent_url="http://localhost:8080",
            # No credentials
            debug=True,
        )

        async with client:
            await client.health_check()

        requests = httpx_mock.get_requests()
        assert len(requests) == 1

        request = requests[0]
        headers = dict(request.headers)

        # No auth headers for health check without credentials
        assert "x-client-secret" not in headers
        assert "x-license-key" not in headers

        print("✅ Health check works without auth headers")


class TestEnterpriseFeatureValidation:
    """Test that enterprise features require credentials before making requests."""

    @pytest.mark.asyncio
    async def test_pre_check_fails_without_credentials(self, httpx_mock):
        """get_policy_approved_context should fail before making request when no credentials."""
        # Don't mock the endpoint - we should fail before making the request
        client = AxonFlow(
            agent_url="http://localhost:8080",
            client_id="test-client",
            # No credentials
            debug=True,
        )

        async with client:
            with pytest.raises(AuthenticationError) as exc_info:
                await client.get_policy_approved_context(
                    user_token="",
                    query="Test query",
                )

            assert "requires credentials" in str(exc_info.value)
            assert "Gateway Mode" in str(exc_info.value)

        # No request should have been made
        requests = httpx_mock.get_requests()
        assert len(requests) == 0

        print("✅ get_policy_approved_context fails without credentials (no request made)")

    @pytest.mark.asyncio
    async def test_audit_fails_without_credentials(self, httpx_mock):
        """audit_llm_call should fail before making request when no credentials."""
        # Don't mock the endpoint - we should fail before making the request
        client = AxonFlow(
            agent_url="http://localhost:8080",
            client_id="test-client",
            # No credentials
            debug=True,
        )

        async with client:
            with pytest.raises(AuthenticationError) as exc_info:
                await client.audit_llm_call(
                    context_id="ctx_123",
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

        # No request should have been made
        requests = httpx_mock.get_requests()
        assert len(requests) == 0

        print("✅ audit_llm_call fails without credentials (no request made)")

    @pytest.mark.asyncio
    async def test_pre_check_works_with_credentials(self, httpx_mock):
        """get_policy_approved_context should work when credentials are provided."""
        httpx_mock.add_response(
            url="http://localhost:8080/api/policy/pre-check",
            json={
                "context_id": "ctx_mock_123",
                "approved": True,
                "policies": [],
                "expires_at": "2025-12-20T12:00:00Z",
            },
        )

        client = AxonFlow(
            agent_url="http://localhost:8080",
            client_id="test-client",
            client_secret="test-secret",  # With credentials
            debug=True,
        )

        async with client:
            result = await client.get_policy_approved_context(
                user_token="",
                query="Test query",
            )

        assert result.context_id == "ctx_mock_123"
        assert result.approved is True

        # Verify request was made with auth headers
        requests = httpx_mock.get_requests()
        assert len(requests) == 1

        request = requests[0]
        headers = dict(request.headers)

        assert headers.get("x-client-secret") == "test-secret"

        print("✅ get_policy_approved_context works with credentials")


class TestCredentialDetection:
    """Test the _has_credentials() helper method."""

    def test_has_credentials_with_client_secret(self):
        """Should detect credentials when client_secret is set."""
        client = AxonFlow(
            agent_url="http://localhost:8080",
            client_id="test-client",
            client_secret="test-secret",
        )
        assert client._has_credentials() is True

    def test_has_credentials_with_license_key(self):
        """Should detect credentials when license_key is set."""
        client = AxonFlow(
            agent_url="http://localhost:8080",
            license_key="test-license",
        )
        assert client._has_credentials() is True

    def test_has_credentials_with_both(self):
        """Should detect credentials when both are set."""
        client = AxonFlow(
            agent_url="http://localhost:8080",
            client_id="test-client",
            client_secret="test-secret",
            license_key="test-license",
        )
        assert client._has_credentials() is True

    def test_no_credentials_with_none(self):
        """Should not detect credentials when nothing is set."""
        client = AxonFlow(
            agent_url="http://localhost:8080",
            client_id="test-client",
            # No client_secret, no license_key
        )
        assert client._has_credentials() is False

    def test_no_credentials_with_empty_string(self):
        """Should not detect credentials when empty string is set."""
        client = AxonFlow(
            agent_url="http://localhost:8080",
            client_id="test-client",
            client_secret="",  # Empty string
        )
        assert client._has_credentials() is False

    def test_has_credentials_with_whitespace(self):
        """Whitespace-only string is still considered credentials (non-empty)."""
        client = AxonFlow(
            agent_url="http://localhost:8080",
            client_id="test-client",
            client_secret="   ",  # Whitespace only
        )
        # Note: This is True because " " is truthy in Python
        # Users should not use whitespace as a credential
        assert client._has_credentials() is True
