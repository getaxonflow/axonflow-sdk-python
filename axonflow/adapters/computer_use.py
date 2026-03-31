"""AxonFlow ComputerUseGovernor — governance middleware for Anthropic Computer Use.

Evaluates Computer Use tool_use blocks against AxonFlow policies before
execution, and scans tool results before feeding them back to Claude.

Drop into the sampling loop at two points:

1. After Claude returns tool_use blocks, call ``check_tool_use()`` on each
   block before executing it.
2. After executing a tool, call ``check_result()`` on the result before
   appending it to the message history.

Example::

    from anthropic import Anthropic
    from axonflow import AxonFlow
    from axonflow.adapters import ComputerUseGovernor

    async with AxonFlow(endpoint="http://localhost:8080") as client:
        governor = ComputerUseGovernor(client)

        # In your sampling loop:
        for block in response.content:
            if block.type == "tool_use":
                check = await governor.check_tool_use({
                    "type": "tool_use",
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })
                if not check.allowed:
                    # Skip this tool call, tell Claude it was blocked
                    continue

                result = execute_tool(block)

                result_check = await governor.check_result(block.name, result)
                if result_check.redacted_result is not None:
                    result = result_check.redacted_result
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from axonflow import AxonFlow


# Default patterns for dangerous bash commands
DEFAULT_BLOCKED_BASH_PATTERNS: list[str] = [
    r"rm\s+-rf\s+/",
    r"dd\s+if=",
    r"mkfs\b",
    r"curl\s+.*\|\s*(ba)?sh",
    r"wget\s+.*\|\s*(ba)?sh",
    r"cat\s+~/\.ssh/",
    r"cat\s+~/\.aws/",
    r"cat\s+\.env\b",
    r"chmod\s+777\b",
    r">\s*/dev/sd[a-z]",
]


@dataclass(frozen=True)
class CheckResult:
    """Result of a governance check on a tool_use block or tool result."""

    allowed: bool
    """Whether the action/result is permitted."""

    block_reason: str | None = None
    """Reason for blocking, if not allowed."""

    redacted_result: str | None = None
    """Redacted version of the tool result, if PII/secrets were found.
    Only populated by ``check_result()``, not ``check_tool_use()``."""

    policies_evaluated: int = 0
    """Number of policies the server evaluated."""


def _derive_connector_type(tool_name: str, action: str | None = None) -> str:
    """Derive AxonFlow connector_type from Computer Use tool name and action.

    Examples:
        _derive_connector_type("computer", "left_click") -> "computer_use.left_click"
        _derive_connector_type("bash") -> "computer_use.bash"
        _derive_connector_type("text_editor") -> "computer_use.text_editor"
    """
    if action:
        return f"computer_use.{action}"
    return f"computer_use.{tool_name}"


class ComputerUseGovernor:
    """Governance middleware for Anthropic Computer Use tool_use blocks.

    Insert into the sampling loop to evaluate every tool action against
    AxonFlow policies before execution, and scan results before they
    reach Claude's context.

    Args:
        client: An authenticated :class:`axonflow.AxonFlow` client.
        blocked_bash_patterns: Regex patterns for bash commands to block
            client-side before even calling the server. Defaults to
            common destructive/exfiltration patterns.
    """

    def __init__(
        self,
        client: AxonFlow,
        *,
        blocked_bash_patterns: list[str] | None = None,
    ) -> None:
        self._client = client
        self._bash_patterns = [
            re.compile(p, re.IGNORECASE)
            for p in (
                blocked_bash_patterns
                if blocked_bash_patterns is not None
                else DEFAULT_BLOCKED_BASH_PATTERNS
            )
        ]

    def _check_bash_locally(self, command: str) -> str | None:
        """Check bash command against local blocked patterns.

        Returns the matching pattern description if blocked, None if allowed.
        """
        for pattern in self._bash_patterns:
            if pattern.search(command):
                return f"Blocked by local pattern: {pattern.pattern}"
        return None

    async def check_tool_use(self, tool_use_block: dict[str, Any]) -> CheckResult:
        """Check a tool_use block before execution.

        Args:
            tool_use_block: The tool_use content block from Claude's response.
                Expected keys: ``name`` (str), ``input`` (dict).
                Optional: ``id`` (str), ``type`` (str).

        Returns:
            CheckResult indicating whether the tool call is allowed.
        """
        name = tool_use_block.get("name", "unknown")
        tool_input = tool_use_block.get("input", {})

        # For bash tools, check locally first (fast, no network)
        if name in ("bash", "bash_20250124", "bash_20241022"):
            command = tool_input.get("command", "")
            if isinstance(command, str):
                local_block = self._check_bash_locally(command)
                if local_block is not None:
                    return CheckResult(
                        allowed=False,
                        block_reason=local_block,
                        policies_evaluated=0,
                    )

        # Derive connector_type from tool name + action
        action = tool_input.get("action") if isinstance(tool_input, dict) else None
        connector_type = _derive_connector_type(
            name,
            action if isinstance(action, str) else None,
        )

        # Serialize input for policy evaluation
        statement = json.dumps(tool_input, default=str)

        check = await self._client.mcp_check_input(
            connector_type=connector_type,
            statement=statement,
            operation="execute",
        )

        if not check.allowed:
            return CheckResult(
                allowed=False,
                block_reason=check.block_reason or "Blocked by AxonFlow policy",
                policies_evaluated=check.policies_evaluated,
            )

        return CheckResult(
            allowed=True,
            policies_evaluated=check.policies_evaluated,
        )

    async def check_result(
        self,
        tool_name: str,
        result: str,
    ) -> CheckResult:
        """Check a tool execution result before feeding it back to Claude.

        Args:
            tool_name: The tool name (e.g., "computer", "bash", "text_editor").
            result: The text result from tool execution.

        Returns:
            CheckResult with allowed status and optional redacted_result.
        """
        connector_type = _derive_connector_type(tool_name)

        check = await self._client.mcp_check_output(
            connector_type=connector_type,
            message=result,
        )

        if not check.allowed:
            return CheckResult(
                allowed=False,
                block_reason=check.block_reason or "Tool result blocked by policy",
                policies_evaluated=check.policies_evaluated,
            )

        if check.redacted_data is not None:
            return CheckResult(
                allowed=True,
                redacted_result=(
                    check.redacted_data
                    if isinstance(check.redacted_data, str)
                    else json.dumps(check.redacted_data, default=str)
                ),
                policies_evaluated=check.policies_evaluated,
            )

        return CheckResult(
            allowed=True,
            policies_evaluated=check.policies_evaluated,
        )
