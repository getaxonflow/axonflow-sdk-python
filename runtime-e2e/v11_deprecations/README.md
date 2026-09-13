# v11 deprecations: each deprecated route is reported once per client

`test.py` drives the SDK against a real AxonFlow agent, and asserts how it reports the platform's deprecation of its legacy policy routes.

## What it proves

| Step | Expected |
|---|---|
| The platform's own stamps on `GET /api/v1/static-policies` and `GET /api/v1/static-policies/effective`, read raw and printed | `X-AxonFlow-Removed-In: v11.1`, and a `Link` naming `/api/v1/typed-policies` as the successor |
| `list_static_policies()` twice on one client | one `PlatformRouteDeprecationWarning`, naming the route, the removal release and the successor |
| The same call from a client derived with `as_user` | nothing new: a derived client shares its parent's memory |
| `get_effective_static_policies()` twice | one report for that route, and nothing new on the second call |

Counts are taken with `warnings.simplefilter("always")`, so Python's own warning filter cannot hide a repeat: they are what the SDK emits.

## What it does not prove

The simulation routes (`simulate`, `impact-report`, `conflicts`), which this SDK also documents as deprecated, are registered only on an Evaluation+ licence. The Go SDK's live `v11_deprecations` leg proves the platform's stamps on them. This SDK's reporting of them rests on its unit tests in `tests/test_legacy_deprecations.py`, which stamp exactly the headers that run observed.

## Running it

Boot an agent from the platform's main (community mode is enough), then:

```bash
AXONFLOW_AGENT_URL=http://localhost:8080 \
AXONFLOW_CLIENT_ID=runtime-e2e AXONFLOW_CLIENT_SECRET=runtime-e2e-secret \
python runtime-e2e/v11_deprecations/test.py
```

It writes nothing to the stack.
