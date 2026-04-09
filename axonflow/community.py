"""Community SaaS registration helper for try.getaxonflow.com."""

from __future__ import annotations

from typing import Any

import httpx

TRY_ENDPOINT = "https://try.getaxonflow.com"


def register_try(label: str = "", endpoint: str = TRY_ENDPOINT) -> dict[str, Any]:
    """Register for a free evaluation tenant on try.getaxonflow.com.

    Returns dict with keys: tenant_id, secret, secret_prefix, expires_at, endpoint, note.
    Store the secret securely — it is shown only once.

    Args:
        label: Optional human-readable name for the registration.
        endpoint: Override the default endpoint (for local testing).
    """
    response = httpx.post(
        f"{endpoint}/api/v1/register",
        json={"label": label} if label else {},
        timeout=10.0,
    )
    response.raise_for_status()
    data: dict[str, Any] = response.json()
    return data
