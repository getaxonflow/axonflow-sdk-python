"""AuthZEN-native authorization for the AxonFlow SDK.

This is the surface the ADR-065 compatibility plan commits to in all five SDKs.
It talks to ``POST /api/v1/access/evaluation``, whose wire shape is generated
from the platform's canonical contract (see :mod:`axonflow.authzen_types_gen`);
nothing in this module re-states a field name or an enum value.

What this replaces, and when
---------------------------
Nothing yet. The existing decision surface (``client.decide``,
``client.explain_decision`` and the gateway/proxy methods) stays wire-stable
through all of v11 and is not deprecated here. This is the surface to write NEW
integrations against, because at v11 the engine behind it becomes the ADR-065
Policy Decision Point with no wire change — an integration written against it
migrates once rather than twice. See ``docs/AUTHZEN_MIGRATION_DRAFT.md``.

The one thing worth knowing before you call it
----------------------------------------------
The server refuses anything it cannot evaluate rather than evaluating around
it. Send a subject property, an unrecognised context member, or an argument
beside the query, and you get an :class:`AuthZENRefusal` naming the exact
member — not a decision computed without it. That is deliberate: a decision
that silently ignored an attribute would tell you the attribute was weighed
when it was not, and every audit of that decision would inherit the claim.

So treat an :class:`AuthZENRefusal` as "fix the request", and retry only when
:attr:`AuthZENRefusal.retryable` is true.
"""

from __future__ import annotations

import json

# Imported at RUNTIME rather than under TYPE_CHECKING: the public type aliases
# below are ordinary assignments, so the names they are built from have to exist
# when the module is imported, not only when a checker reads it.
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias, TypeVar

from pydantic import BaseModel, ValidationError

from axonflow.authzen_types_gen import (
    AUTHZEN_CONTRACT_SCHEMA_VERSION,
    AUTHZEN_ERROR_CODE_EVALUATION_UNAVAILABLE,
    AUTHZEN_ERROR_CODE_INCOMPLETE_EVALUATION,
    AUTHZEN_ERROR_CODE_MALFORMED_ENVELOPE,
    AUTHZEN_ERROR_CODE_UNEVALUABLE_ATTRIBUTE,
    AUTHZEN_OPERATIONAL_STATE_ALLOW,
    AUTHZEN_OPERATIONAL_STATE_ERROR,
    AUTHZEN_OPERATIONAL_STATE_VALUES,
    AUTHZEN_PROFILE_V1,
    AuthZENAction,
    AuthZENApprovalRequirement,
    AuthZENBulk,
    AuthZENEnvelope,
    AuthZENError,
    AuthZENErrorCode,
    AuthZENObligation,
    AuthZENOperationalState,
    AuthZENRequest,
    AuthZENResource,
    AuthZENResponse,
    # Imported for its NAME, not to be called: AuthZENDecision inherits the
    # `context: AuthZENResponseContext | None` annotation from a module that
    # uses postponed evaluation, so pydantic resolves that forward reference
    # against THIS module's namespace. Without the name here the subclass is
    # never fully defined and every model_validate on it raises.
    AuthZENResponseContext,  # noqa: F401 - resolves AuthZENDecision's forward ref
    AuthZENSubject,
)
from axonflow.exceptions import AuthenticationError, AxonFlowError

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "AUTHZEN_ATTRIBUTE_MARKER",
    "AUTHZEN_PATH",
    "AUTHZEN_PROFILE_HEADER",
    "AUTHZEN_PROTOCOL_DECISION_STATE_DISAGREEMENT",
    "AUTHZEN_PROTOCOL_MISSING_PROFILE_CONTEXT",
    "AUTHZEN_PROTOCOL_OBLIGATIONS_ON_REFUSAL",
    "AUTHZEN_PROTOCOL_STRUCTURE_TOO_DEEP",
    "AUTHZEN_PROTOCOL_UNDECODABLE_BODY",
    "AUTHZEN_PROTOCOL_UNKNOWN_OPERATIONAL_STATE",
    "AUTHZEN_PROTOCOL_UNRESOLVED_ATTRIBUTE",
    "AUTHZEN_PROTOCOL_UNSPECIFIED",
    "AUTHZEN_PROTOCOL_UNSUPPORTED_PROFILE",
    "AUTHZEN_UNKNOWN_CLOSURE_TRUNCATED",
    "AUTHZEN_UNKNOWN_CLOSURE_UNAVAILABLE",
    "AUTHZEN_UNKNOWN_MALFORMED_VALUE",
    "AUTHZEN_UNKNOWN_NOT_SUPPLIED",
    "AUTHZEN_UNKNOWN_REQUIRED_ABSENT",
    "AUTHZEN_UNKNOWN_RESOLUTION_FAILED",
    "AUTHZEN_UNKNOWN_SCHEMA_MISMATCH",
    "AUTHZEN_UNKNOWN_STALE",
    "AuthZENAttribute",
    "AuthZENAttributeState",
    "AuthZENDecision",
    "AuthZENProtocolError",
    "AuthZENProtocolErrorKind",
    "AuthZENRefusal",
    "AuthZENRefusedBy",
    "AuthZENTransport",
    "build_envelope",
    "to_wire",
]

# The AuthZEN evaluation endpoint.
AUTHZEN_PATH: Final = "/api/v1/access/evaluation"

# How a Policy Enforcement Point negotiates the AxonFlow profile.
#
# The SDK always sends it. AuthZEN 1.0's response is a bare boolean, and the
# four-valued state, the obligations and the approval challenge ride in the
# response context, which the server returns only to a caller that asked for it
# by version. This SDK understands the profile, so there is no reason to ask for
# less than it can read — and a response WITHOUT the context is therefore a
# protocol failure here rather than a decision with no obligations.
AUTHZEN_PROFILE_HEADER: Final = "X-Axonflow-AuthZEN-Profile"


# --------------------------------------------------------------------------
# Why an attribute could not be established.
# --------------------------------------------------------------------------
#
# These mirror ADR-065's tri-state reason codes so an operator reading an SDK
# refusal and an operator reading a platform trace use the same words. They are
# CLIENT-LOCAL: an unknown attribute never reaches the wire, because the whole
# point is that the request is not sent. The reason is free-form on purpose —
# a closed set hand-copied from the platform would be a transcription that
# drifts, and it would buy nothing, since nothing on the far side reads it.
AUTHZEN_UNKNOWN_NOT_SUPPLIED: Final = "attribute_not_supplied"
AUTHZEN_UNKNOWN_RESOLUTION_FAILED: Final = "resolution_failed"
AUTHZEN_UNKNOWN_STALE: Final = "stale"
AUTHZEN_UNKNOWN_SCHEMA_MISMATCH: Final = "schema_mismatch"
AUTHZEN_UNKNOWN_CLOSURE_UNAVAILABLE: Final = "closure_unavailable"
AUTHZEN_UNKNOWN_CLOSURE_TRUNCATED: Final = "closure_truncated"
AUTHZEN_UNKNOWN_MALFORMED_VALUE: Final = "malformed_value"
AUTHZEN_UNKNOWN_REQUIRED_ABSENT: Final = "required_attribute_absent"


# --------------------------------------------------------------------------
# The public type aliases.
# --------------------------------------------------------------------------
#
# Named rather than left inline so a caller can annotate its own handler with
# the same vocabulary the SDK uses, and so the surface matches the sibling
# TypeScript SDK, which exports these three names from its package root.

# Who declined to evaluate: this SDK before sending, or the gateway.
AuthZENRefusedBy: TypeAlias = Literal["client", "gateway"]

# The three states of a tri-state attribute.
AuthZENAttributeState: TypeAlias = Literal["known", "absent", "unknown"]

# The transport ``evaluate_envelope`` runs an envelope through: it is handed a
# path, a JSON body and the profile headers, and answers with the status and the
# raw bytes. The status is left UNINTERPRETED on purpose - on this route a 4xx
# body is a typed refusal document rather than an error string.
AuthZENTransport: TypeAlias = Callable[
    [str, "dict[str, Any]", "dict[str, str]"], Awaitable["tuple[int, bytes]"]
]


class AuthZENRefusal(AxonFlowError):
    """The request was NOT evaluated, and here is the typed reason why.

    A refusal is not a denial. ``decision=false`` says the request WAS
    evaluated and the answer was no; a refusal says no decision exists. Code
    that treats every error as a deny fails closed — which is safe — but will
    block traffic that should have been allowed once the request is corrected.

    :attr:`refused_by` says who made the call. ``"gateway"`` is a refusal
    document the server sent; ``"client"`` is this SDK declining to send a
    request it can already see will not be evaluated — an attribute the caller
    could not resolve, or an evaluation with no subject. The code vocabulary is
    shared because the REASONS are shared: an incomplete evaluation is an
    incomplete evaluation whoever notices it first.
    """

    def __init__(
        self,
        code: AuthZENErrorCode,
        message: str,
        *,
        refused_by: AuthZENRefusedBy,
        pointer: str | None = None,
        supported: list[str] | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(
            message,
            details={
                "code": code,
                "pointer": pointer,
                "supported": supported,
                "request_id": request_id,
                "refused_by": refused_by,
            },
        )
        self.code = code
        self.pointer = pointer
        self.supported = supported
        self.request_id = request_id
        self.refused_by = refused_by

    @classmethod
    def from_body(cls, body: AuthZENError) -> AuthZENRefusal:
        """Build a refusal from the structured document the server sent."""
        return cls(
            body.code,
            body.message,
            refused_by="gateway",
            pointer=body.pointer,
            supported=body.supported,
            request_id=body.request_id,
        )

    @property
    def retryable(self) -> bool:
        """Whether sending the same request again could give a different answer.

        Only a dependency failure the GATEWAY reported is. Every other code
        names something about the request itself, which will not change on a
        retry — so a client that retries on any refusal burns its budget on
        requests that cannot succeed.

        A client-side refusal is never retryable, whatever its code: this SDK
        does not resolve the caller's attributes, so nothing it can do will
        change the answer. Reading retryability off the code alone would have
        told a caller to retry an attribute its own resolver failed to produce.
        """
        return (
            self.refused_by == "gateway" and self.code == AUTHZEN_ERROR_CODE_EVALUATION_UNAVAILABLE
        )

    def __str__(self) -> str:
        where = f" at {self.pointer}" if self.pointer else ""
        return f"axonflow: {self.code}{where}: {self.message}"


# --------------------------------------------------------------------------
# Which protocol failure this is.
# --------------------------------------------------------------------------
#
# The message already says; the point of a code is that it says it in a form a
# caller can branch on. Without one, the eight causes below collapse into one
# string, and an operator wanting the class's own advice -- "upgrade the SDK OR
# go and look at the deployment" -- has to regex the message to learn which of
# those two things to do. They are different actions, and only the cause
# distinguishes them.
#
# The grouping, which is why the discriminant is worth carrying:
#   UPGRADE THE SDK - the deployment is ahead of this build:
#       unsupported_profile, unknown_operational_state
#   LOOK AT THE DEPLOYMENT - something in the path is wrong or in the way:
#       missing_profile_context (a proxy dropped the negotiation header, or an
#       older gateway), decision_state_disagreement, obligations_on_refusal,
#       undecodable_body
#   LOOK AT THE CALLING CODE - the SDK's own belt tripped on the request:
#       unresolved_attribute, structure_too_deep
AUTHZEN_PROTOCOL_MISSING_PROFILE_CONTEXT: Final = "missing_profile_context"
AUTHZEN_PROTOCOL_UNSUPPORTED_PROFILE: Final = "unsupported_profile"
AUTHZEN_PROTOCOL_UNKNOWN_OPERATIONAL_STATE: Final = "unknown_operational_state"
AUTHZEN_PROTOCOL_DECISION_STATE_DISAGREEMENT: Final = "decision_state_disagreement"
AUTHZEN_PROTOCOL_OBLIGATIONS_ON_REFUSAL: Final = "obligations_on_refusal"
AUTHZEN_PROTOCOL_UNDECODABLE_BODY: Final = "undecodable_body"
AUTHZEN_PROTOCOL_UNRESOLVED_ATTRIBUTE: Final = "unresolved_attribute"
AUTHZEN_PROTOCOL_STRUCTURE_TOO_DEEP: Final = "structure_too_deep"

# The default, for a protocol error constructed by hand. Never emitted by this
# module: every site inside it names its cause. It exists so `kind` is additive
# -- `AuthZENProtocolError(msg)` keeps working -- and so an unset kind reads as
# "nobody said", which is honest, rather than borrowing some other cause's name.
AUTHZEN_PROTOCOL_UNSPECIFIED: Final = "unspecified"

AuthZENProtocolErrorKind: TypeAlias = Literal[
    "missing_profile_context",
    "unsupported_profile",
    "unknown_operational_state",
    "decision_state_disagreement",
    "obligations_on_refusal",
    "undecodable_body",
    "unresolved_attribute",
    "structure_too_deep",
    "unspecified",
]


class AuthZENProtocolError(AxonFlowError):
    """A 200 whose body this build cannot safely interpret.

    Deliberately NOT an :class:`AuthZENRefusal`. A refusal carries the server's
    own typed code from a closed vocabulary the server owns; a response this
    build cannot read is not something the server said, and dressing it in a
    server code would tell the caller the gateway refused when it did not. The
    two also demand different actions: a refusal means fix the request, a
    protocol error means upgrade the SDK or go and look at the deployment.

    :attr:`kind` says WHICH of those, from the closed set above. It is additive
    -- the message is unchanged and the one-argument constructor still works --
    and it exists because the advice in the paragraph above names two different
    actions, and until there was a discriminant a caller had to match on the
    message text to tell them apart. Message text is not an interface.

    It is always fail-closed: no decision is returned, so a caller that lets it
    propagate blocks the operation.
    """

    def __init__(
        self,
        message: str,
        *,
        kind: AuthZENProtocolErrorKind = AUTHZEN_PROTOCOL_UNSPECIFIED,
    ) -> None:
        super().__init__(message, details={"kind": kind})
        self.kind = kind


# The marker every tri-state attribute carries, and the ONLY thing
# :func:`_is_attribute` looks for besides the class itself.
#
# It exists because recognising an attribute by its class alone has a silent
# failure mode in the dangerous direction: an attribute that has been copied -
# ``dataclasses.asdict``, a cache that round-trips through JSON, a worker
# boundary - is no longer an instance, so an UNKNOWN one would be resolved as
# ordinary data and SENT. Recognising it by SHAPE alone has the mirror failure:
# a caller's own bag carrying ``state``/``value``/``reason`` is read as an
# attribute, and a legitimate request is refused with a message asserting the
# caller could not establish a value it did establish.
#
# A marker closes both. It survives every ordinary copy (it is an ordinary
# field), and no caller's data carries it by accident. The sibling SDK uses the
# same key for the same reason.
AUTHZEN_ATTRIBUTE_MARKER: Final = "__axonflow_authzen_attribute__"


def _attribute_parts(value: object) -> tuple[str, Any, str]:
    """The (state, value, reason) of an attribute, instance or copied dict.

    Only ever called behind :func:`_is_attribute`, which has already
    established that the value is one of those two shapes.

    The returned state is whatever the document carries, NOT normalised into
    the three this build knows: a copied dict with an unrecognised or missing
    state yields that string (or ``""``), and ``_resolve_value`` refuses it.
    Mapping an unknown state onto a known one here would put the fail-open back
    one frame lower, where it is harder to see.
    """
    if isinstance(value, AuthZENAttribute):
        return value.state, value.value, value.reason
    mapping: dict[str, Any] = value  # type: ignore[assignment]
    return str(mapping.get("state", "")), mapping.get("value"), str(mapping.get("reason", ""))


def _is_attribute(value: object) -> bool:
    """Whether ``value`` is a tri-state attribute, however it was copied.

    Recognition keys on the MARKER alone, deliberately, and not on the marker
    plus a state this build happens to know.

    Pairing them was a fail-open (#234). A document carrying
    ``__axonflow_authzen_attribute__: True`` with any other ``state`` — a newer
    producer's, or a corrupted one — was reported as NOT an attribute, and so
    was walked as ordinary data and sent on the wire, marker key and all. By
    the surface's own reasoning that document IS an attribute; it is one this
    build cannot interpret. Evaluating around it records an attribute nobody
    resolved as one that was weighed, and it leaks the marker convention to the
    gateway.

    Recognising it here routes it to the same refusal an ``unknown`` attribute
    gets: ``_resolve_value`` matches ``known`` and ``absent`` explicitly and
    raises ``_Unresolvable`` for everything else, so an unrecognised state
    (including a missing one) refuses locally, non-retryably, with a pointer to
    the member — which is what the Go SDK does (sdk-go#210).

    A marker that is present but not boolean ``True`` stays data: only a
    positive self-identification counts, so a caller's own bag with a
    similarly-named key cannot be turned into a refusal.
    """
    if isinstance(value, AuthZENAttribute):
        return True
    if isinstance(value, dict):
        return value.get(AUTHZEN_ATTRIBUTE_MARKER) is True
    return False


@dataclass(frozen=True)
class AuthZENAttribute:
    """One policy-visible attribute in exactly one of three states.

    ``None`` cannot express this. ADR-065's model has three:

    ``known``
        The authoritative source returned a value. It is sent.
    ``absent``
        The source successfully established that there is NO value. Absence is
        a FACT, not a failure, so the member is omitted and the request is
        sent — a policy that handles absence gets to handle it.
    ``unknown``
        The value could not be established. The request is NOT sent. Sending it
        would have the gateway evaluate as though the attribute were absent,
        and the resulting decision — and every audit of it — would record that
        an attribute was weighed when nobody ever read it. That is the exact
        failure the whole surface refuses to commit, one hop earlier.

    Collapsing absent into unknown is the defect this type exists to prevent,
    and it is not hypothetical: on the platform side an ABSENT ``subject.type``
    was read as the one supported value, so omitting the field bypassed the
    impersonation refusal that naming it correctly triggered.

    Where it may be used: inside the ATTRIBUTE bags — ``context`` on a request
    or a bulk envelope, and the ``properties`` bag on a subject, action or
    resource — at any depth. Not on the structural members (``subject.id``,
    ``action.name``, ``resource.type`` …): those are the identity of the
    question being asked, not data about it, and an identity the caller cannot
    resolve is not an attribute whose absence a policy could evaluate — there
    is simply no request to make.

    >>> AuthZENAttribute.known("acme-corp")
    >>> AuthZENAttribute.absent()
    >>> AuthZENAttribute.unknown(AUTHZEN_UNKNOWN_RESOLUTION_FAILED)
    """

    state: AuthZENAttributeState
    value: Any = None
    reason: str = ""
    # Carried as an ordinary field so it survives dataclasses.asdict, a JSON
    # round trip and a worker boundary. See AUTHZEN_ATTRIBUTE_MARKER.
    __axonflow_authzen_attribute__: bool = True

    @classmethod
    def known(cls, value: Any) -> AuthZENAttribute:  # noqa: ANN401 - any JSON value
        """The source returned this value."""
        return cls(state="known", value=value)

    @classmethod
    def absent(cls) -> AuthZENAttribute:
        """The source established that there is no value."""
        return cls(state="absent")

    @classmethod
    def unknown(cls, reason: str) -> AuthZENAttribute:
        """The value could not be established, for the named reason.

        The reason is mandatory. An unknown with no reason is what a bare
        ``None`` already was, and the whole point of the third state is that it
        carries why.
        """
        if not reason.strip():
            msg = (
                "an unknown attribute must name why it could not be established; "
                "an unknown with no reason carries no more information than None, "
                "which is the collapse this type exists to prevent"
            )
            raise ValueError(msg)
        return cls(state="unknown", reason=reason)


class AuthZENDecision(AuthZENResponse):
    """The decision, with the readings a Policy Enforcement Point acts on.

    It extends the generated wire type rather than wrapping it, so ``.decision``
    and ``.context`` remain exactly what the server sent while the readings
    below stay in hand-written code the generator never has to know about.
    """

    @property
    def allowed(self) -> bool:
        """Whether the enforcement point may proceed.

        Read this rather than ``.decision``. ``decision`` is AuthZEN 1.0's
        collapsed boolean; the operational STATE is what the policy engine
        actually produced, and exactly one state permits execution. Requiring
        both means a response whose boolean and state disagree can never be
        read as an allow — and such a response is refused before it gets here
        anyway, so this is the second of two locks rather than the only one.

        An allow with an undischarged MANDATORY obligation is not an allow.
        See :attr:`mandatory_obligations`.
        """
        return (
            self.decision is True
            and self.context is not None
            and self.context.state == AUTHZEN_OPERATIONAL_STATE_ALLOW
        )

    @property
    def state(self) -> AuthZENOperationalState:
        """The four-valued operational state."""
        # Unreachable via `client.evaluate`, which refuses a context-less 200
        # before constructing this. Stated rather than assumed, because the
        # type is public and a caller can build one by hand — and the safe
        # reading of an outcome that carries no state is not ALLOW.
        if self.context is None:
            return AUTHZEN_OPERATIONAL_STATE_ERROR
        return self.context.state

    @property
    def decision_id(self) -> str | None:
        """The id of the evaluation that DETERMINED this outcome.

        For a bulk envelope this is the entry that decided the meet, not the
        last one evaluated: it is the id an operator looks up to explain the
        answer.
        """
        return None if self.context is None else self.context.decision_id

    @property
    def reason(self) -> str | None:
        """The safe machine-readable reason code, when the server sent one."""
        return None if self.context is None else self.context.reason

    @property
    def category(self) -> str | None:
        """The coarse outcome category, when the server sent one."""
        return None if self.context is None else self.context.category

    @property
    def obligations(self) -> list[AuthZENObligation]:
        """Instructions the enforcement point must discharge before proceeding."""
        if self.context is None or self.context.obligations is None:
            return []
        return self.context.obligations

    @property
    def mandatory_obligations(self) -> list[AuthZENObligation]:
        """The obligations that are not optional.

        A mandatory obligation that cannot be discharged means the operation
        must NOT proceed, even though :attr:`allowed` is true. This SDK cannot
        make that call for you — whether your enforcement point can discharge a
        redaction is a fact about your seam, not about the decision — so it
        gives you the list and stays out of the way.
        """
        return [o for o in self.obligations if o.mandatory]

    @property
    def approval(self) -> AuthZENApprovalRequirement | None:
        """The approval challenge, when the state is CHALLENGE."""
        return None if self.context is None else self.context.approval


# Resolve the inherited forward reference now, at import time, rather than
# leaving pydantic to attempt it on first use. Deferring it would turn a
# packaging mistake into a runtime failure on the caller's FIRST authorization
# decision, which is the worst possible moment to discover it.
AuthZENDecision.model_rebuild()


# --------------------------------------------------------------------------
# Tri-state resolution
# --------------------------------------------------------------------------


# The JSON Pointer escapes, per RFC 6901. A correlation key containing a slash
# would otherwise produce a pointer naming a member that does not exist, on the
# refusal whose entire diagnostic value is the pointer.
def _escape_pointer_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


class _Unresolvable(Exception):
    """Internal: an attribute the caller could not establish."""

    def __init__(self, pointer: str, reason: str) -> None:
        super().__init__(pointer)
        self.pointer = pointer
        self.reason = reason


# A sentinel distinguishing "this member resolved to no value, drop it" from
# "this member resolved to the value None". They are different: a caller may
# legitimately send a JSON null, and reusing None for the drop signal would
# silently rewrite one into the other.
_DROP: Final = object()

# How deep an attribute bag may nest before the SDK stops walking it.
#
# Without a bound, a bag that refers to itself recurses until the interpreter
# gives up, and the caller gets a RecursionError out of `evaluate` -- an error
# type nothing documents and no enforcement point catches. A bound turns that
# into the same typed refusal every other malformed bag gets. 64 is far past
# anything a policy attribute path plausibly nests (the platform's own attribute
# paths are three or four segments) and far short of the interpreter's limit.
_MAX_ATTRIBUTE_DEPTH: Final = 64


def _resolve_value(value: Any, pointer: str, depth: int = 0) -> Any:  # noqa: ANN401 - any JSON
    """Resolve one value, recursing through containers."""
    if depth > _MAX_ATTRIBUTE_DEPTH:
        msg = (
            f"nests deeper than {_MAX_ATTRIBUTE_DEPTH} levels, which this SDK will not walk; "
            f"a bag that refers to itself would otherwise recurse until the interpreter stopped"
        )
        raise _Unresolvable(pointer, msg)
    if _is_attribute(value):
        state, payload, reason = _attribute_parts(value)
        if state == "known":
            # A known attribute may itself hold a container carrying more
            # attributes; resolving the payload keeps the rule uniform rather
            # than depending on how deeply a caller nested its resolver output.
            return _resolve_value(payload, pointer, depth + 1)
        if state == "absent":
            return _DROP
        raise _Unresolvable(pointer, reason)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            resolved = _resolve_value(
                item, f"{pointer}/{_escape_pointer_token(str(key))}", depth + 1
            )
            if resolved is not _DROP:
                out[key] = resolved
        return out
    if isinstance(value, (list, tuple)):
        # Tuples are walked too, and returned as lists. The belt walks them, so
        # a resolver that did not would leave an attribute inside a tuple
        # unresolved: the belt then refused a request the resolver should simply
        # have resolved, and a CYCLE routed through a tuple escaped the depth
        # bound entirely and came back as a RecursionError. Two walkers with
        # different container sets is the shape of that whole class of bug.
        #
        # An ABSENT element is dropped rather than left as a hole. A list with a
        # gap in it is a different list, and the index a policy reads would
        # shift under it either way; dropping is the reading that matches "there
        # is no value here".
        items = []
        for index, item in enumerate(value):
            resolved = _resolve_value(item, f"{pointer}/{index}", depth + 1)
            if resolved is not _DROP:
                items.append(resolved)
        return items
    return value


def _resolve_bag(bag: dict[str, Any] | None, pointer: str) -> dict[str, Any] | None:
    """Resolve one attribute bag.

    ABSENCE DOES NOT CASCADE. A bag whose every member resolved absent is sent
    as an empty object, not deleted: the bag is the caller's structure and the
    attributes are the data inside it, and an SDK that removed a container the
    caller placed would be editing the question rather than resolving the
    answer.

    The lever a caller wants sits one level in, which is where the attributes
    are: ``context={"args": ..., "correlation": AuthZENAttribute.absent()}``
    drops ``correlation`` and keeps everything else. Omitting the bag itself is
    ordinary Python — do not pass it — because a bag that is not part of the
    question is not an attribute whose absence anything could evaluate.

    A BAG THAT IS ITSELF ATTRIBUTE-SHAPED IS REFUSED, not resolved. Nothing
    stops a caller putting an attribute where the bag goes -- a copied
    ``AuthZENAttribute`` is an ordinary dict, so pydantic accepts it as the
    ``dict[str, Any]`` the member is annotated as -- and the resolver then hands
    back whatever that attribute held: the drop sentinel for an ABSENT one, a
    bare scalar for a ``known("x")``. Neither is a bag, and constructing the
    model around one raised pydantic's ``ValidationError`` straight out of
    ``evaluate``, which documents ``AuthZENRefusal``, ``AuthZENProtocolError``
    and ``AuthenticationError`` and nothing else. A fail-closed handler catching
    those three did not catch it.

    So the annotation this line used to carry was not merely optimistic, it was
    the bug: ``_resolve_value`` returns ``Any``, and calling it ``dict`` is what
    let a non-dict through to the constructor unexamined. It is checked now, and
    reported as the same typed refusal the sibling TypeScript SDK gives for the
    identical input -- code ``malformed_envelope``, the bag's own pointer, and
    the message ``"<pointer> must be an object"``.
    """
    if bag is None:
        return None
    resolved: Any = _resolve_value(bag, pointer)
    if not isinstance(resolved, dict):
        raise AuthZENRefusal(
            AUTHZEN_ERROR_CODE_MALFORMED_ENVELOPE,
            f"{pointer or '/'} must be an object",
            refused_by="client",
            pointer=pointer,
        )
    return resolved


def _resolve_request(request: AuthZENRequest, at: str) -> AuthZENRequest:
    return AuthZENRequest(
        subject=_resolve_subject(request.subject, at),
        action=_resolve_action(request.action, at),
        resource=_resolve_resource(request.resource, at),
        context=_resolve_bag(request.context, f"{at}/context"),
    )


def _resolve_subject(subject: AuthZENSubject | None, at: str) -> AuthZENSubject | None:
    if subject is None:
        return None
    return AuthZENSubject(
        type=subject.type,
        id=subject.id,
        properties=_resolve_bag(subject.properties, f"{at}/subject/properties"),
    )


def _resolve_action(action: AuthZENAction | None, at: str) -> AuthZENAction | None:
    if action is None:
        return None
    return AuthZENAction(
        name=action.name,
        properties=_resolve_bag(action.properties, f"{at}/action/properties"),
    )


def _resolve_resource(resource: AuthZENResource | None, at: str) -> AuthZENResource | None:
    if resource is None:
        return None
    return AuthZENResource(
        type=resource.type,
        id=resource.id,
        properties=_resolve_bag(resource.properties, f"{at}/resource/properties"),
    )


def _envelope(
    *,
    evaluation: AuthZENRequest | None = None,
    evaluations: AuthZENBulk | None = None,
) -> AuthZENEnvelope:
    """Construct the envelope, reporting a caller mistake as a TYPED refusal.

    The generated model raises pydantic's ``ValidationError`` for the rules no
    annotation can carry - exactly one member, the singular member's own
    required set. That is the right exception at the point a caller builds a
    model by hand, and the wrong one coming out of ``evaluate``: the method
    documents ``AuthZENRefusal``, ``AuthZENProtocolError`` and
    ``AuthenticationError``, and an enforcement point that catches those would
    not catch a ``ValidationError`` -- so a mistyped request would escape a
    fail-closed handler as an exception nothing on the path expects.

    It also keeps the two SDKs saying the same thing. TypeScript has no
    construction-time validation at all, so it reports the same mistakes as a
    client-side refusal; without this, the identical bad envelope produced a
    refusal in one SDK and a framework exception in the other.
    """
    try:
        return AuthZENEnvelope(evaluation=evaluation, evaluations=evaluations)
    except ValidationError as exc:
        raise AuthZENRefusal(
            AUTHZEN_ERROR_CODE_MALFORMED_ENVELOPE,
            f"the envelope does not satisfy the AuthZEN contract: {exc}",
            refused_by="client",
            pointer="",
        ) from exc


def build_envelope(
    *,
    evaluation: AuthZENRequest | Mapping[str, Any] | None = None,
    evaluations: AuthZENBulk | Mapping[str, Any] | None = None,
) -> AuthZENEnvelope:
    """Build an envelope: coerce, RESOLVE, check completeness, then construct.

    That order is the same in every SDK, and it has to be: an envelope with two
    problems - an unresolvable attribute AND a missing action - must be reported
    the same way in each, or a customer comparing two languages is told two
    different things about one request.

    Coercion comes first because a caller may pass a plain mapping. Reading
    ``request.subject`` off a dict raises ``AttributeError``, which is neither
    documented nor typed - the exception this function exists to stop escaping.
    """
    evaluation = _coerce(AuthZENRequest, evaluation, "/evaluation")
    evaluations = _coerce(AuthZENBulk, evaluations, "/evaluations")

    try:
        if evaluation is not None:
            evaluation = _resolve_request(evaluation, "/evaluation")
        if evaluations is not None:
            evaluations = _resolve_bulk(evaluations)
    except _Unresolvable as unresolvable:
        raise _unresolvable_refusal(unresolvable) from unresolvable

    if evaluation is not None:
        _check_complete(evaluation, None, "/evaluation")
    elif evaluations is not None:
        base = AuthZENRequest(
            subject=evaluations.subject,
            action=evaluations.action,
            resource=evaluations.resource,
            context=evaluations.context,
        )
        for index, entry in enumerate(evaluations.evaluations):
            _check_complete(entry, base, f"/evaluations/evaluations/{index}")
    envelope = _envelope(evaluation=evaluation, evaluations=evaluations)
    # The belt runs here as well as on the send path, because this function's
    # whole job is to hand back an envelope that is ready to send. The sibling
    # SDK's buildEnvelope does the same, so a caller that builds ahead of time
    # gets the same answer in both languages.
    _assert_fully_resolved(envelope)
    return envelope


_ModelT = TypeVar("_ModelT", bound=BaseModel)


def _coerce(model: type[_ModelT], value: object, at: str) -> _ModelT | None:
    """Accept a model or a plain mapping, reporting a mistake as a refusal.

    A caller handing ``evaluate`` a dict is doing an ordinary thing, and it used
    to work: the envelope model coerced it. Reaching for ``.subject`` on it
    instead raises ``AttributeError`` - no code, no pointer, and outside the
    documented raise set, which is the whole class of exception this path exists
    to keep in.
    """
    if value is None or isinstance(value, model):
        return value
    try:
        return model.model_validate(value)
    except ValidationError as exc:
        raise AuthZENRefusal(
            AUTHZEN_ERROR_CODE_MALFORMED_ENVELOPE,
            f"the {at.lstrip('/')} member does not satisfy the AuthZEN contract: {exc}",
            refused_by="client",
            pointer=at,
        ) from exc


def _unresolvable_refusal(unresolvable: _Unresolvable) -> AuthZENRefusal:
    """The one refusal an UNKNOWN attribute produces, wherever it is found.

    Built in one place because two entry points resolve - ``build_envelope`` on
    the client's path and ``resolve_envelope`` for an envelope a caller already
    holds - and two copies of this message would drift into two explanations of
    the same event.
    """
    msg = (
        f"the attribute at {unresolvable.pointer} could not be established "
        f"({unresolvable.reason}), so this request was not sent. The gateway would "
        f"have evaluated as though the attribute had no value, and the decision - "
        f"and every audit of it - would record that it was considered when nothing "
        f"read it. Establish the value, or send it as an explicitly ABSENT "
        f"attribute if the source proved there is none."
    )
    return AuthZENRefusal(
        AUTHZEN_ERROR_CODE_UNEVALUABLE_ATTRIBUTE,
        msg,
        refused_by="client",
        pointer=unresolvable.pointer,
    )


def _resolve_bulk(bulk: AuthZENBulk) -> AuthZENBulk:
    """Resolve a plural envelope's shared base and each of its entries."""
    return AuthZENBulk(
        subject=_resolve_subject(bulk.subject, "/evaluations"),
        action=_resolve_action(bulk.action, "/evaluations"),
        resource=_resolve_resource(bulk.resource, "/evaluations"),
        context=_resolve_bag(bulk.context, "/evaluations/context"),
        evaluations=[
            _resolve_request(entry, f"/evaluations/evaluations/{index}")
            for index, entry in enumerate(bulk.evaluations)
        ],
    )


def resolve_envelope(envelope: AuthZENEnvelope) -> AuthZENEnvelope:
    """Return the envelope with every tri-state attribute resolved to the wire.

    Raises :class:`AuthZENRefusal` with ``refused_by="client"`` and the JSON
    Pointer of the offending member when an attribute is UNKNOWN.

    The pointers match the server's own vocabulary — ``/evaluation/...`` for a
    singular envelope, ``/evaluations/evaluations/<i>/...`` for a plural entry
    — so a client-side refusal and a gateway refusal name the same member the
    same way, and a caller does not have to learn two pointer dialects.
    """
    try:
        if envelope.evaluation is not None:
            return _envelope(evaluation=_resolve_request(envelope.evaluation, "/evaluation"))
        if envelope.evaluations is not None:
            return _envelope(evaluations=_resolve_bulk(envelope.evaluations))
    except _Unresolvable as unresolvable:
        raise _unresolvable_refusal(unresolvable) from unresolvable
    # Unreachable: the generated envelope validator refuses an envelope with
    # neither member set. Stated rather than assumed, because returning the
    # input unchanged here would send an envelope no resolver had walked. The
    # code is MALFORMED_ENVELOPE, matching what the gateway's own mapEnvelope
    # answers for an envelope it cannot read - and matching the sibling SDKs,
    # which must not name the same mistake with two different codes.
    msg = "the envelope names neither an evaluation nor an evaluations member"
    raise AuthZENRefusal(
        AUTHZEN_ERROR_CODE_MALFORMED_ENVELOPE, msg, refused_by="client", pointer=""
    )


# --------------------------------------------------------------------------
# Completeness
# --------------------------------------------------------------------------


def _base_member(base: AuthZENRequest | None, name: str) -> Any:  # noqa: ANN401 - any member
    """The shared base's member, or None when there is no base."""
    return getattr(base, name) if base is not None else None


def _check_complete(request: AuthZENRequest, base: AuthZENRequest | None, at: str) -> None:
    """Check the one invariant the artifact says it cannot express.

    The artifact marks every member of ``authzen_request`` structurally
    optional, because a plural entry inherits anything it omits from the shared
    base. Whether the MERGED entry names a subject, an action and a resource is
    a cross-object property no per-object schema can carry, and the platform's
    own projection enforces it server-side.

    This is deliberately the ONLY thing checked here, and it is checked by
    PRESENCE alone. Everything else the server refuses — which action names are
    evaluable, which resource types exist, which correlation keys this
    deployment records — is deployment state the SDK does not have. A client
    that guessed at it would refuse requests a newer gateway accepts, and the
    caller would have no way to tell an SDK that is out of date from a request
    that is wrong.
    """
    missing = [
        name
        for name, value, inherited in (
            ("subject", request.subject, base.subject if base else None),
            ("action", request.action, base.action if base else None),
            ("resource", request.resource, base.resource if base else None),
        )
        if value is None and inherited is None
    ]
    if missing:
        joined = ", ".join(missing)
        msg = (
            f"after inheriting from the shared base this evaluation still has no "
            f"{joined}; there is nothing to evaluate"
        )
        raise AuthZENRefusal(
            AUTHZEN_ERROR_CODE_INCOMPLETE_EVALUATION, msg, refused_by="client", pointer=at
        )

    # `is not None`, not `or`. A pydantic model is truthy today, so the two
    # behave identically - which is exactly the assumption this repo's
    # falsey-clobber lint exists to stop code resting on. The TypeScript
    # sibling uses `??` here, which is null-only for the same reason.
    subject = request.subject if request.subject is not None else _base_member(base, "subject")
    if subject is not None and not subject.id.strip():
        msg = "the subject id must not be blank; a decision has to name the caller it was made for"
        raise AuthZENRefusal(
            AUTHZEN_ERROR_CODE_INCOMPLETE_EVALUATION,
            msg,
            refused_by="client",
            pointer=f"{at}/subject/id",
        )


def check_envelope_complete(envelope: AuthZENEnvelope) -> None:
    """Refuse an envelope that cannot produce a decision, before the round trip."""
    if envelope.evaluation is not None:
        _check_complete(envelope.evaluation, None, "/evaluation")
        return
    if envelope.evaluations is not None:
        bulk = envelope.evaluations
        base = AuthZENRequest(
            subject=bulk.subject,
            action=bulk.action,
            resource=bulk.resource,
            context=bulk.context,
        )
        for index, entry in enumerate(bulk.evaluations):
            _check_complete(entry, base, f"/evaluations/evaluations/{index}")


# --------------------------------------------------------------------------
# The response direction
# --------------------------------------------------------------------------


def _decode_refusal(raw: bytes) -> AuthZENError | None:
    """Decode a structured refusal document, or None if the body is not one.

    Decoded LENIENTLY, unlike the decision path, and the asymmetry is
    deliberate. Strictness on a DECISION is a safety control: an unknown member
    means the server is speaking a profile this build cannot fully read, and the
    unread part may be the one that constrains an allow. A refusal constrains
    nothing -- it says no decision exists -- so the same strictness buys no
    safety and costs the caller the whole point of the surface: one additive
    field on the refusal envelope would degrade every typed refusal into a bare
    error, losing the code, the pointer, the supported set and the retryable
    signal on the one path whose entire purpose is to be branchable.

    So unknown members are dropped here, and the shape is still checked: a body
    that carries no code or no message is not a refusal document and returns
    None, which sends the caller down the generic-error path rather than
    fabricating a refusal the server did not make.
    """
    try:
        decoded = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(decoded, dict):
        return None
    known = {"code", "pointer", "message", "supported", "request_id"}
    try:
        body = AuthZENError.model_validate({k: v for k, v in decoded.items() if k in known})
    except (ValidationError, ValueError):
        return None
    # An EMPTY code is not a refusal document either, and the artifact does not
    # give `code` a min_length - so nothing else refuses it. A refusal whose
    # code matches no constant falls through every caller's branch table and
    # reports `retryable=False`, which is the shape of an answer rather than an
    # answer. The generic-error path at least says what the status was.
    if not body.code.strip():
        return None
    return body


def _validate_decision(response: AuthZENResponse, raw: bytes) -> None:
    """Refuse a 200 this build cannot act on.

    Every check here closes a way for an un-actionable body to be read as an
    allow. A decoded response that is merely well-typed is not enough: the
    boolean and the state are two renderings of one outcome, and a build that
    trusts either alone will act on a decision the other contradicts.
    """
    body = raw.decode("utf-8", errors="replace")

    if response.context is None:
        msg = (
            f"the server answered without the profile context. This SDK negotiates "
            f"{AUTHZEN_PROFILE_HEADER}: {AUTHZEN_PROFILE_V1} on every request, so a "
            f"response carrying only the boolean means the gateway did not honour the "
            f"negotiation — an older build, or a proxy that dropped the header. The "
            f"obligations and the approval challenge that CONSTRAIN an allow ride in "
            f"that payload, so an allow without it is an allow whose mandatory "
            f"conditions cannot be read. body={body}"
        )
        raise AuthZENProtocolError(msg, kind=AUTHZEN_PROTOCOL_MISSING_PROFILE_CONTEXT)

    context = response.context
    if context.profile != AUTHZEN_PROFILE_V1:
        msg = (
            f"the server answered with AuthZEN profile {context.profile!r}; this build "
            f"can only interpret {AUTHZEN_PROFILE_V1!r}. The obligations and approval "
            f"challenge that constrain an allow are carried in that payload, so the "
            f"decision cannot be acted on safely. Upgrade the SDK."
        )
        raise AuthZENProtocolError(msg, kind=AUTHZEN_PROTOCOL_UNSUPPORTED_PROFILE)

    if context.state not in AUTHZEN_OPERATIONAL_STATE_VALUES:
        msg = (
            f"the server reported the operational state {context.state!r}, which this "
            f"build does not know. Under profile {AUTHZEN_PROFILE_V1} the state set is "
            f"closed, so a new value means the response was produced by something this "
            f"SDK cannot interpret — and a state whose meaning is unknown must not be "
            f"resolved into permission. body={body}"
        )
        raise AuthZENProtocolError(msg, kind=AUTHZEN_PROTOCOL_UNKNOWN_OPERATIONAL_STATE)

    executable = context.state == AUTHZEN_OPERATIONAL_STATE_ALLOW
    if response.decision != executable:
        msg = (
            f"the decision boolean ({response.decision}) and the operational state "
            f"({context.state}) disagree; exactly one state permits execution, so one "
            f"of the two renderings of this outcome is wrong and there is no safe way "
            f"to choose between them. body={body}"
        )
        raise AuthZENProtocolError(msg, kind=AUTHZEN_PROTOCOL_DECISION_STATE_DISAGREEMENT)

    if not executable and context.obligations:
        msg = (
            f"the server attached obligations to a {context.state} decision. Obligations "
            f"ride only on an executable decision: instructions on a refusal invite an "
            f"enforcement point to discharge them and proceed. body={body}"
        )
        raise AuthZENProtocolError(msg, kind=AUTHZEN_PROTOCOL_OBLIGATIONS_ON_REFUSAL)

    # `schema_version` is deliberately NOT enforced. The PROFILE is the
    # negotiated contract and is checked above; schema_version is carried so a
    # support conversation can name the contract a deployment answered from.
    # Enforcing both would mean the two have to be bumped in lockstep, and this
    # SDK would start refusing decisions over a discrepancy that changes
    # nothing it reads.


async def evaluate_envelope(
    send: AuthZENTransport,
    envelope: AuthZENEnvelope,
) -> AuthZENDecision:
    """Run one envelope through ``send`` and interpret the answer.

    ``send`` is the SDK's own transport — the same authenticated HTTP client,
    retry policy and headers every other method uses. It is passed in rather
    than built here so this module owns the AuthZEN semantics and nothing else;
    a second transport would be a second place for credentials, timeouts and
    proxy configuration to drift out of step with the client the user
    configured.
    """
    resolved = resolve_envelope(envelope)
    check_envelope_complete(resolved)
    # The belt runs on the MODEL, before serialisation. Run after model_dump it
    # could never fire: pydantic serialises AuthZENAttribute -- a dataclass --
    # into an ordinary {"state": ..., "value": ..., "reason": ...} object, so
    # the isinstance check was handed a plain dict and an unresolved UNKNOWN
    # attribute went out on the wire wearing that shape. Measured, not assumed.
    _assert_fully_resolved(resolved)

    body = resolved.model_dump(exclude_none=True)

    status, raw = await send(
        AUTHZEN_PATH,
        body,
        {AUTHZEN_PROFILE_HEADER: AUTHZEN_PROFILE_V1},
    )

    if status == 401:  # noqa: PLR2004
        # Authentication is answered by the gateway's own middleware, before
        # the route runs, so it never carries an AuthZEN refusal document.
        # Surfacing it as the SDK's existing AuthenticationError keeps one
        # exception for "your credentials are wrong" across every method on
        # this client, instead of a second one only AuthZEN callers know to
        # catch.
        msg = f"Invalid credentials for {AUTHZEN_PATH}: {raw.decode('utf-8', errors='replace')}"
        raise AuthenticationError(msg)

    if status != 200:  # noqa: PLR2004
        refusal = _decode_refusal(raw)
        if refusal is not None:
            raise AuthZENRefusal.from_body(refusal)
        # A non-OK body that is not a refusal document still surfaces as an
        # error — never as a decision.
        msg = f"HTTP {status} from {AUTHZEN_PATH}: {raw.decode('utf-8', errors='replace')}"
        raise AxonFlowError(msg)

    try:
        response = AuthZENResponse.model_validate_json(raw)
    except (ValidationError, ValueError) as exc:
        # Strict decoding on the success path. An unknown member in a decision
        # is a server speaking a profile this build does not understand, and
        # quietly dropping it would mean acting on a partial reading of an
        # authorization decision.
        msg = (
            f"the decision could not be decoded: {exc}. "
            f"body={raw.decode('utf-8', errors='replace')}"
        )
        raise AuthZENProtocolError(msg, kind=AUTHZEN_PROTOCOL_UNDECODABLE_BODY) from exc

    _validate_decision(response, raw)
    return AuthZENDecision.model_validate(response.model_dump())


def _assert_fully_resolved(value: object, path: str = "", depth: int = 0) -> None:
    """Guarantee no tri-state attribute reaches the wire.

    ``resolve_envelope`` walks every bag the contract declares, so on today's
    contract this is a belt with nothing to catch. It exists for the case that
    does not announce itself: a container kind added to the artifact later, or
    an attribute reaching a member the resolver does not visit. Without it that
    attribute is not a crash -- pydantic serialises the dataclass into an
    ordinary ``{"state": ..., "value": ..., "reason": ...}`` object -- so the
    request is SENT, carrying a resolver's internal shape where the gateway
    expects a value, and an UNKNOWN attribute reaches the network after all.

    It walks pydantic MODELS as well as plain containers, because the envelope
    it is handed is a model tree and stopping at the first ``BaseModel`` would
    make it inert for everything below the top level.
    """
    if depth > _MAX_ATTRIBUTE_DEPTH:
        # Bounded for the same reason the resolver is, and by the same number.
        # The two walkers used to disagree - one bounded, one not - so a cycle
        # routed through a container only the unbounded one visited came back as
        # a RecursionError, which is the error type this bound exists to remove.
        msg = (
            f"the structure at {path or '/'} nests deeper than {_MAX_ATTRIBUTE_DEPTH} "
            f"levels; this SDK will not walk it, and a structure that refers to itself "
            f"would otherwise recurse until the interpreter stopped"
        )
        raise AuthZENProtocolError(msg, kind=AUTHZEN_PROTOCOL_STRUCTURE_TOO_DEEP)
    if _is_attribute(value):
        msg = (
            f"an unresolved AuthZENAttribute reached the wire at {path or '/'}. "
            f"Tri-state attributes are only supported inside the context and "
            f"properties bags; this one is somewhere the resolver does not reach."
        )
        raise AuthZENProtocolError(msg, kind=AUTHZEN_PROTOCOL_UNRESOLVED_ATTRIBUTE)
    if isinstance(value, BaseModel):
        for name in type(value).model_fields:
            _assert_fully_resolved(getattr(value, name), f"{path}/{name}", depth + 1)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_fully_resolved(item, f"{path}/{_escape_pointer_token(str(key))}", depth + 1)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _assert_fully_resolved(item, f"{path}/{index}", depth + 1)


def to_wire(envelope: AuthZENEnvelope) -> dict[str, Any]:
    """The document this SDK would send for ``envelope``.

    Exported for tests and for support: "what did the SDK actually put on the
    wire" is the first question of every integration problem, and answering it
    by reading the client's source is how the answer ends up wrong.

    It runs the same resolution, completeness check and belt ``evaluate`` runs,
    and returns the same object ``evaluate`` hands the transport -- so it can be
    compared against a packet capture member for member. It deliberately does
    NOT return a string: serialising here with different options from the ones
    httpx uses would produce bytes the SDK never sends, which is worse than
    useless to somebody diffing against a capture.
    """
    resolved = resolve_envelope(envelope)
    check_envelope_complete(resolved)
    _assert_fully_resolved(resolved)
    return resolved.model_dump(exclude_none=True)


# Re-exported so a caller can compare against the contract version its types
# were generated from without importing the generated module by name.
CONTRACT_SCHEMA_VERSION: Final = AUTHZEN_CONTRACT_SCHEMA_VERSION
