"""Real-stack proof of the v11.0.0 wire through the SDK.

Asserts, through the SDK's public surface against a real v11 agent and
orchestrator:

1. ``decide`` carries ``engine=anchored``, a ``policy_bundle`` digest and a
   ``subject_type``; when ``policy_identities`` is present it names
   ``evaluated_policies`` one for one, in order.
2. The gateway pre-check carries ``decision_id``, a ``verdict`` of allow or
   deny, and the same provenance.
3. MCP check-output carries the provenance.
4. A legacy static-policy read issues ``PlatformRouteDeprecationWarning``
   naming ``/api/v1/typed-policies`` as the successor and ``v11.1`` as the
   removal release.
5. A valid legacy static-policy write and a valid dynamic-policy write each
   raise ``LegacyPolicyWriteFrozenError``.

Leg 5 needs the agent and the orchestrator on the application database role,
as a deployment runs them: the freeze is a revoke on that role, and a stack
connected as the database owner is not bound by it. See README.md.

Run::

    AXONFLOW_AGENT_URL=http://localhost:8080 \\
    AXONFLOW_CLIENT_ID=runtime-e2e AXONFLOW_CLIENT_SECRET=runtime-e2e-secret \\
    python runtime-e2e/v11_decision_provenance/test.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from axonflow import (  # noqa: E402
    AxonFlow,
    AxonFlowError,
    DecideRequest,
    LegacyPolicyWriteFrozenError,
    PlatformRouteDeprecationWarning,
)
from axonflow.policies import (  # noqa: E402
    CreateDynamicPolicyRequest,
    CreateStaticPolicyRequest,
    DynamicPolicyAction,
    DynamicPolicyCondition,
    PolicyAction,
    PolicyCategory,
    PolicySeverity,
)

ENDPOINT = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")
CLIENT_ID = os.environ.get("AXONFLOW_CLIENT_ID", "runtime-e2e")
CLIENT_SECRET = os.environ.get("AXONFLOW_CLIENT_SECRET", "runtime-e2e-secret")

FAILURES: list[str] = []


def check(ok: bool, description: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'}: {description}")
    if not ok:
        FAILURES.append(description)


async def decide_leg(client: AxonFlow) -> None:
    resp = await client.decide(
        DecideRequest(
            stage="tool", query="look up the weather", target={"type": "tool", "tool": "search"}
        )
    )
    print(
        f"  decide: verdict={resp.verdict} engine={resp.engine} subject_type={resp.subject_type} "
        f"policy_bundle={resp.policy_bundle} evaluated={resp.evaluated_policies} "
        f"identities={resp.policy_identities} packs={resp.policy_packs} document_version={resp.document_version}"
    )
    check(resp.engine == "anchored", "decide names the anchored engine")
    check(bool(resp.policy_bundle), "decide carries the policy bundle digest")
    check(bool(resp.subject_type), "decide carries the subject type")
    if resp.policy_identities is not None:
        check(
            [p.id for p in resp.policy_identities] == list(resp.evaluated_policies),
            "decide's policy_identities name evaluated_policies one for one, in order",
        )


async def pre_check_leg(client: AxonFlow) -> None:
    result = await client.pre_check(user_token="tok", query="hello")
    print(
        f"  pre-check: approved={result.approved} decision_id={result.decision_id} verdict={result.verdict} "
        f"engine={result.engine} subject_type={result.subject_type} policy_bundle={result.policy_bundle}"
    )
    check(bool(result.decision_id), "pre-check carries the decision id")
    check(result.verdict in ("allow", "deny"), "pre-check carries the canonical verdict")
    check(result.engine == "anchored", "pre-check names the anchored engine")
    check(bool(result.policy_bundle), "pre-check carries the policy bundle digest")


async def mcp_check_output_leg(client: AxonFlow) -> None:
    resp = await client.mcp_check_output("postgres", message="hello")
    print(
        f"  mcp check-output: allowed={resp.allowed} engine={resp.engine} "
        f"subject_type={resp.subject_type} policy_bundle={resp.policy_bundle}"
    )
    check(resp.engine == "anchored", "MCP check-output names the anchored engine")
    check(bool(resp.policy_bundle), "MCP check-output carries the policy bundle digest")


async def deprecated_read_leg(client: AxonFlow) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await client.list_static_policies()
    stamped = [w.message for w in caught if isinstance(w.message, PlatformRouteDeprecationWarning)]
    for w in stamped:
        print(f"  legacy read warning: {w}")
    check(
        len(stamped) == 1, "a legacy static-policy read issues one PlatformRouteDeprecationWarning"
    )
    if stamped:
        check(
            stamped[0].successor == "/api/v1/typed-policies",
            "the warning names the typed route as the successor",
        )
        check(stamped[0].removed_in == "v11.1", "the warning names v11.1 as the removal release")


async def frozen_write_leg(client: AxonFlow) -> None:
    probe = f"w3o-runtime-probe-{uuid.uuid4().hex[:8]}"
    try:
        await client.create_static_policy(
            CreateStaticPolicyRequest(
                name=probe,
                category=PolicyCategory.SECURITY_SQLI,
                pattern=r"(?i)" + probe.replace("-", "_"),
                severity=PolicySeverity.LOW,
                action=PolicyAction.WARN,
            )
        )
        check(
            False, "a legacy static-policy write raises LegacyPolicyWriteFrozenError (it succeeded)"
        )
    except LegacyPolicyWriteFrozenError as e:
        print(f"  static write refused: code={e.code} message={e.message}")
        check(True, "a legacy static-policy write raises LegacyPolicyWriteFrozenError")
    except AxonFlowError as e:
        check(
            False,
            f"a legacy static-policy write raises LegacyPolicyWriteFrozenError (got {type(e).__name__}: {e})",
        )
    try:
        await client.create_dynamic_policy(
            CreateDynamicPolicyRequest(
                name=probe,
                type="risk",
                category="dynamic-risk",
                conditions=[
                    DynamicPolicyCondition(field="risk_score", operator="greater_than", value=0.99)
                ],
                actions=[DynamicPolicyAction(type="log", config={})],
            )
        )
        check(
            False,
            "a legacy dynamic-policy write raises LegacyPolicyWriteFrozenError (it succeeded)",
        )
    except LegacyPolicyWriteFrozenError as e:
        print(f"  dynamic write refused: code={e.code} message={e.message}")
        check(True, "a legacy dynamic-policy write raises LegacyPolicyWriteFrozenError")
    except AxonFlowError as e:
        check(
            False,
            f"a legacy dynamic-policy write raises LegacyPolicyWriteFrozenError (got {type(e).__name__}: {e})",
        )


async def main() -> int:
    print(f"agent: {ENDPOINT}")
    async with AxonFlow(
        endpoint=ENDPOINT, client_id=CLIENT_ID, client_secret=CLIENT_SECRET
    ) as client:
        for leg in (
            decide_leg,
            pre_check_leg,
            mcp_check_output_leg,
            deprecated_read_leg,
            frozen_write_leg,
        ):
            print(f"== {leg.__name__}")
            try:
                await leg(client)
            except AxonFlowError as e:
                check(False, f"{leg.__name__} raised {type(e).__name__}: {e}")
    if FAILURES:
        print(f"\nFAIL: v11_decision_provenance ({len(FAILURES)} assertion(s))")
        return 1
    print("\nPASS: v11_decision_provenance")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
