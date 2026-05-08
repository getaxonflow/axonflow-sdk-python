# Runtime proof — Sandbox-mode telemetry fires with stream=sandbox (v8)

Verifies the v8 contract: a `mode=Mode.SANDBOX` client (or the
`AxonFlow.sandbox()` factory) produces an anonymous heartbeat ping that
lands in checkpoint DynamoDB with the row tagged `stream="sandbox"`.

## When to run

**Post-deploy verification.** Two infrastructure prerequisites:

1. **`axonflow-enterprise` PR #2005 deployed** — without the server-side
   wire-allowlist, the Lambda hardcodes `stream=heartbeat` regardless of
   payload, and this test will fail at the assertion step. Confirm with:
   ```sh
   curl -sS -X POST -H 'Content-Type: application/json' \
     -d '{"sdk":"python","sdk_version":"8.0.0","stream":"community_saas_operational","instance_id":"x"}' \
     https://checkpoint.getaxonflow.com/v1/ping
   # Expect HTTP 400 "invalid stream value"
   ```
2. **AWS credentials** with read on `/aws/lambda/prod-axonflow-checkpoint`.

## Usage

```sh
AWS_REGION=us-east-1 ./test.sh
```

## What it asserts

1. Builds a tiny Python program against the local SDK via `pip install -e ..`.
2. The program constructs an `AxonFlow(mode=Mode.SANDBOX, ...)` client
   pointing at an unreachable endpoint. The SDK fires the anonymous
   heartbeat during construction (checkpoint POST is independent of the
   agent endpoint, so the unreachable agent doesn't suppress telemetry).
3. The Lambda's CloudWatch audit log records an `event_stored` row with
   `sdk=python/8` AND `stream=sandbox`.

## Pre-v8 behavior (regression-guard context)

In v7.x, `mode=Mode.SANDBOX` triggered a default suppression rule in
`_is_telemetry_enabled()` — sandbox-mode clients produced ZERO pings
unless `telemetry=True` was passed explicitly. The `telemetry` kwarg has
been removed in v8.0 and the suppression rule with it; sandbox-mode now
fires by default and tags the payload with `stream="sandbox"`. This
test guards against any future refactor restoring a mode-based gate.
