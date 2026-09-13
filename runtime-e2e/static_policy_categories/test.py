"""Real-stack proof that a static-policy read no longer fails on the platform's categories.

A v11 platform answers ``GET /api/v1/static-policies/effective`` with policies whose
category the SDK's enum did not name (``security-dangerous``), and the strict enum
failed the whole read. This drives the SDK against a real agent and asserts:

1. ``get_effective_static_policies()`` returns instead of raising.
2. The platform's ``security-dangerous`` policies come back as
   ``PolicyCategory.SECURITY_DANGEROUS``.
3. Every category this platform returns is one the SDK knows: none comes back as a
   plain string. (A later platform's new category would, rather than failing the read.)

Run against a community stack::

    AXONFLOW_AGENT_URL=http://localhost:8080 \\
    AXONFLOW_CLIENT_ID=runtime-e2e AXONFLOW_CLIENT_SECRET=runtime-e2e-secret \\
    python runtime-e2e/static_policy_categories/test.py
"""

from __future__ import annotations

import asyncio
import collections
import os
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from axonflow import AxonFlow  # noqa: E402
from axonflow.policies import PolicyCategory  # noqa: E402

ENDPOINT = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")
CLIENT_ID = os.environ.get("AXONFLOW_CLIENT_ID", "runtime-e2e")
CLIENT_SECRET = os.environ.get("AXONFLOW_CLIENT_SECRET", "runtime-e2e-secret")

FAILURES: list[str] = []


def check(ok: bool, description: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'}: {description}")
    if not ok:
        FAILURES.append(description)


async def run(client: AxonFlow) -> None:
    # The route is deprecated and stamped; its warning is not this proof's subject.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        policies = await client.get_effective_static_policies()
    check(True, f"get_effective_static_policies() returned {len(policies)} policies")
    counts = collections.Counter(
        (type(p.category).__name__, getattr(p.category, "value", p.category)) for p in policies
    )
    for (kind, value), n in sorted(counts.items()):
        print(f"  {n:3d}  {kind:15} {value}")
    dangerous = [p for p in policies if p.category == "security-dangerous"]
    check(len(dangerous) > 0, "the platform returns security-dangerous policies")
    check(
        all(p.category is PolicyCategory.SECURITY_DANGEROUS for p in dangerous),
        "each comes back as PolicyCategory.SECURITY_DANGEROUS",
    )
    unknown = sorted({p.category for p in policies if not isinstance(p.category, PolicyCategory)})
    check(unknown == [], f"every category this platform returns is known (unknown: {unknown})")


async def main() -> int:
    print(f"agent: {ENDPOINT}")
    async with AxonFlow(
        endpoint=ENDPOINT, client_id=CLIENT_ID, client_secret=CLIENT_SECRET
    ) as client:
        try:
            await run(client)
        except Exception as e:  # noqa: BLE001 - any failure is reported as a failed proof
            check(False, f"the run raised {type(e).__name__}: {e}")
    if FAILURES:
        print(f"\nFAIL: static_policy_categories ({len(FAILURES)} assertion(s))")
        return 1
    print("\nPASS: static_policy_categories")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
