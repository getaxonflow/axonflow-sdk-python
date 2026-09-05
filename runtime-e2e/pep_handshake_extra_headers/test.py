"""Real-stack proof of per-call ``extra_headers`` (axonflow-enterprise#3763).

Asserts, through the SDK's real public surface and over REAL SOCKETS, the two
properties the ADR-065 PEP capability handshake depends on:

1. a declaration passed to one governed call REACHES THE WIRE on that call, and
2. it DOES NOT LEAK - not into the next call, not into another method, and not
   into the client's default headers.

WHY A REAL LISTENER RATHER THAN THE UNIT TESTS' httpx_mock. The unit tests
assert what the SDK hands to a mock transport. That cannot prove the header
survives the REAL transport: httpx merges per-request headers over client
defaults inside the layer the mock replaces, so a change in that merge - or a
client-level header of the same name added later - would be invisible to them.
This driver runs a real ``http.server`` and the SDK's own httpx client sends
real bytes to it, so the assertions are on the headers a server actually
received. Nothing about the SDK is mocked; the stand-in is the PEER, exactly as
in the neighbouring ``adapter_telemetry`` driver.

WHY THE LEAK CASE IS THE POINT. Without it a per-call declaration silently
becomes a per-client one on the second request, and a process that is TWO
enforcement points - a request path and a response path declaring different
capability sets - would have one path's declaration answer for the other's
call. That is the collapse the parameter exists to prevent, and it is only
observable across two requests on one client.

Run::

    python runtime-e2e/pep_handshake_extra_headers/test.py
"""

from __future__ import annotations

import asyncio
import http.server
import json
import socketserver
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from axonflow import AxonFlow  # noqa: E402

HEADER = "X-Axonflow-PEP-Handshake"
REQUEST_DECL = "eyJwcm9maWxlX3ZlcnNpb24iOjEsInBlcF9pZCI6ImFkay1yZXF1ZXN0In0"
RESPONSE_DECL = "eyJwcm9maWxlX3ZlcnNpb24iOjEsInBlcF9pZCI6ImFkay1yZXNwb25zZSJ9"

RECEIVED: list[dict[str, str]] = []


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        # Record the path and every header this request actually carried.
        RECEIVED.append({"path": self.path, **{k: v for k, v in self.headers.items()}})
        body = json.dumps({"allowed": True, "policies_evaluated": 1}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        """Silence the stdlib access log; the assertions are the output."""


FAILURES: list[str] = []


def check(condition: bool, description: str) -> None:
    if condition:
        print(f"  PASS: {description}")
    else:
        print(f"  FAIL: {description}")
        FAILURES.append(description)


async def main() -> int:
    with socketserver.TCPServer(("127.0.0.1", 0), _Handler) as server:
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()

        client = AxonFlow(
            endpoint=f"http://127.0.0.1:{port}",
            client_id="runtime-e2e",
            client_secret="runtime-e2e-secret",  # noqa: S106
        )

        # Two governed calls on ONE client, presenting DIFFERENT declarations,
        # then a third presenting none.
        await client.mcp_check_input(
            "postgres", "select 1", extra_headers={HEADER: REQUEST_DECL}
        )
        await client.mcp_check_output(
            "postgres", message="hi", extra_headers={HEADER: RESPONSE_DECL}
        )
        await client.mcp_check_input("postgres", "select 2")

        server.shutdown()

    if len(RECEIVED) != 3:
        print(f"  FAIL: expected 3 requests on the wire, got {len(RECEIVED)}")
        return 1

    first, second, third = RECEIVED

    check(
        first["path"].endswith("/api/v1/mcp/check-input")
        and first.get(HEADER) == REQUEST_DECL,
        "the request-path declaration reached the wire on check-input",
    )
    check(
        second["path"].endswith("/api/v1/mcp/check-output")
        and second.get(HEADER) == RESPONSE_DECL,
        "the response-path declaration reached the wire on check-output",
    )
    # The two-enforcement-points property, observable only across two requests.
    check(
        first.get(HEADER) != second.get(HEADER),
        "two calls on ONE client presented DIFFERENT declarations",
    )
    # The leak property. A third call passing nothing must carry nothing.
    check(
        HEADER not in third,
        "a call passing no declaration carried NO handshake header at all",
    )
    # Absent, not empty: a present-but-empty value is malformed to the platform
    # and would refuse the request, which an absent header does not.
    check(
        third.get(HEADER, None) is None,
        "the header is ABSENT rather than present-and-empty when unconfigured",
    )
    # The credential headers are untouched on every request, so a declaration
    # cannot displace authentication.
    check(
        all(r.get("Authorization", "").startswith("Basic ") for r in RECEIVED),
        "every request still authenticated with its Basic credential",
    )

    if FAILURES:
        print(f"\nFAIL: pep_handshake_extra_headers ({len(FAILURES)} assertion(s))")
        return 1
    print("\nPASS: pep_handshake_extra_headers (6 assertions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
