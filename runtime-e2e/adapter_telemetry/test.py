"""Real-stack proof of the adapter registry (axonflow-enterprise#3682).

Asserts, through the SDK's real public surface and over real sockets:

1. The SDK's OWN LangGraph adapter declares itself on the first ping, with no
   telemetry code in the application.
2. An adapter registered AFTER the first request misses that ping.
3. An over-cap (65-byte) adapter name is dropped WHOLE, not truncated, and does
   not take the valid name with it.
4. The adapter still ships when ``/health`` is unreachable or malformed.

WHY THERE ARE LISTENERS, AND WHAT IS STILL REAL. The SDK does not expose its
telemetry client, and the real checkpoint service is PRODUCTION — a runtime
proof must not deliver test pings to it. So this driver runs real
``http.server`` listeners on both sides and bytes flow real -> real through the
SDK's own httpx calls: a real client, its real gate, its real daemon thread,
its real atexit flush. Nothing about the SDK is mocked; the stand-ins are the
two PEERS, exactly as in the neighbouring ``license_tier_telemetry`` driver.

Each case runs in a FRESH CHILD PROCESS. Both the heartbeat state and the
adapter registry are module-level singletons, so one process would emit a
single ping carrying the union of every case's registrations and every
assertion after the first would read the first case's body.

Run::

    python runtime-e2e/adapter_telemetry/test.py
"""

from __future__ import annotations

import asyncio
import contextlib
import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from axonflow import AxonFlow, register_adapter  # noqa: E402
from axonflow.heartbeat import _resolve_stamp_path  # noqa: E402

FAILURES = 0

CHILD_ENV_VAR = "AXONFLOW_E2E_ADAPTER_CHILD"
CHILD_NAMES = "AXONFLOW_E2E_ADAPTER_NAMES"
CHILD_VIA_ADAPTER = "AXONFLOW_E2E_ADAPTER_VIA_CONSTRUCTOR"
CHILD_LATE = "AXONFLOW_E2E_ADAPTER_REGISTER_LATE"


def fail(msg: str) -> None:
    global FAILURES  # noqa: PLW0603
    FAILURES += 1
    print(f"FAIL: {msg}", file=sys.stderr)


def ok(msg: str) -> None:
    print(f"PASS: {msg}")


def clear_stamp() -> None:
    stamp = _resolve_stamp_path()
    if stamp is not None:
        with contextlib.suppress(OSError):
            stamp.unlink()


def run_child() -> None:
    """Construct one real client, declare adapters, make ONE request."""
    clear_stamp()

    for name in filter(None, os.environ.get(CHILD_NAMES, "").split("\x1f")):
        register_adapter(name)

    client = AxonFlow(
        endpoint=os.environ.get("AXONFLOW_E2E_PLATFORM_ENDPOINT", "http://127.0.0.1:1"),
        client_id="rt-e2e",
        client_secret="rt-e2e",
    )

    if os.environ.get(CHILD_VIA_ADAPTER):
        # The REAL public surface, not a register_adapter call. Constructed
        # after the client (it takes one) but BEFORE the first request, which
        # is what puts it on the first ping now that the heartbeat fires on
        # first use rather than at construction.
        from axonflow.adapters.langgraph import AxonFlowLangGraphAdapter

        AxonFlowLangGraphAdapter(client=client, workflow_name="rt-e2e")

    async def _touch() -> None:
        # THE HEARTBEAT FIRES HERE. The call fails against the stand-in
        # platform, deliberately: the heartbeat rides the ATTEMPT.
        with contextlib.suppress(Exception):
            await client.list_decisions()

    asyncio.run(_touch())

    if os.environ.get(CHILD_LATE):
        register_adapter("registered-too-late")


def capture_one_ping(
    platform_endpoint: str,
    names: list[str] | None = None,
    via_adapter: bool = False,
    late: bool = False,
) -> bytes:
    captured: dict[str, bytes] = {}
    delivered = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            captured["body"] = self.rfile.read(length)
            resp = json.dumps({"latest_version": None}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)
            delivered.set()

        def log_message(self, *_args: object) -> None:
            return

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as srv:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        env = dict(os.environ)
        env[CHILD_ENV_VAR] = "1"
        env[CHILD_NAMES] = "\x1f".join(names or [])
        env["AXONFLOW_E2E_PLATFORM_ENDPOINT"] = platform_endpoint
        env["AXONFLOW_CHECKPOINT_URL"] = f"http://127.0.0.1:{srv.server_address[1]}/v1/ping"
        env["AXONFLOW_TELEMETRY"] = ""
        if via_adapter:
            env[CHILD_VIA_ADAPTER] = "1"
        else:
            env.pop(CHILD_VIA_ADAPTER, None)
        if late:
            env[CHILD_LATE] = "1"
        else:
            env.pop(CHILD_LATE, None)
        try:
            proc = subprocess.run(  # noqa: S603
                [sys.executable, str(Path(__file__).resolve())],
                env=env,
                check=False,
                capture_output=True,
                timeout=30,
            )
            delivered.wait(timeout=5)
            if proc.returncode != 0:
                # A dead child produces no ping, which the callers would
                # otherwise report as SDK fail-open. Name the real cause.
                fail(
                    f"the child process died (exit {proc.returncode}) — HARNESS failure, "
                    f"not SDK behaviour.\nstderr:\n{proc.stderr.decode(errors='replace')}"
                )
        except subprocess.TimeoutExpired:
            fail(
                f"the child did not exit within 30s (endpoint={platform_endpoint!r}, "
                f"names={names!r}, via_adapter={via_adapter}, late={late}) — harness failure"
            )
        finally:
            srv.shutdown()
            srv.server_close()
    return captured.get("body", b"")


def start_stand_in_platform(status: int, body: str) -> tuple[str, object]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            payload = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            return

    srv = socketserver.TCPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", srv


def features_on_wire(body: bytes) -> tuple[list[str], bool]:
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return [], False
    if "features" not in payload:
        return [], False
    return list(payload["features"]), True


HEALTH = json.dumps(
    {
        "status": "healthy",
        "version": "10.4.0",
        "tier": "Enterprise",
        "edition": "enterprise",
        "deployment_mode": "self_hosted",
    }
)


def main() -> int:
    if os.environ.get(CHILD_ENV_VAR):
        run_child()
        return 0

    print("=== MATRIX MODE (stand-in platform + checkpoint) ===\n")

    # --- 1. The shipped adapter declares itself on the FIRST ping. --------
    print("-- 1. the shipped LangGraph adapter declares itself, no caller telemetry code --")
    endpoint, srv = start_stand_in_platform(200, HEALTH)
    body = capture_one_ping(endpoint, via_adapter=True)
    srv.shutdown()
    features, present = features_on_wire(body)
    if not body:
        fail("no ping captured")
    elif not present:
        fail(f"`features` key absent from the wire; body: {body!r}")
    elif "adapter:langgraph" not in features:
        fail(
            f"features = {features}; constructing AxonFlowLangGraphAdapter must declare "
            "adapter:langgraph on the first ping without the application calling "
            "register_adapter itself"
        )
    else:
        ok(f"AxonFlowLangGraphAdapter alone put features = {features} on the first ping")

    # --- 2. Registered AFTER the first request misses that ping. ---------
    print("\n-- 2. an adapter registered AFTER the first request misses that ping --")
    endpoint, srv = start_stand_in_platform(200, HEALTH)
    body = capture_one_ping(endpoint, via_adapter=True, late=True)
    srv.shutdown()
    features, present = features_on_wire(body)
    if not present:
        fail("`features` absent, so this case cannot distinguish absence from a failed run")
    elif "adapter:registered-too-late" in features:
        fail(f"features = {features} carries an adapter registered AFTER the ping was sent")
    elif "adapter:langgraph" not in features:
        fail(
            f"features = {features} lost the adapter registered BEFORE the request — "
            "without this the absence assertion above is vacuous"
        )
    else:
        ok(f"features = {features}: declared-before is on the wire, registered-after is not")

    # --- 3. A 65-byte name is dropped WHOLE. -----------------------------
    print("\n-- 3. a 65-byte adapter name is dropped whole, not truncated --")
    endpoint, srv = start_stand_in_platform(200, HEALTH)
    body = capture_one_ping(endpoint, names=["a" * 65, "langchain"])
    srv.shutdown()
    features, present = features_on_wire(body)
    if not present:
        fail(f"`features` absent; body: {body!r}")
    elif "adapter:" + "a" * 65 in features:
        fail("the 65-byte name reached the wire in full")
    elif "adapter:" + "a" * 64 in features:
        fail(
            "the 65-byte name was TRUNCATED to 64 and sent; a truncated adapter name is a "
            "name nothing is running, and the receiver records it as a real value"
        )
    elif "adapter:langchain" not in features:
        fail(
            f"features = {features} lost the VALID name too — an over-cap value must be "
            "dropped alone, not take the array with it"
        )
    else:
        ok(f"features = {features}: the over-cap name dropped whole, the valid one kept")

    # --- 4. The adapter survives /health failure modes. ------------------
    print("\n-- 4. the adapter survives /health failure modes --")
    unreachable, srv = start_stand_in_platform(200, "{}")
    # BOTH calls, and that is the fix for a real harness bug rather than a
    # tidy-up. shutdown() only stops serve_forever; the LISTENING SOCKET stays
    # bound, so the OS keeps accepting connections into the backlog and never
    # answering them. The child's request then blocked until its own timeout
    # and the case read as "the child did not exit within 30s" — a harness
    # failure that looks exactly like an SDK hang. server_close() releases the
    # socket so the port genuinely refuses, which is what "unreachable" means.
    srv.shutdown()
    srv.server_close()
    cases: list[tuple[str, str, object]] = [("platform unreachable", unreachable, None)]
    for name, status, health_body in [
        ("health returns 500", 500, "{}"),
        ("health returns malformed JSON", 200, '{"version":'),
        ("health carries none of the relayed fields", 200, '{"status":"healthy"}'),
    ]:
        ep, s = start_stand_in_platform(status, health_body)
        cases.append((name, ep, s))

    for name, ep, s in cases:
        b = capture_one_ping(ep, names=["langchain"])
        if s is not None:
            s.shutdown()
        if not b:
            fail(f"{name}: the ping was SUPPRESSED — telemetry must degrade, not stop")
            continue
        f, present = features_on_wire(b)
        if not present or "adapter:langchain" not in f:
            fail(
                f"{name}: adapter:langchain absent (features={f}). The adapter is the SDK's "
                "own knowledge and must not depend on /health"
            )
            continue
        ok(f"{name:45s} ping delivered, features = {f}")

    if FAILURES:
        print(f"\n{FAILURES} assertion(s) FAILED", file=sys.stderr)
        return 1
    print("\nAll assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
