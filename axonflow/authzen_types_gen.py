"""AuthZEN wire types. GENERATED FILE — DO NOT EDIT.

Source: tests/fixtures/authzen-surface.json
  artifact:        axonflow-authzen-surface v1
  profile:         axonflow-authzen-profile-2026-08-29
  contract schema: 2026-08-29
  schema digest:   sha256:647e16f769766f0ee8cf4913aaf5ac5c4567660fd2903da6766eead5db279efe

Regenerate with::

    python3 scripts/gen_authzen_types.py

Editing this file by hand is pointless: tests/test_authzen_generator.py
regenerates it in memory and compares bytes, so a hand edit fails CI on the
next run.
"""

from __future__ import annotations

from typing import Any, Final, TypeAlias

from pydantic import BaseModel, ConfigDict, model_validator


class _AuthZENModel(BaseModel):
    """Base for every generated AuthZEN model. extra="forbid" is the decode-side half of
    the surface's central rule: an unknown member in a decision is a server speaking a
    profile this build does not understand, and quietly dropping it would mean acting on
    a partial reading of an authorization decision. On the request side it catches a
    member the caller invented before it becomes a 422.
    """

    model_config = ConfigDict(extra="forbid")


# The profile a Policy Enforcement Point negotiates to receive anything beyond
# the boolean decision. AuthZEN 1.0's response is a bare boolean; the
# four-valued state, the obligations, the approval challenge and the safe reason
# code all ride in the response context and are returned ONLY to a caller that
# asked for them by version.
AUTHZEN_PROFILE_V1: Final = "axonflow-authzen-profile-2026-08-29"

# The contract version these types were generated from. It is the value the
# server echoes in AuthZENResponseContext.schema_version.
AUTHZEN_CONTRACT_SCHEMA_VERSION: Final = "2026-08-29"

# The digest of the JSON Schema the artifact was reduced from. It is carried so
# a support conversation can establish which contract a deployed SDK was built
# against without reading its dependency tree.
AUTHZEN_SOURCE_SCHEMA_SHA256: Final = (
    "sha256:647e16f769766f0ee8cf4913aaf5ac5c4567660fd2903da6766eead5db279efe"
)


# AuthZENErrorCode: a closed set of values the server may send.
#
# It is a plain str alias rather than an Enum so an unrecognised value from a newer
# server round-trips instead of raising on decode or landing on a neighbouring constant.
# Use AUTHZEN_ERROR_CODE_VALUES to tell a value this build knows from one it does not.
AuthZENErrorCode: TypeAlias = str

AUTHZEN_ERROR_CODE_MALFORMED_ENVELOPE: Final[AuthZENErrorCode] = "malformed_envelope"
AUTHZEN_ERROR_CODE_INCOMPLETE_EVALUATION: Final[AuthZENErrorCode] = "incomplete_evaluation"
AUTHZEN_ERROR_CODE_UNSUPPORTED_SUBJECT: Final[AuthZENErrorCode] = "unsupported_subject"
AUTHZEN_ERROR_CODE_UNSUPPORTED_ACTION: Final[AuthZENErrorCode] = "unsupported_action"
AUTHZEN_ERROR_CODE_UNSUPPORTED_RESOURCE: Final[AuthZENErrorCode] = "unsupported_resource"
AUTHZEN_ERROR_CODE_UNEVALUABLE_ATTRIBUTE: Final[AuthZENErrorCode] = "unevaluable_attribute"
AUTHZEN_ERROR_CODE_MISSING_EVALUABLE_CONTENT: Final[AuthZENErrorCode] = "missing_evaluable_content"
AUTHZEN_ERROR_CODE_EVALUATION_UNAVAILABLE: Final[AuthZENErrorCode] = "evaluation_unavailable"

# Every value of authzen_error_code this build knows, in the artifact's order.
AUTHZEN_ERROR_CODE_VALUES: Final[tuple[AuthZENErrorCode, ...]] = (
    AUTHZEN_ERROR_CODE_MALFORMED_ENVELOPE,
    AUTHZEN_ERROR_CODE_INCOMPLETE_EVALUATION,
    AUTHZEN_ERROR_CODE_UNSUPPORTED_SUBJECT,
    AUTHZEN_ERROR_CODE_UNSUPPORTED_ACTION,
    AUTHZEN_ERROR_CODE_UNSUPPORTED_RESOURCE,
    AUTHZEN_ERROR_CODE_UNEVALUABLE_ATTRIBUTE,
    AUTHZEN_ERROR_CODE_MISSING_EVALUABLE_CONTENT,
    AUTHZEN_ERROR_CODE_EVALUATION_UNAVAILABLE,
)


# AuthZENCategory: a closed set of values the server may send.
#
# It is a plain str alias rather than an Enum so an unrecognised value from a newer
# server round-trips instead of raising on decode or landing on a neighbouring constant.
# Use AUTHZEN_CATEGORY_VALUES to tell a value this build knows from one it does not.
AuthZENCategory: TypeAlias = str

AUTHZEN_CATEGORY_ALLOWED: Final[AuthZENCategory] = "allowed"
AUTHZEN_CATEGORY_NOT_PERMITTED: Final[AuthZENCategory] = "not_permitted"
AUTHZEN_CATEGORY_APPROVAL_REQUIRED: Final[AuthZENCategory] = "approval_required"
AUTHZEN_CATEGORY_TEMPORARILY_UNAVAILABLE: Final[AuthZENCategory] = "temporarily_unavailable"
AUTHZEN_CATEGORY_INVALID_REQUEST: Final[AuthZENCategory] = "invalid_request"

# Every value of category this build knows, in the artifact's order.
AUTHZEN_CATEGORY_VALUES: Final[tuple[AuthZENCategory, ...]] = (
    AUTHZEN_CATEGORY_ALLOWED,
    AUTHZEN_CATEGORY_NOT_PERMITTED,
    AUTHZEN_CATEGORY_APPROVAL_REQUIRED,
    AUTHZEN_CATEGORY_TEMPORARILY_UNAVAILABLE,
    AUTHZEN_CATEGORY_INVALID_REQUEST,
)


# AuthZENIdentifierKind: a closed set of values the server may send.
#
# It is a plain str alias rather than an Enum so an unrecognised value from a newer
# server round-trips instead of raising on decode or landing on a neighbouring constant.
# Use AUTHZEN_IDENTIFIER_KIND_VALUES to tell a value this build knows from one it does
# not.
AuthZENIdentifierKind: TypeAlias = str

AUTHZEN_IDENTIFIER_KIND_ORGANIZATION: Final[AuthZENIdentifierKind] = "organization"
AUTHZEN_IDENTIFIER_KIND_PRINCIPAL: Final[AuthZENIdentifierKind] = "principal"
AUTHZEN_IDENTIFIER_KIND_GROUP: Final[AuthZENIdentifierKind] = "group"
AUTHZEN_IDENTIFIER_KIND_RESOURCE: Final[AuthZENIdentifierKind] = "resource"
AUTHZEN_IDENTIFIER_KIND_ACTION: Final[AuthZENIdentifierKind] = "action"
AUTHZEN_IDENTIFIER_KIND_TOOL: Final[AuthZENIdentifierKind] = "tool"
AUTHZEN_IDENTIFIER_KIND_CLIENT: Final[AuthZENIdentifierKind] = "client"
AUTHZEN_IDENTIFIER_KIND_SESSION: Final[AuthZENIdentifierKind] = "session"

# Every value of identifier_kind this build knows, in the artifact's order.
AUTHZEN_IDENTIFIER_KIND_VALUES: Final[tuple[AuthZENIdentifierKind, ...]] = (
    AUTHZEN_IDENTIFIER_KIND_ORGANIZATION,
    AUTHZEN_IDENTIFIER_KIND_PRINCIPAL,
    AUTHZEN_IDENTIFIER_KIND_GROUP,
    AUTHZEN_IDENTIFIER_KIND_RESOURCE,
    AUTHZEN_IDENTIFIER_KIND_ACTION,
    AUTHZEN_IDENTIFIER_KIND_TOOL,
    AUTHZEN_IDENTIFIER_KIND_CLIENT,
    AUTHZEN_IDENTIFIER_KIND_SESSION,
)


# AuthZENObligationType: a closed set of values the server may send.
#
# It is a plain str alias rather than an Enum so an unrecognised value from a newer
# server round-trips instead of raising on decode or landing on a neighbouring constant.
# Use AUTHZEN_OBLIGATION_TYPE_VALUES to tell a value this build knows from one it does
# not.
AuthZENObligationType: TypeAlias = str

AUTHZEN_OBLIGATION_TYPE_APPROVAL_CHALLENGE: Final[AuthZENObligationType] = "approval_challenge"
AUTHZEN_OBLIGATION_TYPE_FIELD_REMOVE: Final[AuthZENObligationType] = "field_remove"
AUTHZEN_OBLIGATION_TYPE_FIELD_REDACT: Final[AuthZENObligationType] = "field_redact"
AUTHZEN_OBLIGATION_TYPE_FIELD_HASH: Final[AuthZENObligationType] = "field_hash"
AUTHZEN_OBLIGATION_TYPE_FIELD_MASK: Final[AuthZENObligationType] = "field_mask"
AUTHZEN_OBLIGATION_TYPE_FIELD_ANNOTATE: Final[AuthZENObligationType] = "field_annotate"
AUTHZEN_OBLIGATION_TYPE_FIELD_TOKENIZE: Final[AuthZENObligationType] = "field_tokenize"
AUTHZEN_OBLIGATION_TYPE_SCHEMA_TRANSFORM: Final[AuthZENObligationType] = "schema_transform"
AUTHZEN_OBLIGATION_TYPE_RESPONSE_FILTER: Final[AuthZENObligationType] = "response_filter"
AUTHZEN_OBLIGATION_TYPE_ROUTE_RESTRICTION: Final[AuthZENObligationType] = "route_restriction"
AUTHZEN_OBLIGATION_TYPE_STEP_UP_AUTHENTICATION: Final[AuthZENObligationType] = (
    "step_up_authentication"
)
AUTHZEN_OBLIGATION_TYPE_QUOTA_RESERVATION: Final[AuthZENObligationType] = "quota_reservation"
AUTHZEN_OBLIGATION_TYPE_IMMUTABLE_AUDIT: Final[AuthZENObligationType] = "immutable_audit"
AUTHZEN_OBLIGATION_TYPE_NOTIFICATION: Final[AuthZENObligationType] = "notification"

# Every value of obligation_type this build knows, in the artifact's order.
AUTHZEN_OBLIGATION_TYPE_VALUES: Final[tuple[AuthZENObligationType, ...]] = (
    AUTHZEN_OBLIGATION_TYPE_APPROVAL_CHALLENGE,
    AUTHZEN_OBLIGATION_TYPE_FIELD_REMOVE,
    AUTHZEN_OBLIGATION_TYPE_FIELD_REDACT,
    AUTHZEN_OBLIGATION_TYPE_FIELD_HASH,
    AUTHZEN_OBLIGATION_TYPE_FIELD_MASK,
    AUTHZEN_OBLIGATION_TYPE_FIELD_ANNOTATE,
    AUTHZEN_OBLIGATION_TYPE_FIELD_TOKENIZE,
    AUTHZEN_OBLIGATION_TYPE_SCHEMA_TRANSFORM,
    AUTHZEN_OBLIGATION_TYPE_RESPONSE_FILTER,
    AUTHZEN_OBLIGATION_TYPE_ROUTE_RESTRICTION,
    AUTHZEN_OBLIGATION_TYPE_STEP_UP_AUTHENTICATION,
    AUTHZEN_OBLIGATION_TYPE_QUOTA_RESERVATION,
    AUTHZEN_OBLIGATION_TYPE_IMMUTABLE_AUDIT,
    AUTHZEN_OBLIGATION_TYPE_NOTIFICATION,
)


# AuthZENOperationalState: a closed set of values the server may send.
#
# It is a plain str alias rather than an Enum so an unrecognised value from a newer
# server round-trips instead of raising on decode or landing on a neighbouring constant.
# Use AUTHZEN_OPERATIONAL_STATE_VALUES to tell a value this build knows from one it does
# not.
AuthZENOperationalState: TypeAlias = str

AUTHZEN_OPERATIONAL_STATE_ALLOW: Final[AuthZENOperationalState] = "ALLOW"
AUTHZEN_OPERATIONAL_STATE_DENY: Final[AuthZENOperationalState] = "DENY"
AUTHZEN_OPERATIONAL_STATE_CHALLENGE: Final[AuthZENOperationalState] = "CHALLENGE"
AUTHZEN_OPERATIONAL_STATE_ERROR: Final[AuthZENOperationalState] = "ERROR"

# Every value of operational_state this build knows, in the artifact's order.
AUTHZEN_OPERATIONAL_STATE_VALUES: Final[tuple[AuthZENOperationalState, ...]] = (
    AUTHZEN_OPERATIONAL_STATE_ALLOW,
    AUTHZEN_OPERATIONAL_STATE_DENY,
    AUTHZEN_OPERATIONAL_STATE_CHALLENGE,
    AUTHZEN_OPERATIONAL_STATE_ERROR,
)


# AuthZENReasonCode: a closed set of values the server may send.
#
# It is a plain str alias rather than an Enum so an unrecognised value from a newer
# server round-trips instead of raising on decode or landing on a neighbouring constant.
# Use AUTHZEN_REASON_CODE_VALUES to tell a value this build knows from one it does not.
AuthZENReasonCode: TypeAlias = str

AUTHZEN_REASON_CODE_PERMITTED: Final[AuthZENReasonCode] = "permitted"
AUTHZEN_REASON_CODE_APPROVAL_REQUIRED: Final[AuthZENReasonCode] = "approval_required"
AUTHZEN_REASON_CODE_EXPLICIT_CONSTRAINT: Final[AuthZENReasonCode] = "explicit_constraint"
AUTHZEN_REASON_CODE_NO_MATCHING_PERMISSION: Final[AuthZENReasonCode] = "no_matching_permission"
AUTHZEN_REASON_CODE_UNKNOWN_CONSTRAINT: Final[AuthZENReasonCode] = "unknown_constraint"
AUTHZEN_REASON_CODE_UNKNOWN_PERMISSION: Final[AuthZENReasonCode] = "unknown_permission"
AUTHZEN_REASON_CODE_UNKNOWN_REQUIREMENT: Final[AuthZENReasonCode] = "unknown_requirement"
AUTHZEN_REASON_CODE_INVALID_INPUT: Final[AuthZENReasonCode] = "invalid_input"
AUTHZEN_REASON_CODE_EVALUATION_ERROR: Final[AuthZENReasonCode] = "evaluation_error"
AUTHZEN_REASON_CODE_UNSUPPORTED_OBLIGATION: Final[AuthZENReasonCode] = "unsupported_obligation"
AUTHZEN_REASON_CODE_OBLIGATION_CONFLICT: Final[AuthZENReasonCode] = "obligation_conflict"
AUTHZEN_REASON_CODE_UNKNOWN_ACTION: Final[AuthZENReasonCode] = "unknown_action"
AUTHZEN_REASON_CODE_UNKNOWN_REALM: Final[AuthZENReasonCode] = "unknown_realm"
AUTHZEN_REASON_CODE_SCHEMA_VIOLATION: Final[AuthZENReasonCode] = "schema_violation"
AUTHZEN_REASON_CODE_DELEGATION_DEPTH_EXCEEDED: Final[AuthZENReasonCode] = (
    "delegation_depth_exceeded"
)
AUTHZEN_REASON_CODE_BUDGET_EXHAUSTED: Final[AuthZENReasonCode] = "budget_exhausted"
AUTHZEN_REASON_CODE_BINDING_MISMATCH: Final[AuthZENReasonCode] = "binding_mismatch"
AUTHZEN_REASON_CODE_APPROVAL_UNSATISFIABLE: Final[AuthZENReasonCode] = "approval_unsatisfiable"
AUTHZEN_REASON_CODE_APPROVAL_EXPIRED: Final[AuthZENReasonCode] = "approval_expired"
AUTHZEN_REASON_CODE_AUTHORING_REJECTED: Final[AuthZENReasonCode] = "authoring_rejected"

# Every value of reason_code this build knows, in the artifact's order.
AUTHZEN_REASON_CODE_VALUES: Final[tuple[AuthZENReasonCode, ...]] = (
    AUTHZEN_REASON_CODE_PERMITTED,
    AUTHZEN_REASON_CODE_APPROVAL_REQUIRED,
    AUTHZEN_REASON_CODE_EXPLICIT_CONSTRAINT,
    AUTHZEN_REASON_CODE_NO_MATCHING_PERMISSION,
    AUTHZEN_REASON_CODE_UNKNOWN_CONSTRAINT,
    AUTHZEN_REASON_CODE_UNKNOWN_PERMISSION,
    AUTHZEN_REASON_CODE_UNKNOWN_REQUIREMENT,
    AUTHZEN_REASON_CODE_INVALID_INPUT,
    AUTHZEN_REASON_CODE_EVALUATION_ERROR,
    AUTHZEN_REASON_CODE_UNSUPPORTED_OBLIGATION,
    AUTHZEN_REASON_CODE_OBLIGATION_CONFLICT,
    AUTHZEN_REASON_CODE_UNKNOWN_ACTION,
    AUTHZEN_REASON_CODE_UNKNOWN_REALM,
    AUTHZEN_REASON_CODE_SCHEMA_VIOLATION,
    AUTHZEN_REASON_CODE_DELEGATION_DEPTH_EXCEEDED,
    AUTHZEN_REASON_CODE_BUDGET_EXHAUSTED,
    AUTHZEN_REASON_CODE_BINDING_MISMATCH,
    AUTHZEN_REASON_CODE_APPROVAL_UNSATISFIABLE,
    AUTHZEN_REASON_CODE_APPROVAL_EXPIRED,
    AUTHZEN_REASON_CODE_AUTHORING_REJECTED,
)


class AuthZENApprovalClause(_AuthZENModel):
    """One immutable threshold clause: a quorum of distinct approvers drawn from a named
    eligible set. It is named here rather than inlined under approval_requirement so it
    corresponds one-to-one with the Go ApprovalClause, which is what lets the drift
    guard compare it and every SDK generate it as a type rather than an anonymous shape.
    """

    quorum: int

    eligible: list[AuthZENIdentifier]

    @model_validator(mode="after")
    def _check_approval_clause(self) -> AuthZENApprovalClause:
        """Enforce the artifact rules pydantic's own annotations cannot carry. Nested
        models are validated by pydantic before this runs, so a violation deeper in the
        tree is reported at the member that carries it.
        """
        if self.eligible is not None and len(self.eligible) < 1:
            msg = "approval_clause: eligible needs at least 1 entry"
            raise ValueError(msg)
        return self


class AuthZENApprovalRequirement(_AuthZENModel):
    """A conjunction of immutable threshold clauses. Clauses are never collapsed by pool
    intersection or union.
    """

    all_of: list[AuthZENApprovalClause]

    separation_of_duties: bool

    expires_at: str

    @model_validator(mode="after")
    def _check_approval_requirement(self) -> AuthZENApprovalRequirement:
        """Enforce the artifact rules pydantic's own annotations cannot carry. Nested
        models are validated by pydantic before this runs, so a violation deeper in the
        tree is reported at the member that carries it.
        """
        if self.all_of is not None and len(self.all_of) < 1:
            msg = "approval_requirement: all_of needs at least 1 entry"
            raise ValueError(msg)
        return self


class AuthZENAction(_AuthZENModel):
    """The AuthZEN action object."""

    name: str

    properties: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_authzen_action(self) -> AuthZENAction:
        """Enforce the artifact rules pydantic's own annotations cannot carry. Nested
        models are validated by pydantic before this runs, so a violation deeper in the
        tree is reported at the member that carries it.
        """
        if self.name is not None and len(self.name) < 1:
            msg = (
                "authzen_action: name must be at least 1 character(s); it is present but too short"
            )
            raise ValueError(msg)
        return self


class AuthZENBulk(_AuthZENModel):
    """The plural envelope: a shared subject, action, resource and context at the top
    level, with one entry per decision. The number of decisions is fixed by the MAPPING,
    never by argument data, so an empty evaluations array is malformed rather than a
    request for zero decisions.
    """

    subject: AuthZENSubject | None = None

    action: AuthZENAction | None = None

    resource: AuthZENResource | None = None

    context: dict[str, Any] | None = None

    evaluations: list[AuthZENRequest]

    @model_validator(mode="after")
    def _check_authzen_bulk(self) -> AuthZENBulk:
        """Enforce the artifact rules pydantic's own annotations cannot carry. Nested
        models are validated by pydantic before this runs, so a violation deeper in the
        tree is reported at the member that carries it.
        """
        if self.evaluations is not None and len(self.evaluations) < 1:
            msg = "authzen_bulk: evaluations needs at least 1 entry"
            raise ValueError(msg)
        return self


class AuthZENEnvelope(_AuthZENModel):
    """The top level. Exactly two members are defined and exactly one may be PRESENT.
    Presence is decided on the KEY SET, not on a decoded pointer, so {"evaluation":
    {...}, "evaluations": null} carries both declared members and is malformed.
    """

    # The singular member. Unlike a plural entry it has no shared base to inherit from,
    # so it must carry its own subject, action and resource.
    evaluation: AuthZENRequest | None = None

    evaluations: AuthZENBulk | None = None

    @model_validator(mode="after")
    def _check_authzen_envelope(self) -> AuthZENEnvelope:
        """Enforce the artifact rules pydantic's own annotations cannot carry. Nested
        models are validated by pydantic before this runs, so a violation deeper in the
        tree is reported at the member that carries it.
        """
        present = sum(
            1
            for value in (
                self.evaluation,
                self.evaluations,
            )
            if value is not None
        )
        if present != 1:
            msg = (
                "authzen_envelope: exactly one of evaluation or evaluations must be set, "
                f"{present} are"
            )
            raise ValueError(msg)
        if self.evaluation is not None and self.evaluation.action is None:
            msg = (
                "authzen_envelope: evaluation has no action; it has no shared base to inherit one "
                "from"
            )
            raise ValueError(msg)
        if self.evaluation is not None and self.evaluation.resource is None:
            msg = (
                "authzen_envelope: evaluation has no resource; it has no shared base to inherit "
                "one from"
            )
            raise ValueError(msg)
        if self.evaluation is not None and self.evaluation.subject is None:
            msg = (
                "authzen_envelope: evaluation has no subject; it has no shared base to inherit one "
                "from"
            )
            raise ValueError(msg)
        return self


class AuthZENError(_AuthZENModel):
    """The structured refusal body, returned when a request could not be EVALUATED. It is a
    separate shape from the response rather than an extra member on it, because a
    refusal is not a decision: a response carrying decision=false says the request was
    evaluated and denied, and returning that for a request that was never evaluated
    would make 'denied' and 'unevaluable' the same event in every audit and every client
    branch.
    """

    code: AuthZENErrorCode

    pointer: str | None = None

    message: str

    supported: list[str] | None = None

    request_id: str | None = None

    @model_validator(mode="after")
    def _check_authzen_error(self) -> AuthZENError:
        """Enforce the artifact rules pydantic's own annotations cannot carry. Nested
        models are validated by pydantic before this runs, so a violation deeper in the
        tree is reported at the member that carries it.
        """
        if self.message is not None and len(self.message) < 1:
            msg = (
                "authzen_error: message must be at least 1 character(s); it is present but too "
                "short"
            )
            raise ValueError(msg)
        return self


class AuthZENRequest(_AuthZENModel):
    """One subject-action-resource-context evaluation. Every member is structurally
    OPTIONAL here because a plural-envelope entry inherits any member it omits from the
    envelope's shared base. The completeness invariant - that the MERGED entry carries a
    subject, an action and a resource - is a cross-object property this document cannot
    express, and AuthZENEnvelope.Project enforces it. A validator that passes this
    schema has therefore NOT established completeness, which is why the singular member
    below carries its own required list.
    """

    subject: AuthZENSubject | None = None

    action: AuthZENAction | None = None

    resource: AuthZENResource | None = None

    context: dict[str, Any] | None = None


class AuthZENResource(_AuthZENModel):
    """The AuthZEN resource object."""

    type: str

    id: str

    properties: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_authzen_resource(self) -> AuthZENResource:
        """Enforce the artifact rules pydantic's own annotations cannot carry. Nested
        models are validated by pydantic before this runs, so a violation deeper in the
        tree is reported at the member that carries it.
        """
        if self.type is not None and len(self.type) < 1:
            msg = (
                "authzen_resource: type must be at least 1 character(s); it is present but too "
                "short"
            )
            raise ValueError(msg)
        if self.id is not None and len(self.id) < 1:
            msg = (
                "authzen_resource: id must be at least 1 character(s); it is present but too short"
            )
            raise ValueError(msg)
        return self


class AuthZENResponse(_AuthZENModel):
    """The AuthZEN reply. `decision` is the collapsed boolean: ALLOW is true and every
    other state is false. It is the only member an un-negotiated enforcement point
    receives.
    """

    decision: bool

    context: AuthZENResponseContext | None = None


class AuthZENResponseContext(_AuthZENModel):
    """The versioned AxonFlow profile payload. It is present only for a Policy Enforcement
    Point that NEGOTIATED the profile; one that did not receives the boolean alone,
    because handing a partial interpretation to a plane that cannot act on it is the
    failure ADR-065 invariant 12 forbids.
    """

    # The only value the server sends is 'axonflow-authzen-profile-2026-08-29'.
    profile: str

    state: AuthZENOperationalState

    category: AuthZENCategory

    reason: AuthZENReasonCode | None = None

    obligations: list[AuthZENObligation] | None = None

    approval: AuthZENApprovalRequirement | None = None

    decision_id: str

    schema_version: str

    @model_validator(mode="after")
    def _check_authzen_response_context(self) -> AuthZENResponseContext:
        """Enforce the artifact rules pydantic's own annotations cannot carry. Nested
        models are validated by pydantic before this runs, so a violation deeper in the
        tree is reported at the member that carries it.
        """
        if self.decision_id is not None and len(self.decision_id) < 1:
            msg = (
                "authzen_response_context: decision_id must be at least 1 character(s); it is "
                "present but too short"
            )
            raise ValueError(msg)
        if self.schema_version is not None and len(self.schema_version) < 1:
            msg = (
                "authzen_response_context: schema_version must be at least 1 character(s); it is "
                "present but too short"
            )
            raise ValueError(msg)
        return self


class AuthZENSubject(_AuthZENModel):
    """The AuthZEN subject object. type and id are canonical identifier components; a
    display name, an email or a token claim is never one of them.
    """

    type: str

    id: str

    properties: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_authzen_subject(self) -> AuthZENSubject:
        """Enforce the artifact rules pydantic's own annotations cannot carry. Nested
        models are validated by pydantic before this runs, so a violation deeper in the
        tree is reported at the member that carries it.
        """
        if self.type is not None and len(self.type) < 1:
            msg = (
                "authzen_subject: type must be at least 1 character(s); it is present but too short"
            )
            raise ValueError(msg)
        if self.id is not None and len(self.id) < 1:
            msg = "authzen_subject: id must be at least 1 character(s); it is present but too short"
            raise ValueError(msg)
        return self


class AuthZENIdentifier(_AuthZENModel):
    """A canonical identifier. Display names, emails, token claims, connector names and
    aliases are never identifiers.
    """

    kind: AuthZENIdentifierKind

    type: str

    qualifier: str | None = None

    local: str

    @model_validator(mode="after")
    def _check_identifier(self) -> AuthZENIdentifier:
        """Enforce the artifact rules pydantic's own annotations cannot carry. Nested
        models are validated by pydantic before this runs, so a violation deeper in the
        tree is reported at the member that carries it.
        """
        if self.local is not None and len(self.local) < 1:
            msg = "identifier: local must be at least 1 character(s); it is present but too short"
            raise ValueError(msg)
        return self


class AuthZENObligation(_AuthZENModel):
    """One typed instruction owned by a named enforcement component."""

    type: AuthZENObligationType

    target: str | None = None

    params: dict[str, str] | None = None

    mandatory: bool

    source_policy: str

    schema_version: int

    @model_validator(mode="after")
    def _check_obligation(self) -> AuthZENObligation:
        """Enforce the artifact rules pydantic's own annotations cannot carry. Nested
        models are validated by pydantic before this runs, so a violation deeper in the
        tree is reported at the member that carries it.
        """
        if self.target is not None and len(self.target) < 1:
            msg = "obligation: target must be at least 1 character(s); it is present but too short"
            raise ValueError(msg)
        if self.source_policy is not None and len(self.source_policy) < 1:
            msg = (
                "obligation: source_policy must be at least 1 character(s); it is present but too "
                "short"
            )
            raise ValueError(msg)
        return self


__all__ = [
    "AUTHZEN_CATEGORY_ALLOWED",
    "AUTHZEN_CATEGORY_APPROVAL_REQUIRED",
    "AUTHZEN_CATEGORY_INVALID_REQUEST",
    "AUTHZEN_CATEGORY_NOT_PERMITTED",
    "AUTHZEN_CATEGORY_TEMPORARILY_UNAVAILABLE",
    "AUTHZEN_CATEGORY_VALUES",
    "AUTHZEN_CONTRACT_SCHEMA_VERSION",
    "AUTHZEN_ERROR_CODE_EVALUATION_UNAVAILABLE",
    "AUTHZEN_ERROR_CODE_INCOMPLETE_EVALUATION",
    "AUTHZEN_ERROR_CODE_MALFORMED_ENVELOPE",
    "AUTHZEN_ERROR_CODE_MISSING_EVALUABLE_CONTENT",
    "AUTHZEN_ERROR_CODE_UNEVALUABLE_ATTRIBUTE",
    "AUTHZEN_ERROR_CODE_UNSUPPORTED_ACTION",
    "AUTHZEN_ERROR_CODE_UNSUPPORTED_RESOURCE",
    "AUTHZEN_ERROR_CODE_UNSUPPORTED_SUBJECT",
    "AUTHZEN_ERROR_CODE_VALUES",
    "AUTHZEN_IDENTIFIER_KIND_ACTION",
    "AUTHZEN_IDENTIFIER_KIND_CLIENT",
    "AUTHZEN_IDENTIFIER_KIND_GROUP",
    "AUTHZEN_IDENTIFIER_KIND_ORGANIZATION",
    "AUTHZEN_IDENTIFIER_KIND_PRINCIPAL",
    "AUTHZEN_IDENTIFIER_KIND_RESOURCE",
    "AUTHZEN_IDENTIFIER_KIND_SESSION",
    "AUTHZEN_IDENTIFIER_KIND_TOOL",
    "AUTHZEN_IDENTIFIER_KIND_VALUES",
    "AUTHZEN_OBLIGATION_TYPE_APPROVAL_CHALLENGE",
    "AUTHZEN_OBLIGATION_TYPE_FIELD_ANNOTATE",
    "AUTHZEN_OBLIGATION_TYPE_FIELD_HASH",
    "AUTHZEN_OBLIGATION_TYPE_FIELD_MASK",
    "AUTHZEN_OBLIGATION_TYPE_FIELD_REDACT",
    "AUTHZEN_OBLIGATION_TYPE_FIELD_REMOVE",
    "AUTHZEN_OBLIGATION_TYPE_FIELD_TOKENIZE",
    "AUTHZEN_OBLIGATION_TYPE_IMMUTABLE_AUDIT",
    "AUTHZEN_OBLIGATION_TYPE_NOTIFICATION",
    "AUTHZEN_OBLIGATION_TYPE_QUOTA_RESERVATION",
    "AUTHZEN_OBLIGATION_TYPE_RESPONSE_FILTER",
    "AUTHZEN_OBLIGATION_TYPE_ROUTE_RESTRICTION",
    "AUTHZEN_OBLIGATION_TYPE_SCHEMA_TRANSFORM",
    "AUTHZEN_OBLIGATION_TYPE_STEP_UP_AUTHENTICATION",
    "AUTHZEN_OBLIGATION_TYPE_VALUES",
    "AUTHZEN_OPERATIONAL_STATE_ALLOW",
    "AUTHZEN_OPERATIONAL_STATE_CHALLENGE",
    "AUTHZEN_OPERATIONAL_STATE_DENY",
    "AUTHZEN_OPERATIONAL_STATE_ERROR",
    "AUTHZEN_OPERATIONAL_STATE_VALUES",
    "AUTHZEN_PROFILE_V1",
    "AUTHZEN_REASON_CODE_APPROVAL_EXPIRED",
    "AUTHZEN_REASON_CODE_APPROVAL_REQUIRED",
    "AUTHZEN_REASON_CODE_APPROVAL_UNSATISFIABLE",
    "AUTHZEN_REASON_CODE_AUTHORING_REJECTED",
    "AUTHZEN_REASON_CODE_BINDING_MISMATCH",
    "AUTHZEN_REASON_CODE_BUDGET_EXHAUSTED",
    "AUTHZEN_REASON_CODE_DELEGATION_DEPTH_EXCEEDED",
    "AUTHZEN_REASON_CODE_EVALUATION_ERROR",
    "AUTHZEN_REASON_CODE_EXPLICIT_CONSTRAINT",
    "AUTHZEN_REASON_CODE_INVALID_INPUT",
    "AUTHZEN_REASON_CODE_NO_MATCHING_PERMISSION",
    "AUTHZEN_REASON_CODE_OBLIGATION_CONFLICT",
    "AUTHZEN_REASON_CODE_PERMITTED",
    "AUTHZEN_REASON_CODE_SCHEMA_VIOLATION",
    "AUTHZEN_REASON_CODE_UNKNOWN_ACTION",
    "AUTHZEN_REASON_CODE_UNKNOWN_CONSTRAINT",
    "AUTHZEN_REASON_CODE_UNKNOWN_PERMISSION",
    "AUTHZEN_REASON_CODE_UNKNOWN_REALM",
    "AUTHZEN_REASON_CODE_UNKNOWN_REQUIREMENT",
    "AUTHZEN_REASON_CODE_UNSUPPORTED_OBLIGATION",
    "AUTHZEN_REASON_CODE_VALUES",
    "AUTHZEN_SOURCE_SCHEMA_SHA256",
    "AuthZENAction",
    "AuthZENApprovalClause",
    "AuthZENApprovalRequirement",
    "AuthZENBulk",
    "AuthZENCategory",
    "AuthZENEnvelope",
    "AuthZENError",
    "AuthZENErrorCode",
    "AuthZENIdentifier",
    "AuthZENIdentifierKind",
    "AuthZENObligation",
    "AuthZENObligationType",
    "AuthZENOperationalState",
    "AuthZENReasonCode",
    "AuthZENRequest",
    "AuthZENResource",
    "AuthZENResponse",
    "AuthZENResponseContext",
    "AuthZENSubject",
]
