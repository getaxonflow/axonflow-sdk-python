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

import logging
import os
import platform
import threading
import uuid

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


def _normalize_arch(arch: str) -> str:
    """Normalize architecture names to match other SDKs."""
    if arch == "aarch64":
        return "arm64"
    if arch == "x86_64":
        return "x64"
    return arch


def _build_payload(mode: str, platform_version: str | None = None) -> dict[str, object]:
    """Build the JSON payload for the checkpoint ping."""
    return {
        "sdk": "python",
        "sdk_version": _SDK_VERSION,
        "platform_version": platform_version,
        "os": platform.system().lower(),
        "arch": _normalize_arch(platform.machine()),
        "runtime_version": platform.python_version(),
        "deployment_mode": mode,
        "features": [],
        "instance_id": str(uuid.uuid4()),
    }


def _do_ping(url: str, mode: str, endpoint: str, debug: bool) -> None:
    """Execute the HTTP POST (runs inside a daemon thread)."""
    try:
        platform_version = _detect_platform_version(endpoint) if endpoint else None
        payload = _build_payload(mode, platform_version)
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
        "Opt out: AXONFLOW_TELEMETRY=off | https://docs.getaxonflow.com/telemetry"
    )

    url = os.environ.get("AXONFLOW_CHECKPOINT_URL", "").strip() or _DEFAULT_CHECKPOINT_URL

    t = threading.Thread(target=_do_ping, args=(url, mode, endpoint, debug), daemon=True)
    t.start()
