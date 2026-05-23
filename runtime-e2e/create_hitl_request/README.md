# `create_hitl_request` — runtime-e2e

Real-stack assertion for the cross-SDK
[`create_hitl_request`](https://github.com/getaxonflow/axonflow-enterprise/issues/2421)
surface added in Python SDK v8.2.0. Sister proof to the equivalent Go /
TypeScript / Java runtime-e2e tests shipping in the same parity sweep.

## What this proves

Drives `AxonFlow.create_hitl_request(...)` through the real `httpx`
transport against a `socketserver.TCPServer` listener that mimics the
platform handler at `platform/agent/hitl/handler.go:177`. Captures the
raw HTTP body, decodes it, and asserts every required field from
`axonflow.hitl.HITLCreateInput` lands on the wire — including the new
`notify_url` field added in
[#2419](https://github.com/getaxonflow/axonflow-enterprise/issues/2419)
— then asserts the SDK parses the platform's `APIResponse{success,
data}` envelope back into a populated `HITLApprovalRequest`.

Runs the production transport against an in-process HTTP server with
no library-level test doubles, which is what the
`Runtime E2E required for user-facing changes` DoD gate is asking for.

## Usage

```bash
python runtime-e2e/create_hitl_request/test.py
```

Exit 0 on PASS, 1 on FAIL. Prints captured wire body + parsed response
fields on success for human-readable confirmation.

## Companion unit coverage

`tests/test_hitl.py::TestCreateHITLRequest` exercises the same surface
through `httpx_mock` for five scenarios (happy path full-fields, minimal
required-fields, bad-`notify_url`-scheme 400 propagation, 401 →
`AuthenticationError`, network failure → `ConnectionError`). The
runtime proof here is the redundant real-stack confirmation.
