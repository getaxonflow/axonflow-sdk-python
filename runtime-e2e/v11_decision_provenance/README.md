# v11_decision_provenance

Real-stack proof that the SDK surfaces the v11.0.0 platform wire: decision provenance on
`decide`, the gateway pre-check and MCP check-output; `PlatformRouteDeprecationWarning` on a
legacy policy read; and `LegacyPolicyWriteFrozenError` on a legacy policy write.

## The stack it needs

A v11 agent and orchestrator. For the frozen-write leg, both must connect as the application
database role (`AXONFLOW_DB_USE_APP_ROLE=true` with `AXONFLOW_DB_APP_ROLE_URL` set), the way a
deployment runs them. The v11 freeze is a revoke on that role. A local stack that connects as the
database owner is not bound by it, and on such a stack the write would succeed.

The platform's local compose file connects as the owner by default. To run as a deployment does:
boot the stack, set the two role passwords with the platform's
`scripts/operators/provision-app-role.sh`, then recreate the agent and orchestrator with
`AXONFLOW_DB_USE_APP_ROLE=true`, `AXONFLOW_DB_APP_ROLE_URL` and `AXONFLOW_DB_PLATFORM_ADMIN_URL`.
The platform's `scripts/setup-e2e-testing.sh production-posture` does the same for an enterprise
stack.

## Run

```
AXONFLOW_AGENT_URL=http://localhost:8080 \
AXONFLOW_CLIENT_ID=runtime-e2e AXONFLOW_CLIENT_SECRET=runtime-e2e-secret \
python runtime-e2e/v11_decision_provenance/test.py
```

It prints each observed value and exits non-zero on any failed assertion.
