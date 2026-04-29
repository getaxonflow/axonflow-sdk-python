"""End-to-end test for the 7-day delivered-heartbeat contract.

Walks through the four-run cycle the spec requires:

    Run 1: cold start (no stamp)              → 1 ping;  stamp present
    Run 2: warm start (fresh stamp)           → 0 pings; stamp unchanged
    Run 3: backdate stamp 8d (os.utime)       → 1 ping;  stamp re-touched
    Run 4: stale stamp + ping returns 503     → 0 successful pings;
                                                 stamp NOT advanced;
                                                 retry on success lands cleanly

This validates stamp-on-DELIVERY semantics (Run 4 — failed POST does not
advance the stamp) and cross-run behavior (Runs 1→2→3 — the stamp file is
the source of truth across "process restarts" simulated by fresh
``HeartbeatState`` construction with the same stamp path).
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from axonflow import heartbeat as hb_module
from axonflow.heartbeat import HeartbeatState, maybe_send_heartbeat


def _wait_for_threads() -> None:
    with hb_module._pending_threads_lock:  # noqa: SLF001
        threads = list(hb_module._pending_threads)
    for t in threads:
        t.join(timeout=2.0)


@pytest.fixture
def stamp_path(tmp_path: Path) -> Path:
    return tmp_path / "stamp"


@pytest.fixture
def telemetry_enabled_env(monkeypatch):
    monkeypatch.delenv("AXONFLOW_TELEMETRY", raising=False)


def _swap_state(stamp_path: Path) -> HeartbeatState:
    """Install a fresh state at the given stamp path, returning the new state."""
    state = HeartbeatState(stamp_path=stamp_path)
    hb_module._state = state  # noqa: SLF001
    return state


def test_four_run_cycle(stamp_path: Path, telemetry_enabled_env, monkeypatch):
    """Run 1: cold → 1 ping;
       Run 2: warm → 0;
       Run 3: stale → 1 (stamp updated);
       Run 4: stale + 503 → 0 successful (stamp unchanged); retry on 200 → 1, stamp updated."""

    # Save real singleton and restore at the end.
    original_state = hb_module._state  # noqa: SLF001

    try:
        # ---- Run 1: cold start, no stamp -----------------------------------
        success_mock = MagicMock(return_value=True)
        with patch("axonflow.telemetry._send_telemetry_ping_now", success_mock):
            _swap_state(stamp_path)
            maybe_send_heartbeat(mode="production", endpoint="http://localhost", telemetry_enabled=True)
            _wait_for_threads()

        assert success_mock.call_count == 1, "Run 1: expected 1 ping on cold start"
        assert stamp_path.exists(), "Run 1: stamp must be written on success"

        # ---- Run 2: simulate fresh process — fresh state, same stamp file --
        success_mock_2 = MagicMock(return_value=True)
        with patch("axonflow.telemetry._send_telemetry_ping_now", success_mock_2):
            _swap_state(stamp_path)
            maybe_send_heartbeat(mode="production", endpoint="http://localhost", telemetry_enabled=True)
            _wait_for_threads()

        assert success_mock_2.call_count == 0, "Run 2: fresh stamp must suppress ping"

        # ---- Run 3: backdate stamp 8d ----------------------------------------
        eight_days_ago = time.time() - 8 * 24 * 3600
        os.utime(stamp_path, (eight_days_ago, eight_days_ago))

        success_mock_3 = MagicMock(return_value=True)
        with patch("axonflow.telemetry._send_telemetry_ping_now", success_mock_3):
            _swap_state(stamp_path)
            maybe_send_heartbeat(mode="production", endpoint="http://localhost", telemetry_enabled=True)
            _wait_for_threads()

        assert success_mock_3.call_count == 1, "Run 3: stale stamp must trigger a fresh ping"
        new_mtime = stamp_path.stat().st_mtime
        assert time.time() - new_mtime < 5, "Run 3: stamp mtime must be ~now after successful ping"

        # ---- Run 4a: backdate again, ping returns failure -------------------
        os.utime(stamp_path, (eight_days_ago, eight_days_ago))
        mtime_before_fail = stamp_path.stat().st_mtime

        failure_mock = MagicMock(return_value=False)
        with patch("axonflow.telemetry._send_telemetry_ping_now", failure_mock):
            _swap_state(stamp_path)
            maybe_send_heartbeat(mode="production", endpoint="http://localhost", telemetry_enabled=True)
            _wait_for_threads()

        assert failure_mock.call_count == 1, "Run 4a: ping must be attempted under stale stamp"
        mtime_after_fail = stamp_path.stat().st_mtime
        assert mtime_before_fail == mtime_after_fail, (
            "Run 4a: failed POST must NOT advance the stamp (delivered-heartbeat semantics)"
        )

        # ---- Run 4b: retry against a successful mock ------------------------
        success_mock_retry = MagicMock(return_value=True)
        with patch("axonflow.telemetry._send_telemetry_ping_now", success_mock_retry):
            _swap_state(stamp_path)
            maybe_send_heartbeat(mode="production", endpoint="http://localhost", telemetry_enabled=True)
            _wait_for_threads()

        assert success_mock_retry.call_count == 1, "Run 4b: retry on success must land 1 ping"
        retry_mtime = stamp_path.stat().st_mtime
        assert time.time() - retry_mtime < 5, "Run 4b: stamp must advance after successful retry"

    finally:
        hb_module._state = original_state  # noqa: SLF001
