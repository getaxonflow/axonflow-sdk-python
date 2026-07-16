"""Real-stack assertion: LangGraph MCP interceptor sends server+tool as two
distinct wire fields instead of concatenating them (#2906, epic #2905/#2904).

Per CLAUDE.md HARD RULE #0 this test MUST hit a real running AxonFlow agent —
no mocks. ``httpx.AsyncClient.request`` is wrapped only to OBSERVE the
outbound wire body (the real POST to the real agent still happens and its
response is asserted on); this mirrors the capture pattern already used in
``runtime-e2e/x-client-id/test.py``.

Regression background: before this fix, ``mcp_tool_interceptor`` sent a
single ``connector_type`` string built by hand-concatenating the MCP
server name and tool name (e.g. ``"orders-server.list_orders"``), so a PEP
could not distinguish "which server" from "which tool" without parsing a
string. ``AxonFlowLangGraphAdapter.mcp_tool_interceptor`` and
``tool_output_wrapper`` now call ``client.mcp_check_input`` /
``mcp_check_output`` with ``connector_type=<server/tool source>`` and
``tool=<tool name>`` as two separate wire fields, matching the platform's
two-field (server, tool) identity contract.

Assertions against a live agent:

  1. ``mcp_tool_interceptor()`` — driven with a real (stand-in) MCP
     ``CallToolRequest``-shaped object exposing ``server_name``/``name``/
     ``args`` — sends ``connector_type="orders-server"`` AND
     ``tool="list_orders"`` as two distinct fields on BOTH the
     check-input and check-output wire calls (not a folded
     ``"orders-server.list_orders"`` string), and the live agent allows
     the call.
  2. Backward compatibility: calling ``client.mcp_check_input`` the OLD
     way — a single ``connector_type``, no ``tool`` argument at all —
     still works against the live agent (200, allowed) and the wire body
     omits the ``tool`` key entirely (no silent regression for callers
     who haven't adopted the two-field contract yet).

Run locally:

    AXONFLOW_AGENT_URL=http://localhost:8080 \
    python3 runtime-e2e/langgraph_server_tool_split/test.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import warnings
from typing import Any

import httpx

from axonflow import AxonFlow
from axonflow.adapters.langgraph import AxonFlowLangGraphAdapter

AGENT_URL = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")
CLIENT_ID = os.environ.get("AXONFLOW_CLIENT_ID", "py-sdk-2906-runtime-e2e")

_orig_request = httpx.AsyncClient.request
_captured: list[dict[str, Any]] = []


async def _patched(self: httpx.AsyncClient, method: str, url: Any, **kw: Any) -> httpx.Response:
    resp = await _orig_request(self, method, url, **kw)
    if "/mcp/check-" in str(url):
        _captured.append({"url": str(url), "json": kw.get("json") or {}})
    return resp


httpx.AsyncClient.request = _patched  # type: ignore[method-assign]

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'} [{name}] {detail}")
    if not ok:
        failures.append(name)


class _FakeCallToolRequest:
    """Stand-in for langchain-mcp-adapters' CallToolRequest.

    ``mcp_tool_interceptor`` only reads ``server_name``, ``name``, and
    ``args`` off the request object, so a minimal stand-in exercises the
    real interceptor code path (connector_type/tool resolution + the two
    real HTTP calls to the live agent) without needing a full
    MultiServerMCPClient + live MCP server stack.
    """

    def __init__(self, server_name: str, name: str, args: dict[str, Any]) -> None:
        self.server_name = server_name
        self.name = name
        self.args = args


async def _fake_handler(_request: _FakeCallToolRequest) -> dict[str, Any]:
    # Stand-in for the real tool execution the interceptor wraps. Returning
    # a plain dict exercises the interceptor's own JSON-serialize-then-
    # check-output path against the live agent exactly as it would for a
    # real MCP tool result.
    return {"orders": [{"id": 1, "status": "shipped"}]}


async def main() -> None:
    client = AxonFlow(endpoint=AGENT_URL, client_id=CLIENT_ID)
    adapter = AxonFlowLangGraphAdapter(client, "runtime-e2e-2906-workflow")

    async with client:
        # 1. mcp_tool_interceptor: connector_type (server) and tool (tool
        #    name) must travel as two distinct wire fields.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            interceptor = adapter.mcp_tool_interceptor()

        request = _FakeCallToolRequest(
            server_name="orders-server",
            name="list_orders",
            args={"customer_id": "cust-42"},
        )

        _captured.clear()
        result = await interceptor(request, _fake_handler)

        check(
            "interceptor-produced-a-result",
            result == {"orders": [{"id": 1, "status": "shipped"}]},
            f"result={result!r}",
        )
        check(
            "interceptor-made-two-live-calls",
            len(_captured) == 2,
            f"observed {len(_captured)} check-input/check-output calls: "
            f"{[c['url'] for c in _captured]}",
        )

        for phase, call in zip(("check-input", "check-output"), _captured, strict=False):
            body = call["json"]
            check(
                f"{phase}-connector-type-is-server-name",
                body.get("connector_type") == "orders-server",
                f"connector_type={body.get('connector_type')!r}",
            )
            check(
                f"{phase}-tool-field-is-tool-name",
                body.get("tool") == "list_orders",
                f"tool={body.get('tool')!r}",
            )
            check(
                f"{phase}-not-folded-into-single-string",
                body.get("connector_type") != "orders-server.list_orders",
                f"connector_type={body.get('connector_type')!r} (must not be the old "
                f"concatenated shape)",
            )

        # 2. Backward compat: the OLD single-connector_type call (no `tool`
        #    kwarg at all) must still work unchanged against the live agent,
        #    and must NOT gain a `tool` key on the wire.
        _captured.clear()
        legacy_check = await client.mcp_check_input(
            connector_type="orders-server",
            statement="list_orders(customer_id=cust-42)",
            operation="execute",
        )
        check(
            "legacy-call-agent-allowed",
            legacy_check.allowed,
            f"allowed={legacy_check.allowed} policies_evaluated={legacy_check.policies_evaluated}",
        )
        legacy_body = _captured[-1]["json"] if _captured else {}
        check(
            "legacy-call-connector-type-unchanged",
            legacy_body.get("connector_type") == "orders-server",
            f"connector_type={legacy_body.get('connector_type')!r}",
        )
        check(
            "legacy-call-omits-tool-field",
            "tool" not in legacy_body,
            f"body={legacy_body}",
        )

    if failures:
        print(f"RESULT: FAIL ({len(failures)}): {failures}")
        sys.exit(1)
    print("RESULT: PASS (11/11)")


if __name__ == "__main__":
    asyncio.run(main())
