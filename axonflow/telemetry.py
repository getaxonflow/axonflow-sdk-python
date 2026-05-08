"""SDK telemetry: fire-and-forget checkpoint ping on client init.

Collects anonymous, non-PII usage data (SDK version, OS, architecture) and
sends it to the AxonFlow checkpoint service. The response may include the
latest available SDK version so we can warn about outdated installs.

Opt-out:
    Set ``AXONFLOW_TELEMETRY=off`` in your environment.

Override endpoint:
    Set ``AXONFLOW_CHECKPOINT_URL`` to a custom URL.
"""

from __future__ import annotations

import atexit
import contextlib
import ipaddress
import logging
import os
import platform
import threading
import time
import uuid
from urllib.parse import urlparse

import httpx

from axonflow._version import __version__ as _SDK_VERSION

logger = logging.getLogger(__name__)

_DEFAULT_CHECKPOINT_URL = "https://checkpoint.getaxonflow.com/v1/ping"
_TIMEOUT_SECONDS = 3
_HTTP_OK = 200
# Minimum HTTP budget (seconds) — below this, skip the operation rather than
# issue a request that is almost guaranteed to time out before any useful
# work completes. Keeps the telemetry path from making "essentially zero
# budget" calls when the shared deadline is nearly spent.
_MIN_BUDGET_SECONDS = 0.1

# Flush-on-exit bookkeeping: track spawned telemetry threads so we can join
# them on interpreter shutdown. Without this, a `daemon=True` thread is killed
# before its HTTP POST completes in short-lived scripts (CLI one-liners,
# serverless handlers, test harnesses), silently dropping telemetry.
_pending_threads: list[threading.Thread] = []
_pending_threads_lock = threading.Lock()
_atexit_registered = False


def _flush_pending_telemetry() -> None:
    """Join any still-running telemetry threads on interpreter shutdown.

    Bounded by the per-thread HTTP timeout (``_TIMEOUT_SECONDS``), so total
    shutdown delay never exceeds the slowest ping's remaining budget.
    Silent on all errors — telemetry must never disrupt shutdown.
    """
    with _pending_threads_lock:
        threads = list(_pending_threads)
    for t in threads:
        # Shutdown-path: any exception from join() must be swallowed silently
        # to not mask the real shutdown reason with a spurious traceback.
        with contextlib.suppress(Exception):
            t.join(timeout=_TIMEOUT_SECONDS)


def _is_telemetry_enabled() -> bool:
    """Determine whether telemetry should fire.

    ``AXONFLOW_TELEMETRY=off`` in the environment is the SOLE opt-out path.
    Telemetry is otherwise ON by default, regardless of mode (sandbox /
    production / anything else). Sandbox-mode pings are tagged
    ``stream="sandbox"`` in the payload so analytics can still distinguish
    them — see ``_build_payload``.

    Historical context: v7.x supported a ``telemetry_enabled: bool | None``
    config field and a ``mode != "sandbox"`` default-suppression rule.
    Both were removed in v8.0 to leave a single, ops-controlled opt-out
    lever and avoid silent suppression that masks real adoption signal.
    See CHANGELOG v8.0.0.

    ``DO_NOT_TRACK`` is intentionally NOT honored. It is commonly inherited
    from host tools and developer environments (CLIs like Codex and Claude
    Code inject it unconditionally), which makes it an unreliable expression
    of user intent for AxonFlow telemetry.
    """
    return os.environ.get("AXONFLOW_TELEMETRY", "").strip().lower() != "off"


def _detect_platform_version(endpoint: str, timeout: float = 2.0) -> str | None:
    """Detect platform version by calling the agent's /health endpoint.

    Returns the version string or None on any failure. The caller passes a
    timeout derived from the shared telemetry deadline so the health probe
    and the checkpoint POST don't stack into a larger combined budget — see
    issue #1692.
    """
    try:
        resp = httpx.get(f"{endpoint}/health", timeout=timeout)
        if resp.status_code == _HTTP_OK:
            body = resp.json()
            version = body.get("version")
            if isinstance(version, str) and version:
                return version
    except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError, AttributeError):
        pass
    return None


# Loopback and any-interface addresses. "0.0.0.0" is intentionally included
# here because it's the canonical bind-all-interfaces address and, in the
# context of an AxonFlow client endpoint, means "talk to localhost".
# noqa: S104 is scoped to the tuple below — this is not a bind operation.
_LOCALHOST_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})  # noqa: S104


def _classify_endpoint(url: str | None) -> str:  # noqa: PLR0911
    """Classify the configured AxonFlow endpoint for analytics (#1525).

    Returns one of:
        ``"localhost"``         — localhost, 127.0.0.1, ::1, 0.0.0.0, ``*.localhost``
        ``"private_network"``   — RFC1918 ranges, link-local, ``*.local``,
                                  ``*.internal``, ``*.lan``, ``*.intranet``
        ``"remote"``            — everything else
        ``"unknown"``           — on any parse failure

    The raw URL is never sent — only the classification. See issue #1525.

    As of v8.0 the legacy ``"community-saas"`` return value is removed —
    deployment topology lives on ``deployment_mode`` (see
    ``_classify_deployment_mode``) per the v1 schema (axonflow-enterprise#2008).
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

    if host in _LOCALHOST_HOSTS or host.endswith(".localhost"):
        return "localhost"

    if any(host.endswith(suffix) for suffix in (".local", ".internal", ".lan", ".intranet")):
        return "private_network"

    # Try parsing as an IP address (v4 or v6).
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Not an IP; treat remaining hostnames as remote.
        return "remote"
    if ip.is_loopback:
        return "localhost"
    if ip.is_private or ip.is_link_local:
        return "private_network"
    return "remote"


def _classify_deployment_mode(url: str | None) -> str:
    """Classify deployment topology for the v1 telemetry schema (#2008).

    Returns one of:
        ``"community_saas"``    — try.getaxonflow.com host or AXONFLOW_TRY=1
        ``"self_hosted"``       — any other reachable endpoint
        ``"unknown"``           — empty / unparseable endpoint

    The classifier deliberately resolves empty/unparseable to ``"unknown"``
    rather than ``"self_hosted"`` to keep the self-hosted bucket clean of
    config gaps. ``AXONFLOW_TRY=1`` is the explicit override path for
    tenants whose endpoint resolves to a custom hostname proxying
    ``try.getaxonflow.com``.
    """
    if os.environ.get("AXONFLOW_TRY") == "1":
        return "community_saas"
    if not url:
        return "unknown"
    try:
        host = urlparse(url).hostname
    except (ValueError, AttributeError):
        return "unknown"
    if not host:
        return "unknown"
    host = host.lower()
    if host == "try.getaxonflow.com" or host.endswith(".try.getaxonflow.com"):
        return "community_saas"
    return "self_hosted"


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
    deployment_mode: str = "unknown",
) -> dict[str, object]:
    """Build the JSON payload for the checkpoint ping.

    v1 telemetry-schema fields (axonflow-enterprise#2008):

    * ``telemetry_type`` — always ``"sdk"`` (discriminator for the
      receiver to route SDK pings vs plugin / platform / synthetic).
    * ``deployment_mode`` — ``self_hosted | community_saas | unknown``,
      derived from the endpoint host plus ``AXONFLOW_TRY=1`` override
      (see ``_classify_deployment_mode``). The ``mode`` parameter is
      kept for legacy callers but no longer drives this dimension.
    * ``profile`` — sourced from ``AXONFLOW_PROFILE``; ``"unknown"``
      when unset. Free-form deployment classifier; analytics only.

    The ``stream`` field classifies the heartbeat sub-stream. Sandbox-mode
    clients emit ``"sandbox"`` so analytics can distinguish dev/test pings
    from production heartbeat without conflating them; production-mode and
    other modes omit the field entirely (we drop None-valued entries before
    JSON-encoding) and the server defaults to ``"heartbeat"``. The
    wire-allowlist is enforced server-side — see checkpoint-service
    ``IsValidIncomingStream``.
    """
    profile = (os.environ.get("AXONFLOW_PROFILE") or "").strip() or "unknown"
    payload: dict[str, object] = {
        "telemetry_type": "sdk",
        "sdk": "python",
        "sdk_version": _SDK_VERSION,
        "platform_version": platform_version,
        "os": platform.system().lower(),
        "arch": _normalize_arch(platform.machine()),
        "runtime_version": platform.python_version(),
        "deployment_mode": deployment_mode,
        "endpoint_type": endpoint_type,
        "features": [],
        "instance_id": str(uuid.uuid4()),
        "profile": profile,
    }
    if mode == "sandbox":
        payload["stream"] = "sandbox"
    return payload


def _send_telemetry_ping_now(url: str, mode: str, endpoint: str, debug: bool) -> bool:
    """Synchronously POST a single telemetry ping.

    Returns ``True`` on HTTP 2xx delivery, ``False`` on any failure (network
    error, timeout, non-2xx response). Runs in the caller's thread — used by
    the heartbeat orchestrator's worker thread, where the boolean return
    drives stamp-on-DELIVERY semantics: only successful POSTs advance the
    stamp file.

    The caller is responsible for the gating decision (whether to send at
    all) — this function does NOT consult ``AXONFLOW_TELEMETRY``,
    ``_is_telemetry_enabled``, the stamp file, or any rate-limit state.

    All HTTP operations share one monotonic deadline so the atexit flush
    handler's ``_TIMEOUT_SECONDS`` budget actually covers the complete
    telemetry path. Previously the /health probe (2s) and the POST
    (``_TIMEOUT_SECONDS``) each had independent timeouts, which meant the
    thread's real worst case was ~5s and the 3s join could return while the
    POST was still in flight — reintroducing the short-lived-process drop
    bug on slow or blackholed endpoints. See issue #1692.
    """
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    try:
        # Health probe uses remaining budget, capped so the POST still has time.
        health_budget = min(1.0, max(0.0, deadline - time.monotonic()))
        platform_version = None
        if endpoint and health_budget > _MIN_BUDGET_SECONDS:
            platform_version = _detect_platform_version(endpoint, timeout=health_budget)
        endpoint_type = _classify_endpoint(endpoint)
        deployment_mode = _classify_deployment_mode(endpoint)
        payload = _build_payload(mode, platform_version, endpoint_type, deployment_mode)

        # POST uses all remaining budget.
        post_budget = max(0.0, deadline - time.monotonic())
        if post_budget < _MIN_BUDGET_SECONDS:
            return False
        resp = httpx.post(url, json=payload, timeout=post_budget)
        if resp.status_code != _HTTP_OK:
            if debug:
                logger.debug("Telemetry ping returned non-2xx: %d", resp.status_code)
            return False
        try:
            body = resp.json()
        except (ValueError, KeyError, TypeError, AttributeError):
            # Body parse failure on a 2xx response is still a successful
            # delivery — the server got the ping, the response decoder is
            # advisory (used only for the version-check warning below).
            return True
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
        return True  # noqa: TRY300 — restructuring as else: would force splitting the try block; the linear flow here is more readable
    except (httpx.HTTPError, OSError, ValueError, TypeError, AttributeError):
        # Silent failure -- never disrupt the caller.
        if debug:
            logger.debug("Telemetry ping failed (non-fatal)", exc_info=True)
        return False


def _do_ping(url: str, mode: str, endpoint: str, debug: bool) -> None:
    """Backward-compat wrapper for tests that exercise the legacy fire-and-forget
    code path. Delegates to ``_send_telemetry_ping_now`` and discards the
    boolean. Production code goes through the heartbeat orchestrator
    (``axonflow.heartbeat.maybe_send_heartbeat``) instead of this function.
    """
    _send_telemetry_ping_now(url, mode, endpoint, debug)


def send_telemetry_ping(
    mode: str,
    endpoint: str,
    debug: bool = False,
) -> None:
    """Fire-and-forget telemetry ping. Runs in a daemon thread.

    Args:
        mode: SDK operation mode (``"production"`` or ``"sandbox"``).
            Sandbox-mode pings fire on the same schedule as production-mode
            pings as of v8.0; the payload is tagged ``stream="sandbox"`` so
            analytics can distinguish them server-side.
        endpoint: The AxonFlow agent endpoint, used to detect the platform
            version via ``/health``.
        debug: When ``True``, log debug-level messages about the ping.

    Note:
        ``AXONFLOW_TELEMETRY=off`` is the SOLE opt-out path. The v7.x
        ``telemetry_enabled`` parameter and ``has_credentials`` parameter
        were removed in v8.0 — see CHANGELOG.
    """
    if not _is_telemetry_enabled():
        return

    logger.info(
        "AxonFlow: anonymous telemetry enabled. "
        "Opt out: AXONFLOW_TELEMETRY=off | https://docs.getaxonflow.com/docs/telemetry"
    )

    url = os.environ.get("AXONFLOW_CHECKPOINT_URL", "").strip() or _DEFAULT_CHECKPOINT_URL

    t = threading.Thread(target=_do_ping, args=(url, mode, endpoint, debug), daemon=True)
    t.start()

    # Register the thread for on-exit flush, and register the atexit handler
    # once per process. Without this, short-lived processes (CLI scripts,
    # serverless, quickstart one-liners) exit before the POST completes and
    # the ping is silently dropped. See issue #1692.
    global _atexit_registered  # noqa: PLW0603  one-shot module-level registration flag
    with _pending_threads_lock:
        # Prune completed threads so the list stays bounded in long-lived
        # processes that instantiate many clients (e.g. per-request handlers).
        _pending_threads[:] = [pt for pt in _pending_threads if pt.is_alive()]
        _pending_threads.append(t)
        if not _atexit_registered:
            atexit.register(_flush_pending_telemetry)
            _atexit_registered = True
