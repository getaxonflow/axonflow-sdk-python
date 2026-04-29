"""End-to-end test for the 7-day delivered-heartbeat contract.

Walks through the four-run cycle the spec requires, against a real
``http.server`` running on localhost — this exercises the actual TCP +
HTTP path the SDK takes to the checkpoint endpoint, mirroring the Go
``httptest`` and Java WireMock E2Es.

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
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from axonflow import heartbeat as hb_module
from axonflow.heartbeat import HeartbeatState, maybe_send_heartbeat


def _wait_for_threads() -> None:
    with hb_module._pending_threads_lock:  # noqa: SLF001
        threads = list(hb_module._pending_threads)
    for t in threads:
        t.join(timeout=5.0)


class _CountingHandler(BaseHTTPRequestHandler):
    """Per-instance request counter + configurable status code."""

    # Class-level config (rebound per-test).
    _status_code: int = 200
    _hits: int = 0
    _hits_lock = threading.Lock()

    def do_POST(self) -> None:  # noqa: N802 — http.server hook
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        with type(self)._hits_lock:
            type(self)._hits += 1
        self.send_response(type(self)._status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}' if type(self)._status_code < 300 else b"")

    def log_message(self, *_args: object) -> None:
        # Silence the default per-request log line so test output stays clean.
        pass


@pytest.fixture
def stamp_path(tmp_path: Path) -> Path:
    return tmp_path / "stamp"


@pytest.fixture
def telemetry_enabled_env(monkeypatch):
    monkeypatch.delenv("AXONFLOW_TELEMETRY", raising=False)


@pytest.fixture
def http_server(monkeypatch):
    """Start a localhost HTTP server. Tests rebind status_code per phase.

    Also restores ``httpx.post`` / ``httpx.get`` to their real
    implementations — the suite-wide autouse ``_disable_telemetry``
    fixture blocks real HTTP egress as a safety net, but this E2E
    legitimately needs a real network roundtrip to a localhost server.
    """
    import httpx
    from httpx._api import get as _real_get
    from httpx._api import post as _real_post

    monkeypatch.setattr(httpx, "post", _real_post)
    monkeypatch.setattr(httpx, "get", _real_get)

    _CountingHandler._status_code = 200
    _CountingHandler._hits = 0
    server = HTTPServer(("127.0.0.1", 0), _CountingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    url = f"http://{host}:{port}/v1/checkpoint"
    monkeypatch.setenv("AXONFLOW_CHECKPOINT_URL", url)
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2.0)


def _swap_state(stamp_path: Path) -> HeartbeatState:
    """Install a fresh state at the given stamp path, returning the new state."""
    state = HeartbeatState(stamp_path=stamp_path)
    hb_module._state = state  # noqa: SLF001
    return state


def _reset_hits(status: int) -> None:
    _CountingHandler._status_code = status
    with _CountingHandler._hits_lock:
        _CountingHandler._hits = 0


def _hits() -> int:
    with _CountingHandler._hits_lock:
        return _CountingHandler._hits


def test_four_run_cycle_real_http(stamp_path: Path, telemetry_enabled_env, http_server):
    """Run 1: cold → 1 ping;
    Run 2: warm → 0;
    Run 3: stale → 1 (stamp updated);
    Run 4: stale + 503 → 0 successful (stamp unchanged); retry on 200 → 1, stamp updated.

    Uses a real localhost ``http.server`` so the TCP + HTTP path the SDK
    takes is exercised end-to-end (matches Go ``httptest`` and Java
    WireMock coverage).
    """
    original_state = hb_module._state  # noqa: SLF001

    try:
        # ---- Run 1: cold start, no stamp ----------------------------------
        _reset_hits(200)
        _swap_state(stamp_path)
        maybe_send_heartbeat(
            mode="production", endpoint="http://localhost", telemetry_enabled=True
        )
        _wait_for_threads()

        assert _hits() == 1, f"Run 1: expected 1 ping on cold start, got {_hits()}"
        assert stamp_path.exists(), "Run 1: stamp must be written on success"

        # ---- Run 2: simulate fresh process — fresh state, same stamp file -
        _reset_hits(200)
        _swap_state(stamp_path)
        maybe_send_heartbeat(
            mode="production", endpoint="http://localhost", telemetry_enabled=True
        )
        _wait_for_threads()

        assert _hits() == 0, f"Run 2: fresh stamp must suppress ping, got {_hits()}"

        # ---- Run 3: backdate stamp 8d -------------------------------------
        eight_days_ago = time.time() - 8 * 24 * 3600
        os.utime(stamp_path, (eight_days_ago, eight_days_ago))

        _reset_hits(200)
        _swap_state(stamp_path)
        maybe_send_heartbeat(
            mode="production", endpoint="http://localhost", telemetry_enabled=True
        )
        _wait_for_threads()

        assert _hits() == 1, f"Run 3: stale stamp must trigger a fresh ping, got {_hits()}"
        new_mtime = stamp_path.stat().st_mtime
        assert time.time() - new_mtime < 5, "Run 3: stamp mtime must be ~now after successful ping"

        # ---- Run 4a: backdate again, server returns 503 -------------------
        os.utime(stamp_path, (eight_days_ago, eight_days_ago))
        mtime_before_fail = stamp_path.stat().st_mtime

        _reset_hits(503)
        _swap_state(stamp_path)
        maybe_send_heartbeat(
            mode="production", endpoint="http://localhost", telemetry_enabled=True
        )
        _wait_for_threads()

        assert _hits() == 1, f"Run 4a: ping must be attempted under stale stamp, got {_hits()}"
        mtime_after_fail = stamp_path.stat().st_mtime
        assert mtime_before_fail == mtime_after_fail, (
            "Run 4a: failed POST must NOT advance the stamp (delivered-heartbeat semantics)"
        )

        # ---- Run 4b: retry against the same server, now returning 200 -----
        _reset_hits(200)
        _swap_state(stamp_path)
        maybe_send_heartbeat(
            mode="production", endpoint="http://localhost", telemetry_enabled=True
        )
        _wait_for_threads()

        assert _hits() == 1, f"Run 4b: retry on success must land 1 ping, got {_hits()}"
        retry_mtime = stamp_path.stat().st_mtime
        assert time.time() - retry_mtime < 5, "Run 4b: stamp must advance after successful retry"

    finally:
        hb_module._state = original_state  # noqa: SLF001
