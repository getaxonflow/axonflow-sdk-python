# Contributing to AxonFlow Python SDK

Thank you for your interest in contributing to the AxonFlow Python SDK! We welcome contributions from the community.

## Getting Started

1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/axonflow-sdk-python.git`
3. Create a feature branch: `git checkout -b feature/your-feature-name`
4. Make your changes
5. Run tests: `pytest`
6. Commit your changes: `git commit -m "Add your feature"`
7. Push to your fork: `git push origin feature/your-feature-name`
8. Open a Pull Request

## Development Setup

### Prerequisites

- Python 3.9 or higher
- Git

### Installation

```bash
git clone https://github.com/getaxonflow/axonflow-sdk-python.git
cd axonflow-sdk-python
pip install -e ".[dev]"
```

### Running Tests

```bash
# Run all tests
pytest

# Run tests with coverage
pytest --cov=axonflow --cov-report=term-missing

# Run tests with verbose output
pytest -v
```

### Wire-shape contract tests

When you add or rename a pydantic `BaseModel` subclass whose class name
matches an OpenAPI schema in the platform specs, a CI job diffs the
fields against the spec and fails the PR on drift. This is enforced by
`tests/test_wire_shape.py` (opt-in via the `wire_shape` marker).

Run locally:

```bash
# Clone the community mirror — the specs live in docs/api/
git clone https://github.com/getaxonflow/axonflow.git ../axonflow

# Point the test at the specs dir and run just the wire-shape tests
AXONFLOW_OPENAPI_SPECS_DIR=../axonflow/docs/api \
  pytest tests/test_wire_shape.py -m wire_shape -v
```

Without the env var, the tests skip cleanly — a plain `pytest` run
doesn't need the specs.

If you legitimately need to update the acknowledged baseline (e.g. a
drift entry was burned down, or a new acknowledged divergence was
added), regenerate it with:

```bash
# Pinning the SHA picks up the current HEAD of the community mirror.
# Alternately pass --sha <commit-sha> to pin explicitly.
python scripts/refresh_wire_shape_baseline.py ../axonflow/docs/api
```

Never regenerate to silence a failure without understanding what drifted;
that defeats the gate.

#### Bumping `openapi_specs_sha`

The wire-shape gate pins the OpenAPI spec revision via
`openapi_specs_sha` in the baseline so a given SDK commit always diffs
against the same spec. Changing that SHA in the same PR that changes
SDK models can silently retarget the gate past drift it should have
caught, so the CI job enforces an extra guardrail: any PR that moves
`openapi_specs_sha` must also carry the `spec-pin-bump` label, which
surfaces the bump for explicit review.

Recommended flow:

1. Open a dedicated PR that updates only `openapi_specs_sha` (and the
   parts of the baseline that change as a consequence: drift entries,
   cross-spec shapes).
2. Apply the `spec-pin-bump` label.
3. Merge.
4. Follow up with the SDK-side changes that the new spec enables.

If it's genuinely one change (platform + SDK shipping together), apply
the label to the single PR — the label just signals the reviewer to
scrutinise the SHA move.

### Running Linting

```bash
# Run Ruff linter
ruff check .

# Run Ruff formatter
ruff format .

# Run MyPy type checking
mypy axonflow
```

### Running Examples

Examples target a local AxonFlow community stack running on
`http://localhost:8080`. Start the stack before running:

```bash
git clone https://github.com/getaxonflow/axonflow.git
cd axonflow && docker compose up -d
```

Then, from this repo:

```bash
export AXONFLOW_AGENT_URL="http://localhost:8080"
export AXONFLOW_CLIENT_ID="demo-client"
export AXONFLOW_CLIENT_SECRET="demo-secret"
```

Run examples:

```bash
# Quickstart (async + sync patterns)
python examples/quickstart.py

# Gateway mode (pre-check + direct LLM + audit)
python examples/gateway_mode.py

# OpenAI interceptor (requires OPENAI_API_KEY)
python examples/openai_integration.py

# WCP retry_context + idempotency_key (enterprise features)
python examples/wcp_retry_idempotency.py
```

## Code Style

- Follow PEP 8 style guide
- Use Ruff for linting and formatting: `ruff check . && ruff format .`
- Use type hints for all function signatures
- Run MyPy for type checking: `mypy axonflow`
- Keep functions focused and well-documented
- Use meaningful variable and function names
- Add docstrings for all public functions and classes

## Pull Request Guidelines

1. **Keep PRs focused**: One feature or fix per PR
2. **Update documentation**: If you change the API, update README.md
3. **Add tests**: All new features should include tests
4. **Pass CI checks**: Ensure all tests pass before submitting
5. **Write clear commit messages**: Describe what and why, not how

### Commit Message Format

```
Add feature: brief description

Detailed explanation of the changes and why they were made.
Any breaking changes should be clearly noted.
```

## Feature Requests

Have an idea for a new feature? We'd love to hear it!

1. Check existing issues to avoid duplicates
2. Open a new issue with the "Feature Request" label
3. Describe the feature and its use case
4. Discuss implementation approach

## Bug Reports

Found a bug? Help us fix it!

1. Check existing issues to avoid duplicates
2. Open a new issue with the "Bug" label
3. Include:
   - Python version
   - Operating system
   - Steps to reproduce
   - Expected behavior
   - Actual behavior
   - Error messages or logs

## Testing

We use pytest for testing. When adding new features:

1. Add unit tests for new functions
2. Add integration tests for API interactions
3. Ensure test coverage remains above 80%
4. Use pytest-httpx for mocking HTTP calls

Example test structure:

```python
import pytest
from pytest_httpx import HTTPXMock

from axonflow import AxonFlow


@pytest.mark.asyncio
async def test_proxy_llm_call(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json={"success": True, "data": "result"})

    async with AxonFlow(
        endpoint="https://test.example.com",
        client_id="test",
        client_secret="test",
    ) as client:
        result = await client.proxy_llm_call("token", "query", "chat")
        assert result.success is True
```

## Documentation

- Update README.md for user-facing changes
- Add docstrings for all public functions and classes
- Include usage examples in docstrings when helpful
- Keep documentation clear and concise

## Code Review Process

1. All PRs require at least one approval
2. Maintainers will review your PR within 3-5 business days
3. Address feedback and update your PR
4. Once approved, a maintainer will merge your PR

## Baseline burndown policy

Several CI gates use a baseline file to grandfather pre-existing findings — the gate fails on any *new* finding but tolerates the listed ones. Baselines exist to land the gate without a giant cleanup PR; they are not intended to be permanent.

When your PR touches a baselined area (e.g. a function listed in `.lint_baselines/falsey_clobber.json`, or a type in `tests/fixtures/wire-shape-baseline.json`), do one of:

- **Burn it down.** Fix the baselined finding in this PR, remove the entry from the baseline file, and note "burndown: `<entry>`" in the PR description.
- **Justify it.** If the finding can't be fixed in this PR (different scope, blocked on a platform change, etc.), say so in the PR description in one line.

Baseline files in this repo:

- `.lint_baselines/falsey_clobber.json` — `or`-falsey-clobber on wire-field accesses
- `tests/fixtures/wire-shape-baseline.json` — wire-shape contract gate

CI does not block PRs that touch a baselined area without addressing it, but reviewers will ask the burndown-or-justify question.

## License

By contributing to AxonFlow Python SDK, you agree that your contributions will be licensed under the MIT License.

## Questions?

If you have questions about contributing, feel free to:

- Open a discussion on GitHub
- Email us at hello@getaxonflow.com
- Check our documentation at https://docs.getaxonflow.com

Thank you for contributing to AxonFlow!
