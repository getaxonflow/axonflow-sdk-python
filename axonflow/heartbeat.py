"""7-day delivered-heartbeat gate for AxonFlow Python SDK telemetry.

Implements the cross-SDK contract:

    AxonFlow emits at most one anonymous heartbeat per environment every
    7 days during SDK activity.

The gate is consulted both at client construction and at every public
HTTP request site (via ``_pre_request_hook``). Each gate run:

1. Re-evaluates ``AXONFLOW_TELEMETRY=off`` / mode-disabled cheaply
   (lock-free) so a mid-process opt-out toggle takes effect immediately.
2. Checks an in-memory 1-hour cache to bound stat() syscall frequency on
   hot request paths.
3. Reads the stamp file mtime as the source of truth for last successful
   delivery across process restarts.
4. Spawns a daemon thread that POSTs the ping and writes the stamp ONLY
   on success — stamp-on-DELIVERY semantics. Failed POSTs leave the
   stamp unchanged so the next call after the 1-hour cache expires
   retries.
5. Coalesces concurrent callers via an in-flight flag so a stampede
   across the boundary fires exactly one POST.

Cross-platform stamp file location (no external deps):

    macOS:   ~/Library/Caches/axonflow/python-telemetry-last-sent
    Linux:   $XDG_CACHE_HOME/axonflow/...  or  ~/.cache/axonflow/...
    Windows: %LOCALAPPDATA%/axonflow/...

If the cache dir cannot be resolved (e.g. AWS Lambda where ``HOME`` is
unset and ``LOCALAPPDATA`` is absent), the stamp path is ``None`` and
the SDK falls back to "one ping per process" — same as today's
pre-heartbeat behavior. No regression for that runtime.
"""

from __future__ import annotations

import atexit
import contextlib
import logging
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Heartbeat cadence constants. Plain module-level so tests can monkey-patch
# them via ``monkeypatch.setattr(axonflow.heartbeat, "HEARTBEAT_INTERVAL_S", ...)``.
HEARTBEAT_INTERVAL_S: float = 7 * 24 * 60 * 60  # 7 days
HEARTBEAT_GUARD_INTERVAL_S: float = 60 * 60  # 1 hour


def _resolve_stamp_path() -> Path | None:  # noqa: PLR0911
    # PLR0911: per-platform branches each return a clearly-labeled path
    # or None. Refactoring to a single return obscures the platform map.
    """Return the OS-native path to the heartbeat stamp file.

    Returns ``None`` when no user-writable cache directory is available
    (e.g. AWS Lambda with no HOME). Caller falls back to per-process
    behavior in that case.

    The path is hand-resolved here rather than via ``platformdirs`` to
    keep the SDK dependency-free. Conventions match Apple's File System
    Programming Guide, the XDG Base Directory Specification, and the
    Windows Known Folders documentation.
    """
    if sys.platform == "darwin":
        home = os.environ.get("HOME")
        if not home:
            return None
        return Path(home) / "Library" / "Caches" / "axonflow" / "python-telemetry-last-sent"
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if not local:
            return None
        return Path(local) / "axonflow" / "python-telemetry-last-sent"
    # Linux / *BSD / others — XDG.
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "axonflow" / "python-telemetry-last-sent"
    home = os.environ.get("HOME")
    if not home:
        return None
    return Path(home) / ".cache" / "axonflow" / "python-telemetry-last-sent"


# Sentinel for "use the OS-native default cache dir" in HeartbeatState
# construction. Distinct from ``None`` (which means "no persistence").
_USE_DEFAULT_CACHE_DIR = object()


class HeartbeatState:
    """Mutable state for the delivered-heartbeat gate.

    Shared as a module-level singleton across all clients in the process so
    multiple ``AxonFlow`` instances coalesce onto a single ping (and a
    single stamp file). All mutable fields are guarded by ``self._lock``.

    ``stamp_path`` semantics:
      * ``Path`` — use this exact location (typically used in tests).
      * ``None`` — no persistence at all (Lambda / restricted env path).
      * ``_USE_DEFAULT_CACHE_DIR`` (default) — auto-resolve via
        ``_resolve_stamp_path()``. Returns ``None`` if the resolver itself
        couldn't find a cache dir.
    """

    def __init__(self, stamp_path: Path | None | object = _USE_DEFAULT_CACHE_DIR) -> None:
        self._lock = threading.Lock()
        self._last_checked_monotonic: float | None = None
        self._in_flight = False
        if stamp_path is _USE_DEFAULT_CACHE_DIR:
            self._stamp_path: Path | None = _resolve_stamp_path()
        else:
            # Caller explicitly chose a path or explicit None for "no persistence".
            self._stamp_path = stamp_path  # type: ignore[assignment]

    @property
    def stamp_path(self) -> Path | None:
        """Read-only accessor for tests."""
        return self._stamp_path

    def read_stamp_mtime(self) -> float | None:
        """Return the stamp file's mtime as a wall-clock seconds-since-epoch.

        Returns ``None`` if the stamp file is absent / unreadable / no path
        could be resolved. Tolerant of every failure mode — a corrupted or
        missing stamp is treated as "never sent" so we re-attempt.
        """
        if self._stamp_path is None:
            return None
        try:
            return self._stamp_path.stat().st_mtime
        except OSError:
            return None

    def write_stamp_atomic(self) -> None:
        """Touch the stamp file with mtime=now via tmp+rename.

        Contents are advisory (a single human-readable line). The SDK uses
        mtime as the source of truth, never the contents. Errors are
        non-fatal — a failed write means the next process retries on
        schedule, which is preferable to silent dropping.
        """
        if self._stamp_path is None:
            return
        try:
            self._stamp_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        # tmp+rename so concurrent writers never observe a torn file.
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix="telemetry-last-sent-",
                suffix=".tmp",
                dir=str(self._stamp_path.parent),
            )
        except OSError:
            return
        try:
            with os.fdopen(fd, "w") as f:
                # Format kept human-readable for debugging only — never parsed.
                f.write(f"last_sent={datetime.now(timezone.utc).isoformat()}\n")
            Path(tmp_name).replace(self._stamp_path)
        except OSError:
            with contextlib.suppress(OSError):
                Path(tmp_name).unlink(missing_ok=True)


# Module-level singleton. Tests that need isolation either monkey-patch
# this attribute or construct their own ``HeartbeatState`` via
# ``replace_heartbeat_state_for_test``.
_state: HeartbeatState = HeartbeatState()


def replace_heartbeat_state_for_test(stamp_path: Path | None) -> HeartbeatState:
    """Test helper: install a fresh ``HeartbeatState`` and return the previous
    one so the caller can restore it. Production code does not call this.

    Pass an explicit ``Path`` to point the stamp at a temp file; pass
    ``None`` to test the "no persistence" path (Lambda-like restricted env).
    """
    global _state  # noqa: PLW0603 — test-only mutator
    previous = _state
    # Pass through literally — None means "no persistence", a Path means
    # "use this exact path". Auto-resolution is reserved for the default
    # module-level constructor, not for test-installed states.
    _state = HeartbeatState(stamp_path=stamp_path)
    return previous


def get_state_for_test() -> HeartbeatState:
    """Test helper: read the current module-level singleton."""
    return _state


# Flush-on-exit bookkeeping: track spawned heartbeat threads so we can join
# them on interpreter shutdown. Without this, a daemon=True thread is killed
# before its HTTP POST completes in short-lived scripts, silently dropping
# the ping. Mirrors the pre-heartbeat pattern in telemetry.py (issue #1692).
_pending_threads: list[threading.Thread] = []
_pending_threads_lock = threading.Lock()
_atexit_registered = False


def _flush_pending_heartbeats() -> None:
    """Join still-running heartbeat threads on interpreter shutdown.

    Bounded by each thread's HTTP timeout (``_TIMEOUT_SECONDS`` in
    telemetry.py — 3s), so total shutdown delay is ≤3s × in-flight count.
    Silent on every failure; never disrupts shutdown.
    """
    with _pending_threads_lock:
        threads = list(_pending_threads)
    for t in threads:
        with contextlib.suppress(Exception):
            t.join(timeout=3.0)


def _register_thread(t: threading.Thread) -> None:
    """Track ``t`` for the atexit flush. Idempotent atexit registration."""
    global _atexit_registered  # noqa: PLW0603
    with _pending_threads_lock:
        # Prune completed threads so the list stays bounded in long-lived
        # processes that handle many requests.
        _pending_threads[:] = [pt for pt in _pending_threads if pt.is_alive()]
        _pending_threads.append(t)
        if not _atexit_registered:
            atexit.register(_flush_pending_heartbeats)
            _atexit_registered = True


def maybe_send_heartbeat(
    mode: str,
    endpoint: str,
    telemetry_enabled: bool | None,
    debug: bool = False,
) -> None:
    """Central gate for telemetry pings.

    Called from ``AxonFlow.__init__`` and ``AxonFlow._pre_request_hook``.
    Implements the contract documented at the top of this module. Never
    raises — heartbeat failures must not surface to the caller.
    """
    # Lazy imports break the heartbeat → telemetry → heartbeat cycle that
    # would otherwise occur if these were top-level imports.
    from axonflow.telemetry import _is_telemetry_enabled, _send_telemetry_ping_now  # noqa: PLC0415

    if not _is_telemetry_enabled(mode, telemetry_enabled, has_credentials=False):
        return

    h = _state
    now_mono = time.monotonic()

    with h._lock:  # noqa: SLF001 — module-private guard, keeping it nested for clarity
        if h._in_flight:
            return
        if h._last_checked_monotonic is not None and (
            now_mono - h._last_checked_monotonic < HEARTBEAT_GUARD_INTERVAL_S
        ):
            return
        h._last_checked_monotonic = now_mono

        mtime = h.read_stamp_mtime()
        if mtime is not None and (time.time() - mtime) < HEARTBEAT_INTERVAL_S:
            return

        h._in_flight = True

    # Out-of-lock: spawn a daemon thread for the actual POST. Atexit-tracked
    # so short-lived processes don't drop the ping (mirrors issue #1692 fix).
    url = os.environ.get("AXONFLOW_CHECKPOINT_URL", "").strip()
    if not url:
        from axonflow.telemetry import _DEFAULT_CHECKPOINT_URL  # noqa: PLC0415 — see lazy-import comment above

        url = _DEFAULT_CHECKPOINT_URL

    def _ping_and_stamp() -> None:
        try:
            ok = _send_telemetry_ping_now(url, mode, endpoint, debug)
        except Exception:  # noqa: BLE001 — defensive; ping must never throw to the worker
            ok = False
        with h._lock:
            h._in_flight = False
            if ok:
                h.write_stamp_atomic()

    t = threading.Thread(target=_ping_and_stamp, daemon=True)
    t.start()
    _register_thread(t)
