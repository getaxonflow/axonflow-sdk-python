# Runtime proof — `license_tier` in SDK telemetry (#3619)

Verifies that the SDK reports the connected platform's licence tier on its telemetry heartbeat, reads it from the `/health` response it **already** fetches for `platform_version`, and **omits** the field on every path where the tier could not be learned.

Closes the gap where telemetry could not distinguish an enterprise-licensed deployment from an unlicensed community one.

## Usage

```sh
# 1. MATRIX — every tier value and every fail-open path, against a local stand-in platform.
python runtime-e2e/license_tier_telemetry/test.py

# 2. REAL PLATFORM — drive the SDK at a live agent and cross-check the wire
#    value against that agent's own /health.
AXONFLOW_E2E_PLATFORM_ENDPOINT=http://localhost:8080 \
  python runtime-e2e/license_tier_telemetry/test.py
```

Mode 2 is the one that proves the contract end to end: it reads the tier from the live platform independently, then asserts the SDK put *that* value on the wire verbatim. If the endpoint is unreachable it asserts the **platform-down** contract instead — ping still delivered, field omitted.

## What it asserts

1. `community`, `evaluation`, `Enterprise`, the csaas `Plus` alias and the transient `starting` each reach the wire byte-for-byte. No client-side case folding or alias mapping — normalization is the receiver's job (checkpoint-service `NormalizeLicenseTier`), and folding here would mask a tier this SDK build predates.
2. On every not-learned path — platform down, HTTP 500, malformed body, no `tier` key, empty `tier`, non-string `tier` — the ping is **still delivered** and `license_tier` is **absent** from the JSON. Never `null`, never a substituted default.
3. `deployment_mode` is unchanged by the tier. The two dimensions stay separate.

## Omission, not null

`platform_version` has always been sent as an explicit `null` when unknown, and that long-standing wire shape is unchanged. `license_tier` is different on purpose: the key is **omitted**. `null` is a claim ("the tier is nothing"); omission is what this wire uses for "we do not know", and the receiver preserves it for legacy pings.

## Mutation proof

| Mutation | Failing assertion |
|---|---|
| Delete `payload["license_tier"] = license_tier` in `_build_payload` | case 1 — `license_tier absent from wire` |
| Drop the `if license_tier is not None:` guard (assign unconditionally) | case 2 — `license_tier present as None` |
| Restore the pre-#3619 early return when `version` is empty | unit test `test_each_field_promoted_on_its_own` — a platform reporting a tier but no version loses the tier |
| Fold the tier client-side (`tier.lower()`) | unit test `test_every_platform_emitted_value_is_forwarded_unchanged[Plus]` |

## CI coverage

The equivalent assertions run in CI as `tests/test_telemetry_license_tier.py` (23 tests), which also stand up real `http.server` listeners on both sides rather than mocking `httpx` — a mocked transport certifies the payload dict, only the wire body proves what the receiver sees. This runtime proof is a real-stack confirmation, not a CI gate.
