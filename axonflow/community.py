"""Legacy registration helper for custom self-hosted Community servers."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

TRY_ENDPOINT = "https://try.getaxonflow.com"
HOSTED_START_URL = "https://portal.try.getaxonflow.com/hosted/start"


def register_try(label: str = "", endpoint: str = TRY_ENDPOINT) -> dict[str, Any]:
    """Register on a custom self-hosted Community server.

    Returns dict with keys: tenant_id, secret, secret_prefix, expires_at, endpoint, note.
    Store the secret securely — it is shown only once.

    New Hosted buyers must verify their email, set up their account and plan,
    then link an API client through the Portal. Existing linked client IDs and
    secrets can be supplied explicitly to the SDK.

    Args:
        label: Optional human-readable name for the registration.
        endpoint: Custom self-hosted Community endpoint. The legacy Hosted
            default raises with Portal setup guidance before any network call.
    """
    if urlsplit(endpoint).hostname in {"try.getaxonflow.com", "try-staging.getaxonflow.com"}:
        msg = (
            f"New Hosted accounts start at the verified Portal {HOSTED_START_URL}: "
            "verify your email, "
            "complete account and plan setup, then link an API client in the Portal. "
            "Configure that linked client ID and secret explicitly in the SDK."
        )
        raise ValueError(msg)

    response = httpx.post(
        f"{endpoint.rstrip('/')}/api/v1/register",
        json={"label": label} if label else {},
        timeout=10.0,
    )
    response.raise_for_status()
    data: dict[str, Any] = response.json()
    return data
