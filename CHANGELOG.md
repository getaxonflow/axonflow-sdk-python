# Changelog

All notable changes to the AxonFlow Python SDK will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
