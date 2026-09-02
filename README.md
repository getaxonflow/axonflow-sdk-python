# AxonFlow Python SDK

Enterprise AI Governance in 3 Lines of Code.

[![PyPI version](https://badge.fury.io/py/axonflow.svg)](https://badge.fury.io/py/axonflow)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Type hints](https://img.shields.io/badge/type%20hints-mypy-brightgreen.svg)](http://mypy-lang.org/)

> **Upgrade strongly recommended.** AxonFlow ships substantial monthly security and quality hardening; staying on the latest major is the security-supported release line. [Latest release](https://github.com/getaxonflow/axonflow-sdk-python/releases/latest) · [Security advisories](https://github.com/getaxonflow/axonflow-sdk-python/security/advisories)

> **Taking a sponsored workflow to production?**
>
> Choose the path that fits:
> - **Self-serve:** free 90-day [Evaluation License](https://getaxonflow.com/evaluation-license?utm_source=readme_sdk_python_eval)
> - **Paid production program:** [Design Partner or Confidential Pilot](https://getaxonflow.com/design-partner?utm_source=readme_sdk_python)  -  one scoped workflow over 60 or 75 days, founder-led rollout support, upfront conversion pricing, and a fixed decision date; public track from $2,000 or confidential track from $4,000
>
> The paid program requires a dated forcing event, written controls, an executive sponsor, and a technical owner. Prices are subject to eligibility and a signed agreement.

> **Questions or feedback?**
>
> Comment in [GitHub Discussions](https://github.com/getaxonflow/axonflow/discussions/239) or email [hello@getaxonflow.com](mailto:hello@getaxonflow.com) for private feedback.

## How This SDK Fits with AxonFlow

This SDK is a client library for interacting with a running AxonFlow control plane. It is used from application or agent code to send execution context, policies, and requests at runtime.

A deployed AxonFlow platform (self-hosted or cloud) is required for end-to-end AI governance. SDKs alone are not sufficient—the platform and SDKs are designed to be used together.

### See AxonFlow in Action

Videos covering different angles of the platform:

- **[Product demos: Platform + Fraud & Risk](https://getaxonflow.com/demo/?utm_source=github&utm_medium=readme&utm_campaign=product_demo&utm_content=axonflow-sdk-python)** - runtime enforcement, HITL approvals, audit evidence, cost visibility, and agentic payment controls
- **[Community Quickstart walkthrough (2 min)](https://youtu.be/BSqU1z0xxCo)** - governed calls, PII blocking, Gateway Mode with LangChain/CrewAI, and MAP from YAML
- **[Architecture deep dive (12 min)](https://youtu.be/Q2CZ1qnquhg)** - how the control plane works, policy enforcement flow, and multi-agent planning

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

[Get a free Evaluation license](https://getaxonflow.com/evaluation-license?utm_source=readme_sdk_python_eval) · [Run a paid production program](https://getaxonflow.com/design-partner?utm_source=readme_sdk_python_eval) · [Full feature matrix](https://docs.getaxonflow.com/docs/features/community-vs-enterprise?utm_source=readme_sdk_python_eval)

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
        client_secret="your-client-secret",
    ) as client:
        # Execute a governed query
        response = await client.proxy_llm_call(
            user_token="user-jwt-token", query="What is AI governance?", request_type="chat"
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
    client_secret="your-client-secret",
) as client:
    response = client.proxy_llm_call(
        user_token="user-jwt-token", query="What is AI governance?", request_type="chat"
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
        user_token="user-jwt", query="Find patient records", data_sources=["postgres"]
    )

    if not ctx.approved:
        raise Exception(f"Blocked: {ctx.block_reason}")

    # 2. Make LLM call directly (your code)
    llm_response = await openai.chat.completions.create(
        model="gpt-4", messages=[{"role": "user", "content": str(ctx.approved_data)}]
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
            total_tokens=llm_response.usage.total_tokens,
        ),
        latency_ms=250,
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
    model="gpt-4", messages=[{"role": "user", "content": "Hello!"}]
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
    params={"sql": "SELECT * FROM users LIMIT 10"},
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
    params={"sql": "SELECT * FROM customers"},
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
    query="Book a flight and hotel for my trip to Paris", domain="travel"
)

print(f"Plan has {len(plan.steps)} steps")

# Execute the plan
result = await client.execute_plan(plan.plan_id)
print(f"Result: {result.result}")
```

### AuthZEN-native authorization

`client.evaluate` asks the gateway an AuthZEN question - may this subject perform this action on this resource? - over `POST /api/v1/access/evaluation`. It is the surface to write **new** integrations against: at v11 the engine behind it becomes AxonFlow's new Policy Decision Point with no wire change, so an integration written here migrates once rather than twice. Nothing is deprecated by it today; `client.decide` and the gateway/proxy methods are wire-stable through all of v11.

```python
from axonflow import (
    AuthZENAction,
    AuthZENRequest,
    AuthZENResource,
    AuthZENSubject,
    AuthZENRefusal,
)

decision = await client.evaluate(
    AuthZENRequest(
        subject=AuthZENSubject(type="gateway", id="llm-gateway-01"),
        action=AuthZENAction(name="llm.completion"),
        resource=AuthZENResource(type="llm", id="llm"),
        context={"args": {"query": user_prompt}},
    )
)

if not decision.allowed:
    raise RuntimeError(f"blocked: {decision.state} ({decision.reason})")
for obligation in decision.mandatory_obligations:
    ...  # an allow you cannot discharge is not an allow
```

Several preconditions of **one** operation go in a bulk envelope, which returns **one** decision - a denied entry denies the operation, so a caller cannot act on the entry it liked:

```python
from axonflow import AuthZENBulk

decision = await client.evaluate_all(
    AuthZENBulk(
        subject=AuthZENSubject(type="gateway", id="llm-gateway-01"),
        action=AuthZENAction(name="tool.call"),
        context={"args": {"query": user_prompt}},
        evaluations=[
            AuthZENRequest(resource=AuthZENResource(type="tool", id="jira/move_issue")),
            AuthZENRequest(resource=AuthZENResource(type="tool", id="jira/update_project")),
        ],
    )
)
```

#### Known gotchas

**A refusal is not a denial.** This surface refuses anything it cannot evaluate rather than evaluating around it - send a subject property or an unrecognised context member and you get an `AuthZENRefusal` naming the exact member, not a decision computed without it. Treating every error as a deny fails closed, which is safe, but blocks traffic that would be allowed once the request is corrected.

```python
try:
    decision = await client.evaluate(request)
except AuthZENRefusal as refusal:
    refusal.code  # e.g. "unevaluable_attribute" - a closed, generated set
    refusal.pointer  # "/evaluation/subject/properties" - the member to fix
    refusal.refused_by  # "client" (this SDK) or "gateway"
    refusal.retryable  # only a gateway dependency failure is
```

`AuthZENProtocolError` is separate and means something else: the gateway answered `200` with a body this build cannot safely act on - no profile context, a profile it cannot read, or a decision boolean that disagrees with its operational state. It is always fail-closed, and the fix is an upgrade or an operator, not a corrected request. Read `.kind` to tell those apart without matching on the message: `unsupported_profile` and `unknown_operational_state` mean upgrade the SDK, while `missing_profile_context`, `decision_state_disagreement`, `obligations_on_refusal` and `undecodable_body` mean go and look at the deployment. A `401` surfaces as the SDK's ordinary `AuthenticationError`, because the gateway answers authentication before this route runs.

**`decision.allowed`, never `decision.decision`.** The bare boolean is AuthZEN 1.0's collapsed rendering; `allowed` additionally requires the operational state to be `ALLOW`, so a `CHALLENGE` or an `ERROR` can never be read as permission.

**Three states, not two.** `None` cannot express the difference between "the source established there is no value" and "the source could not be reached", and collapsing them is how an attribute nobody resolved gets recorded as one that was weighed. Attributes inside the `context` and `properties` bags may be explicit:

```python
from axonflow import AuthZENAttribute, AUTHZEN_UNKNOWN_RESOLUTION_FAILED

context = {
    "args": {"query": user_prompt},
    "correlation": {
        "session_id": AuthZENAttribute.absent(),  # a fact: omitted, request sent
        "trace_id": AuthZENAttribute.unknown(  # not a fact: refused locally,
            AUTHZEN_UNKNOWN_RESOLUTION_FAILED  # nothing is sent
        ),
    },
}
```

The tri-state applies to attribute **data**, not to the structural members (`subject.id`, `action.name`, …): those are the identity of the question being asked, and an identity you cannot resolve is not an attribute whose absence a policy could evaluate - there is no request to make.

**Today's mapping is deliberately narrow.** `subject.type` must be `"gateway"` (an end-user subject needs the identity plane, which activates at v11); an `llm` or `agent` resource id must be the stage name itself, not a provider/model pair, because nothing on the serving path reads a provider or a model; a `tool` resource id is `"server/tool"`, because both halves ARE read. Everything else is refused by name.

The wire types are **generated** from the platform's canonical contract artifact (`scripts/gen_authzen_types.py`); CI fails if the committed module is not what the artifact produces. Runnable example: [`examples/authzen_evaluation.py`](examples/authzen_evaluation.py). Migration notes: [`docs/AUTHZEN_MIGRATION_DRAFT.md`](docs/AUTHZEN_MIGRATION_DRAFT.md).

## Configuration

```python
from axonflow import AxonFlow, Mode, RetryConfig

client = AxonFlow(
    endpoint="https://your-agent.axonflow.com",
    client_id="your-client-id",  # Required for enterprise features
    client_secret="your-client-secret",  # Required for enterprise features
    mode=Mode.PRODUCTION,  # or Mode.SANDBOX
    debug=True,  # Enable debug logging
    timeout=60.0,  # Request timeout in seconds
    retry_config=RetryConfig(  # Retry configuration
        enabled=True,
        max_attempts=3,
        initial_delay=1.0,
        max_delay=30.0,
    ),
    cache_enabled=True,  # Enable response caching
    cache_ttl=60.0,  # Cache TTL in seconds
)
```

## Reading decisions: who is asking decides what comes back

`explain_decision` and `list_decisions` — and the audit reads — are scoped to
the **per-user identity** you present, not to the tenant credential. Since
platform #2922:

| What you present | What an enterprise stack returns |
|---|---|
| a tenant-wide role (`admin`, `owner`, `policy_admin`) | the whole tenant |
| any other identity (`developer`, `viewer`) | only the rows attributed to it |
| **no identity** | **nothing at all** — every list is empty, every explain is not-found |

`client_id`/`client_secret` authenticate the **organization**. They do not say
who is asking, so on their own they land in the third row. Community and
Community-SaaS deployments are single-operator and read tenant-wide with no
identity needed.

```python
client = AxonFlow(
    endpoint="http://localhost:8080",
    client_id=os.environ["AXONFLOW_CLIENT_ID"],
    client_secret=os.environ["AXONFLOW_CLIENT_SECRET"],
    user_token=os.environ["AXONFLOW_USER_TOKEN"],  # <- the per-user identity
)

# Per call:
exp = await client.explain_decision(decision_id, user_token=users_token)

# Or, for a process acting on behalf of several people, derive a client bound
# to one person. Unlike the per-call keyword, which only the read methods
# accept, this reaches EVERY method.
rows = await client.as_user(alices_token).list_decisions()
```

The token is a per-user JWT — minted by the customer portal's user-token API,
or for local testing by `scripts/generate-jwt.sh --kind user`. It is **not** the
tenant JWT and not `client_secret`. It is sent as `X-User-Token`, is never
logged, never reaches telemetry, and is never sent to any origin but the
configured endpoint.

### Telling the outcomes apart

"Not found", "not yours" and "no identity resolved" used to arrive as the same
`404`, and an unscoped list arrived as an ordinary empty page. Both now carry a
cause:

```python
from axonflow.read_identity import ReadScopeError

try:
    decisions = await client.list_decisions()
except ReadScopeError as err:
    # The platform resolved no identity, so it returned zero rows by
    # construction. The empty answer was never evidence about your data.
    assert err.identity_missing
```

`explain_decision` is where the other scope shows up. Under `own-rows` the
platform answers "not attributed to you" and "not there at all" with the **same
404**, deliberately, so that a miss cannot be used to probe for another user's
rows — the error reports the scope the read ran under, never a claim about what
exists.

> **A valid token can still resolve to nobody.** The platform reserves the whole
> of `@axonflow.local` and `@axonflow.internal` for *shared* identities and
> censuses them to nothing before scoping. A correctly-signed developer token
> minted at `demo-user@axonflow.local` — which is `generate-jwt.sh`'s own
> default — reads zero rows and reports `identity_missing`, exactly like no
> token at all. Mint per-user identities at a real domain.

> **Setting `user_token` affects more than reads.** The header rides every
> request and the agent validates it on every route it proxies — not just the
> scoped reads. A stale or rotated token therefore turns `list_connectors`,
> `install_connector` and policy CRUD into `401`s rather than merely unscoping a
> read. That is the correct, fail-closed direction, but it puts this value in
> the same rotation story as `client_secret`.

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
    user_token="user-123", query="My SSN is 123-45-6789"
)
# result.approved = True, result.requires_redaction = True (SSN detected)

# SQL Injection Detection - Block malicious queries
result = await client.get_policy_approved_context(
    user_token="user-123", query="SELECT * FROM users; DROP TABLE users;"
)
# result.approved = False, result.block_reason = "SQL injection detected"

# Static Policies - List and manage built-in policies
policies = await client.list_policies()
# Returns: [Policy(name="pii-detection", enabled=True), ...]

# Dynamic Policies - Create runtime policies
await client.create_dynamic_policy(
    name="block-competitor-queries",
    conditions={"contains": ["competitor", "pricing"]},
    action="block",
)

# MCP Connectors - Query external data sources
resp = await client.query_connector(
    user_token="user-123",
    connector_name="postgres-db",
    operation="query",
    params={"sql": "SELECT name FROM customers"},
)

# Multi-Agent Planning - Orchestrate complex workflows
plan = await client.generate_plan(query="Research AI governance regulations", domain="legal")
result = await client.execute_plan(plan.plan_id)

# Audit Logging - Track all LLM interactions
await client.audit_llm_call(
    context_id=ctx.context_id,
    response_summary="AI response summary",
    provider="openai",
    model="gpt-4",
    token_usage=TokenUsage(prompt_tokens=100, completion_tokens=200, total_tokens=300),
    latency_ms=450,
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
    check_types=["security", "style", "performance"],
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

### Platform licence tier (`license_tier`)

Each heartbeat also reports the licence tier of the AxonFlow platform the SDK is configured to talk to — for example `community`, `evaluation`, `Enterprise`, or the transient `starting` while a platform is still booting. This lets us tell an enterprise-licensed deployment apart from an unlicensed community one in aggregate adoption figures, which the heartbeat previously could not distinguish.

What is and is not collected:

- **Collected:** the coarse tier string only.
- **Not collected:** your licence key, its expiry, its seat or node count, your organisation's name, and any other licence detail. The SDK never reads your licence key.

The value is read from the `tier` field of the platform's own `/health` response — the same response the heartbeat already fetches to report the platform version, and an endpoint that returns this field to any caller without authentication. **No additional network request is made, and the SDK gains no access to anything `/health` does not already return.**

**This is an adoption-analytics signal, not an entitlement one.** The value is whatever the platform at your configured endpoint reported about itself, relayed unchanged: the SDK derives nothing and verifies nothing, and the receiver cannot verify the relay either. Whoever operates that endpoint controls the value completely, so it must never gate entitlement, unlock a feature, or enter any authorization or billing decision. It is used only for aggregate adoption figures.

The field is **omitted entirely** whenever the tier could not be determined — the platform is unreachable, returns an error, returns an unparseable body, or returns no `tier` field. It is never defaulted to a guessed value, so an absent field means "not known", never "community".

`AXONFLOW_TELEMETRY=off` suppresses this field along with the rest of the heartbeat.

`DO_NOT_TRACK` is **not** honored as an opt-out for AxonFlow telemetry. It is commonly inherited from host tools and developer environments, which makes it an unreliable expression of user intent.

See [Telemetry Documentation](https://docs.getaxonflow.com/docs/telemetry) for full details.

## License

MIT - See [LICENSE](LICENSE) for details.
