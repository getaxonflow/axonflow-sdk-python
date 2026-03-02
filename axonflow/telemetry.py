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


def _is_telemetry_enabled(
    mode: str,
    telemetry_enabled: bool | None,
) -> bool:
    """Determine whether telemetry should fire.

    Priority (highest to lowest):
    1. ``DO_NOT_TRACK=1`` environment variable  -> disabled
    2. ``AXONFLOW_TELEMETRY=off`` environment variable -> disabled
    3. Explicit config value (``telemetry_enabled``) -> use that
    4. Mode-based default: OFF for sandbox, ON for production
    """
    # Environment-level opt-out always wins.
    if os.environ.get("DO_NOT_TRACK", "").strip() == "1":
        return False
    if os.environ.get("AXONFLOW_TELEMETRY", "").strip().lower() == "off":
        return False

    # Explicit config override.
    if telemetry_enabled is not None:
        return telemetry_enabled

    # Default: off for sandbox, on for production.
    return mode != "sandbox"


def _build_payload(mode: str) -> dict:
    """Build the JSON payload for the checkpoint ping."""
    return {
        "sdk": "python",
        "sdk_version": _SDK_VERSION,
        "platform_version": None,
        "os": platform.system(),
        "arch": platform.machine(),
        "runtime_version": platform.python_version(),
        "deployment_mode": mode,
        "features": [],
        "instance_id": str(uuid.uuid4()),
    }


def _do_ping(url: str, payload: dict, debug: bool) -> None:
    """Execute the HTTP POST (runs inside a daemon thread)."""
    try:
        resp = httpx.post(url, json=payload, timeout=_TIMEOUT_SECONDS)
        if resp.status_code == 200:
            try:
                body = resp.json()
            except Exception:
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
    except Exception:
        # Silent failure -- never disrupt the caller.
        if debug:
            logger.debug("Telemetry ping failed (non-fatal)", exc_info=True)


def send_telemetry_ping(
    mode: str,
    endpoint: str,
    telemetry_enabled: bool | None,
    debug: bool = False,
) -> None:
    """Fire-and-forget telemetry ping. Runs in a daemon thread.

    Args:
        mode: SDK operation mode (``"production"`` or ``"sandbox"``).
        endpoint: The AxonFlow agent endpoint (unused in the ping itself but
            kept for future use / consistency with other SDK implementations).
        telemetry_enabled: Explicit config override.  ``None`` means use the
            mode-based default.
        debug: When ``True``, log debug-level messages about the ping.
    """
    if not _is_telemetry_enabled(mode, telemetry_enabled):
        return

    url = os.environ.get("AXONFLOW_CHECKPOINT_URL", "").strip() or _DEFAULT_CHECKPOINT_URL
    payload = _build_payload(mode)

    t = threading.Thread(target=_do_ping, args=(url, payload, debug), daemon=True)
    t.start()
