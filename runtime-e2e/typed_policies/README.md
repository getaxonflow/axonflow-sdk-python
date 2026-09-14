# Typed policy authoring: the six routes through the agent

`test.py` drives `client.typed_policies` against a real AxonFlow agent and orchestrator, and asserts what the platform answered to each of the six routes the agent proxies under `/api/v1/typed-policies`.

## What it proves

| Call | Expected answer |
|---|---|
| `active()` before anything is activated | `None`, from the platform's `404` whose reason is `nothing_active` |
| `edition()` | the deployment's root and construct boundary; its vocabulary named by `catalog_digest` and a `registry_version`, and `catalog_fixture` false |
| `system()` | the shipped system corpus: its digest and controls, with at least one control named and one mandatory, and every `mandatory` a `bool` |
| `validate(document, fixtures)` | success, with no rejecting finding |
| `publish(document, fixtures)` | a signed artifact's digest, and `template_omissions` naming every organization template control the document omits (`len(omitted) == of > 0`) |
| `activate(digest)` | success: the digest is promoted to active, with the same `template_omissions` as the publication |
| `active()` after the activation | the document just activated, as the exact signed source; its author is the caller the agent resolved, not the `someone-else` the document names (on Community: `Client` / `axonflow-api-credential` / the client id) |
| `activate(digest)` again | `TypedPolicyRefusal` with status 409 and reason `activation_refused`: activation promotes, and the version does not advance |
| `publish(document, [])` | `TypedPolicyRefusal` with status 422 and reason `publication_refused`, whose message names the missing fixtures |
| `validate()` on the document with one action changed to `tool.not_registered` | `success` false, with the platform's rejecting finding `ACTION_NOT_REGISTERED` on `grant.refund` |
| `publish()` of that document | `TypedPolicyRefusal` with status 422 and reason `document_refused`, carrying the same finding |

The document is `tests/fixtures/typed_policy_publish_body.json`, the body the platform's own route test proves publishable, marshalled by the platform's own types. The driver gives it a unique `document_id` per run.

## What it does not prove

Rolling back to an earlier document and withdrawing the active one are customer portal operations that the agent does not proxy, so the SDK has no method for either and this driver does not exercise them. The separation-of-duties refusal (`APPROVER_IS_AUTHOR`) needs an edition with separation of duties; the unit tests in `tests/test_typed_policies.py` assert its shape, and the platform's suites own the rule.

Two members are covered by unit tests only. `TypedPolicyRefusal.policy`, the policy a tier refusal names, needs a deployment at its tier ceiling. `template_omissions_unavailable`, the reason the platform could not produce the omission report, needs a document store that cannot be read. This driver sets up neither.

## Running it

Boot a FRESH stack from the platform's main, one on which nothing is active for the organization, then:

```bash
AXONFLOW_AGENT_URL=http://localhost:8080 \
AXONFLOW_CLIENT_ID=runtime-e2e AXONFLOW_CLIENT_SECRET=runtime-e2e-secret \
python runtime-e2e/typed_policies/test.py
```

The run writes: it publishes and activates a document, so the organization's active document afterwards is the one the run activated, and a second run against the same stack fails its first assertion.
