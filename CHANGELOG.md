# Changelog

All notable changes to the AxonFlow Python SDK will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Enterprise: Close PR** (`close_pr`): Close a PR without merging and optionally delete the branch
  - Useful for cleaning up test/demo PRs created by code governance examples
  - Supports all Git providers: GitHub, GitLab, Bitbucket
  - Requires enterprise portal authentication
- **PRRecord.closed_at**: Added optional `closed_at` field to track when a PR was closed

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
