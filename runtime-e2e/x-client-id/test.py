"""Real-stack assertion: SDK emits X-Client-ID: <effective_client_id> (v9).

Per CLAUDE.md HARD RULE #0 — this test MUST hit a real agent.

Approach: wrap httpx so we can capture the outbound X-Client-ID header
on the actual wire send. The agent does NOT need to reflect this header
(unlike X-Axonflow-Client which uses scope_mismatch echo). We just
read what the SDK wrote.
"""

import asyncio
import contextlib
import os
import sys

import httpx

from axonflow import AxonFlow

ENDPOINT = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")
TENANT = os.environ.get("AXONFLOW_TENANT_ID")
SECRET = os.environ.get("AXONFLOW_TENANT_SECRET")

if not TENANT or not SECRET:
    sys.stderr.write(
        "AXONFLOW_TENANT_ID + AXONFLOW_TENANT_SECRET must be set; see ../README.md\n"
    )
    sys.exit(2)

_orig = httpx.AsyncClient.request
_captured: dict[str, str] = {}


async def patched(self, method, url, **kw):
    # Merge SDK's default headers (set on AsyncClient construction) with
    # any per-request override so we read what's actually sent.
    headers = {**dict(self.headers), **dict(kw.get("headers") or {})}
    _captured["x-client-id"] = headers.get("x-client-id", headers.get("X-Client-ID", ""))
    return await _orig(self, method, url, **kw)


httpx.AsyncClient.request = patched


async def main():
    print(f"Asserting wire X-Client-ID = {TENANT}")
    client = AxonFlow(endpoint=ENDPOINT, client_id=TENANT, client_secret=SECRET)
    async with client, contextlib.suppress(Exception):
        # outcome of the call doesn't matter; only the captured header
        await client.proxy_llm_call(user_token="", query="ping", request_type="chat")
    got = _captured.get("x-client-id", "")
    if got != TENANT:
        sys.stderr.write(f"FAIL: wire X-Client-ID = {got!r}, want {TENANT!r}\n")
        sys.exit(1)
    print(f"PASS: wire X-Client-ID = {got!r}")


asyncio.run(main())
