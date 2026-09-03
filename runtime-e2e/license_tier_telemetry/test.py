"""Real-stack proof of the SDK's license_tier telemetry field (#3619).

Runs real ``http.server`` listeners on both sides of the telemetry path: a
stand-in platform serving ``/health``, and a stand-in checkpoint receiver
capturing the outgoing POST. Bytes flow real -> real; nothing is mocked.

TWO MODES::

    # 1. MATRIX (default) — every tier value and every fail-open path.
    python runtime-e2e/license_tier_telemetry/test.py

    # 2. REAL PLATFORM — drive the SDK at a live agent and cross-check the
    #    wire value against that agent's own /health.
    AXONFLOW_E2E_PLATFORM_ENDPOINT=http://localhost:8080 \
      python runtime-e2e/license_tier_telemetry/test.py

Mode 2 proves the contract end to end: it reads the tier from the live
platform independently, then asserts the SDK put THAT value on the wire
verbatim. If the endpoint is unreachable it asserts the platform-DOWN
contract instead — ping still delivered, field omitted.

Mutation proof: drop the ``if license_tier is not None:`` guard in
``_build_payload`` and rerun — case 2 fails with ``license_tier present``.
Delete the ``payload["license_tier"] = license_tier`` assignment and case 1
fails with ``license_tier absent from wire``.

Companion CI coverage: ``tests/test_telemetry_license_tier.py`` (23 tests),
which also drive real sockets. This runtime proof is a real-stack
confirmation, not a CI gate.
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
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx

from axonflow import AxonFlow
from axonflow.heartbeat import _resolve_stamp_path

FAILURES = 0


def fail(msg: str) -> None:
    global FAILURES  # noqa: PLW0603
    FAILURES += 1
    print(f"FAIL: {msg}", file=sys.stderr)


def ok(msg: str) -> None:
    print(f"PASS: {msg}")


@contextmanager
def stand_in_platform(status: int, body: str) -> Iterator[str]:
    """A stand-in platform whose /health returns a fixed status and raw body."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/health":
                self.send_response(404)
                self.end_headers()
                return
            payload = body.encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            return

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as srv:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            yield f"http://127.0.0.1:{srv.server_address[1]}"
        finally:
            srv.shutdown()


CHILD_ENV_VAR = "AXONFLOW_E2E_TIER_CHILD"


def clear_stamp() -> None:
    """Remove the 7-day heartbeat stamp so the ping actually fires."""
    stamp = _resolve_stamp_path()
    if stamp is not None:
        with contextlib.suppress(OSError):
            stamp.unlink()


def run_child() -> None:
    """The re-executed half: construct ONE real client and let the SDK ping.

    This is the whole point of a runtime proof — it goes through the public
    constructor, the heartbeat gate, the daemon thread and the atexit flush,
    rather than calling the private ping function the unit tests already
    cover. A fresh PROCESS per case is required because the heartbeat state
    is a module-level singleton: one process delivers at most one ping no
    matter how many clients it builds.
    """
    clear_stamp()
    client = AxonFlow(
        endpoint=os.environ.get("AXONFLOW_E2E_PLATFORM_ENDPOINT", ""),
        client_id="rt-e2e",
        client_secret="rt-e2e",
    )
    # THE HEARTBEAT FIRES ON THE FIRST REQUEST, not at construction
    # (axonflow-enterprise#3682). Constructing a client and exiting no longer
    # pings at all, so this driver has to make a call — which is also a more
    # faithful runtime proof, since a client nobody uses is not a deployment
    # worth reporting.
    #
    # The call FAILS against the stand-in platform, deliberately: the
    # heartbeat rides the ATTEMPT to make a request, so a caller whose first
    # API call fails is still a caller.
    async def _touch() -> None:
        with contextlib.suppress(Exception):
            await client.list_decisions()

    asyncio.run(_touch())
    # The atexit flush handler joins the telemetry thread on interpreter exit.


def capture_one_ping(platform_endpoint: str) -> bytes:
    """Run one REAL client — in a fresh child process — and return the wire body."""
    captured: dict[str, bytes] = {}
    delivered = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            captured["body"] = self.rfile.read(length)
            resp = json.dumps({"latest_version": None, "alerts": []}).encode()
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
        env["AXONFLOW_E2E_PLATFORM_ENDPOINT"] = platform_endpoint
        env["AXONFLOW_CHECKPOINT_URL"] = f"http://127.0.0.1:{srv.server_address[1]}/v1/ping"
        env["AXONFLOW_TELEMETRY"] = ""
        try:
            # The child's exit status and stderr are INSPECTED, not discarded.
            # A child that dies at construction produces no ping, which the
            # callers below would otherwise report as "the ping was SUPPRESSED
            # — telemetry must degrade, not stop": a fail-open accusation
            # against the SDK for what is actually a harness failure. Name the
            # real cause instead.
            proc = subprocess.run(  # noqa: S603
                [sys.executable, str(Path(__file__).resolve())],
                env=env,
                check=False,
                capture_output=True,
                timeout=30,
            )
            delivered.wait(timeout=5)
            if proc.returncode != 0:
                fail(
                    f"the child process died (exit {proc.returncode}) — this is a HARNESS "
                    f"failure, not SDK fail-open behaviour.\nstderr:\n"
                    f"{proc.stderr.decode(errors='replace')}"
                )
        except subprocess.TimeoutExpired:
            fail("the child process did not exit within 30s — harness failure, not SDK behaviour")
        finally:
            srv.shutdown()
    return captured.get("body", b"")


def tier_on_wire(body: bytes) -> tuple[bool, str]:
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return False, ""
    if "license_tier" not in payload:
        return False, ""
    return True, payload["license_tier"]


def run_against_real_platform(endpoint: str) -> None:
    print(f"=== REAL PLATFORM MODE: {endpoint} ===\n")

    try:
        health = httpx.get(f"{endpoint}/health", timeout=5.0).json()
    except (httpx.HTTPError, OSError, ValueError) as err:
        # Platform DOWN — a first-class real-world case, not a harness error.
        print(
            f"Platform unreachable at {endpoint} ({err})\n  -> asserting the DOWN contract instead.\n"
        )
        body = capture_one_ping(endpoint)
        if not body:
            fail("platform down: the ping was SUPPRESSED — telemetry must degrade, not stop")
            return
        print(f"Telemetry wire body: {body.decode()}\n")
        present, value = tier_on_wire(body)
        if present:
            fail(
                f"platform down: license_tier present as {value!r} — must be omitted when not learned"
            )
            return
        ok("platform down: ping still delivered, license_tier omitted (not defaulted)")
        return

    print(f"Live /health tier: {health.get('tier')!r}\n")
    if not health.get("tier"):
        fail("live platform reported no tier — cannot cross-check")
        return

    body = capture_one_ping(endpoint)
    if not body:
        fail("no telemetry ping captured against the live platform")
        return
    print(f"Telemetry wire body: {body.decode()}\n")

    present, value = tier_on_wire(body)
    if not present:
        fail(f"license_tier absent from wire; the live platform reported tier={health['tier']!r}")
    elif value != health["tier"]:
        fail(f"license_tier on wire = {value!r}, live platform /health said {health['tier']!r}")
    else:
        ok(f"license_tier={value!r} on the wire matches the live platform's own /health verbatim")


def run_matrix() -> None:
    print("=== MATRIX MODE (stand-in platform) ===\n")

    print("-- 1. verbatim round-trip of every platform-emitted tier --")
    for tier in ["community", "evaluation", "Enterprise", "Plus", "starting"]:
        body_json = json.dumps({"status": "healthy", "version": "10.3.0", "tier": tier})
        with stand_in_platform(200, body_json) as endpoint:
            wire = capture_one_ping(endpoint)

        present, value = tier_on_wire(wire)
        if not wire:
            fail(f"tier={tier}: no ping captured")
        elif not present:
            fail(f"tier={tier}: license_tier absent from wire; body: {wire.decode()}")
        elif value != tier:
            fail(f"tier={tier}: license_tier on wire = {value!r}, want verbatim {tier!r}")
        else:
            ok(f"tier={value!r:<13} forwarded verbatim")

    print("\n-- 2. fail-open paths: field omitted, ping still delivered --")
    with stand_in_platform(200, "{}") as dead_endpoint:
        dead = dead_endpoint

    # NOTE: "endpoint not configured" is deliberately absent here. The public
    # AxonFlow(...) constructor REJECTS an empty endpoint ("endpoint is
    # required"), so that case is unreachable through the real client and can
    # only be driven against the private ping function — which is what the
    # unit suite does (test_endpoint_not_configured). A runtime proof that
    # reached for the private function to keep a row would stop being a
    # runtime proof.
    cases: list[tuple[str, str, object]] = [
        ("platform unreachable", dead, None),
    ]
    specs = [
        ("health returns 500", 500, json.dumps({"tier": "Enterprise"})),
        ("health returns malformed JSON", 200, '{"tier":"Enterprise"'),
        ("health has no tier key", 200, json.dumps({"status": "healthy", "version": "10.3.0"})),
        ("health has an empty tier", 200, json.dumps({"version": "10.3.0", "tier": ""})),
        ("health has a non-string tier", 200, json.dumps({"version": "10.3.0", "tier": 42})),
    ]

    def check(name: str, wire: bytes) -> None:
        if not wire:
            fail(f"{name}: the ping was SUPPRESSED — telemetry must degrade, not stop")
            return
        if b'"telemetry_type"' not in wire:
            fail(f"{name}: ping body is not a well-formed sdk ping: {wire.decode()}")
            return
        present, value = tier_on_wire(wire)
        if present:
            fail(f"{name}: license_tier present as {value!r} — must be omitted when not learned")
            return
        ok(f"{name:<32} ping delivered, license_tier omitted")

    for name, endpoint, _ in cases:
        check(name, capture_one_ping(endpoint))
    for name, status, spec_body in specs:
        with stand_in_platform(status, spec_body) as endpoint:
            check(name, capture_one_ping(endpoint))

    print("\n-- 3. deployment_mode is independent of the tier --")
    for name, spec_body in [
        ("with tier", json.dumps({"version": "10.3.0", "tier": "Enterprise"})),
        ("without tier", json.dumps({"version": "10.3.0"})),
    ]:
        with stand_in_platform(200, spec_body) as endpoint:
            wire = capture_one_ping(endpoint)
        if not wire:
            # Same guard sections 1 and 2 carry. Without it a broken child
            # raises JSONDecodeError here, killing the run mid-way so the
            # remaining rows never execute and no failure summary prints.
            fail(f"{name}: no ping captured")
            continue
        mode = json.loads(wire)["deployment_mode"]
        if mode != "self_hosted":
            fail(
                f"{name}: deployment_mode = {mode!r}, want 'self_hosted' — the tier must not alter topology"
            )
            continue
        ok(f"{name:<14} deployment_mode={mode!r} unchanged")


def main() -> int:
    if os.environ.get(CHILD_ENV_VAR):
        run_child()
        return 0

    real = os.environ.get("AXONFLOW_E2E_PLATFORM_ENDPOINT")
    if real:
        run_against_real_platform(real)
    else:
        run_matrix()

    if FAILURES:
        print(f"\n{FAILURES} assertion(s) FAILED", file=sys.stderr)
        return 1
    print("\nAll assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
