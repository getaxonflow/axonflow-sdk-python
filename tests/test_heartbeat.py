"""Tests for the 7-day delivered-heartbeat gate.

The matrix mirrors the cross-SDK reference (see Go SDK heartbeat_test.go):

    1. cold start, no stamp           → 1 ping fires, stamp written
    2. fresh stamp (1d old)           → 0 pings
    3. stale stamp (8d old)           → 1 ping, stamp updated
    4. 5 calls within 1h cache        → exactly 1 ping
    5. cache expired + stale stamp    → 2nd ping fires
    6. AXONFLOW_TELEMETRY=off mid-run → 0 pings, stamp unchanged
    7. 100 concurrent callers         → exactly 1 ping (stampede coalesced)
    8. no cache dir (stamp_path=None) → 1 ping per process, no crash
    9. ping returns failure           → stamp NOT written; retry on success works
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from axonflow import heartbeat as hb_module
from axonflow.heartbeat import (
    HEARTBEAT_GUARD_INTERVAL_S,
    HEARTBEAT_INTERVAL_S,
    maybe_send_heartbeat,
    replace_heartbeat_state_for_test,
)

# Helpers --------------------------------------------------------------------


def _wait_for_threads() -> None:
    """Block until all spawned heartbeat threads complete.

    The gate spawns daemon threads for each ping. Tests inspect mock-fetch
    call counts after the gate runs, which is racy without an explicit join.
    """
    with hb_module._pending_threads_lock:  # noqa: SLF001 — module-private guard
        threads = list(hb_module._pending_threads)
    for t in threads:
        t.join(timeout=2.0)


@pytest.fixture
def isolated_state(tmp_path: Path):
    """Replace the module-level singleton with a fresh state pointing at a
    temp stamp file, restoring on cleanup. Each test gets clean state.
    """
    stamp = tmp_path / "stamp"
    previous = replace_heartbeat_state_for_test(stamp)
    yield hb_module._state  # noqa: SLF001
    replace_heartbeat_state_for_test(None)  # restore default resolution
    hb_module._state = previous  # noqa: SLF001 — explicit restore for safety


@pytest.fixture
def mock_ping_success(monkeypatch):
    """Patch ``_send_telemetry_ping_now`` to succeed instantly. Returns a
    MagicMock the test can assert on for call counts.
    """
    mock = MagicMock(return_value=True)
    monkeypatch.setattr("axonflow.telemetry._send_telemetry_ping_now", mock)
    return mock


@pytest.fixture
def mock_ping_failure(monkeypatch):
    """Patch ``_send_telemetry_ping_now`` to fail. Stamp must NOT advance."""
    mock = MagicMock(return_value=False)
    monkeypatch.setattr("axonflow.telemetry._send_telemetry_ping_now", mock)
    return mock


@pytest.fixture
def telemetry_enabled_env(monkeypatch):
    """Ensure neither AXONFLOW_TELEMETRY=off nor any opt-out is set so the
    gate exercises the firing path. Conftest.py blocks real httpx egress
    via httpx.post / httpx.get patches, so this is safe even if mocks miss.
    """
    monkeypatch.delenv("AXONFLOW_TELEMETRY", raising=False)


# 9-case matrix --------------------------------------------------------------


def test_cold_start_no_stamp_fires_once(isolated_state, mock_ping_success, telemetry_enabled_env):
    """Case 1: cold start, no stamp → 1 ping, stamp written."""
    maybe_send_heartbeat(mode="production", endpoint="http://localhost")
    _wait_for_threads()

    assert mock_ping_success.call_count == 1
    assert isolated_state.stamp_path.exists(), "stamp file should be written on success"


def test_fresh_stamp_does_not_fire(isolated_state, mock_ping_success, telemetry_enabled_env):
    """Case 2: stamp written 1d ago → 0 pings."""
    isolated_state.stamp_path.parent.mkdir(parents=True, exist_ok=True)
    isolated_state.stamp_path.write_text("last_sent=test\n")
    one_day_ago = __import__("time").time() - 24 * 3600
    os.utime(isolated_state.stamp_path, (one_day_ago, one_day_ago))

    maybe_send_heartbeat(mode="production", endpoint="http://localhost")
    _wait_for_threads()

    assert mock_ping_success.call_count == 0


def test_stale_stamp_fires_and_updates(isolated_state, mock_ping_success, telemetry_enabled_env):
    """Case 3: stamp 8d old → 1 ping, mtime now ~now."""
    isolated_state.stamp_path.parent.mkdir(parents=True, exist_ok=True)
    isolated_state.stamp_path.write_text("last_sent=test\n")
    import time as _time

    eight_days_ago = _time.time() - 8 * 24 * 3600
    os.utime(isolated_state.stamp_path, (eight_days_ago, eight_days_ago))

    maybe_send_heartbeat(mode="production", endpoint="http://localhost")
    _wait_for_threads()

    assert mock_ping_success.call_count == 1
    new_mtime = isolated_state.stamp_path.stat().st_mtime
    assert _time.time() - new_mtime < 5, "stamp mtime should be ~now after successful ping"


def test_rate_limit_within_1h_fires_once(isolated_state, mock_ping_success, telemetry_enabled_env):
    """Case 4: 5 calls within the 1h in-memory cache → exactly 1 ping."""
    for _ in range(5):
        maybe_send_heartbeat(mode="production", endpoint="http://localhost")
    _wait_for_threads()

    assert mock_ping_success.call_count == 1


def test_after_rate_limit_expiry_fires_again(
    isolated_state, mock_ping_success, telemetry_enabled_env
):
    """Case 5: backdate cache + stamp → 2nd ping fires."""
    import time as _time

    # First call: ping fires, stamp written.
    maybe_send_heartbeat(mode="production", endpoint="http://localhost")
    _wait_for_threads()
    assert mock_ping_success.call_count == 1

    # Backdate in-memory cache (2h ago) AND stamp file (8d ago).
    with isolated_state._lock:  # noqa: SLF001
        isolated_state._last_checked_monotonic = _time.monotonic() - 2 * 3600  # noqa: SLF001
    eight_days_ago = _time.time() - 8 * 24 * 3600
    os.utime(isolated_state.stamp_path, (eight_days_ago, eight_days_ago))

    maybe_send_heartbeat(mode="production", endpoint="http://localhost")
    _wait_for_threads()

    assert mock_ping_success.call_count == 2


def test_opt_out_mid_process_stops_pings(
    isolated_state, mock_ping_success, telemetry_enabled_env, monkeypatch
):
    """Case 6: AXONFLOW_TELEMETRY=off after first ping → 0 further pings, stamp unchanged."""
    import time as _time

    maybe_send_heartbeat(mode="production", endpoint="http://localhost")
    _wait_for_threads()
    assert mock_ping_success.call_count == 1

    # Toggle opt-out, force gates open, snapshot stamp mtime AFTER the test
    # manipulation (so we only catch SDK-side changes).
    monkeypatch.setenv("AXONFLOW_TELEMETRY", "off")
    with isolated_state._lock:  # noqa: SLF001
        isolated_state._last_checked_monotonic = _time.monotonic() - 2 * 3600  # noqa: SLF001
    eight_days_ago = _time.time() - 8 * 24 * 3600
    os.utime(isolated_state.stamp_path, (eight_days_ago, eight_days_ago))
    mtime_before = isolated_state.stamp_path.stat().st_mtime

    maybe_send_heartbeat(mode="production", endpoint="http://localhost")
    _wait_for_threads()

    assert mock_ping_success.call_count == 1, "opt-out should suppress 2nd ping"
    mtime_after = isolated_state.stamp_path.stat().st_mtime
    assert mtime_before == mtime_after, "stamp mtime must not advance under opt-out"


def test_concurrent_callers_coalesce_to_one_ping(
    isolated_state, mock_ping_success, telemetry_enabled_env
):
    """Case 7: 100 concurrent threads all crossing the boundary → exactly 1 ping."""
    barrier = threading.Barrier(100)

    def runner():
        barrier.wait()
        maybe_send_heartbeat(mode="production", endpoint="http://localhost")

    threads = [threading.Thread(target=runner) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2.0)
    _wait_for_threads()

    assert mock_ping_success.call_count == 1, (
        f"expected exactly 1 ping under stampede, got {mock_ping_success.call_count}"
    )


def test_no_cache_dir_pings_but_no_stamp(mock_ping_success, telemetry_enabled_env):
    """Case 8: stamp_path=None (Lambda-like) → ping per process, no crash, no stamp."""
    # ``stamp_path=None`` simulates the UserCacheDir() failure case (e.g. AWS
    # Lambda where HOME is unset and LOCALAPPDATA is absent).
    previous = replace_heartbeat_state_for_test(None)
    try:
        state = hb_module._state  # noqa: SLF001 — the freshly-installed singleton
        maybe_send_heartbeat(mode="production", endpoint="http://localhost")
        _wait_for_threads()
        assert mock_ping_success.call_count == 1, "1st ping must fire even without cache dir"

        # 1h cache holds within the same process even without a stamp file.
        maybe_send_heartbeat(mode="production", endpoint="http://localhost")
        _wait_for_threads()
        assert mock_ping_success.call_count == 1, "in-memory cache must still suppress 2nd call"

        # Backdate cache, call again — fires again because no stamp gate exists.
        import time as _time

        with state._lock:  # noqa: SLF001
            state._last_checked_monotonic = _time.monotonic() - 2 * 3600  # noqa: SLF001
        maybe_send_heartbeat(mode="production", endpoint="http://localhost")
        _wait_for_threads()
        assert mock_ping_success.call_count == 2, (
            "ping fires again when cache expires and no stamp exists"
        )
    finally:
        hb_module._state = previous  # noqa: SLF001


def test_ping_failure_stamp_not_written(isolated_state, mock_ping_failure, telemetry_enabled_env):
    """Case 9: ping returns False → stamp NOT written; retry on success works."""
    maybe_send_heartbeat(mode="production", endpoint="http://localhost")
    _wait_for_threads()
    assert mock_ping_failure.call_count == 1
    assert not isolated_state.stamp_path.exists(), "failed POST must not write stamp"

    # Backdate cache, swap mock to success, retry.
    import time as _time

    with isolated_state._lock:  # noqa: SLF001
        isolated_state._last_checked_monotonic = _time.monotonic() - 2 * 3600  # noqa: SLF001

    success_mock = MagicMock(return_value=True)
    with patch("axonflow.telemetry._send_telemetry_ping_now", success_mock):
        maybe_send_heartbeat(mode="production", endpoint="http://localhost")
        _wait_for_threads()

    assert success_mock.call_count == 1
    assert isolated_state.stamp_path.exists(), "stamp must be written on successful retry"
