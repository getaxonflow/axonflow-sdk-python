# Runtime proof — `org_id` in SDK telemetry payload (v9.1)

Verifies the v9.1 contract for the Python SDK: every telemetry ping
body carries an `org_id` field, populated from the `ORG_ID` env var
with a `local-dev-org` sentinel fallback. Issue #2277.

## Usage

```sh
# ORG_ID set — operator-supplied (self-hosted) or cs_<uuid> (Community SaaS):
ORG_ID=acme-corp python runtime-e2e/v91_org_id_telemetry/test.py

# ORG_ID unset — local-dev-org sentinel:
unset ORG_ID && python runtime-e2e/v91_org_id_telemetry/test.py
```

Expected output:

```
PASS: telemetry wire payload carries org_id='acme-corp' (expected='acme-corp')
Wire body: {"telemetry_type":"sdk","sdk":"python", ... ,"org_id":"acme-corp"}
```

## What it asserts

1. The SDK constructed under any config emits a telemetry POST.
2. The POST body is valid JSON.
3. The body has an `org_id` key.
4. The value matches `$ORG_ID` (when set) or `local-dev-org` (when
   unset).

## CI coverage

This runtime proof is a redundant real-stack confirmation alongside
the functional E2E tests in `tests/test_telemetry.py`:

- `TestTelemetryOrgIDHelperUnit` — helper env→sentinel fallback
- `TestBuildPayloadIncludesOrgID` — payload always carries field
- `TestSendTelemetryPingOrgIDOnWire` — three subtests confirming the
  wire literal across acme-corp / cs_-prefixed / sentinel modes

## Mutation proof

Remove the `"org_id": _telemetry_org_id(),` line from
`axonflow/telemetry.py`'s `_build_payload` and rerun. The proof exits
with `FAIL: org_id = None, want '<expected>'`.

## Cross-SDK parity

Companion runtime-e2e tests live under the same subdirectory in the
other 4 SDKs:

- `axonflow-sdk-go/runtime-e2e/v91_org_id_telemetry/`
- `axonflow-sdk-typescript/runtime-e2e/v91_org_id_telemetry/`
- `axonflow-sdk-java/runtime-e2e/v91_org_id_telemetry/`
- `axonflow-sdk-rust/runtime-e2e/v91_org_id_telemetry/`

All five SDKs emit `org_id` with the same wire name, same sentinel
value (`local-dev-org`), and the same precedence (env → sentinel).
