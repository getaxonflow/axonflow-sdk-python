# AxonFlow Python SDK

Enterprise AI Governance in 3 Lines of Code.

[![PyPI version](https://badge.fury.io/py/axonflow.svg)](https://badge.fury.io/py/axonflow)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Type hints](https://img.shields.io/badge/type%20hints-mypy-brightgreen.svg)](http://mypy-lang.org/)

> **Upgrade strongly recommended.** AxonFlow ships substantial monthly security and quality hardening; staying on the latest major is the security-supported release line. [Latest release](https://github.com/getaxonflow/axonflow-sdk-python/releases/latest) · [Security advisories](https://github.com/getaxonflow/axonflow-sdk-python/security/advisories)

> **Evaluating AxonFlow for a real deployment?**
>
> Choose the path that fits:
> - **Self-serve:** free 90-day [Evaluation License](https://getaxonflow.com/evaluation-license?utm_source=readme_sdk_python_eval)
> - **Hands-on:** [Design Partner Program](https://getaxonflow.com/design-partner?utm_source=readme_sdk_python) — Enterprise access for the scoped engagement, founder-led architecture and rollout support, and preferential pricing after successful rollout
>
> Priority support, architecture review, incident-readiness review, and roadmap input are included for selected partners. We reply within 48 hours.

> **Questions or feedback?**
>
> Comment in [GitHub Discussions](https://github.com/getaxonflow/axonflow/discussions/239) or email [hello@getaxonflow.com](mailto:hello@getaxonflow.com) for private feedback.

## How This SDK Fits with AxonFlow

This SDK is a client library for interacting with a running AxonFlow control plane. It is used from application or agent code to send execution context, policies, and requests at runtime.

A deployed AxonFlow platform (self-hosted or cloud) is required for end-to-end AI governance. SDKs alone are not sufficient—the platform and SDKs are designed to be used together.

### See AxonFlow in Action

Three short videos covering different angles of the platform:

- **[Community Quickstart Demo (Code + Terminal, 2.5 min)](https://youtu.be/BSqU1z0xxCo)** — governed calls, PII block, Gateway Mode with LangChain/CrewAI, and MAP from YAML
- **[Runtime Control Demo (Portal + Workflow, 3 min)](https://youtu.be/6UatGpn7KwE)** — approvals, retry safety, execution state, and the audit viewer
- **[Architecture Deep Dive (12 min)](https://youtu.be/Q2CZ1qnquhg)** — how the control plane works, policy enforcement flow, and multi-agent planning

## Installation

```bash
pip install axonflow
```

With LLM provider support:
```bash
pip install axonflow[openai]      # OpenAI integration
pip install axonflow[anthropic]   # Anthropic integration
pip install axonflow[all]         # All integrations
```

## Evaluation Tier (Free License)

Need more capacity than Community without moving to Enterprise? Evaluation uses the same core features with higher limits:

| Limit | Community | Evaluation (Free) | Enterprise |
|-------|-----------|-------------------|------------|
| Tenant policies | 20 | 50 | Unlimited |
| Org-wide policies | 0 | 5 | Unlimited |
| Audit retention | 3 days | 14 days | 3650 days |
| Concurrent executions | 5 | 25 | Unlimited |
| Pending execution approvals | 5 | 25 | Unlimited |
| Evidence export (CSV / JSON) | — | 5,000 records · 14d window · 3/day | Unlimited |
| Policy simulation | — | 300 / day | Unlimited |

Concurrent executions applies to MAP and WCP executions per tenant. Pending execution approvals applies to MAP confirm/step mode and WCP approval queues.

> **Note:** Evidence export and policy simulation are licensed AxonFlow platform capabilities available alongside the SDK on your deployed platform — not language-specific SDK helpers. Access them via the platform API or customer portal. The SDK row is included to show what your licensed deployment unlocks at each tier.

[Get a free Evaluation license](https://getaxonflow.com/evaluation-license?utm_source=readme_sdk_python_eval) · [Apply for Design Partner](https://getaxonflow.com/design-partner?utm_source=readme_sdk_python_eval) · [Full feature matrix](https://docs.getaxonflow.com/docs/features/community-vs-enterprise?utm_source=readme_sdk_python_eval)

## Try Without Installing

Skip local setup entirely — try AxonFlow instantly at [**try.getaxonflow.com**](https://docs.getaxonflow.com/docs/deployment/community-saas):

```bash
# 1. Register (30 seconds)
curl -X POST https://try.getaxonflow.com/api/v1/register \
  -H "Content-Type: application/json" -d '{"label":"my-trial"}'

# 2. Set credentials and auto-connect
export AXONFLOW_TRY=1
export AXONFLOW_CLIENT_ID=cs_your-tenant-id
export AXONFLOW_CLIENT_SECRET=your-secret
```

No Docker, no license, no installation. Rate-limited to 20 req/min. [Learn more](https://docs.getaxonflow.com/docs/deployment/community-saas).

## Quick Start

### Async Usage (Recommended)

```python
import asyncio
from axonflow import AxonFlow

async def main():
    async with AxonFlow(
        endpoint="https://your-agent.axonflow.com",
        client_id="your-client-id",
        client_secret="your-client-secret"
    ) as client:
        # Execute a governed query
        response = await client.proxy_llm_call(
            user_token="user-jwt-token",
            query="What is AI governance?",
            request_type="chat"
        )
        print(response.data)

asyncio.run(main())
```

### Sync Usage

```python
from axonflow import AxonFlow

with AxonFlow.sync(
    endpoint="https://your-agent.axonflow.com",
    client_id="your-client-id",
    client_secret="your-client-secret"
) as client:
    response = client.proxy_llm_call(
        user_token="user-jwt-token",
        query="What is AI governance?",
        request_type="chat"
    )
    print(response.data)
```

## Features

### Gateway Mode

For lowest-latency LLM calls with full governance and audit compliance:

```python
from axonflow import AxonFlow, TokenUsage

async with AxonFlow(...) as client:
    # 1. Pre-check: Get policy approval
    ctx = await client.get_policy_approved_context(
        user_token="user-jwt",
        query="Find patient records",
        data_sources=["postgres"]
    )

    if not ctx.approved:
        raise Exception(f"Blocked: {ctx.block_reason}")

    # 2. Make LLM call directly (your code)
    llm_response = await openai.chat.completions.create(
        model="gpt-4",
        messages=[{"role": "user", "content": str(ctx.approved_data)}]
    )

    # 3. Audit the call
    await client.audit_llm_call(
        context_id=ctx.context_id,
        response_summary=llm_response.choices[0].message.content[:100],
        provider="openai",
        model="gpt-4",
        token_usage=TokenUsage(
            prompt_tokens=llm_response.usage.prompt_tokens,
            completion_tokens=llm_response.usage.completion_tokens,
            total_tokens=llm_response.usage.total_tokens
        ),
        latency_ms=250
    )
```

### OpenAI Integration

Transparent governance for existing OpenAI code:

```python
from openai import OpenAI
from axonflow import AxonFlow
from axonflow.interceptors.openai import wrap_openai_client

openai = OpenAI()
axonflow = AxonFlow(...)

# Wrap client - governance is now automatic
wrapped = wrap_openai_client(openai, axonflow, user_token="user-123")

# Use as normal
response = wrapped.chat.completions.create(
    model="gpt-4",
    messages=[{"role": "user", "content": "Hello!"}]
)
```

### MCP Connectors

Query data through MCP connectors:

```python
# List available connectors
connectors = await client.list_connectors()

# Query a connector
result = await client.query_connector(
    user_token="user-jwt",
    connector_name="postgres",
    operation="query",
    params={"sql": "SELECT * FROM users LIMIT 10"}
)
```

### MCP Policy Features (v3.2.0)

**Exfiltration Detection** - Prevent large-scale data extraction:

```python
# Query with exfiltration limits (default: 10K rows, 10MB)
result = await client.query_connector(
    user_token="user-jwt",
    connector_name="postgres",
    operation="query",
    params={"sql": "SELECT * FROM customers"}
)

# Check exfiltration info
if result.policy_info.exfiltration_check.exceeded:
    print(f"Limit exceeded: {result.policy_info.exfiltration_check.limit_type}")

# Configure: MCP_MAX_ROWS_PER_QUERY=1000, MCP_MAX_BYTES_PER_QUERY=5242880
```

**Dynamic Policy Evaluation** - Orchestrator-based rate limiting, budget controls:

```python
# Response includes dynamic policy info when enabled
if result.policy_info.dynamic_policy_info.orchestrator_reachable:
    print(f"Policies evaluated: {result.policy_info.dynamic_policy_info.policies_evaluated}")
    for policy in result.policy_info.dynamic_policy_info.matched_policies:
        print(f"  {policy.policy_name}: {policy.action}")

# Enable: MCP_DYNAMIC_POLICIES_ENABLED=true
```

### Multi-Agent Planning

Generate and execute multi-agent plans:

```python
# Generate a plan
plan = await client.generate_plan(
    query="Book a flight and hotel for my trip to Paris",
    domain="travel"
)

print(f"Plan has {len(plan.steps)} steps")

# Execute the plan
result = await client.execute_plan(plan.plan_id)
print(f"Result: {result.result}")
```

## Configuration

```python
from axonflow import AxonFlow, Mode, RetryConfig

client = AxonFlow(
    endpoint="https://your-agent.axonflow.com",
    client_id="your-client-id",               # Required for enterprise features
    client_secret="your-client-secret",       # Required for enterprise features
    mode=Mode.PRODUCTION,                     # or Mode.SANDBOX
    debug=True,                               # Enable debug logging
    timeout=60.0,                             # Request timeout in seconds
    retry_config=RetryConfig(                 # Retry configuration
        enabled=True,
        max_attempts=3,
        initial_delay=1.0,
        max_delay=30.0,
    ),
    cache_enabled=True,                       # Enable response caching
    cache_ttl=60.0,                           # Cache TTL in seconds
)
```

## Error Handling

```python
from axonflow.exceptions import (
    AxonFlowError,
    PolicyViolationError,
    AuthenticationError,
    RateLimitError,
    TimeoutError,
)

try:
    response = await client.proxy_llm_call(...)
except PolicyViolationError as e:
    print(f"Blocked by policy: {e.block_reason}")
except RateLimitError as e:
    print(f"Rate limited: {e.limit}/{e.remaining}, resets at {e.reset_at}")
except AuthenticationError:
    print("Invalid credentials")
except TimeoutError:
    print("Request timed out")
except AxonFlowError as e:
    print(f"AxonFlow error: {e.message}")
```

## Response Types

All responses are Pydantic models with full type hints:

```python
from axonflow import (
    ClientResponse,
    PolicyApprovalResult,
    PlanResponse,
    ConnectorResponse,
)

# Full autocomplete and type checking support
response: ClientResponse = await client.proxy_llm_call(...)
print(response.success)
print(response.data)
print(response.policy_info.policies_evaluated)
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run linting
ruff check .
ruff format .

# Run type checking
mypy axonflow
```

## Examples

Complete working examples for all features are available in the [examples folder](https://github.com/getaxonflow/axonflow/tree/main/examples).

### Community Features

```python
# PII Detection - Automatically detect sensitive data
result = await client.get_policy_approved_context(
    user_token="user-123",
    query="My SSN is 123-45-6789"
)
# result.approved = True, result.requires_redaction = True (SSN detected)

# SQL Injection Detection - Block malicious queries
result = await client.get_policy_approved_context(
    user_token="user-123",
    query="SELECT * FROM users; DROP TABLE users;"
)
# result.approved = False, result.block_reason = "SQL injection detected"

# Static Policies - List and manage built-in policies
policies = await client.list_policies()
# Returns: [Policy(name="pii-detection", enabled=True), ...]

# Dynamic Policies - Create runtime policies
await client.create_dynamic_policy(
    name="block-competitor-queries",
    conditions={"contains": ["competitor", "pricing"]},
    action="block"
)

# MCP Connectors - Query external data sources
resp = await client.query_connector(
    user_token="user-123",
    connector_name="postgres-db",
    operation="query",
    params={"sql": "SELECT name FROM customers"}
)

# Multi-Agent Planning - Orchestrate complex workflows
plan = await client.generate_plan(
    query="Research AI governance regulations",
    domain="legal"
)
result = await client.execute_plan(plan.plan_id)

# Audit Logging - Track all LLM interactions
await client.audit_llm_call(
    context_id=ctx.context_id,
    response_summary="AI response summary",
    provider="openai",
    model="gpt-4",
    token_usage=TokenUsage(prompt_tokens=100, completion_tokens=200, total_tokens=300),
    latency_ms=450
)
```

### Enterprise Features

These features require an AxonFlow Enterprise license:

```python
# Code Governance - Automated PR reviews with AI
pr_result = await client.review_pull_request(
    repo_owner="your-org",
    repo_name="your-repo",
    pr_number=123,
    check_types=["security", "style", "performance"]
)

# Cost Controls - Budget management for LLM usage
budget = await client.get_budget("team-engineering")
# Returns: Budget(limit=1000.00, used=234.56, remaining=765.44)

# MCP Policy Enforcement - Automatic PII redaction in connector responses
resp = await client.query_connector("user", "postgres", "SELECT * FROM customers", {})
# resp.policy_info.redacted = True
# resp.policy_info.redacted_fields = ["ssn", "credit_card"]
```

For enterprise features, contact [sales@getaxonflow.com](mailto:sales@getaxonflow.com).

## Documentation

- [Getting Started](https://docs.getaxonflow.com/docs/sdk/python-getting-started)
- [Gateway Mode Guide](https://docs.getaxonflow.com/docs/sdk/gateway-mode)

## Support

- **Documentation**: https://docs.getaxonflow.com
- **Issues**: https://github.com/getaxonflow/axonflow-sdk-python/issues
- **Email**: hello@getaxonflow.com

If you are evaluating AxonFlow in a company setting and cannot open a public issue, you can share feedback or blockers confidentially here:
[Anonymous evaluation feedback form](https://getaxonflow.com/feedback)

No email required. Optional contact if you want a response.

## Sandbox Mode

```python
# Quick sandbox client for local testing — defaults to http://localhost:8080.
from axonflow import AxonFlow

client = AxonFlow.sandbox()
```

> Sandbox-mode clients fire telemetry like every other client — anonymous SDK
> heartbeat, classification-only payload, opt-out via `AXONFLOW_TELEMETRY=off`.
> Pings are tagged `stream="sandbox"` server-side so dev/test usage is
> distinguishable from production heartbeat. (Pre-v8.0 sandbox-mode pings
> were silently suppressed; the suppression was removed in v8.0 to give a
> single ops-controlled opt-out lever.)

## Telemetry

This SDK sends anonymous usage telemetry (SDK version, OS, enabled features) to help improve AxonFlow.
No prompts, payloads, or PII are ever collected. Opt out: `AXONFLOW_TELEMETRY=off`.

`AXONFLOW_TELEMETRY=off` is the **sole opt-out lever** as of v8.0. The
v7.x `telemetry` keyword argument on `AxonFlow(...)` and the
corresponding `AxonFlowConfig.telemetry` field have been removed; the
previous silent suppression of sandbox-mode pings has also been removed
(sandbox-mode pings now fire and are tagged `stream="sandbox"` so
they're distinguishable from production heartbeat).

### Scope of `AXONFLOW_TELEMETRY=off`

`AXONFLOW_TELEMETRY=off` disables the anonymous SDK heartbeat (version, OS, architecture). On **self-hosted** and **in-VPC** deployments, that heartbeat is the only data the SDK sends to AxonFlow, so setting `=off` means we receive nothing. On **Community SaaS** (`try.getaxonflow.com`) the hosted service also processes operational data — registrations, audit logs, policy enforcement records, workflow state, plan data, and request-header metadata aggregated for usage analytics — as part of running the platform; that operational data flow is governed by the [Privacy Policy](https://getaxonflow.com/privacy/), not by `AXONFLOW_TELEMETRY`.

`DO_NOT_TRACK` is **not** honored as an opt-out for AxonFlow telemetry. It is commonly inherited from host tools and developer environments, which makes it an unreliable expression of user intent.

See [Telemetry Documentation](https://docs.getaxonflow.com/docs/telemetry) for full details.

## License

MIT - See [LICENSE](LICENSE) for details.
