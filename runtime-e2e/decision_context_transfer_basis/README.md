# decision_context_transfer_basis (v8.4.0)

Real-stack proof for the v8.4.0 SDK surface (platform epic #2508):

- **`DecisionSummary.context` / `DecisionExplanation.context`** — the sanitized
  request context a PEP attaches to a Decision Mode call is surfaced back
  through `list_decisions` and `explain_decision`.
- **`AuditLogEntry.transfer_basis = "pasal_56b_dpa"`** — the Pasal 56(b) explicit
  DPA tag round-trips verbatim.

The driver acts as the PEP (raw `POST /api/v1/decide` — that endpoint is not
SDK-wrapped per ADR-056), then reads the decision back through the SDK against a
real running agent and asserts `context` is populated with the forwarded keys.

## Run

```
export AXONFLOW_AGENT_URL=http://localhost:8080
export AXONFLOW_TENANT_ID=buku-e-py-e2e
export AXONFLOW_TENANT_SECRET=buku-e-secret
python runtime-e2e/decision_context_transfer_basis/test.py
```

Exits non-zero if the SDK does not surface the new fields.
