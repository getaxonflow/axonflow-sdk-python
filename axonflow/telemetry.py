"""SDK telemetry: fire-and-forget checkpoint ping on the client's first request.

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
from typing import NamedTuple, cast
from urllib.parse import urlparse

import httpx

from axonflow._version import __version__ as _SDK_VERSION

logger = logging.getLogger(__name__)

_DEFAULT_CHECKPOINT_URL = "https://checkpoint.getaxonflow.com/v1/ping"
_TIMEOUT_SECONDS = 3
_HTTP_OK = 200
#: Boundaries of the HTTP status classes this module distinguishes. Named
#: rather than inlined so the two predicates below read as one decision each.
_HTTP_REDIRECT_MIN = 300
_HTTP_REDIRECT_MAX_EXCLUSIVE = 400


def _is_success(status_code: int) -> bool:
    """True for any 2xx.

    A RANGE, not ``== 200``. Every sibling SDK treats any 2xx as delivery
    (Go ``StatusCode < 300``, Rust ``status().is_success()``, TypeScript
    ``response.ok``, Java ``isSuccessful()``); Python alone compared against
    200 exactly, so a checkpoint answering 202 read as a failure and the same
    ping was retried at every gate run forever with the stamp never advancing.
    """
    return _HTTP_OK <= status_code < _HTTP_REDIRECT_MIN


def _is_redirect(status_code: int) -> bool:
    """True for any 3xx.

    Distinguished from an ordinary non-2xx so a REFUSED REDIRECT is
    observable. It is the one failure on this path that would otherwise look
    like success: a client that follows a 301/302/303 does not re-POST, it
    converts the request to a bodyless GET, so a followed redirect yields a
    200 for a ping that carried nothing.
    """
    return _HTTP_REDIRECT_MIN <= status_code < _HTTP_REDIRECT_MAX_EXCLUSIVE


#: Bounds every value this SDK puts on the telemetry wire that it did not
#: author itself — every string promoted out of a ``/health`` response, and
#: every adapter name handed to :func:`register_adapter`.
#:
#: WHY A DROP AND NOT A TRUNCATION. The checkpoint refuses a request body over
#: 64 KiB. A single 70 KB value from a hostile or broken ``/health`` therefore
#: produces a ping that is rejected WHOLE — the version, the tier, the org id,
#: every dimension lost, not just the oversized one — and because the stamp is
#: only written on a 2xx, the SDK retries that same doomed request at every
#: gate run for as long as ``/health`` keeps answering that way. Dropping the
#: offending value alone keeps the ping under the limit and preserves every
#: other dimension.
#:
#: It is dropped rather than truncated because a truncated value is a value
#: nobody reported: 64 bytes of a 70 KB string is not a licence tier and not an
#: adapter name, and relaying it would put a fabricated observation on the
#: wire. Absent is the honest answer, and this path already has a well-defined
#: meaning for absent.
#:
#: BYTES, NOT CHARACTERS — and in Python that distinction has to be written
#: out, because ``len(s)`` counts code points. Every check against this bound
#: uses ``len(s.encode("utf-8"))``. The bound is bytes because the thing being
#: bounded, the serialized request body, is bytes: 33 accented characters are
#: 33 code points and 66 bytes.
#:
#: Same bound the receiver applies to these coarse enums
#: (checkpoint-service ``MaxCoarseEnumValueBytes``), and ~3.5x the longest
#: legitimate value.
_MAX_RELAYED_VALUE_BYTES = 64

#: Bounds on the ``features`` array itself, mirroring the receiver's own
#: ``MaxFeatures`` / ``MaxFeatureBytes``. Applying them client-side means an
#: over-long array is shaped HERE, where the SDK still knows what it dropped,
#: rather than silently at ingest.
#:
#: READ WHAT THESE TWO ACTUALLY REACH. The entry cap is live: register 33
#: adapters and the 33rd does not reach the wire. The byte cap is a BACKSTOP
#: that today's only producer cannot trigger — :func:`register_adapter`
#: already refuses a name over ``_MAX_RELAYED_VALUE_BYTES``, so the longest
#: entry it can emit is ``len("adapter:") + 64 == 72`` bytes. It is tested
#: directly on :func:`_bound_features` rather than through the registry,
#: because a test driven through the registry could not express it.
_MAX_FEATURES = 32
_MAX_FEATURE_BYTES = 128

#: Marks a ``features[]`` entry as an adapter identifier. The vocabulary is
#: SERVER-DEFINED (checkpoint-service ``FeatureAdapterPrefix``) and is not
#: this SDK's to extend.
_FEATURE_ADAPTER_PREFIX = "adapter:"
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


def _byte_len(value: str) -> int:
    """Length of ``value`` in UTF-8 BYTES.

    Spelled as its own function so every bound in this module is measured the
    same way and no call site can quietly fall back to ``len()``, which counts
    code points. The two disagree for any non-ASCII input, and the wire is
    bytes.
    """
    return len(value.encode("utf-8"))


# --------------------------------------------------------------------------
# The adapter registry — the ONLY producer of ``features`` entries.
# --------------------------------------------------------------------------

# A set, so a framework that registers on every wrapper construction — the
# ordinary case for an adapter whose constructor runs per request — declares
# itself once on the wire rather than N times. Guarded by a lock because
# registration can race a heartbeat thread reading it.
_adapter_registry: set[str] = set()
_adapter_registry_lock = threading.Lock()


def register_adapter(name: str) -> None:
    """Declare that a framework adapter is driving this SDK.

    The next telemetry heartbeat carries ``adapter:<name>`` in its
    ``features`` array. A framework adapter (LangChain, LangGraph, LiteLLM,
    …) wrapping this SDK is indistinguishable from bare SDK use on every
    other telemetry dimension — same ``sdk``, same ``sdk_version``, same
    endpoint. This is the one call that makes the difference visible, and it
    is adoption signal only.

    IT ADDS NO REQUEST. The name rides the ``features`` array of the heartbeat
    that already fires; there is no second ping, no second endpoint and no new
    configuration surface. Calling this does not itself send anything.

    Idempotent and thread-safe. Call it once at import time; calling it per
    request is harmless but pointless, since the set already deduplicates.

    THE NAME IS NOT VALIDATED AGAINST A LIST, DELIBERATELY. The canonical
    vocabulary lives on the receiver (checkpoint-service
    ``NormalizeAdapterFeature``, which folds an unrecognised name into
    ``adapter:unknown`` at READ time while keeping the raw name on the row).
    An allowlist here would be a second vocabulary that drifts from the first:
    a name this SDK build predates would be dropped at the client instead of
    arriving and rendering as "someone is using an adapter we do not know
    about" — precisely the signal the unknown bucket exists to preserve.

    So the only transformations are the two the receiver also applies before
    matching: strip surrounding whitespace, and lowercase. What is refused is
    refused for a reason that is not about vocabulary:

    * a name empty after stripping — there is nothing to declare, and
      ``adapter:`` alone is not an identifier;
    * a name longer than :data:`_MAX_RELAYED_VALUE_BYTES` — dropped WHOLE,
      never truncated, for the reason recorded on that constant.

    Both refusals are silent, and a non-string argument is refused the same
    way: this is a telemetry declaration on a fire-and-forget path, and
    raising would invite a caller to fail their own startup over an analytics
    detail.

    Args:
        name: The adapter's own name, e.g. ``"langchain"``.
    """
    # A RUNTIME type check, despite the `str` annotation, and the cast is what
    # makes it survive mypy rather than a silencing comment.
    #
    # Python's annotations are erased at runtime and this is a fire-and-forget
    # telemetry call reachable from untyped user code, so `register_adapter(None)`
    # is a real thing a caller can do. Without the guard it raises
    # AttributeError out of a telemetry helper that documents refusing bad input
    # silently — telemetry must never disrupt the caller. Coercing with
    # `str(name)` would be worse still: it would put the literal text `None` on
    # the wire as an adapter name.
    #
    # `cast(object, name)` widens the static type so the isinstance is genuinely
    # informative to mypy instead of provably-unreachable. The public signature
    # stays `str`, so a typed caller still gets the error at their call site.
    if not isinstance(cast("object", name), str):
        return
    normalized = name.strip().lower()
    if not normalized or _byte_len(normalized) > _MAX_RELAYED_VALUE_BYTES:
        return
    with _adapter_registry_lock:
        _adapter_registry.add(normalized)


def _bound_features(features: list[str]) -> list[str]:
    """Apply the receiver's array bounds: at most :data:`_MAX_FEATURES`
    entries, none over :data:`_MAX_FEATURE_BYTES` bytes.

    An over-long entry is DROPPED rather than truncated, which is where this
    deliberately differs from the receiver's own ``BoundFeatures``. The
    receiver truncates because it is defending storage against arbitrary
    clients and a truncated entry harmlessly folds into its unknown bucket.
    Here the entry is something this process declared about itself, and a
    truncated adapter name is a name nothing is running.
    """
    out: list[str] = []
    for feature in features:
        if _byte_len(feature) > _MAX_FEATURE_BYTES:
            continue
        out.append(feature)
        if len(out) == _MAX_FEATURES:
            break
    return out


def _registered_features() -> list[str]:
    """Render the registry as the ``features`` array for one ping.

    Sorted so the wire is deterministic — two processes that registered the
    same adapters in a different order produce the same array, which is what
    lets a test assert on the whole field, and what makes "which 32 survive"
    a defined answer rather than a set-iteration accident.
    """
    with _adapter_registry_lock:
        names = sorted(_adapter_registry)
    return _bound_features([_FEATURE_ADAPTER_PREFIX + name for name in names])


def _reset_adapter_registry_for_test() -> set[str]:
    """Test helper: empty the registry and return what was there so the
    caller can restore it. The registry is process-global by design, so a
    test that registers an adapter would otherwise leak it into every later
    test's ping. Production code does not call this.
    """
    global _adapter_registry  # noqa: PLW0603 — test-only mutator
    with _adapter_registry_lock:
        previous = _adapter_registry
        _adapter_registry = set()
    return previous


def _restore_adapter_registry_for_test(previous: set[str]) -> None:
    """Test helper: restore a registry saved by
    :func:`_reset_adapter_registry_for_test`.
    """
    global _adapter_registry  # noqa: PLW0603 — test-only mutator
    with _adapter_registry_lock:
        _adapter_registry = previous


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


class PlatformHealthProbe(NamedTuple):
    """What a single ``/health`` fetch established.

    Each field is INDEPENDENT: a response carrying one but not another
    yields a partially-populated result rather than discarding all of them.
    ``None`` means "not learned" and is omitted from the wire — it never
    degrades to a default, an empty string, or a JSON ``null``. See
    ``_build_payload`` for why that distinction is load-bearing.

    TRUST BOUNDARY. These values are whatever is answering at the endpoint the
    caller configured. The SDK derives nothing from them, verifies nothing
    about them, and the receiver cannot verify the relay either. They are
    adoption analytics; they must never gate entitlement, unlock a feature, or
    enter an authorization or billing decision.
    """

    platform_version: str | None
    license_tier: str | None
    #: ``/health`` → ``edition``. The BUILD the platform is running,
    #: ``community`` or ``enterprise``. Added platform-side by
    #: axonflow-enterprise#3660; absent against any platform that predates it,
    #: which is exactly what "omitted when not learned" already handles.
    #:
    #: NOT derivable from anything else here: the Community-SaaS fleet runs
    #: the ENTERPRISE build against the community-saas schema, so neither the
    #: topology nor the licence tier implies it.
    edition: str | None = None
    #: ``/health`` → ``deployment_mode``, relayed on the wire as
    #: ``platform_deployment_mode``.
    #:
    #: READ THE NAMES CAREFULLY — THIS IS THE TRAP THIS CONTRACT IS MOST LIKELY
    #: TO BE GOT WRONG ON. The ``/health`` member is called ``deployment_mode``
    #: because there the platform is describing ITSELF. On the ping,
    #: ``deployment_mode`` already means something else entirely: the TOPOLOGY
    #: bucket this SDK derives from the endpoint URL it was configured with.
    #: They are different dimensions, and mapping ``/health``'s member onto the
    #: topology field would overwrite a value every existing dashboard reads.
    platform_deployment_mode: str | None = None


_EMPTY_HEALTH_PROBE = PlatformHealthProbe(
    platform_version=None,
    license_tier=None,
    edition=None,
    platform_deployment_mode=None,
)


def _learned(body: dict[str, object], key: str) -> str | None:
    """Promote one ``/health`` member to a relayable value, or ``None``.

    Learned only when the member is present, is a string, is non-empty, and
    is within :data:`_MAX_RELAYED_VALUE_BYTES`. An absent key, a non-string
    value, an explicit ``""`` and an over-long string are all NOT LEARNED —
    the field stays ``None`` rather than becoming a value the platform did not
    report.

    A non-string is refused rather than coerced: ``{"tier": 42}`` becoming
    ``"42"`` would land in the receiver's unknown bucket as though the
    platform had reported a tier. Absent is the honest answer.
    """
    value = body.get(key)
    if not isinstance(value, str) or not value:
        return None
    if _byte_len(value) > _MAX_RELAYED_VALUE_BYTES:
        # The VALUE is deliberately not logged: it is remote-controlled text,
        # and the diagnostic exists to say which field was dropped and why.
        logger.debug(
            "Telemetry: /health field %r exceeded %d bytes (%d bytes); omitted",
            key,
            _MAX_RELAYED_VALUE_BYTES,
            _byte_len(value),
        )
        return None
    return value


def _probe_platform_health(endpoint: str, timeout: float = 2.0) -> PlatformHealthProbe:
    """Probe the agent's ``/health`` endpoint ONCE for every telemetry dimension.

    Returns both fields ``None`` on any failure — unreachable endpoint,
    non-2xx, unparseable body — so telemetry degrades to omitting the fields
    and never fails the ping or raises into the caller.

    This is the SDK's only ``/health`` fetch on the telemetry path. The
    licence tier, the edition and the platform's own deployment mode all ride
    along on the response already being fetched for the version. Adding a
    second request here would double the telemetry path's blocking budget and
    its failure surface — do not.

    The caller passes a timeout derived from the shared telemetry deadline so
    the health probe and the checkpoint POST don't stack into a larger
    combined budget — see issue #1692.
    """
    try:
        # NO REDIRECTS ON THE TELEMETRY PATH, STATED EXPLICITLY.
        #
        # ``follow_redirects=False`` is already httpx's default, so this is a
        # pin rather than a fix — and it is written out precisely because a
        # default is not a decision. A future httpx release, or a maintainer
        # copying this call to a client that sets a different default, would
        # silently change behaviour that matters: a 30x from ``/health``
        # followed silently means every value promoted below would describe
        # the REDIRECT TARGET rather than the endpoint the caller configured.
        # A captive portal, a misconfigured proxy or an http->https hop is
        # enough to make the heartbeat report a platform the user never
        # pointed at.
        #
        # The 30x itself then fails the status check below and yields an empty
        # probe — "not learned", the honest answer.
        resp = httpx.get(f"{endpoint}/health", timeout=timeout, follow_redirects=False)
        if resp.status_code != _HTTP_OK:
            if _is_redirect(resp.status_code):
                # Named separately from an ordinary non-2xx so a refused
                # redirect is OBSERVABLE rather than silent. The Location
                # value is deliberately NOT logged: it is remote-controlled
                # text, and the diagnostic only needs to say what was refused.
                logger.debug(
                    "Telemetry: /health answered %d (a redirect); refused, relayed fields omitted",
                    resp.status_code,
                )
            return _EMPTY_HEALTH_PROBE
        body = resp.json()
        if not isinstance(body, dict):
            # Belt-and-braces. The broad except below already covers this
            # shape (a list would raise AttributeError on .get), so no test
            # can distinguish this guard's presence — it is here to make the
            # not-an-object case an explicit cheap return rather than an
            # exception, not because a test pins it.
            return _EMPTY_HEALTH_PROBE

        # Each field is promoted independently, through ONE helper. Four
        # copies of the same two conditions is the shape that gets found in
        # production by the field the bound was not applied to.
        return PlatformHealthProbe(
            platform_version=_learned(body, "version"),
            # Verbatim, including the transient "starting" the agent returns
            # before its licence is validated. "starting" is a real signal
            # the receiver buckets deliberately, not an error to filter
            # client-side.
            license_tier=_learned(body, "tier"),
            edition=_learned(body, "edition"),
            # NOTE THE NAME CHANGE, AND THAT IT IS DELIBERATE. The /health
            # member is `deployment_mode` (the platform describing itself);
            # the wire field is `platform_deployment_mode`. This SDK's OWN
            # `deployment_mode` is a different dimension — the topology it
            # derives from its endpoint URL — and promoting /health's member
            # into it would overwrite a value every existing dashboard reads.
            platform_deployment_mode=_learned(body, "deployment_mode"),
        )
    except Exception:  # noqa: BLE001 — see below; a probe must never raise
        # Deliberately broad. This is a best-effort probe on the telemetry
        # path, whose overriding constraint is that telemetry never disrupts
        # the caller — there is no exception from it worth propagating.
        #
        # An explicit tuple was a fail-CLOSED trap here: httpx.InvalidURL does
        # NOT subclass httpx.HTTPError, so a malformed endpoint (e.g. the
        # unclosed-bracket typo "http://[::1") escaped the old explicit
        # tuple of HTTPError, OSError, ValueError, KeyError, TypeError and
        # AttributeError, and raised out of _send_telemetry_ping_now, which documents
        # that it returns False on any failure. Enumerating exception types
        # here is whack-a-mole against a third-party hierarchy; catching
        # everything is the property we actually want.
        return _EMPTY_HEALTH_PROBE


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


#: Sentinel emitted on the telemetry wire when ``ORG_ID`` is unset (the
#: default-config Community-mode developer case). See #2277.
ORG_ID_LOCAL_DEV_SENTINEL = "local-dev-org"


def _telemetry_org_id() -> str:
    """Return the ``org_id`` value to emit on the next telemetry ping.

    Reads ``ORG_ID`` from the environment (the operator's explicit
    configuration for self-hosted deployments, or the ``cs_<uuid>``
    tenant identifier on Community SaaS) and falls back to
    :data:`ORG_ID_LOCAL_DEV_SENTINEL` when unset. Always returns a
    non-empty string. See #2277.
    """
    value = os.environ.get("ORG_ID", "")
    if value:
        return value
    return ORG_ID_LOCAL_DEV_SENTINEL


def _build_payload(
    mode: str,
    platform_version: str | None = None,
    endpoint_type: str = "unknown",
    deployment_mode: str = "unknown",
    license_tier: str | None = None,
    edition: str | None = None,
    platform_deployment_mode: str | None = None,
) -> dict[str, object]:
    """Build the JSON payload for the checkpoint ping.

    v1 telemetry-schema fields (axonflow-enterprise#2008):

    * ``telemetry_type`` — always ``"sdk"`` (discriminator for the
      receiver to route SDK pings vs plugin / platform / synthetic).
    * ``deployment_mode`` — ``self_hosted | community_saas | unknown``,
      derived from the endpoint host plus ``AXONFLOW_TRY=1`` override
      (see ``_classify_deployment_mode``). The ``mode`` parameter is
      kept for legacy callers but no longer drives this dimension.

    The ``stream`` field classifies the heartbeat sub-stream. Sandbox-mode
    clients emit ``"sandbox"`` so analytics can distinguish dev/test pings
    from production heartbeat without conflating them; production-mode and
    other modes omit the field entirely (we drop None-valued entries before
    JSON-encoding) and the server defaults to ``"heartbeat"``. The
    wire-allowlist is enforced server-side — see checkpoint-service
    ``IsValidIncomingStream``.

    ``license_tier`` is the licence tier the connected platform reported on
    its own ``/health`` response — ``"community"``, ``"evaluation"``,
    ``"Enterprise"``, the csaas ``"Plus"`` alias for EnterprisePlus, or the
    transient ``"starting"``. Coarse adoption signal only: no licence key,
    no expiry, no seat count, no customer name. Issue #3619.

    THREE SIMILARLY-NAMED CONCEPTS LIVE NEARBY. Do not merge them:

    1. ``deployment_mode`` — SDK-derived TOPOLOGY:
       ``self_hosted | community_saas | unknown``, classified from the
       endpoint URL. Says WHERE the platform runs.
    2. The platform's own ``DEPLOYMENT_MODE`` env var — a server-side
       setting deciding which schema/tables the binary uses. Never read by
       this SDK and never sent on this field.
    3. ``license_tier`` — what the platform REPORTED about its own
       licensing, for adoption analytics.

    ITEM 3 IS NOT AN ENTITLEMENT FACT. This SDK relays whatever ``/health``
    returned, and the receiver cannot verify the relay: whoever operates the
    endpoint the client was pointed at controls the value completely. It must
    never gate entitlement, unlock a feature, or enter any authorization or
    billing decision. See axonflow-enterprise#3619.

    A community-mode binary can run on any topology and vice versa, so
    neither field is derivable from the other.

    The tier is sent verbatim. Casing and alias folding is the receiver's
    job (checkpoint-service ``NormalizeLicenseTier``) and is deliberately
    NOT duplicated here — a client that folded locally would silently mask a
    tier this SDK build predates.

    ``None`` OMITS the key entirely, which is what "not learned" means on
    this wire. Absent must never become a known value: emitting
    ``"community"`` for a platform we could not reach would be a false claim
    about a customer's deployment. Note this differs from
    ``platform_version``, which has always been sent as an explicit ``null``
    — that is its long-standing wire shape and is left unchanged.
    """
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
        # The adapter registry is the ONLY producer of this array. Read here
        # rather than snapshotted at import so an adapter that registers after
        # the first client is built still reaches the next heartbeat.
        "features": _registered_features(),
        "instance_id": str(uuid.uuid4()),
        "org_id": _telemetry_org_id(),
    }
    if mode == "sandbox":
        payload["stream"] = "sandbox"
    # Keys inserted ONLY when the value was learned. Setting one to None would
    # serialize as JSON null, which is a claim ("the tier is nothing") rather
    # than an omission ("we do not know the tier"). Presence is has(key), not
    # truthiness — and "" never reaches here, because _learned refuses it.
    if license_tier is not None:
        payload["license_tier"] = license_tier
    # Relayed verbatim, omitted when not learned. NOTE that /health's
    # `deployment_mode` member lands on `platform_deployment_mode` here, NOT
    # on `deployment_mode` above, which is the topology this SDK derived from
    # its own endpoint URL. See PlatformHealthProbe.
    if edition is not None:
        payload["edition"] = edition
    if platform_deployment_mode is not None:
        payload["platform_deployment_mode"] = platform_deployment_mode
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
        # One /health fetch supplies both platform_version and license_tier.
        # Re-read on every heartbeat rather than cached for the process
        # lifetime: a licence can be applied to, or expire on, a running
        # platform, and a cached tier would keep reporting the pre-change
        # tier for as long as the client lives.
        probe = _EMPTY_HEALTH_PROBE
        if endpoint and health_budget > _MIN_BUDGET_SECONDS:
            probe = _probe_platform_health(endpoint, timeout=health_budget)
        endpoint_type = _classify_endpoint(endpoint)
        deployment_mode = _classify_deployment_mode(endpoint)
        payload = _build_payload(
            mode,
            probe.platform_version,
            endpoint_type,
            deployment_mode,
            probe.license_tier,
            probe.edition,
            probe.platform_deployment_mode,
        )

        # POST uses all remaining budget.
        post_budget = max(0.0, deadline - time.monotonic())
        if post_budget < _MIN_BUDGET_SECONDS:
            return False
        # NO REDIRECTS, AND HERE IT IS A CORRECTNESS BUG RATHER THAN A PRIVACY
        # ONE. An HTTP client that follows a 301/302/303 does not re-POST: it
        # converts the request to a bodyless GET. A followed redirect on the
        # checkpoint POST would therefore produce a 200 for a request that
        # carried NO PAYLOAD, this function would report delivery, and the
        # caller would write the 7-day stamp — leaving the installation silent
        # for a week on a ping that was never actually sent. A 200 meaning "we
        # delivered nothing" is the worst possible shape for this path,
        # because it is indistinguishable from success at every layer above.
        #
        # As on the probe, False is already httpx's default and is stated
        # explicitly so it is a decision rather than an inherited default.
        resp = httpx.post(url, json=payload, timeout=post_budget, follow_redirects=False)
        # A 2xx RANGE, not `== 200`. Every sibling SDK treats any 2xx as
        # delivery (Go `resp.StatusCode < 300`, Rust `status().is_success()`,
        # TypeScript `response.ok`, Java `isSuccessful()`); Python alone
        # compared against 200 exactly, so a checkpoint answering 202 would
        # have been read as a failure and the same ping retried at every gate
        # run forever, with the stamp never advancing.
        if not _is_success(resp.status_code):
            if _is_redirect(resp.status_code):
                # Observable, and distinct from an ordinary non-2xx: a refused
                # redirect is the one failure here that would otherwise look
                # like success.
                logger.debug(
                    "Telemetry: checkpoint answered %d (a redirect); refused, ping NOT delivered "
                    "and the stamp will not advance",
                    resp.status_code,
                )
            elif debug:
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
    except Exception:  # noqa: BLE001 — telemetry must never disrupt the caller
        # Deliberately broad, same reasoning as _probe_platform_health: this
        # function documents "False on any failure", and an explicit tuple
        # could not honour that. httpx.InvalidURL does not subclass
        # httpx.HTTPError, so a malformed AXONFLOW_CHECKPOINT_URL raised
        # straight through the previous tuple.
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
        "AxonFlow: telemetry enabled. "
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
