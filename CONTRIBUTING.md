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

Set up your environment variables:

```bash
export AXONFLOW_AGENT_URL="https://staging-eu.getaxonflow.com"
export AXONFLOW_LICENSE_KEY="your-license-key"
```

Run examples:

```bash
# Basic example
python examples/basic_usage.py

# Gateway mode example
python examples/gateway_mode.py

# Interceptors example
python examples/interceptors.py
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
async def test_execute_query(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json={"success": True, "data": "result"})

    async with AxonFlow(
        endpoint="https://test.example.com",
        client_id="test",
        client_secret="test",
    ) as client:
        result = await client.execute_query("token", "query", "chat")
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

## License

By contributing to AxonFlow Python SDK, you agree that your contributions will be licensed under the MIT License.

## Questions?

If you have questions about contributing, feel free to:

- Open a discussion on GitHub
- Email us at dev@getaxonflow.com
- Check our documentation at https://docs.getaxonflow.com

Thank you for contributing to AxonFlow!
