"""Per-call ``extra_headers`` on the governed methods.

This parameter is the ADR-065 PEP capability handshake's attach point for this
SDK (getaxonflow/axonflow-enterprise#3763). What makes it necessary, rather
than a convenience over the client's default headers, is that ONE PROCESS CAN
BE TWO ENFORCEMENT POINTS: a client whose request path and response path can
discharge different obligations must present different declarations on each,
and a process-wide default header can only carry one document.

So the tests below assert two things, and the second matters as much as the
first:

1. the header reaches the wire on every governed method, and
2. it does NOT leak - not into the next call, and not into the client's
   defaults.

Without (2) a per-call declaration silently becomes a per-client one on the
second request, which is exactly the collapse the parameter exists to avoid.
"""

import pytest

from axonflow import AxonFlow

HANDSHAKE = "eyJwcm9maWxlX3ZlcnNpb24iOjF9"
OTHER = "eyJwcm9maWxlX3ZlcnNpb24iOjJ9"
HEADER = "X-Axonflow-PEP-Handshake"


def _client():
    return AxonFlow(
        endpoint="http://localhost:8080",
        client_id="test-client",
        client_secret="test-secret",
    )


class TestExtraHeadersReachTheWire:
    @pytest.mark.asyncio
    async def test_mcp_check_input(self, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:8080/api/v1/mcp/check-input",
            json={"allowed": True, "policies_evaluated": 1},
        )
        await _client().mcp_check_input(
            "postgres", "select 1", extra_headers={HEADER: HANDSHAKE}
        )
        assert httpx_mock.get_requests()[-1].headers[HEADER] == HANDSHAKE

    @pytest.mark.asyncio
    async def test_mcp_check_output(self, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:8080/api/v1/mcp/check-output",
            json={"allowed": True, "policies_evaluated": 1},
        )
        await _client().mcp_check_output(
            "postgres", message="hi", extra_headers={HEADER: HANDSHAKE}
        )
        assert httpx_mock.get_requests()[-1].headers[HEADER] == HANDSHAKE

    @pytest.mark.asyncio
    async def test_check_tool_input_forwards_it_through_the_delegation(self, httpx_mock):
        # check_tool_input delegates to mcp_check_input. A kwarg added to the
        # signature but not forwarded would type-check, run, and send nothing.
        httpx_mock.add_response(
            url="http://localhost:8080/api/v1/mcp/check-input",
            json={"allowed": True, "policies_evaluated": 1},
        )
        await _client().check_tool_input(
            "postgres", "select 1", extra_headers={HEADER: HANDSHAKE}
        )
        assert httpx_mock.get_requests()[-1].headers[HEADER] == HANDSHAKE

    @pytest.mark.asyncio
    async def test_check_tool_output_forwards_it_through_the_delegation(self, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:8080/api/v1/mcp/check-output",
            json={"allowed": True, "policies_evaluated": 1},
        )
        await _client().check_tool_output(
            "postgres", message="hi", extra_headers={HEADER: HANDSHAKE}
        )
        assert httpx_mock.get_requests()[-1].headers[HEADER] == HANDSHAKE


class TestExtraHeadersDoNotLeak:
    """The property that makes a per-call declaration safe to vary."""

    @pytest.mark.asyncio
    async def test_a_header_from_one_call_does_not_reach_the_next(self, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:8080/api/v1/mcp/check-input",
            json={"allowed": True, "policies_evaluated": 1},
            is_reusable=True,
        )
        client = _client()

        await client.mcp_check_input("postgres", "a", extra_headers={HEADER: HANDSHAKE})
        await client.mcp_check_input("postgres", "b")

        first, second = httpx_mock.get_requests()[-2:]
        assert first.headers[HEADER] == HANDSHAKE
        # The whole point. If this leaks, the second call is attributed to an
        # enforcement point that did not make it.
        assert HEADER not in second.headers

    @pytest.mark.asyncio
    async def test_two_calls_can_present_DIFFERENT_declarations(self, httpx_mock):
        # The two-enforcement-points case this parameter exists for: a request
        # path and a response path in one process, declaring different sets.
        httpx_mock.add_response(
            url="http://localhost:8080/api/v1/mcp/check-input",
            json={"allowed": True, "policies_evaluated": 1},
            is_reusable=True,
        )
        client = _client()

        await client.mcp_check_input("postgres", "a", extra_headers={HEADER: HANDSHAKE})
        await client.mcp_check_input("postgres", "b", extra_headers={HEADER: OTHER})

        first, second = httpx_mock.get_requests()[-2:]
        assert first.headers[HEADER] == HANDSHAKE
        assert second.headers[HEADER] == OTHER

    @pytest.mark.asyncio
    async def test_it_does_not_mutate_the_client_default_headers(self, httpx_mock):
        httpx_mock.add_response(
            url="http://localhost:8080/api/v1/mcp/check-input",
            json={"allowed": True, "policies_evaluated": 1},
        )
        client = _client()
        before = dict(client._http_client.headers)

        await client.mcp_check_input("postgres", "a", extra_headers={HEADER: HANDSHAKE})

        assert HEADER not in client._http_client.headers
        assert dict(client._http_client.headers) == before

    @pytest.mark.asyncio
    async def test_omitting_it_changes_nothing(self, httpx_mock):
        # The additive claim, asserted rather than argued: a caller that does
        # not pass the parameter sends exactly what it sent before.
        httpx_mock.add_response(
            url="http://localhost:8080/api/v1/mcp/check-input",
            json={"allowed": True, "policies_evaluated": 1},
        )
        client = _client()

        await client.mcp_check_input("postgres", "a")

        req = httpx_mock.get_requests()[-1]
        assert HEADER not in req.headers
        # The credential headers the client always sends are untouched.
        assert req.headers["Authorization"].startswith("Basic ")
        assert req.headers["X-Client-ID"] == "test-client"


class TestExtraHeadersDoNotOverrideCredentials:
    @pytest.mark.asyncio
    async def test_the_authorization_header_still_authenticates(self, httpx_mock):
        # httpx merges per-request headers over defaults. That is fine for a
        # declaration and would not be fine for a credential, so this pins that
        # an ordinary declaration leaves auth alone.
        httpx_mock.add_response(
            url="http://localhost:8080/api/v1/mcp/check-input",
            json={"allowed": True, "policies_evaluated": 1},
        )
        await _client().mcp_check_input(
            "postgres", "a", extra_headers={HEADER: HANDSHAKE}
        )
        req = httpx_mock.get_requests()[-1]
        assert req.headers["Authorization"].startswith("Basic ")
        assert req.headers[HEADER] == HANDSHAKE
