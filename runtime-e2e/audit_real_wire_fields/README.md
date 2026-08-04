# audit_real_wire_fields (#3254)

Real-stack proof for the audit read model's real wire fields
(getaxonflow/axonflow-enterprise#3254 additive interim).

The orchestrator has never served `query_summary`/`success`/`blocked`/
`risk_score`/`latency_ms`/`policy_violations`/`metadata` on the 9.x
line; the real wire carries `policy_decision`, `policy_details` and
`response_time_ms`, and the search filter the server reads is `action`
(not `request_type`).

## What this proves

Drives the real SDK (`client.audit_tool_call` + `client.search_audit_logs`)
against a real running agent:

1. Freshly written success/failure rows come back with `policy_decision`
   populated (`allowed` / `error`) and `policy_details` carrying the tool
   name and error message, on the TYPED `AuditLogEntry`.
2. The seven deprecated fiction fields stay at their defaults on real rows.
3. `AuditSearchRequest(action=...)` filters server-side (returns only the
   matching verdict, including this run's row).
4. A search filtered only by a nonsense `request_type` returns unfiltered
   results - the 9.x server does not read that filter (the deprecation
   claim).

## Run

```
export AXONFLOW_AGENT_URL=http://localhost:8080
export AXONFLOW_TENANT_ID=<client id>
export AXONFLOW_TENANT_SECRET=<secret>
python runtime-e2e/audit_real_wire_fields/test.py
```
