# Security Policy

## Supported Versions

We release patches for security vulnerabilities. Currently supported versions:

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

The AxonFlow team takes security bugs seriously. We appreciate your efforts to responsibly disclose your findings.

### How to Report

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please report them via email to:

**security@getaxonflow.com**

You should receive a response within 48 hours. If for some reason you do not, please follow up via email to ensure we received your original message.

### What to Include

Please include the following information in your report:

- Type of vulnerability (e.g., authentication bypass, code injection, etc.)
- Full paths of source file(s) related to the vulnerability
- Location of the affected source code (tag/branch/commit or direct URL)
- Step-by-step instructions to reproduce the issue
- Proof-of-concept or exploit code (if possible)
- Impact of the vulnerability, including how an attacker might exploit it

### What to Expect

After submitting a vulnerability report:

1. **Acknowledgment**: We'll acknowledge receipt within 48 hours
2. **Investigation**: We'll investigate and validate the vulnerability
3. **Updates**: We'll keep you informed of our progress
4. **Resolution**: We'll work on a fix and coordinate disclosure timing with you
5. **Credit**: With your permission, we'll publicly credit you for the discovery

### Disclosure Policy

- We'll work with you to understand and resolve the issue quickly
- We'll keep you informed throughout the process
- We'll publicly disclose the vulnerability once a fix is released
- We request that you keep the vulnerability confidential until we've had a chance to address it

## Security Best Practices

When using the AxonFlow Python SDK:

### 1. Credential Management

**Never** hardcode credentials in your source code:

```python
# BAD - Credentials in code
client = AxonFlow(
    agent_url="https://staging-eu.getaxonflow.com",
    client_id="client-id-here",
    client_secret="secret-here",
)

# GOOD - Use environment variables
import os

client = AxonFlow(
    agent_url=os.environ["AXONFLOW_AGENT_URL"],
    client_id=os.environ["AXONFLOW_CLIENT_ID"],
    client_secret=os.environ["AXONFLOW_CLIENT_SECRET"],
)
```

### 2. TLS/SSL Configuration

Always use HTTPS endpoints for production:

```python
# GOOD - HTTPS endpoint
client = AxonFlow(
    agent_url="https://api.getaxonflow.com",
    client_id=client_id,
    client_secret=secret,
)

# WARNING - HTTP should only be used for local development
client = AxonFlow(
    agent_url="http://localhost:8080",
    client_id=client_id,
    client_secret=secret,
)
```

### 3. Timeout Configuration

Set appropriate timeouts to prevent resource exhaustion:

```python
client = AxonFlow(
    agent_url=agent_url,
    client_id=client_id,
    client_secret=client_secret,
    timeout=30.0,  # Reasonable timeout in seconds
)
```

### 4. Input Validation

Always validate and sanitize user inputs before sending to AxonFlow:

```python
def process_user_query(user_input: str) -> dict:
    # Validate input length
    if len(user_input) > 10000:
        raise ValueError("input too long")

    # Sanitize input
    sanitized = sanitize_input(user_input)

    # Send to AxonFlow
    result = await client.execute_query("user-token", sanitized, "chat")
    return result
```

### 5. Error Handling

Never expose sensitive information in error messages:

```python
try:
    result = await client.execute_query(token, query, "chat")
except AxonFlowError as e:
    # BAD - Exposes details
    raise Exception(f"query failed with token {token}: {e}")

    # GOOD - Generic error message
    logger.error(f"Query failed: {e}")
    raise Exception("query failed, please try again")
```

### 6. Dependency Management

Keep dependencies up to date:

```bash
# Check for updates
pip list --outdated

# Update dependencies
pip install --upgrade axonflow

# Or with pip-tools
pip-compile --upgrade requirements.in
pip-sync requirements.txt
```

### 7. Production Mode

Use production mode for production deployments to enable fail-open strategy:

```python
client = AxonFlow(
    agent_url=agent_url,
    client_id=client_id,
    client_secret=client_secret,
    mode="production",  # Fail-open if AxonFlow unavailable
)
```

### 8. Debug Mode

**Never** enable debug mode in production:

```python
import os

client = AxonFlow(
    agent_url=agent_url,
    client_id=client_id,
    client_secret=client_secret,
    debug=os.environ.get("ENV") != "production",  # Only in dev
)
```

## Known Security Considerations

### 1. Client Credentials

Client credentials (`client_id` and `client_secret`) provide access to your AxonFlow account. Treat them like passwords:

- Store in environment variables or secure vaults
- Rotate regularly
- Never commit to version control
- Use different credentials for development and production

### 2. User Tokens

User tokens identify end-users in your application. Ensure:

- Tokens are unique per user
- Tokens are properly authenticated before use
- Tokens don't contain sensitive information
- Tokens are transmitted securely

### 3. Caching

The SDK's caching feature stores responses in memory:

- Cache is per-instance (not shared across processes)
- Cache entries expire based on TTL
- Sensitive data in cache is not encrypted
- Consider disabling cache for highly sensitive operations

### 4. Retry Logic

The SDK's retry logic will retry failed requests:

- Retries use exponential backoff
- Failed requests may be logged
- Consider disabling retries for non-idempotent operations

## Security Updates

We'll announce security updates through:

1. GitHub Security Advisories
2. Email notifications to package consumers (if possible via PyPI)
3. Release notes in GitHub releases

To receive security updates:

- Watch this repository for releases
- Subscribe to security advisories
- Check https://pypi.org/project/axonflow/ regularly

## Questions?

If you have questions about this security policy, please email security@getaxonflow.com.

Thank you for helping keep AxonFlow and our users safe!
