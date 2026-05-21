"""Real-stack assertion: SDK emits org_id field in telemetry heartbeat (v9.1).

Issue #2277. Sister proof to the Go/TS/Java/Rust SDK runtime-e2e tests.

The proof stands up a local HTTP listener and drives the SDK's
``send_telemetry_ping`` against it. Captures the raw POST body and
asserts the wire JSON literally contains ``"org_id":"<value>"``.

Usage:

    # ORG_ID set — operator-supplied (self-hosted) or cs_<uuid> (Community SaaS):
    ORG_ID=acme-corp python runtime-e2e/v91_org_id_telemetry/test.py

    # ORG_ID unset — local-dev-org sentinel:
    unset ORG_ID && python runtime-e2e/v91_org_id_telemetry/test.py

Companion functional E2E coverage runs in CI via
``tests/test_telemetry.py::TestSendTelemetryPingOrgIDOnWire``. This
runtime proof is a redundant real-stack confirmation, not a CI gate.
"""

from __future__ import annotations

import json
import os
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler

from axonflow.telemetry import ORG_ID_LOCAL_DEV_SENTINEL, send_telemetry_ping


def main() -> int:
    expected = os.environ.get("ORG_ID") or ORG_ID_LOCAL_DEV_SENTINEL

    captured: dict[str, bytes] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            captured["body"] = self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"latest_version": None, "alerts": []}).encode())

        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"version": "8.0.0-runtime-e2e"}).encode())

        def log_message(self, *_args: object) -> None:
            return

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as srv:
        port = srv.server_address[1]
        thread = threading.Thread(
            target=lambda: [srv.handle_request(), srv.handle_request()],
            daemon=True,
        )
        thread.start()

        os.environ["AXONFLOW_CHECKPOINT_URL"] = f"http://127.0.0.1:{port}/v1/ping"
        os.environ.pop("AXONFLOW_TELEMETRY", None)

        send_telemetry_ping(mode="production", endpoint=f"http://127.0.0.1:{port}")
        thread.join(timeout=5.0)

    body = captured.get("body")
    if not body:
        sys.stderr.write("FAIL: no telemetry body captured\n")
        return 1

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"FAIL: body is not valid JSON: {exc}\nBody: {body!r}\n")
        return 1

    got = payload.get("org_id")
    if got != expected:
        sys.stderr.write(f"FAIL: org_id = {got!r}, want {expected!r}\nBody: {body!r}\n")
        return 1

    print(f"PASS: telemetry wire payload carries org_id={got!r} (expected={expected!r})")
    print(f"Wire body: {body.decode()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
