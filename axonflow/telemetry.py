"""SDK telemetry: fire-and-forget checkpoint ping on client init.

Collects anonymous, non-PII usage data (SDK version, OS, architecture) and
sends it to the AxonFlow checkpoint service. The response may include the
latest available SDK version so we can warn about outdated installs.

Opt-out:
    Set ``DO_NOT_TRACK=1`` or ``AXONFLOW_TELEMETRY=off`` in your environment.

Override endpoint:
    Set ``AXONFLOW_CHECKPOINT_URL`` to a custom URL.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import platform
import threading
import uuid
from urllib.parse import urlparse

import httpx

from axonflow._version import __version__ as _SDK_VERSION

logger = logging.getLogger(__name__)

_DEFAULT_CHECKPOINT_URL = "https://checkpoint.getaxonflow.com/v1/ping"
_TIMEOUT_SECONDS = 3
_HTTP_OK = 200


def _is_telemetry_enabled(
    mode: str,
    telemetry_enabled: bool | None,
    has_credentials: bool,  # noqa: ARG001  kept for API compat
) -> bool:
    """Determine whether telemetry should fire.

    Priority (highest to lowest):
    1. ``DO_NOT_TRACK=1`` environment variable  -> disabled
    2. ``AXONFLOW_TELEMETRY=off`` environment variable -> disabled
    3. Explicit config value (``telemetry_enabled``) -> use that
    4. Default: ON for all modes except sandbox
    """
    # Environment-level opt-out always wins.
    if os.environ.get("DO_NOT_TRACK", "").strip() == "1":
        return False
    if os.environ.get("AXONFLOW_TELEMETRY", "").strip().lower() == "off":
        return False

    # Explicit config override.
    if telemetry_enabled is not None:
        return telemetry_enabled

    # Default: ON everywhere except sandbox mode.
    return mode != "sandbox"


def _detect_platform_version(endpoint: str) -> str | None:
    """Detect platform version by calling the agent's /health endpoint.

    Returns the version string or None on any failure.
    """
    try:
        resp = httpx.get(f"{endpoint}/health", timeout=2)
        if resp.status_code == _HTTP_OK:
            body = resp.json()
            version = body.get("version")
            if isinstance(version, str) and version:
                return version
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return None


def _classify_endpoint(url: str | None) -> str:
    """Classify the configured AxonFlow endpoint for analytics (#1525).

    Returns one of:
        ``"localhost"``         — localhost, 127.0.0.1, ::1, 0.0.0.0, ``*.localhost``
        ``"private_network"``   — RFC1918 ranges, link-local, ``*.local``,
                                  ``*.internal``, ``*.lan``, ``*.intranet``
        ``"remote"``            — everything else
        ``"unknown"``           — on any parse failure

    The raw URL is never sent — only the classification. See ADR or issue #1525.
    """
    if not url:
        return "unknown"
    try:
        host = urlparse(url).hostname
    except (ValueError, AttributeError):
        return "unknown"
    if not host:
        return "unknown"
    host = host.lower()

    if host in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or host.endswith(".localhost"):
        return "localhost"

    if any(host.endswith(suffix) for suffix in (".local", ".internal", ".lan", ".intranet")):
        return "private_network"

    # Try parsing as an IP address (v4 or v6).
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_loopback:
            return "localhost"
        if ip.is_private or ip.is_link_local:
            return "private_network"
        return "remote"
    except ValueError:
        # Not an IP; treat remaining hostnames as remote.
        return "remote"


def _normalize_arch(arch: str) -> str:
    """Normalize architecture names to match other SDKs."""
    if arch == "aarch64":
        return "arm64"
    if arch == "x86_64":
        return "x64"
    return arch


def _build_payload(
    mode: str,
    platform_version: str | None = None,
    endpoint_type: str = "unknown",
) -> dict[str, object]:
    """Build the JSON payload for the checkpoint ping."""
    return {
        "sdk": "python",
        "sdk_version": _SDK_VERSION,
        "platform_version": platform_version,
        "os": platform.system().lower(),
        "arch": _normalize_arch(platform.machine()),
        "runtime_version": platform.python_version(),
        "deployment_mode": mode,
        "endpoint_type": endpoint_type,
        "features": [],
        "instance_id": str(uuid.uuid4()),
    }


def _do_ping(url: str, mode: str, endpoint: str, debug: bool) -> None:
    """Execute the HTTP POST (runs inside a daemon thread)."""
    try:
        platform_version = _detect_platform_version(endpoint) if endpoint else None
        endpoint_type = _classify_endpoint(endpoint)
        payload = _build_payload(mode, platform_version, endpoint_type)
        resp = httpx.post(url, json=payload, timeout=_TIMEOUT_SECONDS)
        if resp.status_code == _HTTP_OK:
            try:
                body = resp.json()
            except (ValueError, KeyError, TypeError, AttributeError):
                return
            latest = body.get("latest_version")
            if latest and latest != _SDK_VERSION:
                logger.warning(
                    "A newer AxonFlow Python SDK is available: %s (current: %s). "
                    "Upgrade with: pip install --upgrade axonflow",
                    latest,
                    _SDK_VERSION,
                )
            if debug:
                logger.debug("Telemetry ping successful: %s", body)
    except (httpx.HTTPError, OSError, ValueError, TypeError, AttributeError):
        # Silent failure -- never disrupt the caller.
        if debug:
            logger.debug("Telemetry ping failed (non-fatal)", exc_info=True)


def send_telemetry_ping(
    mode: str,
    endpoint: str,
    telemetry_enabled: bool | None,
    has_credentials: bool = False,
    debug: bool = False,
) -> None:
    """Fire-and-forget telemetry ping. Runs in a daemon thread.

    Args:
        mode: SDK operation mode (``"production"`` or ``"sandbox"``).
        endpoint: The AxonFlow agent endpoint, used to detect the platform
            version via ``/health``.
        telemetry_enabled: Explicit config override.  ``None`` means use the
            mode-based default.
        has_credentials: Whether the client was initialized with credentials
            (clientId + clientSecret). Used to distinguish managed cloud from
            self-hosted/community deployments for the default behavior.
        debug: When ``True``, log debug-level messages about the ping.
    """
    if not _is_telemetry_enabled(mode, telemetry_enabled, has_credentials):
        return

    logger.info(
        "AxonFlow: anonymous telemetry enabled. "
        "Opt out: AXONFLOW_TELEMETRY=off | https://docs.getaxonflow.com/docs/telemetry"
    )

    url = os.environ.get("AXONFLOW_CHECKPOINT_URL", "").strip() or _DEFAULT_CHECKPOINT_URL

    t = threading.Thread(target=_do_ping, args=(url, mode, endpoint, debug), daemon=True)
    t.start()
