"""Real-stack assertion: SDK emits X-Axonflow-Client: sdk-python/<__version__>.

Per CLAUDE.md HARD RULE #0 — this test MUST hit a real agent.
"""

import asyncio
import os
import sys

import httpx

from axonflow import AxonFlow
from axonflow._version import __version__

EXPECTED = f"sdk-python/{__version__}"
ENDPOINT = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")
TENANT = os.environ.get("AXONFLOW_TENANT_ID")
SECRET = os.environ.get("AXONFLOW_TENANT_SECRET")
PLUGIN_TOKEN = os.environ.get("AXONFLOW_E2E_PLUGIN_TOKEN")

_missing = []
if not TENANT:
    _missing.append("AXONFLOW_TENANT_ID")
if not SECRET:
    _missing.append("AXONFLOW_TENANT_SECRET")  # noqa: S105
if not PLUGIN_TOKEN:
    _missing.append("AXONFLOW_E2E_PLUGIN_TOKEN")  # noqa: S105
if _missing:
    sys.stderr.write("required env vars not set; see ../README.md\n")
    sys.exit(2)

# Wrap httpx so we can inject X-License-Token (forces scope-mismatch path
# so the agent echoes our X-Axonflow-Client value back in its response).
_orig = httpx.AsyncClient.request


async def patched(self, method, url, **kw):
    headers = dict(kw.get("headers") or {})
    headers["X-License-Token"] = PLUGIN_TOKEN
    kw["headers"] = headers
    return await _orig(self, method, url, **kw)


httpx.AsyncClient.request = patched


async def main():
    print(f"Asserting wire X-Axonflow-Client = {EXPECTED}")
    client = AxonFlow(endpoint=ENDPOINT, client_id=TENANT, client_secret=SECRET)
    async with client:
        try:
            await client.proxy_llm_call(user_token="", query="ping", request_type="chat")
        except Exception as e:
            msg = str(e)
            if EXPECTED in msg:
                print(f"PASS: agent reflected {EXPECTED} in scope_mismatch response")
                sys.exit(0)
            # Some error wrappers may strip the agent's body; do a fallback check.
            if "scope_mismatch" in msg or "Invalid credentials" in msg:
                print(
                    f"PARTIAL: agent rejected as expected but response didn't echo client header verbatim ({msg[:200]})",
                    file=sys.stderr,
                )
                sys.exit(1)
            print(f"FAIL: unexpected error: {msg[:200]}", file=sys.stderr)
            sys.exit(1)
        print("UNEXPECTED 200 — agent should have rejected scope_mismatch", file=sys.stderr)
        sys.exit(1)


asyncio.run(main())
