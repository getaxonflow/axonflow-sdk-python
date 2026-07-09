"""Real-stack assertion: interceptor sync bridge enforces inside a running loop.

Per CLAUDE.md HARD RULE #0 this test MUST hit a real running AxonFlow agent —
no mocks (the OpenAI client below is the REAL ``openai`` package; it is never
contacted because governance blocks before the provider call).

Regression background (getaxonflow/axonflow-enterprise#2861): every provider
interceptor's sync wrap path ran the async governance check with
``loop.run_until_complete``, which raises ``RuntimeError: This event loop is
already running`` whenever the caller sits inside a running loop (FastAPI
handler, Jupyter, an async app driving a sync provider client). The
governance check crashed instead of completing — the async-adapter-bypass
class. A second detection bug sent ``AsyncOpenAI`` clients down that sync
path: openai>=1 decorates its async ``create`` with a plain-``def`` wrapper,
so ``asyncio.iscoroutinefunction`` returned False.

Assertions against a live agent:

  1. A REAL sync ``openai.OpenAI`` client wrapped with ``wrap_openai_client``
     and driven FROM INSIDE ``asyncio.run`` gets a blocked verdict for a
     stacked-SQLi prompt: ``PolicyViolationError`` raised, no RuntimeError,
     and the provider is never called (dummy API key would fail loudly).
  2. The same wrapped client called from plain sync code enforces
     identically.
  3. A REAL ``openai.AsyncOpenAI`` client is detected as async
     (``is_async_callable``) — its wrapped ``create`` is a coroutine
     function, so the verdict is awaited on the caller's loop.

Run locally (after `source /tmp/axonflow-e2e-env.sh` from the enterprise
setup script; requires `pip install openai`):

    AXONFLOW_AGENT_URL=http://localhost:8080 \
    AXONFLOW_CLIENT_ID="$AXONFLOW_CLIENT_ID" \
    AXONFLOW_CLIENT_SECRET="$AXONFLOW_CLIENT_SECRET" \
    AXONFLOW_USER_TOKEN="$AXONFLOW_USER_TOKEN" \
    python3 runtime-e2e/interceptor_sync_bridge/test.py
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys

import openai

from axonflow import AxonFlow
from axonflow.exceptions import PolicyViolationError
from axonflow.interceptors._sync_bridge import is_async_callable
from axonflow.interceptors.openai import wrap_openai_client

AGENT_URL = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")
CLIENT_ID = os.environ.get("AXONFLOW_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("AXONFLOW_CLIENT_SECRET", "")
USER_TOKEN = os.environ.get("AXONFLOW_USER_TOKEN", "")

# Blocked on every stack posture by the sys_sqli_* static policies.
SQLI_PROMPT = "SELECT * FROM users WHERE 1=1; DROP TABLE users;--"

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'} [{name}] {detail}")
    if not ok:
        failures.append(name)


def make_wrapped_sync_client(axonflow: AxonFlow) -> object:
    # REAL openai client, dummy key: if governance ever failed open, the
    # provider call would error loudly on auth instead of being blocked.
    sync_openai = openai.OpenAI(api_key="sk-dummy-never-called", max_retries=0)
    return wrap_openai_client(sync_openai, axonflow, user_token=USER_TOKEN)


def drive_blocked(wrapped: object, where: str) -> None:
    try:
        wrapped.chat.completions.create(  # type: ignore[attr-defined]
            model="gpt-4",
            messages=[{"role": "user", "content": SQLI_PROMPT}],
        )
        check(where, False, "call succeeded — governance did not block")
    except PolicyViolationError as e:
        check(where, True, f"blocked as expected: {e}")
    except RuntimeError as e:
        check(where, False, f"RuntimeError (bridge regression): {e}")


def main() -> None:
    if not CLIENT_ID or not CLIENT_SECRET or not USER_TOKEN:
        print(
            "FAIL: AXONFLOW_CLIENT_ID, AXONFLOW_CLIENT_SECRET and "
            "AXONFLOW_USER_TOKEN must be set (source /tmp/axonflow-e2e-env.sh)"
        )
        sys.exit(1)

    axonflow = AxonFlow(
        endpoint=AGENT_URL,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
    )
    wrapped = make_wrapped_sync_client(axonflow)

    # 1. Sync provider client driven from INSIDE a running event loop.
    async def driver() -> None:
        drive_blocked(wrapped, "sync-client-inside-running-loop")

    asyncio.run(driver())

    # 2. Same wrapped client from plain sync code.
    drive_blocked(wrapped, "sync-client-plain-sync")

    # 3. AsyncOpenAI must be detected as async through openai's decorators.
    async_openai = openai.AsyncOpenAI(api_key="sk-dummy-never-called", max_retries=0)
    detected = is_async_callable(async_openai.chat.completions.create)
    check(
        "async-client-detection",
        detected,
        "AsyncOpenAI.create detected as coroutine function through @required_args",
    )
    if detected:
        # Fresh AxonFlow instance for the async leg: httpx connection pools
        # are loop-affine, so one instance must not be shared between the
        # sync bridge loop and a caller's own loop (see _sync_bridge docs).
        axonflow_async = AxonFlow(
            endpoint=AGENT_URL,
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
        )
        wrapped_async = wrap_openai_client(async_openai, axonflow_async, user_token=USER_TOKEN)
        installed = wrapped_async.chat.completions.create
        check(
            "async-client-gets-async-wrapper",
            inspect.iscoroutinefunction(installed),
            "wrapped create is async def (verdict awaited on caller's loop)",
        )

        async def async_driver() -> None:
            try:
                await wrapped_async.chat.completions.create(
                    model="gpt-4",
                    messages=[{"role": "user", "content": SQLI_PROMPT}],
                )
                check("async-client-blocked", False, "call succeeded — not blocked")
            except PolicyViolationError as e:
                check("async-client-blocked", True, f"blocked as expected: {e}")

        asyncio.run(async_driver())

    if failures:
        print(f"RESULT: FAIL ({len(failures)}): {failures}")
        sys.exit(1)
    print("RESULT: PASS (5/5)")


if __name__ == "__main__":
    main()
