"""Auth Header Verification Tests for AxonFlow Python SDK.

These tests verify that auth headers are correctly handled based on credentials,
not on localhost vs non-localhost URLs.

Key behavior:
- When credentials are provided (client_id + client_secret), OAuth2 Basic auth is used
- When no credentials are provided, headers are not sent
- This works for any endpoint (localhost or remote)
"""

import base64

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
    async def test_oauth2_basic_auth_with_client_credentials(self, httpx_mock):
        """OAuth2 Basic auth should be sent when client_id + client_secret are provided."""
        httpx_mock.add_response(
            url="http://localhost:8080/api/request",
            json={"success": True, "data": {"answer": "4"}, "blocked": False},
        )

        client = AxonFlow(
            endpoint="http://localhost:8080",
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

        # Should use OAuth2 Basic auth format
        expected_credentials = base64.b64encode(b"test-client:test-secret").decode()
        assert headers.get("authorization") == f"Basic {expected_credentials}"
        assert headers.get("x-tenant-id") == "test-client"
        # Should NOT use old X-Client-Secret header
        assert "x-client-secret" not in headers
        print("✅ OAuth2 Basic auth sent with client credentials")


class TestAuthHeadersWithoutCredentials:
    """Verify auth headers are correctly handled in community mode (client_id only)."""

    @pytest.mark.asyncio
    async def test_community_mode_client_id_only(self, httpx_mock):
        """Community mode: X-Tenant-ID is set, but no Authorization header."""
        httpx_mock.add_response(
            url="http://localhost:8080/api/request",
            json={"success": True, "data": {"answer": "4"}, "blocked": False},
        )

        client = AxonFlow(
            endpoint="http://localhost:8080",
            client_id="test-client",
            # No client_secret - community mode
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

        # Authorization header should NOT be set without client_secret
        assert "authorization" not in headers
        # X-Tenant-ID SHOULD be set from client_id
        assert headers.get("x-tenant-id") == "test-client"
        # Old headers should not be present
        assert "x-license-key" not in headers
        assert "x-client-secret" not in headers

        print("✅ Community mode: X-Tenant-ID set, no Authorization header")

    @pytest.mark.asyncio
    async def test_no_auth_headers_for_health_check(self, httpx_mock):
        """Health check should not require auth headers."""
        httpx_mock.add_response(
            url="http://localhost:8080/health",
            json={"status": "healthy"},
        )

        client = AxonFlow(
            endpoint="http://localhost:8080",
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
        assert "authorization" not in headers
        assert "x-license-key" not in headers

        print("✅ Health check works without auth headers")


class TestEnterpriseFeatureValidation:
    """Test that enterprise features require client_id before making requests."""

    @pytest.mark.asyncio
    async def test_pre_check_fails_without_client_id(self, httpx_mock):
        """get_policy_approved_context should fail before making request when no client_id."""
        # Don't mock the endpoint - we should fail before making the request
        client = AxonFlow(
            endpoint="http://localhost:8080",
            # No client_id - truly no credentials
            debug=True,
        )

        async with client:
            with pytest.raises(AuthenticationError) as exc_info:
                await client.get_policy_approved_context(
                    user_token="",
                    query="Test query",
                )

            assert "requires client_id" in str(exc_info.value)
            assert "Gateway Mode" in str(exc_info.value)

        # No request should have been made
        requests = httpx_mock.get_requests()
        assert len(requests) == 0

        print("✅ get_policy_approved_context fails without client_id (no request made)")

    @pytest.mark.asyncio
    async def test_audit_fails_without_client_id(self, httpx_mock):
        """audit_llm_call should fail before making request when no client_id."""
        # Don't mock the endpoint - we should fail before making the request
        client = AxonFlow(
            endpoint="http://localhost:8080",
            # No client_id - truly no credentials
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

            assert "requires client_id" in str(exc_info.value)
            assert "Gateway Mode" in str(exc_info.value)

        # No request should have been made
        requests = httpx_mock.get_requests()
        assert len(requests) == 0

        print("✅ audit_llm_call fails without client_id (no request made)")

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
            endpoint="http://localhost:8080",
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

        # Verify request was made with OAuth2 Basic auth header
        requests = httpx_mock.get_requests()
        assert len(requests) == 1

        request = requests[0]
        headers = dict(request.headers)

        expected_credentials = base64.b64encode(b"test-client:test-secret").decode()
        assert headers.get("authorization") == f"Basic {expected_credentials}"

        print("✅ get_policy_approved_context works with credentials")


class TestCredentialDetection:
    """Test the _has_credentials() helper method.

    With OAuth2 pattern, _has_credentials() checks for client_id (not client_secret).
    client_id is required for most API calls.
    client_secret is optional for community mode but required for enterprise.
    """

    def test_has_credentials_with_client_id(self):
        """Should detect credentials when client_id is set."""
        client = AxonFlow(
            endpoint="http://localhost:8080",
            client_id="test-client",
            # No client_secret - community mode
        )
        assert client._has_credentials() is True

    def test_has_credentials_with_full_oauth2(self):
        """Should detect credentials when both client_id and client_secret are set."""
        client = AxonFlow(
            endpoint="http://localhost:8080",
            client_id="test-client",
            client_secret="test-secret",
        )
        assert client._has_credentials() is True

    def test_no_credentials_without_client_id(self):
        """Should not detect credentials when client_id is not set."""
        client = AxonFlow(
            endpoint="http://localhost:8080",
            # No client_id
        )
        assert client._has_credentials() is False

    def test_no_credentials_with_empty_client_id(self):
        """Should not detect credentials when client_id is empty string."""
        client = AxonFlow(
            endpoint="http://localhost:8080",
            client_id="",  # Empty string
        )
        assert client._has_credentials() is False
