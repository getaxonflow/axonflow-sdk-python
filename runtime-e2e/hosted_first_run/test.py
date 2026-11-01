"""Guarded website-first proof against a real staging Portal and Agent."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from axonflow import AxonFlow  # noqa: E402 - run this checkout's SDK
from axonflow.community import HOSTED_START_URL, register_try  # noqa: E402

PORTAL = "https://portal.try-staging.getaxonflow.com"
AGENT = "https://try-staging.getaxonflow.com"


async def main() -> None:
    if (
        os.getenv("AXONFLOW_HOSTED_E2E_PORTAL") != PORTAL
        or os.getenv("AXONFLOW_HOSTED_E2E_ENDPOINT") != AGENT
    ):
        print("SKIP: set both exact staging Portal and Agent origins")
        return

    client_id = os.getenv("AXONFLOW_HOSTED_E2E_CLIENT_ID")
    client_secret = os.getenv("AXONFLOW_HOSTED_E2E_CLIENT_SECRET")
    if not client_id or not client_secret:
        msg = "Linked staging client ID and secret are required for the real-stack proof"
        raise RuntimeError(msg)
    if os.getenv("AXONFLOW_TRY") == "1":
        msg = "Unset AXONFLOW_TRY so the SDK uses the exact staging Agent endpoint"
        raise RuntimeError(msg)

    try:
        register_try("new Hosted buyer")
    except ValueError as error:
        if HOSTED_START_URL not in str(error):
            msg = "Hosted registration did not direct to the verified Portal"
            raise AssertionError(msg) from error
    else:
        msg = "Anonymous Hosted registration was allowed"
        raise AssertionError(msg)

    async with httpx.AsyncClient(timeout=10.0) as http:
        portal = await http.get(f"{PORTAL}/hosted/start")
        portal.raise_for_status()
        if not portal.text:
            msg = "Verified Portal returned an empty Hosted start page"
            raise AssertionError(msg)

    async with AxonFlow(endpoint=AGENT, client_id=client_id, client_secret=client_secret) as sdk:
        decision = await sdk.mcp_check_input(
            connector_type="python_sdk.hosted_first_run",
            statement="echo axonflow-hosted-first-run-probe",
            operation="execute",
        )
        if decision.allowed is not True:
            msg = "Linked staging Agent did not allow the harmless governed probe"
            raise AssertionError(msg)

    print("PASS: verified Portal start and linked Python SDK governed Agent decision")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:  # noqa: BLE001
        print(f"FAIL: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(1) from error
