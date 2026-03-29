# Changelog

All notable changes to the AxonFlow Python SDK will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [5.3.0] - 2026-03-30

### Added

- **`AxonFlowChatModel` LangChain adapter**: Wraps any `BaseChatModel` with pre-check + audit governance. Extracted `_GovernanceMixin` base class for shared governance logic. Includes `with_fallbacks` override to wrap each fallback in governance, and `batch`/`abatch` `NotImplementedError` to prevent silent bypass.
- **`AxonFlowRunnableBinding`**: Governance wrapper for LangChain runnables with transparent delegation.
- **Input checking in `tool_output_wrapper()`**: `tool_output_wrapper()` now calls `mcp_check_input` before tool execution, enforcing input policies on local `@tool` functions in LangGraph workflows.

### Fixed

- **`mcp_query()` 403 handling**: `mcp_query()` previously raised `ConnectorError` on HTTP 403 responses. Now treats 403 as a valid policy-blocked response consistent with `mcp_check_input()` and `mcp_check_output()`, returning a `ConnectorResponse` with `blocked=True` instead of raising an exception.
- **Mypy type errors in LangChain adapter**: Fixed `int()` argument type errors in `_extract_token_usage()` for dict values that could be `None`.

---

## [5.2.0] - 2026-03-25

### Added

- `simulate_policies()` — dry-run all active policies against an input query. Returns allowed/blocked status, applied policies, risk score, and daily usage. Requires Evaluation tier or above.
- `get_policy_impact_report()` — test a single policy against multiple inputs and get aggregate match/block statistics.
- `detect_policy_conflicts()` — analyze active policies for contradictions, shadows, and redundancies. Optionally filter to conflicts involving a specific policy.
- `AxonFlowLangGraphAdapter.tool_output_wrapper()` — returns an async wrapper for LangGraph `ToolNode(awrap_tool_call=...)` that enforces output policy checks on local `@tool` functions. Fixes a gap where locally defined tools bypassed `mcp_tool_interceptor` policy enforcement.
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

- **`AxonFlowLangGraphAdapter.mcp_tool_interceptor()`**: Factory method returning an async callable ready for use with `MultiServerMCPClient(tool_interceptors=[...])`. Enforces AxonFlow input and output policies around every MCP tool call: `mcp_check_input → handler() → mcp_check_output`. Handles policy blocks and returns redacted output when `mcp_check_output` applies redaction.
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

- **fail_workflow()** (#1187): Fail a workflow with optional reason
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

- **WCP Approval Gates** (Issue #1169): HITL approval and rejection for workflow steps
  - `approve_step(workflow_id, step_id)` - Approve a pending workflow step
  - `reject_step(workflow_id, step_id, reason=None)` - Reject a step with optional reason
  - `get_pending_approvals(limit=20)` - List steps awaiting human approval

- **MAP Plan Cancellation** (Issue #1072): Cancel running multi-agent plans
  - `cancel_plan(plan_id, reason=None)` - Cancel a plan with optional reason

- **MAP Plan Update** (Issue #1072): Modify plan configuration before or during execution
  - `update_plan(plan_id, **kwargs)` - Update execution mode, domain, or version

- **MAP Plan Versioning and Rollback** (Issue #1072): Version history and rollback support
  - `get_plan_versions(plan_id)` - List plan version history
  - `rollback_plan(plan_id, version)` - Rollback to a previous version (raises on 409 conflict)
  - New types in response: `RollbackPlanResponse`, `PlanVersion`

- **Webhook Subscriptions** (Issue #1169): Event notification management
  - `create_webhook(url, events, **kwargs)` - Create a webhook subscription
  - `list_webhooks()` - List active webhook subscriptions
  - `get_webhook(webhook_id)` - Get webhook details
  - `update_webhook(webhook_id, *, url=None, events=None, secret=None, active=None, description=None)` - Update webhook with typed parameters
  - `delete_webhook(webhook_id)` - Delete a webhook subscription
  - Available on both `AxonFlow` (async) and `SyncAxonFlow` (sync) clients

- **Unified Execution Cancellation** (EPIC #1074): Cancel running executions across both MAP and WCP subsystems
  - `cancel_execution(execution_id, reason=None)` - Cancel a unified execution via `POST /api/v1/unified/executions/{id}/cancel`
  - Available on both `AxonFlow` (async) and `SyncAxonFlow` (sync) clients
  - Propagates to MAP `cancel_plan()` or WCP `abort_workflow()` based on execution type

### Fixed

- **`execute_plan` status hardcoded**: `execute_plan()` always returned `status="completed"` regardless of actual server response. Now reads status from response (`data.status` > `metadata.status` > default), correctly surfacing `awaiting_approval` for WCP confirm mode.
- **Unified execution API URLs** (EPIC #1074): `get_execution_status()` and `list_unified_executions()` now use correct `/api/v1/unified/executions` path (was incorrectly pointing to `/api/v1/executions` which is the Execution Replay API)
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

- **Unified Execution Tracking** (Issue #1075 - EPIC #1074): Consistent status tracking for MAP plans and WCP workflows
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

- **Workflow Policy Enforcement** (Issues #1019, #1020, #1021): Policy transparency for workflow operations
  - `StepGateResponse` now includes `policies_evaluated` and `policies_matched` fields with `PolicyMatch` type
  - `PolicyMatch` class with `policy_id`, `policy_name`, `action`, `reason` for policy transparency
  - `PolicyEvaluationResult` class for MAP execution with `allowed`, `applied_policies`, `risk_score`
  - Workflow operations (`workflow_created`, `workflow_step_gate`, `workflow_completed`) logged to audit trail

---

## [1.5.0] - 2026-01-17

### Added

- **Workflow Control Plane** (Issue #834): Governance gates for external orchestrators
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

- **MCP Exfiltration Detection** (Issue #966): `ConnectorPolicyInfo` now includes `exfiltration_check` with row/volume limit information
  - `ExfiltrationCheckInfo` type with `rows_returned`, `row_limit`, `bytes_returned`, `byte_limit`, `within_limits` fields
  - Prevents large-scale data extraction via MCP queries
  - Configurable via `MCP_MAX_ROWS_PER_QUERY` and `MCP_MAX_BYTES_PER_QUERY` environment variables

- **MCP Dynamic Policies** (Issue #968): `ConnectorPolicyInfo` now includes `dynamic_policy_info` for Orchestrator-evaluated policies
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
    client_id="my-app",  # Used for request identification
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
  - Specify allowed providers via `DynamicPolicyAction(type="route", config={"allowed_providers": [...]})`
  - Enables GDPR, HIPAA, and RBI compliance by restricting LLM routing to specific providers

### Fixed

- **toggle_dynamic_policy HTTP Method**: Changed from PATCH to PUT to match API specification
- **ListExecutionsResponse null handling**: Fixed validation error when API returns `null` for executions field (now returns empty list)

## [1.0.0] - 2026-01-05

### Breaking Changes

- **BREAKING**: Renamed `agent_url` to `endpoint` in `AxonFlowConfig`
- **BREAKING**: Removed `orchestrator_url` and `portal_url` config options (Agent now proxies all routes per ADR-026)
- **BREAKING**: Dynamic policy API path changed from `/api/v1/policies/dynamic` to `/api/v1/dynamic-policies`

### Added

- **Audit Log Reading**: Added `search_audit_logs()` for searching audit logs with filters (user email, client ID, time range, request type)
- **Tenant Audit Logs**: Added `get_audit_logs_by_tenant()` for retrieving audit logs scoped to a specific tenant
- **Audit Types**: Added `AuditLogEntry`, `AuditSearchRequest`, `AuditQueryOptions`, and `AuditSearchResponse` types
- **PII Redaction Support**: Added `requires_redaction` field to `PolicyApprovalResult` (Issue #891)
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

- **Gemini Interceptor** - Support for Google Generative AI models (#8)
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
