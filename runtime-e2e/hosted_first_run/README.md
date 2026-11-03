# Hosted first-run staging proof

This guarded test requires the exact staging Portal and Agent origins and a client ID and secret linked through a verified, paid staging account. Run it only after the staging stack and public TLS/DNS are restored:

```bash
export AXONFLOW_HOSTED_E2E_PORTAL=https://portal.try-staging.getaxonflow.com
export AXONFLOW_HOSTED_E2E_ENDPOINT=https://try-staging.getaxonflow.com
export AXONFLOW_HOSTED_E2E_CLIENT_ID='<linked-staging-client-id>'
export AXONFLOW_HOSTED_E2E_CLIENT_SECRET='<linked-staging-client-secret>'
python3 runtime-e2e/hosted_first_run/test.py
```

Unset `AXONFLOW_TRY` for this staging run because that legacy switch selects the production Hosted origin. The test verifies that the old Hosted registration helper stops before HTTP, the real Portal serves the Hosted start page, and the linked SDK receives an allow decision for a harmless check-input call from the real Agent. It does not perform signup, start Checkout, charge a card, or issue credentials. Without both exact staging origins it reports SKIP.
