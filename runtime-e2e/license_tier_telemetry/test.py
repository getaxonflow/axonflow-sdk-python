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

import http.server
import json
import os
import socketserver
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager

import httpx

from axonflow.telemetry import _send_telemetry_ping_now

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


def capture_one_ping(platform_endpoint: str) -> bytes:
    """Run one real ping against ``platform_endpoint``; return the wire body."""
    captured: dict[str, bytes] = {}

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

        def log_message(self, *_args: object) -> None:
            return

    os.environ["AXONFLOW_TELEMETRY"] = ""
    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as srv:
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}/v1/ping"
        try:
            _send_telemetry_ping_now(url, "production", platform_endpoint, debug=False)
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

    cases: list[tuple[str, str, object]] = [
        ("endpoint not configured", "", None),
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
        mode = json.loads(wire)["deployment_mode"]
        if mode != "self_hosted":
            fail(
                f"{name}: deployment_mode = {mode!r}, want 'self_hosted' — the tier must not alter topology"
            )
            continue
        ok(f"{name:<14} deployment_mode={mode!r} unchanged")


def main() -> int:
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
