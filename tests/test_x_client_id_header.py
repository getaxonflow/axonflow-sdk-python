"""X-Client-ID header verification (v9 identity).

Every governed request carries X-Client-ID alongside Basic Auth. The
agent's apiAuthMiddleware overwrites the header with its own auth-derived
value, so a missing or wrong client-side header is harmless server-side.
These tests pin SDK-emitted behaviour so future regressions are caught
early.
"""

import pytest

from axonflow import AxonFlow


class TestXClientIDHeader:
    """Pin SDK-emitted X-Client-ID header value across config paths."""

    @pytest.mark.asyncio
    async def test_community_default(self, httpx_mock):
        """No client_id configured → X-Client-ID: community."""
        httpx_mock.add_response(
            url="http://localhost:8080/api/request",
            json={"success": True, "data": {"answer": "ok"}, "blocked": False},
        )

        client = AxonFlow(endpoint="http://localhost:8080")
        async with client:
            await client.proxy_llm_call(
                user_token="",
                query="q",
                request_type="chat",
            )

        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        headers = dict(requests[0].headers)
        assert headers.get("x-client-id") == "community"

    @pytest.mark.asyncio
    async def test_configured_client(self, httpx_mock):
        """client_id="acme" → X-Client-ID: acme."""
        httpx_mock.add_response(
            url="http://localhost:8080/api/request",
            json={"success": True, "data": {"answer": "ok"}, "blocked": False},
        )

        client = AxonFlow(
            endpoint="http://localhost:8080",
            client_id="acme",
            client_secret="secret",
        )
        async with client:
            await client.proxy_llm_call(
                user_token="",
                query="q",
                request_type="chat",
            )

        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        headers = dict(requests[0].headers)
        assert headers.get("x-client-id") == "acme"

    @pytest.mark.asyncio
    async def test_no_legacy_tenant_id_header(self, httpx_mock):
        """SDK does NOT emit X-Tenant-ID (agent accepts it as alias for back-compat)."""
        httpx_mock.add_response(
            url="http://localhost:8080/api/request",
            json={"success": True, "data": {"answer": "ok"}, "blocked": False},
        )

        client = AxonFlow(
            endpoint="http://localhost:8080",
            client_id="acme",
            client_secret="secret",
        )
        async with client:
            await client.proxy_llm_call(
                user_token="",
                query="q",
                request_type="chat",
            )

        requests = httpx_mock.get_requests()
        assert len(requests) == 1
        headers = dict(requests[0].headers)
        assert "x-tenant-id" not in headers
