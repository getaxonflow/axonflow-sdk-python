"""Real-stack assertion: ComputerUseGovernor reports the (server, tool)
identity as two separate wire fields instead of concatenating them, and the
tool NAME is never dropped (#2910, epic #2905/#2904, RULING 1).

Per CLAUDE.md HARD RULE #0 this test MUST hit a real running AxonFlow agent —
no mocks. ``httpx.AsyncClient.request`` is wrapped only to OBSERVE the
outbound wire body (the real POST to the real agent still happens and its
response is asserted on); this mirrors the capture pattern already used in
``runtime-e2e/x-client-id/test.py``.

Regression background: ``_derive_connector_type(tool_name, action)`` folded
the tool name and the action into a single ``connector_type`` string
(``"computer_use.{action}"``), silently discarding ``tool_name`` whenever
``action`` was present — every actioned Computer Use tool call (e.g.
``computer`` with ``action="left_click"``) collapsed to the same
``connector_type`` shape regardless of which tool actually triggered it.
``_derive_connector_type`` is removed; ``ComputerUseGovernor`` now sends
``connector_type="computer_use"`` (the constant connector/domain marker) and
``tool=<tool name>`` — the same ``(server, tool)`` mapping the LangGraph
adapter uses. The action is preserved inside ``statement`` (the serialized
tool input), NOT in ``tool`` and NOT in ``operation`` (the agent-api spec
constrains ``operation`` to ``{query, execute}``).

Assertions against a live agent:

  1. An actioned tool (``"computer"`` + ``action="left_click"``) sends
     ``connector_type="computer_use"`` AND ``tool="computer"`` as two
     distinct wire fields — not the old folded ``"computer_use.left_click"``
     string, not the action in ``tool`` — with the action still present in
     the serialized ``statement``, ``operation`` still ``"execute"``, and the
     live agent allows it.
  2. A non-actioned tool (``"bash"``) sends ``connector_type="computer_use"``
     and ``tool="bash"``, and the live agent allows it.
  3. Two calls to the SAME tool with DIFFERENT actions produce the SAME
     ``connector_type`` AND the SAME ``tool`` (the tool identity is stable) —
     the differing action lives in ``statement``. This is exactly what the
     old bug broke (it dropped the tool name entirely).
  4. ``check_result()`` sends the same identity on the response plane
     (``connector_type="computer_use"``, ``tool=<tool name>``) so request-
     and response-plane rows correlate. The platform does not yet consume
     ``tool`` on check-output (#2955); it is sent forward-compatibly.

Run locally:

    AXONFLOW_AGENT_URL=http://localhost:8080 \
    python3 runtime-e2e/computer_use_tool_name_split/test.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Any

import httpx

from axonflow import AxonFlow
from axonflow.adapters.computer_use import ComputerUseGovernor

AGENT_URL = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")
CLIENT_ID = os.environ.get("AXONFLOW_CLIENT_ID", "py-sdk-2910-runtime-e2e")

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


async def main() -> None:
    client = AxonFlow(endpoint=AGENT_URL, client_id=CLIENT_ID)
    governor = ComputerUseGovernor(client)

    async with client:
        # 1. Actioned tool: connector_type is the constant domain marker,
        #    tool is the tool NAME (never the action), the action survives in
        #    the serialized statement, and operation stays spec-compliant.
        _captured.clear()
        result = await governor.check_tool_use(
            {"name": "computer", "input": {"action": "left_click", "coordinate": [100, 200]}}
        )
        body = _captured[-1]["json"] if _captured else {}
        check(
            "actioned-connector-type-is-domain-marker",
            body.get("connector_type") == "computer_use",
            f"connector_type={body.get('connector_type')!r}",
        )
        check(
            "actioned-tool-field-is-tool-name",
            body.get("tool") == "computer",
            f"tool={body.get('tool')!r}",
        )
        check(
            "actioned-not-folded-into-single-string",
            body.get("connector_type") != "computer_use.left_click",
            f"connector_type={body.get('connector_type')!r} (must not be the old "
            f"concatenated shape)",
        )
        check(
            "actioned-action-preserved-in-statement",
            "left_click" in (body.get("statement") or ""),
            f"statement={body.get('statement')!r}",
        )
        check(
            "actioned-operation-spec-compliant",
            body.get("operation") == "execute",
            f"operation={body.get('operation')!r} (must stay in the "
            f"agent-api enum {{query, execute}})",
        )
        check(
            "actioned-agent-allowed",
            result.allowed and result.policies_evaluated > 0,
            f"allowed={result.allowed} policies_evaluated={result.policies_evaluated}",
        )

        # 2. Non-actioned tool: connector_type is still the domain marker and
        #    tool carries the tool name.
        _captured.clear()
        result2 = await governor.check_tool_use({"name": "bash", "input": {"command": "ls"}})
        body2 = _captured[-1]["json"] if _captured else {}
        check(
            "non-actioned-connector-type",
            body2.get("connector_type") == "computer_use",
            f"connector_type={body2.get('connector_type')!r}",
        )
        check(
            "non-actioned-tool-field-is-tool-name",
            body2.get("tool") == "bash",
            f"tool={body2.get('tool')!r}",
        )
        check(
            "non-actioned-agent-allowed",
            result2.allowed,
            f"allowed={result2.allowed}",
        )

        # 3. Same tool, different actions: the (connector_type, tool) identity
        #    is stable across both calls — the differing action lives in the
        #    statement, not the tool identity. The old bug dropped the tool
        #    name entirely, so identity could not survive at all.
        _captured.clear()
        await governor.check_tool_use({"name": "computer", "input": {"action": "screenshot"}})
        first = _captured[-1]["json"] if _captured else {}
        await governor.check_tool_use(
            {"name": "computer", "input": {"action": "left_click", "coordinate": [1, 1]}}
        )
        second = _captured[-1]["json"] if _captured else {}
        check(
            "same-tool-different-actions-share-identity",
            first.get("connector_type") == second.get("connector_type") == "computer_use"
            and first.get("tool") == second.get("tool") == "computer",
            f"first=({first.get('connector_type')!r},{first.get('tool')!r}) "
            f"second=({second.get('connector_type')!r},{second.get('tool')!r})",
        )
        check(
            "same-tool-different-actions-distinguishable-in-statement",
            "screenshot" in (first.get("statement") or "")
            and "left_click" in (second.get("statement") or ""),
            f"first statement={first.get('statement')!r} "
            f"second statement={second.get('statement')!r}",
        )

        # 4. check_result(): same identity on the response plane so request-
        #    and response-plane rows correlate. The platform does not yet
        #    consume `tool` on check-output (#2955); it is sent
        #    forward-compatibly.
        _captured.clear()
        result4 = await governor.check_result("computer", "some tool output text")
        body4 = _captured[-1]["json"] if _captured else {}
        check(
            "check-result-connector-type-is-domain-marker",
            body4.get("connector_type") == "computer_use",
            f"connector_type={body4.get('connector_type')!r}",
        )
        check(
            "check-result-tool-field-is-tool-name",
            body4.get("tool") == "computer",
            f"tool={body4.get('tool')!r}",
        )
        check(
            "check-result-agent-allowed",
            result4.allowed,
            f"allowed={result4.allowed}",
        )

    if failures:
        print(f"RESULT: FAIL ({len(failures)}): {failures}")
        sys.exit(1)
    print(f"RESULT: PASS ({14 - len(failures)}/14)")


if __name__ == "__main__":
    asyncio.run(main())
