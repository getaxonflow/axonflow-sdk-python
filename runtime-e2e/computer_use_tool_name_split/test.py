"""Real-stack assertion: ComputerUseGovernor preserves both tool_name and
action instead of dropping tool_name whenever action is present (#2910,
epic #2905/#2904).

Per CLAUDE.md HARD RULE #0 this test MUST hit a real running AxonFlow agent —
no mocks. ``httpx.AsyncClient.request`` is wrapped only to OBSERVE the
outbound wire body (the real POST to the real agent still happens and its
response is asserted on); this mirrors the capture pattern already used in
``runtime-e2e/x-client-id/test.py``.

Regression background: ``_derive_connector_type(tool_name, action)`` folded
both into a single ``connector_type`` string (``"computer_use.{action}"``),
silently discarding ``tool_name`` whenever ``action`` was present — every
actioned Computer Use tool call (e.g. ``computer`` with
``action="left_click"``) collapsed to the same ``connector_type`` shape
regardless of which tool actually triggered it. ``_derive_connector_type``
is removed; ``ComputerUseGovernor.check_tool_use`` now sends
``connector_type=name`` and ``tool=action`` as two separate wire fields.

Assertions against a live agent:

  1. An actioned tool (``"computer"`` + ``action="left_click"``) sends
     ``connector_type="computer"`` AND ``tool="left_click"`` as two
     distinct wire fields — not the old folded ``"computer_use.left_click"``
     string, and not dropping the tool name — and the live agent allows it.
  2. A non-actioned tool (``"text_editor"``, no ``action`` key) sends
     ``connector_type="text_editor"`` and omits ``tool`` entirely, and the
     live agent allows it.
  3. Two calls to the SAME tool with DIFFERENT actions produce the SAME
     ``connector_type`` but DIFFERENT ``tool`` values — exactly what the
     old bug broke (both actioned calls used to collapse to
     indistinguishable ``connector_type`` strings).
  4. ``check_result()`` — which has no ``action`` available at its call
     site — sends ``connector_type=tool_name`` and never sends ``tool``,
     matching its documented pre-existing behavior (no regression there).

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
        # 1. Actioned tool: tool_name AND action must both survive as two
        #    separate wire fields (the exact bug: action present used to
        #    discard tool_name).
        _captured.clear()
        result = await governor.check_tool_use(
            {"name": "computer", "input": {"action": "left_click", "coordinate": [100, 200]}}
        )
        body = _captured[-1]["json"] if _captured else {}
        check(
            "actioned-connector-type-is-bare-tool-name",
            body.get("connector_type") == "computer",
            f"connector_type={body.get('connector_type')!r}",
        )
        check(
            "actioned-tool-field-carries-action",
            body.get("tool") == "left_click",
            f"tool={body.get('tool')!r}",
        )
        check(
            "actioned-not-folded-into-single-string",
            body.get("connector_type") != "computer_use.left_click",
            f"connector_type={body.get('connector_type')!r} (must not be the old "
            f"concatenated shape)",
        )
        check(
            "actioned-agent-allowed",
            result.allowed and result.policies_evaluated > 0,
            f"allowed={result.allowed} policies_evaluated={result.policies_evaluated}",
        )

        # 2. Non-actioned tool: `tool` must be entirely absent on the wire
        #    (client.py only adds it when truthy).
        _captured.clear()
        result2 = await governor.check_tool_use({"name": "text_editor", "input": {}})
        body2 = _captured[-1]["json"] if _captured else {}
        check(
            "non-actioned-connector-type",
            body2.get("connector_type") == "text_editor",
            f"connector_type={body2.get('connector_type')!r}",
        )
        check(
            "non-actioned-tool-field-absent",
            "tool" not in body2,
            f"body={body2}",
        )
        check(
            "non-actioned-agent-allowed",
            result2.allowed,
            f"allowed={result2.allowed}",
        )

        # 3. Same tool, different actions: connector_type must match while
        #    tool differs — exactly what the old bug could not do (it
        #    dropped tool_name, and different-action calls on unrelated
        #    tools could even collide on the folded string).
        _captured.clear()
        await governor.check_tool_use({"name": "computer", "input": {"action": "screenshot"}})
        first = _captured[-1]["json"] if _captured else {}
        await governor.check_tool_use(
            {"name": "computer", "input": {"action": "left_click", "coordinate": [1, 1]}}
        )
        second = _captured[-1]["json"] if _captured else {}
        check(
            "same-tool-different-actions-share-connector-type",
            first.get("connector_type") == second.get("connector_type") == "computer",
            f"first={first.get('connector_type')!r} second={second.get('connector_type')!r}",
        )
        check(
            "same-tool-different-actions-distinguishable-by-tool",
            first.get("tool") == "screenshot" and second.get("tool") == "left_click",
            f"first tool={first.get('tool')!r} second tool={second.get('tool')!r}",
        )

        # 4. check_result(): no action available at its call site, so
        #    connector_type=tool_name and `tool` is never sent.
        _captured.clear()
        result4 = await governor.check_result("computer", "some tool output text")
        body4 = _captured[-1]["json"] if _captured else {}
        check(
            "check-result-connector-type-is-tool-name",
            body4.get("connector_type") == "computer",
            f"connector_type={body4.get('connector_type')!r}",
        )
        check(
            "check-result-tool-field-absent",
            "tool" not in body4,
            f"body={body4}",
        )
        check(
            "check-result-agent-allowed",
            result4.allowed,
            f"allowed={result4.allowed}",
        )

    if failures:
        print(f"RESULT: FAIL ({len(failures)}): {failures}")
        sys.exit(1)
    print("RESULT: PASS (12/12)")


if __name__ == "__main__":
    asyncio.run(main())
