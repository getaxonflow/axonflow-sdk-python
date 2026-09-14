# v11_examples

Real-stack proof that the two v11 examples run as the README says. `run.sh` installs the SDK from this tree into a virtual environment in a temporary directory, and runs `examples/pep_handshake.py` and `examples/typed_policies.py` with that interpreter against a live Community agent, in the README's order: the handshake example first. Every run starts from the temporary directory, outside the tree, with no body file: `typed_policies.py` finds its default document from its own location. Nothing is mocked.

## Precondition

Before any run, `curl` asks `GET /api/v1/typed-policies/active` and requires `404` with reason `nothing_active`: no typed document is active. Otherwise the leg stops with exit 2, because it changes the organization's active policy and needs a fresh stack. The check is made outside the SDK, so an SDK regression is never reported as a stale stack.

## What it proves

| Run | Expected |
|---|---|
| 1. `pep_handshake.py` | exits 0; the first decide's verdict is `allow`; the declaration the platform would refuse fails in the client at `/pep_id`, before anything is sent |
| 2. `typed_policies.py` without publishing, the README's default | exits 0; `active()` reads the platform's `nothing_active` as nothing active |
| 3. `typed_policies.py` with `AXONFLOW_TYPED_POLICY_PUBLISH=1` | exits 0; prints the publication's template-omission report before it activates; the document is published and activated |
| 4. `typed_policies.py` asked to publish a document the save-time checks reject | exits 1, printing the platform's typed `422 document_refused`: an example that was asked to publish and could not must not report success |
| 5. `typed_policies.py` publishing the same document again | the publication is accepted, and the activation is refused as a typed `409 activation_refused`; exits 1, since an example asked to activate and refused must not report success either |
| 6. `pep_handshake.py` again, after the activation | printed as an observation, not asserted (below) |

Run 4 publishes a copy of `tests/fixtures/typed_policy_publish_body.json` with one action the registry does not contain (the same edit `runtime-e2e/typed_policies` makes), written to the temporary directory. It is refused before anything is admitted, so it changes nothing on the stack.

Run 5 is refused because activation promotes. An artifact's digest covers its publication time, so publishing the same document again produces a new artifact, but its document version is the active one's, and a version that does not advance is not promoted.

## Why the sixth run is an observation

After a document with an organization-scope constraint is activated, a decide that does not supply the attribute the constraint conditions on is denied fail-closed with reasons ["unknown_constraint"]; supply the attribute or run this example on a fresh stack. From v11.0.0 the deny's first reason is that code, followed by one naming each constraint it could not evaluate and the attribute it needed (getaxonflow/axonflow-enterprise#4247). The example's default document is such a document, so the sixth run shows that deny. It is the platform's by-design answer, not the SDK's, so this leg prints it rather than pinning it. It is why the README runs the handshake example first.

A stack built before #4247 shows only the bare `["unknown_constraint"]`.

## What it does not prove

- **That the handshake reaches the wire.** A fresh Community stack allows the first decide with or without a declaration, and the `/pep_id` refusal happens in the client. The wire proof is `runtime-e2e/pep_handshake_planes`.
- **That the organization carries no other state.** The precondition proves no typed document is active. A recorded detection override, or a legacy per-policy override on a platform that still accepts them, would still apply. And while getaxonflow/axonflow-enterprise#4255 is open, an unreadable document store also answers `nothing_active`.

## Run

Community on the application database role, on a fresh stack:

```
AXONFLOW_AGENT_URL=http://localhost:8080 ./runtime-e2e/v11_examples/run.sh
```

It leaves `AXONFLOW_CLIENT_ID` and `AXONFLOW_CLIENT_SECRET` unset, so the examples present the client id `community`, and on Community the organization is the deployment's (`ORG_ID`). `PYTHON` names the interpreter the virtual environment is built from (default `python3`; 3.10 or later), and installing the SDK needs its dependencies from the package index. It exits 0 when every assertion passes, 1 when one fails, and 2 when `timeout` (GNU coreutils) or a suitable Python is not installed, the agent is not reachable, or a typed document is already active. It changes the organization's active policy.
