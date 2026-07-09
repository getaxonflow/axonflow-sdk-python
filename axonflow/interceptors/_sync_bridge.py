"""Shared sync-over-async bridge for interceptor governance calls.

Every provider interceptor has a sync wrap path that must run the async
governance check (``AxonFlow.proxy_llm_call``) to completion BEFORE the
provider call. The old per-interceptor pattern::

    loop = asyncio.get_event_loop()
    response = loop.run_until_complete(...)

raises ``RuntimeError: This event loop is already running`` whenever the
caller sits inside a running loop (FastAPI handler, Jupyter, an async app
driving a sync provider client). A governance check that crashes instead of
completing is the async-adapter-bypass failure class: callers who catch and
continue would proceed ungoverned.

``run_coroutine_sync`` executes the coroutine on ONE persistent background
event loop (daemon thread, created lazily) and blocks the caller until the
verdict is in — whether or not the calling thread has a running loop. A
persistent loop matters: the AxonFlow client's underlying HTTP pool is
loop-affine, so running each governance call on a throwaway loop
(``asyncio.run`` per call) breaks the SECOND call with "Event loop is
closed". Either way the governance result — allow, block, or error — is
returned/raised synchronously and can never be skipped.

Sharing note: an ``AxonFlow`` instance's HTTP pool binds to the first loop
that uses it. An instance driven through this bridge (sync-wrapped provider
clients) must not ALSO be awaited on a caller's own loop — construct one
instance per context (one for sync-wrapped clients, one for async-wrapped).
"""

from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


class _BridgeState:
    """Holder for the shared background loop (avoids module-global rebinding)."""

    lock = threading.Lock()
    loop: asyncio.AbstractEventLoop | None = None


def is_async_callable(fn: Any) -> bool:
    """Detect coroutine functions THROUGH ``functools.wraps`` decorators.

    Modern provider SDKs (openai>=1, anthropic) decorate their async
    ``create`` methods with plain-``def`` wrappers (e.g. ``@required_args``)
    that return the coroutine. ``asyncio.iscoroutinefunction`` on the bound
    method is then False, so an ``AsyncOpenAI`` client silently got the SYNC
    wrap path. ``inspect.unwrap`` follows ``__wrapped__`` to the real
    ``async def``.
    """
    if inspect.iscoroutinefunction(fn):
        return True
    try:
        return inspect.iscoroutinefunction(inspect.unwrap(fn))
    except ValueError:  # __wrapped__ cycle — be conservative
        return False


def _get_bridge_loop() -> asyncio.AbstractEventLoop:
    """Lazily start the shared background loop (daemon thread)."""
    with _BridgeState.lock:
        if _BridgeState.loop is None or _BridgeState.loop.is_closed():
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=loop.run_forever,
                name="axonflow-interceptor-bridge",
                daemon=True,
            )
            thread.start()
            _BridgeState.loop = loop
        return _BridgeState.loop


def run_coroutine_sync(coro: Coroutine[Any, Any, T]) -> T:
    """Run *coro* to completion from sync code, inside or outside a loop.

    Always executes on the shared background loop so repeated calls reuse
    the same loop (and therefore the same HTTP connection pool), and blocks
    the calling thread until the result — the verdict cannot be skipped.
    """
    loop = _get_bridge_loop()
    return asyncio.run_coroutine_threadsafe(coro, loop).result()
