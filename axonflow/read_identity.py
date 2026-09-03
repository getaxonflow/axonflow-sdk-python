"""Read-path per-user identity and the platform's read-scope contract.

Since platform #2922 the role-scoped read routes (audit / decisions /
overrides) answer from the identity the CALLER presents, not from the tenant
credential alone. The tenant credential in ``Authorization`` says which
organization is asking; it does not say WHO. A caller that presents no per-user
identity to an enterprise stack is not "a caller who sees everything" and is
not "a caller who sees nothing by coincidence" — it is a caller the platform
cannot scope, and every scoped read it makes returns zero rows by construction.

This module carries the whole surface:

* the per-user identity itself (``AxonFlow(user_token=...)`` for a client-wide
  identity, the ``user_token=`` keyword on a read for a per-call one), stamped
  as the ``X-User-Token`` header from exactly ONE site — the httpx request
  event hook installed on both of the client's transports. There is no
  per-method header plumbing, deliberately: the platform reads the header once
  in its own proxy middleware (``platform/agent/proxy.go``
  ``proxyAuthMiddleware``), not per route, so a per-method sprinkle here would
  be a second, drifting copy of a decision the platform makes in one place.

* the response side of the same contract: ``X-Axonflow-Read-Scope``, which the
  platform stamps on every scoped read (``platform/orchestrator/read_scope.go``
  ``applyReadScopeHeader``) to say which of the three scopes the answer was
  computed under. Without it, a 404 from explain and an empty list from
  ``list_decisions`` are indistinguishable from "the row is not there", which
  is how a governed read comes to report a confident, vacuous nothing.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Final

import httpx

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterator

from axonflow.exceptions import AxonFlowError

__all__ = [
    "HEADER_READ_SCOPE",
    "HEADER_USER_TOKEN",
    "ReadScope",
    "ReadScopeError",
]

#: The request header carrying the per-user identity.
#:
#: This constant is the SDK's only spelling of it. The header is set in exactly
#: one place (:func:`stamp_read_identity`, installed as an httpx request hook);
#: if you find yourself setting it in a method, the method is the wrong
#: altitude.
HEADER_USER_TOKEN: Final = "X-User-Token"  # noqa: S105 - a header NAME, not a secret

#: The response header the platform stamps on scoped reads.
HEADER_READ_SCOPE: Final = "X-Axonflow-Read-Scope"


class ReadScope(str):
    """The scope the platform computed a role-scoped read under.

    Taken from the ``X-Axonflow-Read-Scope`` response header. A ``str``
    subclass rather than an ``Enum`` for one deliberate reason: a scope value a
    newer platform names and this build does not recognise must round-trip
    verbatim instead of failing to construct or decoding to a neighbour.

    Three named values are the platform's closed set. Two states are NOT in it
    and are deliberately distinct from each other and from the three:

    * :data:`ABSENT` — the response carried no such header. That is what a
      pre-#2922 platform, a non-scoped route, or a proxy that dropped the
      header looks like. It means "not stated", never "none": treating an
      absent header as a scope of ``none`` would turn every older stack's
      perfectly good read into a refusal.

    * any other non-empty string — preserved verbatim so a caller can see what
      it was, and never a trigger for a refusal: this header is the platform's
      account of a decision it has ALREADY made and applied, so an unrecognised
      value is a reporting gap on our side, not a licence to invent an outcome.
    """

    #: No ``X-Axonflow-Read-Scope`` header at all. Distinct from :data:`NONE`.
    ABSENT: ReadScope
    #: Tenant-wide: a tenant-wide role (admin / owner / policy_admin), or a
    #: Community / Community-SaaS deployment where the whole tenant is the one
    #: operator.
    TENANT: ReadScope
    #: Narrowed to the rows attributed to the identity presented. A miss under
    #: this scope means "not yours", which is not "not there".
    OWN_ROWS: ReadScope
    #: The platform RESOLVED no per-user identity and the caller holds no
    #: tenant-wide authority, so it returned zero rows by construction. Under
    #: this scope a read CANNOT have returned data, so its empty answer says
    #: nothing about what exists.
    #:
    #: "Resolved none" is wider than "presented none", and the difference is
    #: worth knowing before you go looking in the wrong place. A token that
    #: validates perfectly still resolves to no identity when its address is
    #: one the platform reserves for SHARED, non-personal identities — the
    #: whole of ``@axonflow.local`` and ``@axonflow.internal``, plus the
    #: community and evaluator addresses. Those name a pool of callers rather
    #: than a person, and scoping a read to one would return the pool, so the
    #: platform deliberately censuses them to nothing. A per-user token minted
    #: with an address in one of those domains therefore reads exactly like no
    #: token at all. (Easy to hit: the platform's own ``generate-jwt.sh``
    #: defaults to ``demo-user@axonflow.local``.)
    NONE: ReadScope


ReadScope.ABSENT = ReadScope("")
ReadScope.TENANT = ReadScope("tenant")
ReadScope.OWN_ROWS = ReadScope("own-rows")
ReadScope.NONE = ReadScope("none")


def read_scope_of(response: httpx.Response | None) -> ReadScope:
    """The scope the platform reported on ``response``.

    A missing response, or one without the header, is :data:`ReadScope.ABSENT`.

    Trimmed and lower-cased, for the same reason the platform's own header
    helpers are: a proxy that normalises header casing or appends whitespace
    must not silently change the answer. The cost of getting that wrong is
    one-sided and quiet — a scope spelled ``None`` would fall to the
    unrecognised branch and the vacuous empty page it describes would come back
    as data again. An unrecognised value is otherwise unchanged, so it still
    round-trips to the caller.
    """
    if response is None:
        return ReadScope.ABSENT
    # `header or ""` would be a falsey-clobber, and an ironic one in this file:
    # the whole point here is that ABSENT and EMPTY are different states, and
    # `or` collapses them. They happen to reach the same answer today (both are
    # ReadScope.ABSENT), which is exactly why the linter is right to flag it —
    # the next member read this way may not be a string.
    raw = response.headers.get(HEADER_READ_SCOPE)
    if raw is None:
        return ReadScope.ABSENT
    return ReadScope(raw.strip().lower())


# --------------------------------------------------------------------------
# Presenting an identity
# --------------------------------------------------------------------------

# The per-call identity override, or None when the call did not set one.
#
# A ContextVar rather than a parameter threaded through every request helper:
# it is what lets ONE mechanism serve every method, including the ones that
# take a filter object and the ones that take none, without each signature
# growing a token argument that the next method would then have to grow too.
# It is also correct under asyncio — each task gets its own copy, so two
# concurrent reads for two different users cannot see each other's identity.
_per_call_identity: ContextVar[str | None] = ContextVar(
    "axonflow_per_call_read_identity", default=None
)


@contextmanager
def use_read_identity(user_token: str | None) -> Iterator[None]:
    """Present ``user_token`` as the identity for reads made inside the block.

    ``None`` means "this call said nothing", and the client-wide identity
    applies. An empty or whitespace-only string is NOT the same thing: it is a
    caller deliberately making the read unidentified, so it clears the
    client-wide identity rather than falling back to it. That distinction has
    to exist, because "unidentified" is a state the platform treats as
    different from every other (see :data:`ReadScope.NONE`).
    """
    token = _per_call_identity.set(user_token)
    try:
        yield
    finally:
        _per_call_identity.reset(token)


def _same_origin(a: httpx.URL, b: httpx.URL) -> bool:
    """Whether two URLs are the same origin: scheme, host and port.

    Port is compared through ``httpx.URL.netloc`` rather than by hand so the
    default-port cases (``http://h`` vs ``http://h:80``) compare equal the way
    httpx itself resolves them. Subdomains are NOT treated as the same origin,
    deliberately: this header is an identity assertion, not a session cookie,
    and "close enough" is not a property an identity should have.
    """
    return a.scheme == b.scheme and a.netloc == b.netloc


def effective_read_identity(client_token: str | None) -> str:
    """The identity a request made right now would actually PRESENT.

    One function rather than the same three lines in two places, because the
    two places are the header stamp and the response-cache key, and they must
    never disagree about who is asking. They did: the stamp resolved the
    per-call override and the key read the client-wide value, so a call made
    inside ``use_read_identity("BOB")`` presented BOB and was served the
    client-wide identity's cached answer — and an explicit empty override, which
    deliberately makes one call unidentified, was served the *identified*
    response. Both are the cross-user leak the key was meant to close, one level
    further in.

    ``None`` from the context var means "this call said nothing", so the
    client-wide value applies. An explicit ``""`` is a caller deliberately
    making one call unidentified and must NOT fall back — "unidentified" is a
    state the platform treats as different from every other (see
    :class:`ReadScope`), and a key that folded it back onto the client-wide
    identity would hand it that identity's rows.
    """
    override = _per_call_identity.get()
    token = client_token if override is None else override
    return (token or "").strip()


def stamp_read_identity(
    client_token: str | None,
    request: httpx.Request,
    endpoint: str | None = None,
) -> None:
    """Stamp the per-user identity on ``request``, if there is one.

    Installed as an httpx request event hook on both of the client's
    transports, so the identity travels on every request without any method
    knowing about it. That is on purpose and mirrors the platform: the agent
    reads ``X-User-Token`` once, in the middleware in front of every proxied
    route, and the routes themselves never look at it.

    **The header is NOT inert on the routes that are not reads.** It is
    validated on every route the agent proxies, which is nearly all of them:
    ``platform/agent/proxy.go`` ``proxyAuthMiddleware`` resolves it before
    dispatch and answers ``401 invalid user token`` for a present-but-INVALID
    one — on ``/api/v1/plans``, ``/api/v1/policies``, ``/api/v1/connectors``,
    ``/api/v1/process``, ``/api/v1/budgets``, ``/api/v1/cost``,
    ``/api/v1/executions`` and the rest, not only on the scoped reads. A
    validated one also overrides the ``X-User-Email`` attribution those writes
    are recorded under. So a stale or rotated ``user_token`` does not degrade
    to "unscoped reads"; it turns ``list_connectors``, ``install_connector``
    and policy CRUD into 401s. Fail-closed is the right direction, but it puts
    the value in the same rotation story as ``client_secret``.

    Genuinely inert only on the routes the agent SERVES ITSELF — only
    ``proxy.go`` and ``mcp_identity.go`` read the header at all. Enumerated
    from the agent router rather than sampled, because replacing one wrong
    census with a shorter wrong census is not a fix::

        /api/request                proxy_llm_call
        /api/v1/decide              decide / decide_and_fulfill — identity here
                                    comes from the request BODY's user_token,
                                    which is the whole reason the read path
                                    needed a surface of its own
        /api/v1/access/evaluation   evaluate (AuthZEN)
        /api/v1/static-policies/*   the system-policy family
        /api/v1/circuit-breaker/*
        /api/v1/hitl/*
        /api/v1/mcp/check-input     pre_check
        /api/v1/mcp/check-output
        /api/v1/register            register (mints the credential)
        /api/policy/pre-check
        /api/audit/llm-call
        /health                     health_check / the telemetry probe

    Everything else this SDK calls is proxied, and therefore validates it.

    **It is never sent anywhere but the configured endpoint.** ``endpoint``
    is compared against the request's origin and the header is dropped when
    they differ. That guard exists for redirects: httpx strips ``Authorization``
    on a cross-origin redirect but its sensitive-header list is fixed and
    ``X-User-Token`` is not on it — and because httpx re-runs request event
    hooks on the redirected request, a hook that stamped unconditionally would
    RE-ADD the per-user credential to a host the caller never named, on exactly
    the hop where the tenant credential is dropped. Measured: the redirected
    request arrived with ``Authorization: None`` and the identity intact.

    The token is a CREDENTIAL. It is written to the header and nowhere else: it
    is never logged (including in debug mode), never carried in an exception
    message, and never reaches telemetry — the heartbeat builds its own request
    with its own client and never passes through here.
    """
    token = effective_read_identity(client_token)
    if token and endpoint and not _same_origin(request.url, httpx.URL(endpoint)):
        # Off-origin: this is a redirect (or a caller-built request) pointing
        # somewhere other than the client's configured endpoint. The identity
        # does not follow.
        token = ""
    if token:
        request.headers[HEADER_USER_TOKEN] = token
    else:
        # Never send an empty header. To the platform a present-but-empty
        # X-User-Token is still an absent one (it strips then tests for empty),
        # but sending it advertises an identity mechanism the caller is not
        # using, and it is one refactor away from a present-but-invalid token,
        # which is a hard 401. The pop also makes an explicit per-call
        # clearing actually clear, rather than leaving the client-wide value
        # that a default header would already have placed here.
        request.headers.pop(HEADER_USER_TOKEN, None)


# --------------------------------------------------------------------------
# Reading the platform's answer honestly
# --------------------------------------------------------------------------


class ReadScopeError(AxonFlowError):
    """A role-scoped read whose answer was decided by the caller's scope.

    It exists because "no rows" and "no identity" are the same bytes on the
    wire. The platform distinguishes them in the ``X-Axonflow-Read-Scope``
    header; this exception is that distinction made visible, so a read that
    could not have succeeded reports a cause instead of a confident nothing.

    Two shapes, told apart by :attr:`identity_missing`:

    * :data:`ReadScope.NONE` — no identity was RESOLVED; the read returned zero
      rows by construction and says nothing about what exists. Remedy: present
      an identity whose address is a real person's — see :data:`ReadScope.NONE`
      for why a valid token can still resolve to nothing.
    * :data:`ReadScope.OWN_ROWS` — an identity WAS resolved, and the row is not
      among the ones attributed to it. That does NOT mean the row exists and
      belongs to somebody else: the platform answers "not attributed to you"
      and "not there at all" with the identical 404, deliberately, so that a
      miss cannot be used to probe for another user's rows. This exception
      therefore reports the scope, not a claim about what exists. Remedy: a
      tenant-wide role (admin / owner / policy_admin) sees the whole tenant.

    The presented token is never included in the message: it is safe to log,
    which is the point of putting the diagnosis in a type rather than in a
    string the caller assembles from the credential.

    It subclasses :class:`~axonflow.exceptions.AxonFlowError`, so callers that
    already catch that keep working — the refusal is a more specific answer to
    the same question, not a new failure mode to route separately.
    """

    def __init__(
        self,
        *,
        scope: ReadScope,
        status_code: int,
        resource: str = "read",
        identifier: str | None = None,
    ) -> None:
        self.scope = scope
        self.status_code = status_code
        self.resource = resource
        self.identifier = identifier
        super().__init__(self._message())

    @property
    def identity_missing(self) -> bool:
        """Whether the read failed because no per-user identity was resolved.

        As opposed to one being resolved and not matching.
        """
        return self.scope == ReadScope.NONE

    def _subject(self) -> str:
        if self.identifier:
            return f'{self.resource} "{self.identifier}"'
        return self.resource

    def _message(self) -> str:
        if self.identity_missing:
            return (
                f"HTTP {self.status_code}: {self._subject()}: the platform resolved no "
                f"per-user identity for this read ({HEADER_READ_SCOPE}: {self.scope}), so it "
                f"returned zero rows by construction and the empty answer says nothing about "
                f"what exists. Either no identity was presented — pass user_token= to the "
                f"AxonFlow constructor or to this call — or the one presented carries an "
                f"address the platform reserves for shared identities (@axonflow.local, "
                f"@axonflow.internal), which resolves to nobody. (platform #2922)"
            )
        return (
            f"HTTP {self.status_code}: {self._subject()} was not found among the rows this "
            f"identity can see: the platform reports {HEADER_READ_SCOPE}: {self.scope}, so the "
            f"read was narrowed to the identity's own rows. It is either not attributed to this "
            f"identity or not there at all — the platform answers both the same way ON PURPOSE, "
            f"so that a miss cannot be used to probe for the existence of another user's rows, "
            f"and this SDK cannot tell them apart either. A tenant-wide role (admin, owner or "
            f"policy_admin) reads the whole tenant. (platform #2922)"
        )


def read_scope_error_for(
    *,
    resource: str,
    identifier: str | None,
    scope: ReadScope,
    status_code: int,
) -> ReadScopeError | None:
    """The typed refusal for a scoped read that came back with nothing.

    ``None`` when the scope does not explain the result: for
    :data:`ReadScope.TENANT` (the caller could see the whole tenant and it
    still was not there — a genuine miss), for :data:`ReadScope.ABSENT` (the
    platform did not state a scope; see :class:`ReadScope` for why absent is
    not none), and for any scope value this build does not recognise (a newer
    platform's; reporting a cause we cannot actually read would be a confident
    wrong diagnosis).
    """
    if scope in (ReadScope.NONE, ReadScope.OWN_ROWS):
        return ReadScopeError(
            scope=scope,
            status_code=status_code,
            resource=resource,
            identifier=identifier,
        )
    return None


def refuse_vacuous_scoped_page(
    response: httpx.Response | None,
    resource: str,
    rows: int,
) -> ReadScopeError | None:
    """The typed refusal for a scoped read that came back EMPTY under a scope
    that could not have returned a row; ``None`` in every other case.

    One helper rather than a check at each read, because "the page is empty and
    the scope is none" is one rule and the reads that need it decode their body
    on more than one path each. A rule copied per return site is a rule that
    ends up applied on some of them.

    The emptiness guard is as load-bearing as the scope guard: a non-empty page
    is never turned into an error, whatever the header says. And only
    :data:`ReadScope.NONE` refuses — an own-rows or tenant-wide read that
    legitimately found nothing is a real answer, and replacing it with an error
    would swap one wrong report for another.
    """
    if rows > 0:
        return None
    if read_scope_of(response) != ReadScope.NONE:
        return None
    return ReadScopeError(
        scope=ReadScope.NONE,
        status_code=response.status_code if response is not None else 0,
        resource=resource,
    )
