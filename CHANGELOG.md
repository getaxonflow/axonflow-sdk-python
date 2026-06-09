# Changelog

All notable changes to the AxonFlow Python SDK will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

<!-- Before tagging a release: rename "[Unreleased]" to "[X.Y.Z] - YYYY-MM-DD"
 and tag v{X.Y.Z}. The release workflow's preflight checks the section
 header matches the tag. -->

## [8.5.0] - 2026-06-09 — Decision Mode PEP: decide → fulfill → forward

Adds the SDK analog of the platform PEP client (`platform/shared/pep`,
ADR-056, epic #2563). A Policy Enforcement Point now follows one path —
**decide → fulfill → forward** — and the SDK makes the engine-fulfillable
obligation contract impossible to misuse: there is **no local redaction
path**, so a `redact_pii` obligation can only be discharged by round-tripping
content through the engine endpoint the obligation names.

### Added

- **`AxonFlow.decide(DecideRequest)` / `SyncAxonFlow.decide`** — the PDP step.
  `POST /api/v1/decide` returns a `DecideResponse` whose `obligations` is a
  list of self-describing `Obligation`s. Decision Mode auth is HTTP Basic
  (org:license), which the client already sends; wrong/demo credentials are
  refused with `AuthenticationError`.
- **`AxonFlow.fulfill_request(decision, statement)`** — discharges every
  request-phase `redact_pii` obligation by POSTing the statement to the
  engine's `check-input` endpoint and returning the **engine-redacted**
  statement. Fails closed with `ObligationNotFulfillableError` when an
  obligation names no request-phase fulfillment, advertises a content-type the
  PEP is not holding, names an endpoint the client will not call, the engine
  call fails, or the engine reports `redaction_evaluated=false`. Never redacts
  locally.
- **`AxonFlow.decide_and_fulfill(DecideRequest)`** — the blessed one-call path
  (decide, then fulfill any request-phase obligation); fail-closed by
  construction.
- **New types**: `DecideRequest`, `DecideResponse`, `Obligation`,
  `ObligationFulfillment`, `DecisionCallerIdentity`, `DecisionTarget`.
- **New exception**: `ObligationNotFulfillableError` (a fail-closed signal).
- **PEP constants** + `has_request_redaction(obligations)` helper
  (`OBLIGATION_REDACT_PII`, `PHASE_REQUEST`/`PHASE_RESPONSE`,
  `CONTENT_TYPE_TEXT`, `VERDICT_ALLOW`/`VERDICT_DENY`/`VERDICT_NEEDS_APPROVAL`,
  endpoint-path constants).
- **`redacted` / `redacted_statement` / `redaction_evaluated` on
  `MCPCheckInputResponse`** and **`redaction_evaluated` on
  `MCPCheckOutputResponse`** — the request-redaction contract fields the agent
  emits (ADR-056). A PEP fulfilling an obligation fails closed when
  `redaction_evaluated` is false.
- **`content_type` on `MCPCheckInputRequest` / `mcp_check_input(...)`** —
  selects the request-redaction detector (defaults to `text/plain`).

### Notes

- SDK semver is decoupled from the platform: this is a **minor** bump from
  8.4.0 (purely additive, optional fields backward-compatible with older
  platforms). The wire-shape baseline records the new fields as an
  acknowledged SDK superset pending the OpenAPI spec catching up.

## [8.4.0] - 2026-05-30 — Decision request context + Pasal 56(b) transfer basis

Targets AxonFlow platform **v8.5.0**.

### Added

- **`context` field on `DecisionSummary` and `DecisionExplanation`** —
  `dict[str, str] | None`. Surfaces the sanitized request context a PEP attaches
  to a Decision Mode call (canonical `lower_snake_case` keys such as `x_ai_agent`,
  `x_session_id`, `x_leader_identity`, and `x-bukuwarung-*`), persisted by the
  platform at the audit row's `policy_details->'context'`. `list_decisions()`
  returns the platform-truncated summary (5 keys); `explain_decision()` returns
  the full map. `None` for pre-v8.4.0 audit rows.
- **`context_truncated` field on `DecisionExplanation`** — `bool | None`. True
  when the agent dropped surplus context keys at write time.
- **`TransferBasis` Literal alias and `TRANSFER_BASIS_*` constants**
  (`TRANSFER_BASIS_ADEQUACY`, `TRANSFER_BASIS_SAFEGUARDS`,
  `TRANSFER_BASIS_PASAL_56B_DPA` = `"pasal_56b_dpa"`, `TRANSFER_BASIS_CONSENT`),
  exported from the package root. Type-safe access to the Indonesia UU PDP
  Pasal 56 legal bases.

### Changed

- **`AuditLogEntry.transfer_basis` documentation** now records `pasal_56b_dpa`
  (Pasal 56(b) explicit DPA tag) alongside `adequacy`, `safeguards`, and
  `consent`. The field stays `str | None` (not a closed `Literal`) so existing
  code passing `safeguards` is unaffected and the SDK never rejects a value a
  newer platform may add on an audit read.

## [8.3.0] - 2026-05-27 — Indonesia PII category + cross-border audit fields

### Added

- **`PolicyCategory.PII_INDONESIA` constant** (`"pii-indonesia"`).
  Enables filtering and creating policies for Indonesian PII detection
  (NIK, KK, NPWP, BPJS) alongside the existing per-jurisdiction categories.
- **`data_residency` and `transfer_basis` fields on `AuditLogEntry`.**
  Optional string fields supporting cross-border data transfer logging.
  `data_residency` is an ISO 3166-1 alpha-2 country code;
  `transfer_basis` is one of `adequacy`, `safeguards`, or `consent`.
  Both default to `None` for backward compatibility with older platform versions.
- **Indonesia compliance example** (`examples/indonesia_compliance.py`):
  demonstrates NIK detection, audit log querying with cross-border fields,
  and policy filtering by the new `pii-indonesia` category.

## [8.2.0] - 2026-05-23 — `create_hitl_request` for explicit HITL row creation

Enables agent-framework plugins (Google ADK, n8n, OpenAI Agents SDK) to
implement the full 4-step HITL approval flow against AxonFlow:

  1. Gate evaluates `require_approval` (via `pre_check` / `check_tool_input`)
  2. Plugin calls `client.create_hitl_request(...)` to enqueue the row
  3. Plugin polls `client.get_hitl_request(approval_id)` until terminal state
  4. Plugin resumes the agent or denies the call based on the decision

Prior to this release the SDK exposed `get_hitl_request` /
`approve_hitl_request` / `reject_hitl_request` (the read + review
surface) but had no method to **create** a row. The platform's
`POST /api/v1/hitl/queue` endpoint has existed since v6.x; only the SDK
surface was missing. Ships as the Python half of the cross-SDK parity
sweep (getaxonflow/axonflow-enterprise#2421) paired with the platform's
new `notify_url` outbound-webhook field
(getaxonflow/axonflow-enterprise#2419).

### Added

- **`client.create_hitl_request(request: HITLCreateInput) -> HITLApprovalRequest`**
  (async). Sync wrapper on `SyncAxonFlow` mirrors the async shape.
- **`HITLCreateInput` model** in `axonflow.hitl` mirroring
  `platform/agent/hitl/handler.go:86 CreateRequestInput`. Required
  fields: `client_id`, `original_query`, `request_type`. Optional fields
  cover policy attribution, severity, compliance framework, and an
  expiry override. `X-Org-ID` / `X-Tenant-ID` are derived from the SDK
  client's configured credentials by the platform's auth middleware —
  callers do not pass them through this method.
- **`notify_url` field on `HITLCreateInput` and `HITLApprovalRequest`
  (forward-look).** Accepted on the wire today but platform-side
  webhook dispatch on terminal state is on the roadmap (NOT live in
  v9.0). Carrying the field through the SDK now means callers can
  populate it once and pick up webhook-driven resume automatically
  when the platform feature lands. Intended consumers: n8n Wait-node
  "On Webhook Call" + ADK polling-free mode.
- Five pytest cases covering: full-fields create, minimal-required-fields
  create, bad-`notify_url`-scheme 400 propagation, 401 mapping to
  `AuthenticationError`, and connection-failure mapping to the SDK's
  `ConnectionError`. Parity with the TS/Go/Java/Rust sister sweep
  (getaxonflow/axonflow-enterprise#2421).

### Compatibility

No breaking changes. New imports are additive in `axonflow.hitl`. The
existing `get_hitl_request` / `approve_hitl_request` /
`reject_hitl_request` methods are unchanged.

Requires AxonFlow platform >= 8.1.0 for `notify_url` webhook delivery
and `Idempotency-Key` request deduplication.

## [8.1.0] - 2026-05-22 — `X-Client-ID` header on every outbound request + `org_id` in telemetry heartbeat

Companion release to the v9 identity cleanup on the platform. Every
governed request now carries an `X-Client-ID: <effective_client_id>`
header alongside the existing Basic Auth + `X-Axonflow-Client` headers.
Value matches the SDK's Basic Auth username — smart default `community`
when no `client_id` is configured.

### Added

- **`X-Client-ID` header on outbound HTTP requests.** Server-side identity
 decisions no longer need to re-decode Basic Auth. The platform's auth
 middleware overwrites the header with its own auth-derived value, so
 caller-supplied values are harmless (no spoofing surface).
- **`org_id` field in the telemetry heartbeat body.** Brings the Python
 SDK telemetry up to parity with the platform — every heartbeat now
 identifies which deployment-organization emitted it. Two sources in
 precedence order:
 1. The `ORG_ID` env var when set (the explicit configuration
    on self-hosted deployments, or the `cs_<uuid>` tenant identifier on
    Community SaaS).
 2. Otherwise the `local-dev-org` sentinel (default-config Community-mode
    developers).
 Always emitted by v8.1+ SDKs; older receivers ignore the field cleanly
 for backward compat. Honors `AXONFLOW_TELEMETRY=off` like every other
 heartbeat field. See
 [getaxonflow.com/privacy/](https://getaxonflow.com/privacy/) for the
 customer-facing commitment that covers this field.

### Changed

- **Telemetry-enabled log line** softened from "anonymous telemetry
 enabled" to "telemetry enabled" to stay coherent with the `org_id`
 addition — the configured `ORG_ID` on self-hosted deployments is not
 anonymized; only the `instance_id` and `cs_<uuid>` Community SaaS
 identifier remain anonymous-by-design.

### Compatibility

- Backward-compatible against v8 and v9 platforms: v8 agents ignore the
 unknown header; v9 agents derive identity from Basic Auth regardless.
- `org_id` is an additive field — older receivers ignore it cleanly,
 legacy SDK builds keep working unchanged.
- No SDK config changes. No removed fields. No changed defaults.

## [8.0.0] - 2026-05-09 — Decision History API + policy_version recorded on every decision + telemetry simplification

**Major release.** The headline feature is the new decision-history client API:
`list_decisions` for paging through recorded decisions, alongside the
`get_decision_explain` method shipped in v7.4.0 — callers can now both list
and drill in. Bundled into a major because the v8 line also tightens the
telemetry contract — see `Removed` at the bottom of this entry for that.

### Added

- **`client.list_decisions(opts)` method.** Pages over recorded decision
 history from the orchestrator, mirroring `GET /api/v1/decisions`.
 Companion to the v7.4.0 `get_decision_explain` method — callers can
 now both list and drill in. Already shipped on `main` and
 graduated into the v8.0 line with this release. See type
 `ListDecisionsOptions` and `DecisionListItem` in `axonflow.decisions`.

### Migration guide (v7 → v8)

- **`AxonFlow(...)` no longer accepts the `telemetry` keyword argument.**
 Code passing `AxonFlow(..., telemetry=True)` or
 `AxonFlow(..., telemetry=False)` will raise `TypeError` at construction
 time. Migration:
 - If you were using it to disable telemetry, set
 `AXONFLOW_TELEMETRY=off` in the environment instead — that's the
 sole opt-out lever as of v8.0.
 - If you were using it to force-enable, the default is now ON for
 every mode so the argument is no longer needed.
- **`AxonFlowConfig.telemetry` field removed.** Code that constructed
 the dataclass directly with `AxonFlowConfig(..., telemetry=...)` will
 fail to instantiate. Drop the field; rely on the env var.

### Telemetry

- **`AXONFLOW_TELEMETRY=off` is the sole opt-out.** `AxonFlow(..., telemetry=...)` keyword argument + `AxonFlowConfig.telemetry` field both removed; sandbox-mode clients now fire on the same 7-day heartbeat schedule as production (was suppressed pre-v8), tagged `stream="sandbox"` so dev pings stay distinguishable.
- **Heartbeat payload v1 schema additions** on the wire: new `telemetry_type` and `deployment_mode` fields. Existing receivers continue working unchanged — strictly additive.

## [7.1.0] - 2026-05-06 — X-Axonflow-Client header + scope-aware license validation

**Companion release to platform v7.7.0.** The Python SDK now sends an
`X-Axonflow-Client` identification header on every governed request, which
the agent uses to derive the SDK request scope and validate it against any
license token's audience claim per the license matrix.

### Added

- **`X-Axonflow-Client: sdk-python/<version>` header** on every governed
 outbound request. Set automatically by the SDK transport; not
 configurable. Agents at v7.7.0+ derive request scope from this header
 and reject cross-quadrant token misuse (e.g. a SaaS Plugin Pro token
 paired with an SDK request) at the validator boundary. Older agents
 (pre-v7.7.0) ignore the header and continue to work unchanged.

### Compatibility

- **No public API changes.** Existing v7.0.x callers `pip install
 --upgrade axonflow` and rebuild against v7.1.0 with no source changes.
- **Backward-compatible against pre-v7.7.0 agents.** The header is
 silently dropped by older agents; the SDK behaves identically against
 v7.0.x / v7.1.x / v7.6.x agents as before.
- **Forward-compatible.** Future agent releases that require the header
 on specific governed surfaces will work with this SDK without further
 client changes.

### Companion releases (same day)

- **Platform v7.7.0** — V1 SaaS Plugin Pro launch, license matrix,
 per-tenant tier resolution, GDPR right-to-erasure
 ([CHANGELOG](https://github.com/getaxonflow/axonflow/blob/main/CHANGELOG.md))
- **Go SDK v7.1.0** / **TypeScript SDK v7.1.0** /
 **Java SDK v7.1.0** — same `X-Axonflow-Client` injection
- **Plugins** — Claude Code / Cursor / Codex v1.2.0; OpenClaw v2.2.0
 with Pro license token paste activating Pro features

axonflow-sdk-rust remains at v0.1.0 (preview); SDK-Rust will gain the
header in a future preview release.

## [7.0.0] - 2026-04-29 — Production, quality, and security hardening — upgrade encouraged

**Upgrade strongly recommended.** Over the past month we've shipped substantial production, quality, and security hardening across the AxonFlow SDKs and platform — upgrade to the latest major for a more secure, reliable, and bug-free experience.

**Security highlights from this release cycle:**
- **Webhook signing-key now exposed by SDK response type** (this release). The `secret` (HMAC-SHA256) field on `WebhookSubscription` — returned by `create_webhook` — was missing from the SDK type, so callers had no way to retrieve the signing key and webhook signature verification was effectively un-implementable. The field is now wired through end-to-end. Documented in [`GHSA-7f4h-6264-89fr`](https://github.com/getaxonflow/axonflow-sdk-python/security/advisories/GHSA-7f4h-6264-89fr).
- **`DO_NOT_TRACK` opt-out removed in favor of `AXONFLOW_TELEMETRY=off`** (this release). `DO_NOT_TRACK` was unreliable because host CLIs and runtimes commonly inject `DO_NOT_TRACK=1` regardless of user intent; an explicit AxonFlow-scoped opt-out is the only signal we honor now.
- **Nightly integration in strict mode against `try.getaxonflow.com`** (this release). A canary that catches platform-side regressions affecting the SDK before they reach a release; failures auto-file a GitHub issue.

Major release across the AxonFlow SDK family. Companion releases ship the same day: TypeScript v7.0.0 / Python v7.0.0 / Go v7.0.0 (with `/v7` module path migration) / Java v7.0.0. The full set of platform-side security fixes shipped alongside this release is documented in the consolidated platform advisory [`GHSA-9h64-2846-7x7f`](https://github.com/getaxonflow/axonflow/security/advisories/GHSA-9h64-2846-7x7f).

**Reliability and bug-fix highlights:**
- **`retry_context` + `idempotency_key` for cross-step de-duplication** (last cycle, v6.x). Workflow steps that retry across pod restarts no longer record duplicate audit entries; idempotency_key flows end-to-end through MAP HITL approve/reject responses.
- **`atexit` flush so short-lived processes deliver telemetry** (last cycle, v6.x). Previously a Python script that registered the client and exited immediately could lose the ping; the queue is now flushed on interpreter exit.
- **Wire-shape contract CI + baseline burndown** (last cycle, v6.x). PR-blocking gate that catches drift between SDK types and platform OpenAPI before consumers hit it; baseline drift list shrunk from 36 to 24 entries with 0 unannotated.

### BREAKING

- **`DO_NOT_TRACK` is no longer honored as an AxonFlow telemetry opt-out.** Use `AXONFLOW_TELEMETRY=off` instead. Host tools and CLIs commonly inject `DO_NOT_TRACK=1` regardless of user intent, which makes it unreliable as a signal.

### Changed

- **Telemetry switched to a 7-day delivered-heartbeat.** At most one anonymous ping per environment every 7 days, with the stamp advanced only after the POST returns 2xx — a transient network failure doesn't silence telemetry until the next window. Concurrent threads are de-duplicated by an in-flight gate. Restricted environments where no cache dir is available (e.g. AWS Lambda) fall back transparently to the previous "one ping per process" behavior.
- `StaticPolicy` and `PolicyVersion` now serialize wire fields in snake_case to match the OpenAPI spec (`created_at`, `updated_at`, `organization_id`, `tenant_id`, `has_override`, `changed_at`, `changed_by`, `change_type`). camelCase aliases remain accepted on input via `validation_alias=AliasChoices(...)`. **Round-trip identity is no longer preserved** for callers that built these models from camelCase dicts — code that signs, hashes, or byte-compares serialized model bodies will see a one-time shape change.

### Added

- `ClientRequest.skip_llm` — optional flag to run policy evaluation only and return without invoking the LLM.

### Fixed

- The `DO_NOT_TRACK=1 is deprecated.` `logger.warning` is no longer emitted on every client construction when `DO_NOT_TRACK=1` is set.

## [6.9.0] - 2026-04-28 — list_providers() + LLMProvider full shape

Minor release. New LLM-provider listing API + pagination wrappers, plus full surfacing of the `LLMProvider` wire shape that previous SDK versions silently dropped on parse. Coordinated cycle: TypeScript v6.2.0 / Go v6.0.0 (major: see SDKCompatibility breaking type change in that release) / Java v6.2.0 ship same day.

### Added

- **`client.list_providers()`** — list configured LLM providers and their health status. Calls `GET /api/v1/llm-providers`, returns a list of `LLMProvider` records (each with optional `LLMProviderHealth`). Supports `provider_type` and `enabled` filters. Both async and sync entry points. Closes the parity gap with the Java SDK and the in-platform listing endpoint that's been live since v4.4.
- **`LLMProvider`** now surfaces the full provider shape: `endpoint`, `model`, `region`, `rate_limit`, `timeout_seconds`, and `settings`. Previously these fields were silently dropped on parse, so deployments couldn't introspect provider configuration via the SDK.
- **`client.list_providers_paged()`** — same arguments as `list_providers()` plus `page` / `page_size`, returns the full `LLMProviderListResponse` with `pagination` metadata. Use this when you need to walk multi-page responses or display pagination controls.
- **`client.list_all_providers()`** — convenience wrapper that walks every page (default `page_size=100`, the server-side cap) and returns the combined list. Closes the silent-truncation-at-20-providers bug in `list_providers()`.

### Fixed

- A single malformed `health` snapshot on one provider in a `list_providers()` response no longer crashes the entire call. The bad provider's `health` is set to `None` and a warning is logged; well-formed siblings parse normally.
- `health_check_detailed()` no longer crashes with `AttributeError: 'dict' object has no attribute 'split'` when the platform returns per-language `min_sdk_version` and `recommended_sdk_version` maps (the actual on-the-wire shape since v4.8.0). `SDKCompatibility` now declares both fields as `dict[str, str]` and exposes `min_sdk_version_for(language)` / `recommended_sdk_version_for(language)` helpers, matching the Java + TypeScript SDKs. Legacy bare-string responses from older platforms are normalised to a python-keyed dict so callers don't have to branch on platform version.
- **`examples/openai_integration.py`** — replaced two bare `except Exception:` blocks with narrow handlers (`openai.OpenAIError` / `PolicyViolationError`). The old broad catch masked SDK regressions, schema drift, and governance failures.
- **`examples/wcp_retry_idempotency.py`** — env-var name corrected from `AXONFLOW_BASE_URL` to `AXONFLOW_AGENT_URL` to match the rest of the SDK and the other examples.

## [6.8.0] - 2026-04-25 — Plugin Batch 1 explainability fields on MCP responses

Minor release. Surfaces fields the AxonFlow agent has emitted since v7.1.0 (Plugin Batch 1) but the SDK didn't declare. Pure field-additions on existing methods — no new SDK methods, no breaking changes. Documented in OpenAPI via platform v7.4.3.

Coordinated cycle: TypeScript v6.1.0 / Go v5.8.0 / Java v6.1.0 ship same day with the same field set.

### Added

- **`MCPCheckInputResponse`** gains 5 optional Plugin Batch 1 fields:
 - `decision_id: str | None` — audit correlator
 - `risk_level: Literal["low", "medium", "high", "critical"] | None`
 - `policy_matches: list[ExplainPolicy] | None` — per-policy explainability records
 - `override_available: bool | None` — whether session override is permitted for the matched policies
 - `override_existing_id: str | None` — already-active override consumed by this decision (if any)
- **`MCPCheckOutputResponse`** gains 3 optional fields:
 - `decision_id`
 - `policy_matches: list[ExplainPolicy] | None`
 - `redacted_message: str | None` — text-redaction counterpart to `redacted_data` (used when the connector returned a string message rather than tabular rows; e.g. execute-style responses)
- **`ExplainPolicy`** is now re-exported from `axonflow.types` (it was previously only in `axonflow.decisions`). Same Pydantic model — Python's snake_case convention naturally aligns wire-shape and SDK types, so no separate model is needed.

All fields default to `None`. Pre-v7.1.0 platforms return `None` for every field; callers should treat absence as "context not available" rather than an error.

### Deferred

`client.explain_decision(decision_id)` and the full `ExplainRule` / `DecisionExplanation` type surface are tracked separately as feature work. This release ships only field-surfacing on existing methods.

## [6.7.0] - 2026-04-25 — Wire-shape canonicalization

Minor release. Purely additive — new fields default to `None`, deprecated aliases preserved for compile-time compat. Coordinated with TypeScript v6.0.0 / Java v6.0.0 / Go v5.7.0 SDK releases. The wire-shape contract gate's pinned OpenAPI spec SHA bumps with the platform v7.4.2 spec corrections; one baseline drift entry (`DynamicPolicyInfo`) auto-resolves.

### Added

- **`WebhookSubscription.secret`** — HMAC-SHA256 signing key now exposed on the response from `create_webhook`. Required to verify the `X-AxonFlow-Signature` header on inbound webhook deliveries; without it, callers couldn't validate payload authenticity. Also adds `org_id` and `tenant_id` (ownership scoping).
- **`StepGateRequest`** carries `tokens_in`, `tokens_out`, `cost_usd` so budget-based policies can evaluate gate-time cost estimates.
- **`StepGateResponse.decision_id`** — unique audit correlator that links a gate response to its audit row.
- **`ListWorkflowsResponse.limit` / `offset`** — pagination echo, surfaced on the response.
- **`StaticPolicy.policy_id` / `priority`** — wire-canonical fields surfaced.
- **`CreateStaticPolicyRequest.priority` / `tags`** and **`UpdateStaticPolicyRequest.priority` / `tags`** — match the spec.
- **`UpdatePlanRequest.metadata`** — accept arbitrary plan metadata, opaque to the platform.
- **`UsageBreakdownItem.group_by`** — dimension name (provider/model/agent/etc.) is now exposed on each item.
- **`BudgetAlert.acknowledged`** — alert dismissal flag.
- **`Budget.org_id` / `tenant_id`** — ownership scoping.
- **`UsageRecord`** gains `created_at`, `success`, `error_message`, `latency_ms`, `team_id`, `tenant_id`, `user_id`, `workflow_id` to match the wire. Legacy `timestamp` field is `DEPRECATED` (orphan read; the wire emits `created_at`).
- **`WorkflowStatusResponse.metadata`** — arbitrary workflow metadata.
- **`CreateWorkflowResponse.started_at`** — wire-canonical timestamp. Legacy `created_at` and `source` are `DEPRECATED` (orphan reads on the create response).
- **`ExecutionSnapshot.retry_count`** — number of retry attempts on a step.
- **`Finding.article`** — regulatory article reference (e.g. MAS FEAT principle number).
- **`PolicyOverride.id` / `enabled_override`** — wire-canonical fields. `active` is `DEPRECATED` (orphan read).
- **`PolicyVersion.id` / `policy_id` / `change_summary` / `snapshot`** — match the wire shape (versions are immutable snapshots, not before/after diffs). `change_description`, `previous_values`, `new_values` are `DEPRECATED` orphan reads.
- **`DynamicPolicyMatch.message`** — wire-canonical name. `reason` is `DEPRECATED` (orphan read).
- **`ExfiltrationCheckInfo.exceeded` / `limit_type`** — match the wire. `within_limits` is `DEPRECATED`.
- **`CancelPlanResponse.success`** — wire-canonical boolean. `message` is `DEPRECATED` (orphan read).
- **`PlanResponse`** gains the wire top-level fields `success`, `version`, `result`, `error`, `workflow_execution_id`, `policy_info`.
- **`ResumePlanResponse.result`** — final aggregated result (canonical wire field). Six fields (`workflow_id`, `message`, `step_result`, `next_step`, `next_step_name`, `total_steps`) are now `DEPRECATED` — none of them were populated by the resume decoder against the actual server response.
- **`MCPCheckInputRequest.client_id` / `tenant_id` / `user_id` / `user_role` / `user_token`** and **`MCPCheckOutputRequest.client_id` / `tenant_id` / `user_id` / `user_token`** — match the spec scoping fields.

### Notes

The above is an audit-driven sweep against the wire-shape contract gate. All changes are additive (new fields default to `None`) or `DEPRECATED`-marked alias fields kept for compile-time compat. Removal scheduled for v7.

The earlier overnight claim that "Python baseline is clean" was wrong — that was a key-name confusion (Python uses `per_model_drift`, the others use `per_type_drift`); a proper audit found 36 drift entries similar in pattern to the TS+Go SDK sweeps. After this sweep, 26 drift entries remain (mostly `DEPRECATED` aliases retained for source-compat + Cat C entries to file separately + Plugin Batch 1 SDK additions pending platform-side spec coverage).

Two platform-side spec corrections filed alongside this work, for issues the audit surfaced where the spec was wrong (server emits the SDK's name): `AISystemRegistry.materiality_classification` and `DynamicPolicyInfo` schema. No SDK change for those — the SDK is correct.

## [6.6.2] - 2026-04-25

### Fixed

- **Runtime `axonflow.__version__` correctly reports the installed version.**
 Wheels for v6.6.1 shipped with `axonflow.__version__` stuck at `"6.6.0"`
 because the release workflow was targeting the wrong file when bumping
 the version constant (it was rewriting `axonflow/__init__.py`, but the
 real constant lives in `axonflow/_version.py` and `__init__.py` only
 re-exports from there). Package metadata read by `pip show` was correct,
 so install/upgrade worked fine; the drift only affected code that read
 `axonflow.__version__` at runtime (telemetry self-identification,
 version-gated feature detection in user code, log output). No functional
 changes — this release ships the same binary behavior as v6.6.1 with
 the runtime version correctly set to `6.6.2`.

## [6.6.1] - 2026-04-24

### Fixed

- **Retire dead staging endpoint across the SDK.** The decommissioned
 `staging-eu.getaxonflow.com` host was still referenced in 11 places,
 including the public `AxonFlow.sandbox()` factory, every example's default
 endpoint, the fixture-recording script, `SECURITY.md`, and `CONTRIBUTING.md`.
 Callers hitting any of these defaults would silently connect to a dead host.
 - `AxonFlow.sandbox()` now targets a local community docker-compose stack
 at `http://localhost:8080` with `demo-client` / `demo-secret` credentials.
 Docstring updated to point users at the hosted registration flow
 (`POST /api/v1/register` + `AXONFLOW_TRY=1`) for the live community SaaS.
 - `examples/quickstart.py`, `examples/gateway_mode.py`,
 `examples/openai_integration.py`, and `scripts/record_fixtures.py` now
 default `AXONFLOW_AGENT_URL` to `http://localhost:8080`.
 - `SECURITY.md` "credentials in code" example uses the neutral
 `https://axonflow.example.com` placeholder.
 - `CONTRIBUTING.md` "Running Examples" section now documents the local
 docker-compose setup, replaces the unused `AXONFLOW_LICENSE_KEY`
 reference with the correct `AXONFLOW_CLIENT_ID` / `AXONFLOW_CLIENT_SECRET`
 variables, and lists the four example files that actually exist
 (previously referenced stale `basic_usage.py` and `interceptors.py`).
- Telemetry pings now deliver reliably from short-lived processes (CLI, serverless, cold-starts).
- Telemetry path is bounded at `_TIMEOUT_SECONDS` (3s) total; the `/health` probe and checkpoint POST share a single deadline instead of stacking.

## [6.6.0] - 2026-04-22

### Added

- **Rich `ApproveStepResponse` / `RejectStepResponse`** — both pydantic models
 now carry the same shape as the step-gate response: `decision` resolves to
 `"allow"` / `"block"`, `retry_context` mirrors the gate response retry state,
 `approved_by` / `approved_at` / `rejected_by` / `rejected_at` carry reviewer
 identity, `approval_id` is the deterministic HITL queue UUID, and
 `policies_matched` reconstructs the governance trail. Legacy fields
 (`workflow_id`, `step_id`, `status`) remain for back-compat; every new field
 is optional so older server responses still deserialize cleanly.
- **`plan_id` on approve/reject responses** — populated when the response
 comes from the MAP plan-scoped endpoint; empty on WCP plane responses.
 Same models work across both endpoints.
- **`get_pending_plan_approvals`** — new client method that lists MAP-plane
 pending approvals (`GET /api/v1/plans/approvals/pending`), the counterpart
 of `get_pending_approvals` for the WCP plane. Accepts an optional
 `plan_id` argument so reviewer tools can scope the listing to one plan.
 Available on Evaluation+ licenses (same tier gate as the MAP step
 approve/reject endpoints). Sync wrapper exposed via
 `SyncAxonFlow.get_pending_plan_approvals`.
- **`PendingApproval.plan_id`** — populated on MAP-plane entries, `None` on
 WCP-plane entries. Mirrors the approve/reject asymmetry. `PendingApproval`
 also gains `step_index`, `decision`, `decision_reason`, `policies_matched`,
 `step_input`, and `approval_status` so reviewer tools can render the full
 approval context without a second request.

### Fixed

- **`approve_step` / `reject_step` / `get_pending_approvals` endpoint URLs** —
 all three previously targeted non-existent paths under
 `/api/v1/workflow-control/` and would fail against a real AxonFlow server.
 Corrected to the canonical `/api/v1/workflows/{id}/steps/{step_id}/(approve|reject)`
 and `/api/v1/workflows/approvals/pending` routes. Customers using these
 methods against a live deployment were receiving 404s; this release makes
 them work.
- **`PendingApprovalsResponse` field names aligned with the wire shape** —
 the model previously declared `approvals` and `total`, which never matched
 the server response (`pending_approvals` and `count`). Renamed fields.
 Callers that read `response.approvals` or `response.total` must update to
 `response.pending_approvals` / `response.count`.

### Deprecated

- `DO_NOT_TRACK=1` as an AxonFlow telemetry opt-out — scheduled for removal after 2026-05-05 in the next major release. Use `AXONFLOW_TELEMETRY=off` instead. The SDK emits a one-line migration warning when `DO_NOT_TRACK=1` is the active control and `AXONFLOW_TELEMETRY=off` is not also set.

### Unchanged

- `approve_step(workflow_id, step_id)` / `reject_step(workflow_id, step_id, reason)`
 method signatures are unchanged — only the response fields grew.

## [6.5.0] - 2026-04-21

### Added

- **`retry_context` and `idempotency_key` support on the step gate** —
 `StepGateResponse` now carries a `retry_context` object on every gate call with the
 true `(workflow_id, step_id)` lifecycle: `gate_count`, `completion_count`,
 `prior_completion_status` (`PriorCompletionStatus` enum —
 `NONE` / `COMPLETED` / `GATED_NOT_COMPLETED`), `prior_output_available`,
 `prior_output`, `prior_completion_at`, `first_attempt_at`, `last_attempt_at`,
 `last_decision`, and `idempotency_key`. Prefer these fields to the legacy
 `cached` / `decision_source` fields.
- **`client.step_gate(..., include_prior_output=False)`** — new keyword-only argument.
 When `True`, the SDK sends `?include_prior_output=true` on the gate call and
 `retry_context.prior_output` is populated when a prior `/complete` has landed.
 Existing callers that omit the kwarg behave unchanged.
- **`StepGateRequest.idempotency_key`** — caller-supplied opaque business-level key
 (max 255 chars). Immutable once recorded on the first gate call for a
 `(workflow_id, step_id)`; subsequent gate/complete calls must pass the same key.
- **`MarkStepCompletedRequest.idempotency_key`** — must match the key set on the
 corresponding gate call, if any. Mismatch (including missing-vs-set on either side)
 surfaces as a typed `IdempotencyKeyMismatchError`.
- **`IdempotencyKeyMismatchError`** — typed exception raised by `step_gate` and
 `mark_step_completed` when the platform returns HTTP 409 with
 `error.code == "IDEMPOTENCY_KEY_MISMATCH"`. Surfaces `workflow_id`, `step_id`,
 `expected_idempotency_key`, `received_idempotency_key`, and the human-readable `message`.
 Exported from `axonflow` top-level.
- **`RetryContext`, `PriorCompletionStatus`** — exported pydantic model + enum.

### Deprecated

- **`StepGateResponse.cached`** and **`StepGateResponse.decision_source`** — still
 populated but deprecated in favor of `retry_context.gate_count > 1` and
 `retry_context.prior_completion_status`. Planned for removal in a future major version.

### Compatibility

Companion to the platform change that introduces `retry_context` on
`POST /api/v1/workflows/{workflow_id}/steps/{step_id}/gate`. Additive only — existing
callers that never set `idempotency_key` or `include_prior_output` see no behavior change.

## [6.4.0] - 2026-04-18

### Added

- **Execution boundary semantics** — `RetryPolicy` enum with `IDEMPOTENT`
 (default) and `REEVALUATE` values. Step gate requests accept `retry_policy`
 to control cached vs fresh evaluation behavior.
- **Step gate response metadata** — `cached` (bool) and `decision_source`
 (str) fields on `StepGateResponse` indicate decision provenance.
- **Workflow checkpoints** — `get_checkpoints(workflow_id)` lists step-gate
 checkpoints. `resume_from_checkpoint(workflow_id, checkpoint_id)` resumes
 from a specific checkpoint with fresh policy evaluation (Enterprise).
- **Checkpoint types** — `Checkpoint`, `CheckpointListResponse`, and
 `ResumeFromCheckpointResponse` models.
- **`AxonFlow.explain_decision(decision_id)`** — fetches the full explanation for a
 previously-made policy decision via `GET /api/v1/decisions/:id/explain`.
 Returns a `DecisionExplanation` with matched policies, risk level, reason,
 override availability, existing override ID (if any), and a rolling-24h
 session hit count for the matched rule. Shape is frozen; additive-only
 fields ensure forward compatibility.
- **`DecisionExplanation`, `ExplainPolicy`, `ExplainRule`** — new Pydantic
 models exported from `axonflow.decisions`.
- **`AuditSearchRequest.decision_id`, `policy_name`, `override_id`** — three
 new optional filter fields on `search_audit_logs`. Use `decision_id` to
 gather every record tied to one decision; `policy_name` to find everything
 matched by a specific policy; `override_id` to reconstruct an override's
 full lifecycle.

### Fixed

- `step_gate()` now correctly passes `retry_policy` in the request body
 and populates `cached`/`decision_source` in the response. Previously
 these fields were defined on the model but not wired through the client.

### Compatibility

Companion to platform v7.1.0. Works against plugin releases (OpenClaw v1.3.0+,
Claude Code v0.5.0+, Cursor v0.5.0+, Codex v0.4.0+) that surface the
`DecisionExplanation` shape. Audit filter fields pass through when unset;
server-side filtering activates on v7.1.0+ platforms. The `DecisionExplanation`
model accepts additive future fields via Pydantic's default extra-fields-ignore
behavior.

## [6.3.0] - 2026-04-09

### Added
- `AXONFLOW_TRY=1` environment variable to connect to `try.getaxonflow.com` shared evaluation server
- `register_try()` helper in `axonflow.community` for self-registering a tenant
- Checkpoint telemetry reports `endpoint_type: "community-saas"` when try mode is active

---

## [6.2.0] - 2026-04-08

### Added

- **Telemetry `endpoint_type` field.** The anonymous telemetry ping now includes an SDK-derived classification of the configured AxonFlow endpoint as one of `localhost`, `private_network`, `remote`, or `unknown`. The raw URL is never sent and is not hashed. This helps distinguish self-hosted evaluation from real production deployments on the checkpoint dashboard. Opt out as before via `DO_NOT_TRACK=1` or `AXONFLOW_TELEMETRY=off`.

### Changed

- Examples and documentation updated to reflect the new AxonFlow platform v6.2.0 defaults for `PII_ACTION` (now `warn` — was `redact`) and the new `AXONFLOW_PROFILE` env var. No SDK API changes; the SDK continues to pass `PII_ACTION` through unchanged.

---

## [6.1.0] - 2026-04-06

### Added

- **`check_tool_input()` / `check_tool_output()`** — generic aliases for tool governance. Existing `mcp_check_input()` / `mcp_check_output()` remain supported.

### Changed

- Anonymous telemetry is now enabled by default for all endpoints, including localhost/self-hosted evaluation. Opt out with `DO_NOT_TRACK=1` or `AXONFLOW_TELEMETRY=off`.

---

## [6.0.0] - 2026-04-05

### BREAKING CHANGES

- **`X-Tenant-ID` header removed.** The SDK no longer sends `X-Tenant-ID`. The server derives tenant from OAuth2 Client Credentials (Basic auth). Requires platform v6.0.0+.
- **`MaterialityClassification` field renamed.** MAS FEAT `AISystemRegistry.materiality` renamed to `materiality_classification` to match server JSON field.

### Added

- **`Status` field on `PlanResponse`.** The server returns plan status (pending, executing, completed, failed, cancelled) which was previously not parsed by the SDK.

### Fixed

- **MCP examples missing `client_id` and `user_token`** in request body for enterprise MCP handler authentication.

---

## [5.4.0] - 2026-04-01

### Added

- **`ComputerUseGovernor` for Anthropic Computer Use**: Middleware for the sampling loop. `check_tool_use()` evaluates tool_use blocks before execution (blocks dangerous bash commands, detects PII). `check_result()` scans results before feeding back to Claude (redacts PII/secrets). Includes 10 default blocked bash patterns for local client-side blocking.

---

## [5.3.0] - 2026-03-31

### Added

- **`GovernedTool` framework-agnostic tool wrapper**: Wraps any LangChain `BaseTool` with AxonFlow input/output governance (`mcp_check_input` before execution, `mcp_check_output` after). Works transparently with LangGraph, LangChain AgentExecutor, CrewAI (via `from_langchain`), and AutoGen (via `LangChainToolAdapter`). Helper: `govern_tools(tools, client)`.
- **`AxonFlowChatModel` LangChain adapter**: Wraps any `BaseChatModel` with pre-check + audit governance. Extracted `_GovernanceMixin` base class for shared governance logic. Includes `with_fallbacks` override to wrap each fallback in governance, and `batch`/`abatch` `NotImplementedError` to prevent silent bypass.
- **`AxonFlowRunnableBinding`**: Governance wrapper for LangChain runnables with transparent delegation.
- **Input checking in `tool_output_wrapper()`**: `tool_output_wrapper()` now calls `mcp_check_input` before tool execution, enforcing input policies on local `@tool` functions in LangGraph workflows.

### Fixed

- **`mcp_query()` 403 handling**: `mcp_query()` previously raised `ConnectorError` on HTTP 403 responses. Now treats all 403s as policy-blocked responses, returning a `ConnectorResponse` with `blocked=True` instead of raising an exception.
- **Mypy type errors in LangChain adapter**: Fixed `int()` argument type errors in `_extract_token_usage()` for dict values that could be `None`.

### Deprecated

- **`mcp_tool_interceptor()`**: Use `GovernedTool` (any framework) or `tool_output_wrapper()` (LangGraph ToolNode) instead. Will be removed after April 15, 2026.

---

## [5.2.0] - 2026-03-25

### Added

- `simulate_policies()` — dry-run all active policies against an input query. Returns allowed/blocked status, applied policies, risk score, and daily usage. Requires Evaluation tier or above.
- `get_policy_impact_report()` — test a single policy against multiple inputs and get aggregate match/block statistics.
- `detect_policy_conflicts()` — analyze active policies for contradictions, shadows, and redundancies. Optionally filter to conflicts involving a specific policy.
- `AxonFlowLangGraphAdapter.tool_output_wrapper()` — returns an async wrapper for LangGraph `ToolNode(awrap_tool_call=...)` that enforces input and output policy checks on local `@tool` functions. Fixes a gap where locally defined tools bypassed `mcp_tool_interceptor` policy enforcement.
- Types: `SimulatePoliciesRequest`, `SimulatePoliciesResponse`, `SimulationDailyUsage`, `ImpactReportInput`, `ImpactReportRequest`, `ImpactReportResult`, `ImpactReportResponse`, `PolicyConflictRef`, `PolicyConflict`, `PolicyConflictResponse`
- `wrap_langgraph()` — 1-line wrapper for compiled LangGraph StateGraphs. Transparently enforces AxonFlow governance at every node transition without modifying the graph definition. Uses langchain-core's `AsyncCallbackHandler` to intercept node execution via `metadata["langgraph_node"]`.
- `GovernedGraph` class — returned by `wrap_langgraph()`, exposes `ainvoke()`, `invoke()`, `astream()`. Each invocation creates a new AxonFlow workflow. Reusable across multiple invocations.
- `NodeConfig` dataclass — per-node configuration overrides (`step_type`, `model`, `provider`, `skip`) for fine-grained control over how individual nodes are governed.
- `govern_tools` parameter — when `True` (default), individual tool calls within LangGraph nodes are automatically gate-checked via `check_tool_gate()` / `tool_completed()`.
- `langchain-core>=0.3.0` added to the `langgraph` optional extra (`pip install axonflow[langgraph]`).

---

## [5.1.0] - 2026-03-19

### Added

- New `langgraph` optional extra for MCP tool interception: `pip install 'axonflow[langgraph]'`. The `mcp` package is now an opt-in dependency rather than being imported unconditionally at the package level.

### Fixed

- `mcp_tool_interceptor()` now wraps redacted output in a `CallToolResult` instead of returning a plain `str`. Previously, when `mcp_check_output` applied redaction, the interceptor returned the redacted string directly, causing `AttributeError: 'str' object has no attribute 'content'` in `langchain-mcp-adapters`.

---

## [5.0.0] - 2026-03-16

### Breaking Changes

- **Dropped Python 3.9 support.** Python 3.9 reached end-of-life in October 2025. The minimum supported version is now Python 3.10. Users on 3.9 should pin to `axonflow<5.0.0`.

### Changed

- Removed `eval_type_backport` dependency (was only required for Python 3.9).
- Modernized type annotations across the codebase: `Optional[X]` → `X | None`, `typing.Callable` → `collections.abc.Callable` (now valid without `from __future__ import annotations` on 3.10+).

---

## [4.2.0] - 2026-03-16

### Added

- `get_circuit_breaker_status()` — query active circuit breaker circuits and emergency stop state
- `get_circuit_breaker_history(limit)` — retrieve circuit breaker trip/reset audit trail
- `get_circuit_breaker_config(tenant_id)` — get effective circuit breaker config (global or tenant-specific)
- `update_circuit_breaker_config(config)` — update per-tenant circuit breaker thresholds

---

## [4.1.0] - 2026-03-14

### Added

- `audit_tool_call()` — record non-LLM tool calls (API, MCP, function) in the audit trail. Returns audit ID, status, and timestamp. Requires Platform v5.1.0+
- `get_audit_logs_by_tenant()` — retrieve audit logs for a tenant with optional pagination
- `search_audit_logs()` — search audit logs with filters (client ID, request type, limit)

### Fixed

- Telemetry pings now suppressed for localhost/127.0.0.1/::1 endpoints unless `telemetry_enabled` is explicitly set to `True`. Prevents telemetry noise during local development.

---

## [4.0.0] - 2026-03-09

### Breaking Changes

- **Removed `total_steps` from `CreateWorkflowRequest`**. Requires Platform v4.5.0+ (recommended v5.0.0+).
 Total steps are auto-computed when the workflow reaches a terminal state.
- **`mcp_check_input()` default `operation` changed from `"query"` to `"execute"`**. Callers relying on
 the implicit `"query"` default must now pass `operation="query"` explicitly. This better reflects the
 default MCP tool call pattern where side effects are unknown.

### Added

- **`AxonFlowLangGraphAdapter.mcp_tool_interceptor()`**: Factory method returning an async callable ready for use with `MultiServerMCPClient(tool_interceptors=[.])`. Enforces AxonFlow input and output policies around every MCP tool call: `mcp_check_input → handler() → mcp_check_output`. Handles policy blocks and returns redacted output when `mcp_check_output` applies redaction.
 - **`MCPInterceptorOptions`**: Configuration dataclass accepted by `mcp_tool_interceptor()` with two fields:
 - `connector_type_fn`: Optional callable to override the default `"{server_name}.{tool_name}"` connector type mapping
 - `operation`: Operation type forwarded to `mcp_check_input` (default: `"execute"`; use `"query"` for known read-only tool calls)
 - `MCPInterceptorOptions` and `WorkflowApprovalRequiredError` are now exported from `axonflow.adapters`

### Fixed

- `mcp_tool_interceptor()` now uses JSON serialization (`json.dumps`) for `statement` and output `message` fields instead of Python `repr()`, ensuring the policy engine receives valid structured data

### Note

`MediaAnalysisResult.extracted_text` was replaced by `has_extracted_text` + `extracted_text_length`
in v3.5.0. This major version formally acknowledges that breaking change.

---

## [3.8.0] - 2026-03-03

### Added

- `health_check_detailed()` method (async + sync) returning `HealthResponse` with platform version, capabilities, and SDK compatibility info
- `has_capability(name)` method on `HealthResponse` to check if platform supports a specific feature
- User-Agent header (`axonflow-sdk-python/{version}`) sent on all HTTP requests
- Version mismatch warning logged when SDK version is below platform's `min_sdk_version`
- `PlatformCapability`, `SDKCompatibility`, `HealthResponse` dataclasses
- `trace_id` field on `CreateWorkflowRequest`, `CreateWorkflowResponse`, `WorkflowStatusResponse`, and `ListWorkflowsOptions` for distributed tracing correlation
- `ToolContext` dataclass for per-tool governance within workflow steps
- `tool_context` field on `StepGateRequest` for tool-level policy enforcement
- `check_tool_gate()` method on LangGraph adapter for per-tool governance gate checks
- `tool_completed()` method on LangGraph adapter for per-tool step completion
- `list_workflows()` now supports `trace_id` filter parameter
- Anonymous runtime telemetry for version adoption tracking and feature usage signals
- `TelemetryEnabled` / `telemetry` configuration option to explicitly control telemetry
- `AXONFLOW_TELEMETRY=off` and `DO_NOT_TRACK=1` environment variable opt-out support

### Fixed

- `__version__` corrected from `3.6.0` to `3.8.0`

---

## [3.7.0] - 2026-02-28

### Added

- **MCP Policy-Check Endpoints** (Platform v4.6.0+): Standalone policy validation for external orchestrators (LangGraph, CrewAI) to enforce AxonFlow policies without executing connector queries
 - `mcp_check_input(connector_type, statement)`: Validate SQL/commands against input policies (SQLi detection, dangerous query blocking, PII in queries, dynamic policies). Returns `allowed=True` or raises with `block_reason`
 - `mcp_check_output(connector_type, response_data)`: Validate MCP response data against output policies (PII redaction, exfiltration limits, dynamic policies). Returns original or redacted data with `policy_info`
 - New types: `MCPCheckInputRequest`, `MCPCheckInputResponse`, `MCPCheckOutputRequest`, `MCPCheckOutputResponse`
 - Async methods with sync wrappers (`mcp_check_input_sync`, `mcp_check_output_sync`)
 - Supports both query-style (`response_data`) and execute-style (`message` + `metadata`) output validation

---

## [3.6.0] - 2026-02-22

### Added

- Media governance configuration methods: `get_media_governance_config()`, `update_media_governance_config()`, `get_media_governance_status()`
- Media governance types: `MediaGovernanceConfig`, `MediaGovernanceStatus`
- Media policy category constants: `CATEGORY_MEDIA_SAFETY`, `CATEGORY_MEDIA_BIOMETRIC`, `CATEGORY_MEDIA_PII`, `CATEGORY_MEDIA_DOCUMENT`
- `mark_step_completed()` now accepts post-execution metrics (`tokens_in`, `tokens_out`, `cost_usd`) via `MarkStepCompletedRequest`

---

## [3.5.0] - 2026-02-19

### Added

- **Media Governance Types**: `MediaContent`, `MediaAnalysisResult`, `MediaAnalysisResponse` for multimodal image governance
- **`proxy_llm_call_with_media()`**: Async + sync methods to send images (base64 or URL) alongside queries for governance analysis before LLM routing

### Breaking

- `MediaAnalysisResult.extracted_text` replaced by `has_extracted_text` (bool) and `extracted_text_length` (int). Raw extracted text is no longer exposed in API responses.

---

## [3.4.0] - 2026-02-13

### Added

- **fail_workflow()**: Fail a workflow with optional reason
 - `async fail_workflow(workflow_id, reason=None)` + sync wrapper
 - Sends `POST /api/v1/workflows/{id}/fail`
- **HITL Queue API** (Enterprise): Human-in-the-loop approval queue management
 - `list_hitl_queue(opts)`: list pending approvals with filtering
 - `get_hitl_request(request_id)`: get approval details
 - `approve_hitl_request(request_id, review)`: approve a request
 - `reject_hitl_request(request_id, review)`: reject a request
 - `get_hitl_stats()`: dashboard statistics
 - New models: `HITLApprovalRequest`, `HITLQueueListOptions`, `HITLQueueListResponse`, `HITLReviewInput`, `HITLStats`

### Fixed

- `StepGateResponse` now includes `policies_evaluated` and `policies_matched` fields from server response

## [3.3.1] - 2026-02-12

### Fixed

- `stream_execution_status()` used incorrect endpoint path (`/api/v1/executions/{id}/stream` → `/api/v1/unified/executions/{id}/stream`), causing 404 errors when streaming execution status updates

## [3.3.0] - 2026-02-10

### Added

- **WCP Approval Gates**: HITL approval and rejection for workflow steps
 - `approve_step(workflow_id, step_id)` - Approve a pending workflow step
 - `reject_step(workflow_id, step_id, reason=None)` - Reject a step with optional reason
 - `get_pending_approvals(limit=20)` - List steps awaiting human approval

- **MAP Plan Cancellation**: Cancel running multi-agent plans
 - `cancel_plan(plan_id, reason=None)` - Cancel a plan with optional reason

- **MAP Plan Update**: Modify plan configuration before or during execution
 - `update_plan(plan_id, **kwargs)` - Update execution mode, domain, or version

- **MAP Plan Versioning and Rollback**: Version history and rollback support
 - `get_plan_versions(plan_id)` - List plan version history
 - `rollback_plan(plan_id, version)` - Rollback to a previous version (raises on 409 conflict)
 - New types in response: `RollbackPlanResponse`, `PlanVersion`

- **Webhook Subscriptions**: Event notification management
 - `create_webhook(url, events, **kwargs)` - Create a webhook subscription
 - `list_webhooks()` - List active webhook subscriptions
 - `get_webhook(webhook_id)` - Get webhook details
 - `update_webhook(webhook_id, *, url=None, events=None, secret=None, active=None, description=None)` - Update webhook with typed parameters
 - `delete_webhook(webhook_id)` - Delete a webhook subscription
 - Available on both `AxonFlow` (async) and `SyncAxonFlow` (sync) clients

- **Unified Execution Cancellation**: Cancel running executions across both MAP and WCP subsystems
 - `cancel_execution(execution_id, reason=None)` - Cancel a unified execution via `POST /api/v1/unified/executions/{id}/cancel`
 - Available on both `AxonFlow` (async) and `SyncAxonFlow` (sync) clients
 - Propagates to MAP `cancel_plan()` or WCP `abort_workflow()` based on execution type

### Fixed

- **`execute_plan` status hardcoded**: `execute_plan()` always returned `status="completed"` regardless of actual server response. Now reads status from response (`data.status` > `metadata.status` > default), correctly surfacing `awaiting_approval` for WCP confirm mode.
- **Unified execution API URLs**: `get_execution_status()` and `list_unified_executions()` now use correct `/api/v1/unified/executions` path (was incorrectly pointing to `/api/v1/executions` which is the Execution Replay API)
- **`update_webhook` typed parameters**: Replaced `**kwargs` with explicit keyword-only arguments for type safety

---

## [3.2.0] - 2026-02-05

### Added

- **Dynamic policy tier support**: `tier` (`PolicyTier`) and `organization_id` fields on `CreateDynamicPolicyRequest`, `UpdateDynamicPolicyRequest`, and `DynamicPolicy` response. Defaults to `PolicyTier.TENANT` when not specified.
- **`ListDynamicPoliciesOptions` filters**: Filter dynamic policies by `tier` and `organization_id`, matching static policy list options.

---

## [3.1.0] - 2026-02-04

### Fixed

- Improved audit-log read reliability when the API returns empty payloads as `entries: null`.
- `search_audit_logs()` now normalizes null entries to an empty list.
- `get_audit_logs_by_tenant()` now normalizes null entries to an empty list.

### Changed

- Simplified internal endpoint resolution by removing legacy helper names `_get_orchestrator_url()` and `_get_portal_url()`.
- Internal portal/orchestrator request construction now uses the configured SDK endpoint directly.
- No public API change.

## [3.0.0] - 2026-02-03

### Breaking Changes

- **Removed `execute_query()`**: Use `proxy_llm_call()` instead (deprecated since v1.7.0). Removed from both `AxonFlow` (async) and `SyncAxonFlow` clients.

### Added

- **`was_redacted()` helper**: Convenience method on `ConnectorResponse` to check if any fields were redacted by PII policies

### Changed

- Updated module docstring examples from `execute_query` to `proxy_llm_call`

---

## [1.7.1] - 2026-01-25

### Changed

- **Gateway Mode smart defaults**: `get_policy_approved_context()` and `audit_llm_call()` now use `"community"` as default client_id when not configured, enabling zero-config usage for community/self-hosted deployments

### Fixed

- **PolicyCategory**: Added `PII_SINGAPORE = "pii-singapore"` enum value for Singapore PII detection policies (NRIC, FIN, UEN patterns)

---

## [1.7.0] - 2026-01-25

### Added

- **Unified Execution Tracking**: Consistent status tracking for MAP plans and WCP workflows
 - `get_execution_status(execution_id)` - Get unified execution status by ID
 - `list_unified_executions(options)` - List executions with type/status filters
 - `ExecutionStatus` Pydantic model with unified fields for both MAP and WCP executions
 - `ExecutionType` enum: `MAP_PLAN`, `WCP_WORKFLOW`
 - `ExecutionStatusValue` enum: `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`, `ABORTED`, `EXPIRED`
 - `StepStatusValue` enum: `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `SKIPPED`, `BLOCKED`, `APPROVAL`
 - `UnifiedStepType` enum: `LLM_CALL`, `TOOL_CALL`, `CONNECTOR_CALL`, `HUMAN_TASK`, `SYNTHESIS`, `ACTION`, `GATE`
 - `UnifiedStepStatus` model with step-level details (duration, cost, policy decisions)
 - Helper methods on `ExecutionStatus`: `is_terminal()`, `get_current_step()`, `calculate_total_cost()`
 - Consistent response format across MAP Multi-Agent Planning and WCP Workflow Control Plane

- **MAS FEAT Compliance Module** (Enterprise): Singapore financial services AI governance
 - AI System Registry: `masfeat.register_system()`, `masfeat.get_system()`, `masfeat.update_system()`, `masfeat.list_systems()`, `masfeat.activate_system()`, `masfeat.retire_system()`, `masfeat.get_registry_summary()`
 - 3-Dimensional Risk Rating: Customer Impact × Model Complexity × Human Reliance
 - Materiality Classification: High (sum≥12), Medium (sum≥8), Low (sum<8)
 - FEAT Assessments: `masfeat.create_assessment()`, `masfeat.get_assessment()`, `masfeat.update_assessment()`, `masfeat.list_assessments()`, `masfeat.submit_assessment()`, `masfeat.approve_assessment()`, `masfeat.reject_assessment()`
 - Assessment Lifecycle: pending → in_progress → completed → approved/rejected
 - Kill Switch: `masfeat.get_kill_switch()`, `masfeat.configure_kill_switch()`, `masfeat.check_kill_switch()`, `masfeat.trigger_kill_switch()`, `masfeat.restore_kill_switch()`, `masfeat.enable_kill_switch()`, `masfeat.disable_kill_switch()`, `masfeat.get_kill_switch_history()`
 - Automatic model shutdown based on accuracy, bias, and error rate thresholds
 - New namespace property: `client.masfeat` (async) and `client.masfeat` (sync via `AxonFlow.sync()`)
 - New types: `AISystemRegistry`, `AISystemUseCase`, `MaterialityClassification`, `SystemStatus`, `FEATAssessment`, `FEATAssessmentStatus`, `FEATPillar`, `KillSwitch`, `KillSwitchStatus`, `KillSwitchEvent`, `KillSwitchEventType`, `RegistrySummary`

- **proxy_llm_call()**: New primary method for Proxy Mode with improved documentation
 - Clearly describes Proxy Mode behavior (AxonFlow makes the LLM call on your behalf)
 - Documents when to use Proxy Mode vs Gateway Mode
 - Same functionality as execute_query, but with clearer naming

- **BudgetInfo**: `QueryResponse.budget_info` for budget enforcement (HTTP 402)

### Deprecated

- **execute_query()**: Deprecated in favor of proxy_llm_call()
 - Will be removed in v3.0.0
 - Emits deprecation warning in debug mode
 - Remains functional as a wrapper around proxy_llm_call()

---

## [1.6.0] - 2026-01-18

### Added

- **Workflow Policy Enforcement**: Policy transparency for workflow operations
 - `StepGateResponse` now includes `policies_evaluated` and `policies_matched` fields with `PolicyMatch` type
 - `PolicyMatch` class with `policy_id`, `policy_name`, `action`, `reason` for policy transparency
 - `PolicyEvaluationResult` class for MAP execution with `allowed`, `applied_policies`, `risk_score`
 - Workflow operations (`workflow_created`, `workflow_step_gate`, `workflow_completed`) logged to audit trail

---

## [1.5.0] - 2026-01-17

### Added

- **Workflow Control Plane**: Governance gates for external orchestrators
 - "LangChain runs the workflow. AxonFlow decides when it's allowed to move forward."
 - `create_workflow()` - Register workflows from LangChain/LangGraph/CrewAI/external
 - `step_gate()` - Check if step is allowed to proceed (allow/block/require_approval)
 - `mark_step_completed()` - Mark a step as completed with optional output data
 - `get_workflow()` - Get workflow status and step history
 - `list_workflows()` - List workflows with filters (status, source, pagination)
 - `complete_workflow()` - Mark workflow as completed
 - `abort_workflow()` - Abort workflow with reason
 - `resume_workflow()` - Resume after approval
 - New types: `WorkflowStatus`, `WorkflowSource`, `GateDecision`, `StepType`, `ApprovalStatus`, `MarkStepCompletedRequest`
 - Helper methods on `StepGateResponse`: `is_allowed()`, `is_blocked()`, `requires_approval()`
 - Helper methods on `WorkflowStatus` and `WorkflowStatusResponse`: `is_terminal()`
 - LangGraph adapter: `axonflow.adapters.langgraph.AxonFlowLangGraphAdapter`

### Fixed

- Datetime parsing now handles variable-length fractional seconds (e.g., 5 digits) for Python 3.9 compatibility

---

## [1.4.0] - 2026-01-14

### Added

- **MCP Exfiltration Detection**: `ConnectorPolicyInfo` now includes `exfiltration_check` with row/volume limit information
 - `ExfiltrationCheckInfo` type with `rows_returned`, `row_limit`, `bytes_returned`, `byte_limit`, `within_limits` fields
 - Prevents large-scale data extraction via MCP queries
 - Configurable via `MCP_MAX_ROWS_PER_QUERY` and `MCP_MAX_BYTES_PER_QUERY` environment variables

- **MCP Dynamic Policies**: `ConnectorPolicyInfo` now includes `dynamic_policy_info` for Orchestrator-evaluated policies
 - `DynamicPolicyInfo` type with `policies_evaluated`, `matched_policies`, `orchestrator_reachable`, `processing_time_ms`
 - `DynamicPolicyMatch` type with `policy_id`, `policy_name`, `policy_type`, `action`, `reason`
 - Supports rate limiting, budget controls, time-based access, and role-based access policies
 - Optional feature - enable via `MCP_DYNAMIC_POLICIES_ENABLED=true`

---

## [1.3.0] - 2026-01-09

### Added

- **MCP Policy Enforcement Response Fields**: `mcp_query()` and `mcp_execute()` now return policy enforcement metadata
 - `redacted: bool` - Whether any fields were redacted by PII policies
 - `redacted_fields: List[str]` - JSON paths of redacted fields (e.g., `rows[0].ssn`)
 - `policy_info: ConnectorPolicyInfo` - Full policy evaluation metadata

- **PolicyInfo types**: New types for policy enforcement metadata
 - `ConnectorPolicyInfo` - Contains `policies_evaluated`, `blocked`, `block_reason`, `redactions_applied`, `processing_time_ms`, `matched_policies`
 - `PolicyMatchInfo` - Details of matched policies including `policy_id`, `policy_name`, `category`, `severity`, `action`

---

## [1.2.0] - 2026-01-08

### Added

- **OAuth2-style client credentials**: New `client_id` and `client_secret` configuration options following OAuth2 client credentials pattern.
 - `client_id` is used for request identification (required for most API calls)
 - `client_secret` is optional - community/self-hosted deployments work without it

- **Enterprise: Close PR** (`close_pr`): Close a PR without merging and optionally delete the branch
 - Useful for cleaning up test/demo PRs created by code governance examples
 - Supports all Git providers: GitHub, GitLab, Bitbucket
 - Requires enterprise portal authentication

### Changed

- **Simplified authentication**: For community mode, simply provide `client_id` for request identification. No `client_secret` needed.

```python
# Community mode - no secret needed
client = AxonFlow(
 endpoint="http://localhost:8080",
 client_id="my-app", # Used for request identification
)
```

### Fixed

- **get_plan_status endpoint**: Fixed endpoint path from `/api/plans/{id}` to `/api/v1/plan/{id}` to match orchestrator API

### Enterprise

- OAuth2 Basic auth: `Authorization: Basic base64(client_id:client_secret)` replaces `X-License-Key` header
- Removed `license_key` configuration option (use `client_id`/`client_secret`)

## [1.1.0] - 2026-01-05

### Added

- **Sensitive Data Category**: Added `SENSITIVE_DATA` to `PolicyCategory` enum for policies that return `sensitive-data` category
- **Provider Restrictions for Compliance**: Support for `allowed_providers` in dynamic policy action config
 - Specify allowed providers via `DynamicPolicyAction(type="route", config={"allowed_providers": [.]})`
 - Enables GDPR, HIPAA, and RBI compliance by restricting LLM routing to specific providers

### Fixed

- **toggle_dynamic_policy HTTP Method**: Changed from PATCH to PUT to match API specification
- **ListExecutionsResponse null handling**: Fixed validation error when API returns `null` for executions field (now returns empty list)

## [1.0.0] - 2026-01-05

### Breaking Changes

- **BREAKING**: Renamed `agent_url` to `endpoint` in `AxonFlowConfig`
- **BREAKING**: Removed `orchestrator_url` and `portal_url` config options (Agent now proxies all routes)
- **BREAKING**: Dynamic policy API path changed from `/api/v1/policies/dynamic` to `/api/v1/dynamic-policies`

### Added

- **Audit Log Reading**: Added `search_audit_logs()` for searching audit logs with filters (user email, client ID, time range, request type)
- **Tenant Audit Logs**: Added `get_audit_logs_by_tenant()` for retrieving audit logs scoped to a specific tenant
- **Audit Types**: Added `AuditLogEntry`, `AuditSearchRequest`, `AuditQueryOptions`, and `AuditSearchResponse` types
- **PII Redaction Support**: Added `requires_redaction` field to `PolicyApprovalResult`
 - When `True`, PII was detected with redact action and response should be processed for redaction
 - Supports new detection defaults: PII defaults to redact instead of block

### Changed

- All SDK methods now route through single Agent endpoint
- Simplified configuration - only `endpoint` field needed
- Removed `_get_orchestrator_url()` and `_get_portal_url()` helper methods (now return endpoint directly)

### Migration Guide

**Before (v0.x):**
```python
client = AxonFlow(
 agent_url="http://localhost:8080",
 orchestrator_url="http://localhost:8081",
 portal_url="http://localhost:8082",
 client_id="my-client",
 client_secret="my-secret",
)
```

**After (v1.x):**
```python
client = AxonFlow(
 endpoint="http://localhost:8080",
 client_id="my-client",
 client_secret="my-secret",
)
```

---

## [0.14.0] - 2026-01-04

### Added

- **Gateway Mode Alias**: Added `pre_check()` as alias for `get_policy_approved_context()` for SDK method parity with Go, TypeScript, and Java SDKs

---

## [0.13.0] - 2026-01-04

### Added

- **Portal Authentication**: Added `loginToPortal()` and `logoutFromPortal()` for session-based authentication
- **Portal URL Configuration**: New `portal_url` config option for Code Governance portal endpoints
- **CSV Export**: Added `export_code_governance_data_csv()` for CSV format exports

### Fixed

- **Code Governance Authentication**: Changed Code Governance methods to use portal session-based auth instead of API key auth
- **Null Array Handling**: Added field_validator for null array handling in `ListPRsResponse` and `ExportResponse`

---

## [0.12.0] - 2026-01-04

### Added

- **Get Connector**: `get_connector(id)` to retrieve details for a specific connector
- **Connector Health Check**: `get_connector_health(id)` to check health status of an installed connector
- **ConnectorHealthStatus type**: New type for connector health responses
- **Orchestrator Health Check**: `orchestrator_health_check()` to verify Orchestrator service health
- **Uninstall Connector**: `uninstall_connector()` to remove installed MCP connectors

### Fixed

- **Connector API Endpoints**: Fixed endpoints to use Orchestrator (port 8081) instead of Agent
 - `list_connectors()` - Changed from Agent `/api/connectors` to Orchestrator `/api/v1/connectors`
 - `install_connector()` - Fixed path to `/api/v1/connectors/{id}/install`
- **Dynamic Policies Endpoint**: Changed from Agent `/api/v1/policies` to Orchestrator `/api/v1/policies/dynamic`

---

## [0.11.0] - 2026-01-04

### Added

- **Execution Replay API**: Debug governed workflows with step-by-step state capture
 - `list_executions()` - List executions with filtering (status, time range)
 - `get_execution()` - Get execution with all step snapshots
 - `get_execution_steps()` - Get individual step snapshots
 - `get_execution_timeline()` - Timeline view for visualization
 - `export_execution()` - Export for compliance/archival
 - `delete_execution()` - Delete execution records

- **Cost Controls**: Budget management and LLM usage tracking
 - `create_budget()` / `get_budget()` / `list_budgets()` - Budget CRUD
 - `update_budget()` / `delete_budget()` - Budget management
 - `get_budget_status()` - Check current budget usage
 - `check_budget()` - Pre-request budget validation
 - `record_usage()` - Record LLM token usage
 - `get_usage_summary()` - Usage analytics and reporting

---

## [0.10.1] - 2025-12-31

### Fixed

- **MCP Connector Endpoint**: Fixed `query_connector()` to use `/api/request` endpoint with `request_type="mcp-query"` instead of deprecated `/mcp/resources/query` endpoint
 - This aligns Python SDK with Go, TypeScript, and Java SDKs
 - Fixes authentication issues in self-hosted mode
 - Ensures proper license validation flow

- **Nested Event Loop Handling**: Fixed `SyncAxonFlow` wrapper to handle nested event loops
 - `execute_query()` and other sync methods now work when called from running event loops
 - Fixes "This event loop is already running" error in Jupyter notebooks and async contexts
 - Uses `ThreadPoolExecutor` to run coroutines safely when event loop is already running

---

## [0.10.0] - 2025-12-30

### Changed

- **Community Mode**: Credentials are now optional for self-hosted/community deployments
 - SDK can be initialized without `api_key` or `license_key` for community features
 - `execute_query()` and `health_check()` work without credentials
 - Auth headers are only sent when credentials are configured

### Added

- `_has_credentials()` method to check if credentials are configured
- `_require_credentials()` helper for enterprise feature validation
- Enterprise features (`get_policy_approved_context`, `audit_llm_call`) now validate credentials at call time

### Fixed

- Gateway Mode methods now raise `AuthenticationError` when called without credentials

---

## [0.9.0] - 2025-12-30

### Fixed

- Fixed `PolicyOverride` model field names (`action_override`, `override_reason`)
- Fixed `list_policy_overrides()` endpoint path
- Fixed `get_static_policy_versions()` response parsing
- Fixed datetime serialization in `create_policy_override()`

> **Note:** These changes affect Enterprise users only. Community users can skip this release.

---

## [0.8.0] - 2025-12-29

### Added

- **Enterprise Policy Features**:
 - `organization_id` field in `CreateStaticPolicyRequest` for organization-tier policies
 - `organization_id` field in `ListStaticPoliciesOptions` for filtering by organization
 - `list_policy_overrides()` method to list all active policy overrides

---

## [0.7.0] - 2025-12-29

### Added

- **Code Governance Metrics & Export APIs** (Enterprise): Compliance reporting for AI-generated code
 - `get_code_governance_metrics()` / `get_code_governance_metrics_sync()` - Returns aggregated statistics (PR counts, file totals, security findings)
 - `export_code_governance_data()` / `export_code_governance_data_sync()` - Exports PR records as JSON for auditors

- **New Types**: `CodeGovernanceMetrics`, `ExportOptions`, `ExportResponse`

---

## [0.6.0] - 2025-12-29

### Added

- **Code Governance Git Provider APIs** (Enterprise): Create PRs from LLM-generated code
 - `validate_git_provider()` - Validate credentials before saving
 - `configure_git_provider()` - Configure GitHub, GitLab, or Bitbucket
 - `list_git_providers()` - List configured providers
 - `delete_git_provider()` - Remove a provider
 - `create_pr()` - Create PR from generated code with audit trail
 - `list_prs()` - List PRs with filtering
 - `get_pr()` - Get PR details
 - `sync_pr_status()` - Sync status from Git provider

- **New Types**: `GitProviderType`, `FileAction`, `CodeFile`, `CreatePRRequest`, `CreatePRResponse`, `PRRecord`, `ListPRsOptions`, `ListPRsResponse`

- **Supported Git Providers**:
 - GitHub (Cloud and Enterprise Server)
 - GitLab (Cloud and Self-Managed)
 - Bitbucket (Cloud and Server/Data Center)

---

## [0.5.0] - 2025-12-28

### Added

- **HITL Support**: `PolicyAction.REQUIRE_APPROVAL` for human oversight policies
 - Use with `create_static_policy()` to trigger approval workflows
 - Enterprise: Full HITL queue integration
 - Community: Auto-approves immediately

- **Code Governance**: `CodeArtifact` type for LLM-generated code detection
 - Language and code type identification
 - Potential secrets and unsafe pattern detection

---

## [0.4.0] - 2025-12-25

### Added

- **Policy CRUD Methods**: Full policy management support for Unified Policy Architecture v2.0.0
 - `list_static_policies()` - List policies with filtering
 - `get_static_policy()` - Get single policy by ID
 - `create_static_policy()` - Create custom policy
 - `update_static_policy()` - Update existing policy
 - `delete_static_policy()` - Delete policy
 - `toggle_static_policy()` - Enable/disable policy
 - `get_effective_static_policies()` - Get merged hierarchy
 - `test_pattern()` - Test regex pattern

- **Policy Override Methods** (Enterprise)
- **Dynamic Policy Methods**
- **New Types**: `StaticPolicy`, `DynamicPolicy`, `PolicyOverride`

## [0.3.1] - 2025-12-23

### Added

- **MAP Timeout Configuration** - New `map_timeout` parameter (default: 120s) for Multi-Agent Planning operations
 - MAP operations involve multiple LLM calls and can take 30-60+ seconds
 - Separate `_map_http_client` with longer timeout
 - `generate_plan()` and `execute_plan()` now use the longer MAP timeout

## [0.3.0] - 2025-12-19

### Added

- **Gemini Interceptor** - Support for Google Generative AI models
 - `wrap_gemini_model()` function for intercepting Gemini API calls
 - Policy enforcement and audit logging for Gemini
- Full feature parity with other SDKs for LLM interceptors

## [0.2.0] - 2025-12-15

### Added

- **Contract Testing Suite** - Validates SDK models against real API responses
 - 19 contract tests covering all response types
 - JSON fixtures for health, query, blocked, plan, and policy responses
 - Prevents API/SDK mismatches before release

- **Integration Test Workflow** - GitHub Actions CI for live testing
 - Contract tests run on every PR
 - Integration tests against staging (on merge to main)
 - Demo script validation
 - Community stack E2E tests (manual trigger)

- **Fixture-Based Test Infrastructure**
 - `tests/fixtures/` directory with recorded API responses
 - `load_json_fixture()` helper in conftest.py
 - Fallback to mock data for backwards compatibility

- **Fixture Recording Script**
 - `scripts/record_fixtures.py` for capturing live API responses

### Changed

- Refactored `tests/conftest.py` with fixture loading utilities
- Added `fixture_*` prefixed fixtures that load from JSON files

### Fixed

- **Datetime parsing with nanoseconds** - `_parse_datetime()` now correctly handles 9-digit fractional seconds from API (was failing with `fromisoformat()`)
- **`generate_plan()` authentication** - Added missing `Authorization` header to plan generation requests (was returning 401)
- **`PolicyViolationError.policy_name`** - Now correctly extracts policy name from `policy_info` in response (was returning `None`)
- Ensured all edge cases for datetime parsing are covered in contract tests

## [0.1.0] - 2025-12-04

### Added

- Initial release of AxonFlow Python SDK
- Async-first client with sync wrappers
- Full type hints with Pydantic v2 models
- Gateway Mode support for lowest-latency LLM calls
 - `get_policy_approved_context()` for pre-checks
 - `audit_llm_call()` for compliance logging
- OpenAI interceptor for transparent governance
- Anthropic interceptor for transparent governance
- MCP connector operations
 - `list_connectors()`
 - `install_connector()`
 - `query_connector()`
- Multi-agent planning
 - `generate_plan()`
 - `execute_plan()`
 - `get_plan_status()`
- Comprehensive exception hierarchy
- Response caching with TTL
- Retry logic with exponential backoff
- Structured logging with structlog
- 95%+ test coverage
- mypy strict mode compatible
- ruff linting compatible
