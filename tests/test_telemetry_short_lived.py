"""Regression test for issue #1692: telemetry must be delivered even when the
Python process exits immediately after client init.

Root cause: ``threading.Thread(..., daemon=True)`` is killed when the interpreter
shuts down. In short-lived scripts (CLI one-liners, serverless handlers, quickstart
snippets, cold-start functions), the HTTP POST to checkpoint never completes.

Fix: ``atexit`` handler that joins the telemetry thread with a short timeout.

This test intentionally runs the SDK in a *subprocess* that exits immediately,
so we exercise the real interpreter-shutdown path — not a mock. The regression
is invisible under in-process test harnesses because pytest keeps the process
alive long enough for the thread to complete.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest


def _free_port() -> int:
    """Return a free TCP port on 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _CapturingHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures POST bodies and serves /health."""

    received: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(body)
        except Exception:  # noqa: BLE001  tolerate any malformed-body shape from test inputs
            payload = {}
        self.__class__.received.append(payload)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"latest_version":"99.99.99","source":"external"}')

    #: Seconds the ``/health`` probe is delayed before answering.
    #:
    #: NOT decoration. With an instantly-answering ``/health`` the whole
    #: telemetry path completes inside the subprocess's own teardown, so
    #: deleting ``axonflow.heartbeat._register_thread`` — the call that enrolls
    #: the worker in the atexit flush this file exists to protect — SURVIVED:
    #: the ping still landed, because there was nothing left to flush.
    #: Measured: at 0 ms the mutant delivers 1 ping; at 500 ms it delivers 0.
    #:
    #: A fixture that cannot express the defect reads exactly like one that
    #: disproves it, so the probe is deliberately slow enough that the POST is
    #: still in flight when the interpreter starts shutting down. It stays well
    #: inside the SDK's own 3 s budget, so the ping itself is unaffected.
    health_delay_seconds: ClassVar[float] = 0.5

    def do_GET(self) -> None:  # noqa: N802
        if self.path.endswith("/health"):
            time.sleep(self.__class__.health_delay_seconds)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok","version":"mock-1.0"}')
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *_args: Any, **_kwargs: Any) -> None:  # silence default access logs
        pass


@pytest.fixture
def mock_checkpoint() -> Any:
    """Spin up a local HTTP server, yield (url, received_list), tear down after."""
    _CapturingHandler.received = []
    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), _CapturingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", _CapturingHandler.received
    finally:
        server.shutdown()
        server.server_close()


def test_telemetry_flushes_on_immediate_exit(mock_checkpoint: Any) -> None:
    """A Python subprocess that exits immediately after AxonFlow() init must
    still deliver its telemetry ping to the checkpoint.

    Without the atexit flush fix, the daemon thread is killed on interpreter
    shutdown and the POST never completes — this test catches that regression.
    """
    base_url, received = mock_checkpoint

    # Subprocess runs a one-liner that instantiates the client and exits.
    # No sleep, no explicit flush — only the SDK's atexit handler should
    # keep the ping alive until delivery.
    #
    # Isolate the subprocess from the developer's real stamp file
    # (~/Library/Caches/axonflow/python-telemetry-last-sent on macOS,
    # ~/.cache/axonflow/... on Linux, %LOCALAPPDATA%/axonflow/... on
    # Windows). Without isolation the heartbeat gate's 7-day delivered
    # cadence (axonflow/heartbeat.py) silently short-circuits the ping
    # whenever the stamp file already exists from a prior run on this
    # machine — the test then asserts on a ping that never fires and
    # we get a spurious failure that masks real atexit-flush
    # regressions. Override the per-platform cache root with a fresh
    # tempdir so the resolver finds no stamp.
    with tempfile.TemporaryDirectory(prefix="axonflow-telemetry-home-") as fake_home:
        env = os.environ.copy()
        env.pop("DO_NOT_TRACK", None)  # autouse conftest fixture doesn't apply to subprocesses
        env.pop("AXONFLOW_TELEMETRY", None)
        env["AXONFLOW_CHECKPOINT_URL"] = f"{base_url}/v1/ping"
        env["HOME"] = fake_home  # macOS + Linux stamp roots
        env["XDG_CACHE_HOME"] = str(Path(fake_home) / ".cache")  # Linux explicit
        env["LOCALAPPDATA"] = fake_home  # Windows stamp root

        result = subprocess.run(  # noqa: S603  sys.executable is trusted; args are literal
            [
                sys.executable,
                "-c",
                # THE FIXTURE NOW MAKES A REQUEST, AND THAT IS THE #3682
                # CHANGE, NOT A WEAKENING. The heartbeat trigger moved from
                # client construction to the client's first outbound request,
                # so a script that constructs a client and never uses it
                # deliberately no longer pings — a heartbeat is a claim about
                # usage. The property under test is unchanged and is still the
                # one this file exists for: a SHORT-LIVED PROCESS MUST NOT DROP
                # ITS PING. So the subprocess does what the caller it models
                # does — construct, make one call, exit immediately — and still
                # asserts the ping arrived. The call fails (nothing serves that
                # route); the heartbeat rides the ATTEMPT, because a caller
                # whose first API call fails is still a caller.
                "import asyncio, contextlib\n"
                "from axonflow import AxonFlow\n"
                "async def main():\n"
                "    c = AxonFlow(endpoint='" + base_url + "')\n"
                "    with contextlib.suppress(Exception):\n"
                "        await c.list_decisions()\n"
                "asyncio.run(main())\n",
            ],
            env=env,
            capture_output=True,
            timeout=15,
            check=False,
        )
    assert result.returncode == 0, (
        f"subprocess failed: stdout={result.stdout!r} stderr={result.stderr!r}"
    )

    # Give the OS a beat to flush the POST; the subprocess has already exited,
    # so any delay here is pure arrival-on-socket slack.
    deadline = time.time() + 5.0
    while time.time() < deadline and not received:
        time.sleep(0.05)

    # EXACTLY one, not ">= 1". A lower bound cannot tell "the flush worked"
    # from "the gate fired twice", and the contract here is one ping per
    # process per interval.
    assert len(received) == 1, (
        "telemetry ping was not received by the mock checkpoint — "
        "the atexit flush regression has returned. "
        f"subprocess stderr: {result.stderr!r}"
    )
    payload = received[0]
    assert payload.get("sdk") == "python"
    assert payload.get("sdk_version")
    assert payload.get("instance_id")
