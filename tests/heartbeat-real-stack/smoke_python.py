"""Cross-platform real-stack smoke for the Python SDK.

Constructs `AxonFlow(...)` against a fake agent + checkpoint server and
verifies the constructor's heartbeat fires through the real public API
plus a stamp lands at the OS-native cache path. Reads:

    AXONFLOW_AGENT_URL       — fake agent base URL
    AXONFLOW_CHECKPOINT_URL  — fake checkpoint URL
    HOME / XDG_CACHE_HOME / LOCALAPPDATA — clean tmpdir so the stamp
                                            lands in a fresh location

Exits 0 on success, 1 on failure (any reason).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from axonflow import AxonFlow


def expected_stamp_path() -> Path:
    """Mirror axonflow.heartbeat._resolve_stamp_path() exactly."""
    if sys.platform == "darwin":
        home = os.environ["HOME"]
        return Path(home) / "Library" / "Caches" / "axonflow" / "python-telemetry-last-sent"
    if sys.platform == "win32":
        local = os.environ["LOCALAPPDATA"]
        return Path(local) / "axonflow" / "python-telemetry-last-sent"
    # Linux / *BSD / others — XDG.
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "axonflow" / "python-telemetry-last-sent"
    home = os.environ["HOME"]
    return Path(home) / ".cache" / "axonflow" / "python-telemetry-last-sent"


async def main() -> int:
    agent = os.environ["AXONFLOW_AGENT_URL"]
    stamp = expected_stamp_path()

    # Construct the SDK through the real public API.
    async with AxonFlow(
        endpoint=agent,
        client_id="smoke-test",
        client_secret="smoke-secret",
    ) as client:
        try:
            await client.health_check()
        except Exception:
            pass

    # Drain the heartbeat thread (Python uses daemon thread + atexit).
    from axonflow import heartbeat as hb

    with hb._pending_threads_lock:
        threads = list(hb._pending_threads)
    for t in threads:
        t.join(timeout=10.0)

    if not stamp.exists():
        print(f"FAIL: stamp not at {stamp}", file=sys.stderr)
        return 1
    print(f"OK: stamp at {stamp}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
