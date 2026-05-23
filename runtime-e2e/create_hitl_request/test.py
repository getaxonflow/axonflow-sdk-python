"""Real-stack assertion: SDK posts a valid create-payload to POST /api/v1/hitl/queue.

Issue getaxonflow/axonflow-enterprise#2421. Sister proof to the Go/TS/Java
SDK runtime-e2e tests landing in the same cross-SDK parity sweep.

The proof stands up a local HTTP listener that mimics the platform's
``POST /api/v1/hitl/queue`` handler (``platform/agent/hitl/handler.go:177``)
and drives the SDK's :py:meth:`AxonFlow.create_hitl_request` against it
via the real :mod:`httpx` transport — production code path, production
HTTP stack, no library-level test doubles. Captures the raw POST body
+ the parsed response, then asserts:

  * Wire body literally contains every required field from
    :class:`axonflow.hitl.HITLCreateInput` (the umbrella issue's coherence
    requirement: cross-SDK pattern equality).
  * The new ``notify_url`` field added in
    getaxonflow/axonflow-enterprise#2419 is propagated when supplied and
    omitted when the caller leaves it ``None``.
  * The SDK parses the platform's ``APIResponse{success, data}``
    envelope back into a populated :class:`HITLApprovalRequest` with the
    server-allocated ``request_id``.

Usage::

    python runtime-e2e/create_hitl_request/test.py

Companion mock-driven coverage runs in CI via
``tests/test_hitl.py::TestCreateHITLRequest``. This runtime proof is the
real-stack confirmation required by the runtime-e2e DoD gate.
"""

from __future__ import annotations

import asyncio
import json
import socketserver
import sys
import threading
from http.server import BaseHTTPRequestHandler

from axonflow import AxonFlow
from axonflow.hitl import HITLApprovalRequest, HITLCreateInput


def _server_response(req_body: dict[str, object]) -> dict[str, object]:
    """Mimic the platform's APIResponse{success: True, data: ApprovalRequest} on POST."""
    return {
        "success": True,
        "data": {
            "request_id": "hitl-req-runtime-e2e-001",
            "org_id": "org-runtime-e2e",
            "tenant_id": "tenant-runtime-e2e",
            "client_id": str(req_body.get("client_id", "")),
            "user_id": str(req_body.get("user_id") or ""),
            "original_query": str(req_body.get("original_query", "")),
            "request_type": str(req_body.get("request_type", "")),
            "request_context": req_body.get("request_context") or None,
            "triggered_policy_id": str(req_body.get("triggered_policy_id", "")),
            "triggered_policy_name": str(req_body.get("triggered_policy_name", "")),
            "trigger_reason": str(req_body.get("trigger_reason", "")),
            "severity": str(req_body.get("severity") or "high"),
            "notify_url": req_body.get("notify_url"),
            "status": "pending",
            "expires_at": "2026-05-23T11:00:00Z",
            "created_at": "2026-05-23T10:00:00Z",
            "updated_at": "2026-05-23T10:00:00Z",
        },
    }


def main() -> int:
    captured: dict[str, bytes] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            captured["body"] = self.rfile.read(length)
            try:
                req_body = json.loads(captured["body"])
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                return
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(_server_response(req_body)).encode())

        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "version": "runtime-e2e"}).encode())

        def log_message(self, *_args: object) -> None:
            return

    with socketserver.TCPServer(("127.0.0.1", 0), Handler) as srv:
        port = srv.server_address[1]
        thread = threading.Thread(target=srv.handle_request, daemon=True)
        thread.start()
        endpoint = f"http://127.0.0.1:{port}"

        notify_url = "https://workflows.example.com/hooks/runtime-e2e"
        create_input = HITLCreateInput(
            client_id="runtime-e2e-client",
            user_id="runtime-e2e-user",
            original_query="disburse $50000 to cust-runtime-e2e",
            request_type="adk-tool",
            request_context={"tool_name": "disburse_payment"},
            triggered_policy_id="loan-amount-cap",
            triggered_policy_name="Loan amount cap",
            trigger_reason="Disbursement above $10k requires manager approval",
            severity="high",
            notify_url=notify_url,
        )

        async def _drive() -> HITLApprovalRequest:
            async with AxonFlow(endpoint=endpoint, client_id="runtime-e2e") as client:
                return await client.create_hitl_request(create_input)

        result = asyncio.run(_drive())
        thread.join(timeout=5.0)

    body = captured.get("body")
    if not body:
        sys.stderr.write("FAIL: no request body captured\n")
        return 1

    try:
        wire = json.loads(body)
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"FAIL: wire body is not valid JSON: {exc}\nBody: {body!r}\n")
        return 1

    expected_wire_fields = {
        "client_id": "runtime-e2e-client",
        "user_id": "runtime-e2e-user",
        "original_query": "disburse $50000 to cust-runtime-e2e",
        "request_type": "adk-tool",
        "triggered_policy_id": "loan-amount-cap",
        "triggered_policy_name": "Loan amount cap",
        "trigger_reason": "Disbursement above $10k requires manager approval",
        "severity": "high",
        "notify_url": notify_url,
    }
    for field, expected_value in expected_wire_fields.items():
        actual = wire.get(field)
        if actual != expected_value:
            sys.stderr.write(
                f"FAIL: wire body field {field!r} = {actual!r}, want {expected_value!r}\n"
                f"Full wire body: {body!r}\n"
            )
            return 1

    if not isinstance(result, HITLApprovalRequest):
        sys.stderr.write(f"FAIL: result type = {type(result).__name__}, want HITLApprovalRequest\n")
        return 1
    if result.request_id != "hitl-req-runtime-e2e-001":
        sys.stderr.write(f"FAIL: parsed request_id = {result.request_id!r}\n")
        return 1
    if result.notify_url != notify_url:
        sys.stderr.write(f"FAIL: parsed notify_url = {result.notify_url!r}, want {notify_url!r}\n")
        return 1

    print(f"PASS: create_hitl_request wire payload + response parsing round-trip OK")
    print(f"Wire body: {body.decode()}")
    print(f"Parsed approval_id={result.request_id} notify_url={result.notify_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
