# Hosted paid-refusal runtime probe

Run `test.py` only against the Hosted staging Agent with two disposable,
separately seeded Basic credentials: an unpaid tenant and a paid tenant whose
monthly governed-event allowance is already exhausted. Start during a fresh
minute so the paid fixture reaches the monthly cap rather than the burst cap.
The probe sends one synthetic governed request for each tenant, disables SDK
telemetry, and refuses any non-staging URL. It does not create or revoke the
fixtures.

```sh
export AXONFLOW_HOSTED_STAGING_AGENT_URL=https://try-staging.getaxonflow.com
export AXONFLOW_HOSTED_UNPAID_CLIENT_ID=cs_synthetic_unpaid
export AXONFLOW_HOSTED_UNPAID_CLIENT_SECRET=... # supply privately
export AXONFLOW_HOSTED_CAPPED_CLIENT_ID=cs_synthetic_capped
export AXONFLOW_HOSTED_CAPPED_CLIENT_SECRET=... # supply privately
python3 runtime-e2e/hosted_paid_refusals/test.py
```

Do not put credential values in shell history or logs. Remove the disposable
fixtures after recording only the redacted result.
