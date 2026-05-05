"""X-Axonflow-Client header injection — ADR-050 §4.

Asserts every governed HTTP request forwards
``X-Axonflow-Client: sdk-python/<__version__>`` so the agent can derive
request scope (sdk) and validate against the token's aud.scope via
HasScope().

Header is sourced from the bundled __version__ constant; the consumer
cannot spoof its own client identity through config (intentional —
honest-99% header injection per ADR-050 §4).
"""

import pytest

from axonflow import AxonFlow
from axonflow._version import __version__


EXPECTED_CLIENT = f"sdk-python/{__version__}"


@pytest.mark.asyncio
async def test_client_header_present_on_proxy_call(httpx_mock):
    """Every governed request includes X-Axonflow-Client."""
    httpx_mock.add_response(
        url="http://localhost:8080/api/request",
        json={"success": True, "data": {"answer": "ok"}, "blocked": False},
    )

    client = AxonFlow(
        endpoint="http://localhost:8080",
        client_id="test-client",
        client_secret="test-secret",
    )
    async with client:
        await client.proxy_llm_call(user_token="", query="ping", request_type="chat")

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    headers = dict(requests[0].headers)
    # httpx normalizes header keys to lowercase
    assert headers.get("x-axonflow-client") == EXPECTED_CLIENT


@pytest.mark.asyncio
async def test_client_header_format_is_id_slash_semver():
    """Sanity: the value matches sdk-python/<semver>.

    The agent's deriveScopeFromClientHeader splits on '/' and maps
    "sdk-*" prefixes to scope=sdk. If we ever ship a different shape
    this fails loudly so we don't regress agent-side parsing.
    """
    import re

    assert EXPECTED_CLIENT.startswith("sdk-python/")
    assert re.match(r"^sdk-python/\d+\.\d+\.\d+", EXPECTED_CLIENT)
    assert EXPECTED_CLIENT.count("/") == 1
