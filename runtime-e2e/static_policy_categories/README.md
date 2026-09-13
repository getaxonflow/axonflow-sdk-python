# Static-policy categories: a read no longer fails on the platform's categories

`test.py` drives the SDK against a real AxonFlow agent, and reads `GET /api/v1/static-policies/effective`, whose policies carry categories the SDK's enum did not name before this change.

## What it proves

| Step | Expected |
|---|---|
| `get_effective_static_policies()` | returns instead of raising a validation error |
| The platform's `security-dangerous` policies | come back as `PolicyCategory.SECURITY_DANGEROUS` |
| Every category this platform returns | is one the SDK knows: none comes back as a plain string |

The driver prints the categories it saw and how many policies carry each.

## What it does not prove

A category a later platform adds would come back as the platform's string rather than failing the read; the unit tests in `tests/test_static_policy_category.py` prove that, and pin the SDK's known set to the platform's shipped-posture categories.

## Running it

Boot an agent from the platform's main (community mode is enough), then:

```bash
AXONFLOW_AGENT_URL=http://localhost:8080 \
AXONFLOW_CLIENT_ID=runtime-e2e AXONFLOW_CLIENT_SECRET=runtime-e2e-secret \
python runtime-e2e/static_policy_categories/test.py
```

It writes nothing to the stack.
