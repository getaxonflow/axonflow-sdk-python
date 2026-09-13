"""Typed policy authoring: the v11 successor to the legacy policy routes.

A v11 platform authors policy as a typed document: validated, published as a
signed artifact pinned by its digest, and promoted to active. Six routes under
``/api/v1/typed-policies`` do that, and the agent proxies all six with this
client's credentials. Reach them as ``client.typed_policies``:

- ``edition()``: what this deployment may author, consulted BEFORE a publication
  rather than learned from a refusal.
- ``validate(document, fixtures)``: every finding for a candidate document. It
  answers identically on every edition; the edition's boundary applies at
  publication.
- ``publish(document, fixtures)``: validates, compiles, runs the declared
  fixtures, signs, and pins the artifact by its digest.
- ``activate(digest)``: promotes a published digest to active.
- ``active()``: the document in force, as the exact bytes that were signed, or
  ``None`` when nothing is active.
- ``system()``: the platform's own controls, read-only.

The organization and the author are the ones this client's credentials resolve
to. The agent stamps both, and neither can be named in a request.

Activation PROMOTES: a digest whose version does not advance past the active one
is refused. Rolling back to an earlier document and withdrawing the active one
are operations of the customer portal, behind its session; the agent does not
proxy them, so this module has no method for either.

On an edition with separation of duties, ``publish`` refuses every publication
with the finding code ``APPROVER_IS_AUTHOR``: publishing through this route names
no approver, and such a deployment approves in the customer portal.

Every refusal raises :class:`~axonflow.exceptions.TypedPolicyRefusal`, carrying
the HTTP status, the platform's ``reason`` and, where there are any, the
findings.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine
from typing import Annotated, Any

import httpx
from pydantic import BaseModel, BeforeValidator, Field

from axonflow.exceptions import AuthenticationError, AxonFlowError, TypedPolicyRefusal

__all__ = [
    "TYPED_POLICIES_PATH",
    "ActiveTypedPolicy",
    "AuthoringFinding",
    "EditionConstructReport",
    "SyncTypedPoliciesNamespace",
    "TypedAuthoringDocumentRequest",
    "TypedAuthoringEdition",
    "TypedPoliciesNamespace",
    "TypedPolicyActivation",
    "TypedPolicyPublication",
    "TypedPolicySystemControl",
    "TypedPolicySystemCorpus",
    "TypedPolicyValidation",
]

TYPED_POLICIES_PATH = "/api/v1/typed-policies"

#: The client's raw transport: ``send(method, path, *, json_data=None)``.
RawSend = Callable[..., Awaitable[httpx.Response]]

# The platform marshals a nil Go slice or map as JSON null, not [] or {}: a
# clean validation answers "findings": null (observed on a real stack). Every
# collection the platform declares without omitempty reads null as empty.
_NULL_AS_EMPTY_LIST = BeforeValidator(lambda v: [] if v is None else v)
_NULL_AS_EMPTY_DICT = BeforeValidator(lambda v: {} if v is None else v)


class EditionConstructReport(BaseModel):
    """What this edition may spend (the spec's ``EditionConstructReport``)."""

    edition: str | None = Field(default=None, description="community, evaluation or enterprise")
    obligation_families: Annotated[list[str], _NULL_AS_EMPTY_LIST] = Field(default_factory=list)
    attribute_namespaces: Annotated[list[str], _NULL_AS_EMPTY_LIST] = Field(default_factory=list)
    group_scope: bool | None = None
    separation_of_duties: bool | None = None
    tier_established: bool | None = None
    reserved: list[str] = Field(
        default_factory=list,
        description="Constructs withheld for want of an edition ruling, not by one.",
    )


class AuthoringFinding(BaseModel):
    """One declared save-time or publication result (the spec's ``AuthoringFinding``)."""

    code: str
    severity: str = Field(description="reject or warn")
    policy_id: str | None = None
    summary: str | None = Field(default=None, description="The declared, code-level sentence.")
    detail: str | None = Field(
        default=None, description="What was wrong, naming the offending value."
    )


class TypedAuthoringDocumentRequest(BaseModel):
    """A candidate document and its fixtures (the spec's ``TypedAuthoringDocumentRequest``)."""

    document: dict[str, Any]
    fixtures: list[dict[str, Any]] | None = None


class TypedAuthoringEdition(BaseModel):
    """What this deployment may author."""

    success: bool = False
    catalog: str | None = Field(default=None, description="The configured authoring vocabulary.")
    root: str | None = Field(
        default=None, description="The one authority root this surface publishes under."
    )
    max_documents: int | None = Field(
        default=None,
        description="Customer-authored documents admitted per organization; -1 is unlimited.",
    )
    constructs: EditionConstructReport | None = None
    persistence: str | None = Field(default=None, description="process, database or unavailable")
    signing_key_custody: str | None = None


class TypedPolicyValidation(BaseModel):
    """Every finding for a candidate document. ``success`` is false when any is a rejection."""

    success: bool = False
    findings: Annotated[list[AuthoringFinding], _NULL_AS_EMPTY_LIST] = Field(default_factory=list)


class TypedPolicyPublication(BaseModel):
    """A published artifact. Activation names ``digest``, never the version."""

    success: bool = False
    digest: str
    version: int | None = None
    findings: Annotated[list[AuthoringFinding], _NULL_AS_EMPTY_LIST] = Field(default_factory=list)


class TypedPolicyActivation(BaseModel):
    """The audited activation record."""

    success: bool = False
    activation: dict[str, Any] = Field(default_factory=dict)


class ActiveTypedPolicy(BaseModel):
    """The document in force.

    ``source`` is the exact byte sequence that was signed, decoded as UTF-8, so a
    caller can verify it; ``document`` is the same bytes parsed.
    """

    source: str
    document: dict[str, Any]


class TypedPolicySystemControl(BaseModel):
    """One shipped control, with what happens when it cannot be evaluated."""

    id: str
    authority: str | None = None
    assurance: str | None = Field(default=None, description="enforcement, gating_risk or advisory")
    mandatory: bool | None = None
    description: str | None = None
    obligations: list[dict[str, Any]] = Field(default_factory=list)


class TypedPolicySystemCorpus(BaseModel):
    """The platform's own controls: the system root activated beneath every organization."""

    root: str | None = None
    version: int | None = None
    digest: str | None = Field(
        default=None, description="The digest an enforcing engine anchors to."
    )
    authority: str | None = None
    controls: Annotated[list[TypedPolicySystemControl], _NULL_AS_EMPTY_LIST] = Field(
        default_factory=list
    )
    assurance_counts: Annotated[dict[str, int], _NULL_AS_EMPTY_DICT] = Field(default_factory=dict)
    document: Annotated[dict[str, Any], _NULL_AS_EMPTY_DICT] = Field(default_factory=dict)


def _raise_for_refusal(response: httpx.Response, route: str) -> None:
    """Raise the typed refusal for a non-2xx answer; return for a 2xx one."""
    status = response.status_code
    if status < 400:  # noqa: PLR2004
        return
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    error = body.get("error")
    message = error if isinstance(error, str) and error else f"HTTP {status} from {route}"
    if status == 401:  # noqa: PLR2004
        raise AuthenticationError(message)
    findings = body.get("findings")
    retry_after = response.headers.get("Retry-After", "")
    raise TypedPolicyRefusal(
        message,
        status=status,
        reason=body.get("reason"),
        code=body.get("code"),
        findings=[
            AuthoringFinding.model_validate(f)
            for f in (findings if isinstance(findings, list) else [])
            if isinstance(f, dict)
        ],
        retry_after=int(retry_after) if retry_after.isdigit() else None,
    )


class TypedPoliciesNamespace:
    """``client.typed_policies``: typed policy authoring through the agent.

    See :mod:`axonflow.typed_policies` for what each operation does and what it
    does not.
    """

    __slots__ = ("_send",)

    def __init__(self, send: RawSend) -> None:
        self._send = send

    async def _json(
        self, method: str, route: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        response = await self._send(method, f"{TYPED_POLICIES_PATH}{route}", json_data=payload)
        _raise_for_refusal(response, route)
        body = response.json()
        if not isinstance(body, dict):
            msg = (
                f"{TYPED_POLICIES_PATH}{route} answered {response.status_code} "
                "with a body that is not an object"
            )
            raise AxonFlowError(msg)
        return body

    async def edition(self) -> TypedAuthoringEdition:
        """What this deployment may author: its construct boundary and document ceiling."""
        return TypedAuthoringEdition.model_validate(await self._json("GET", "/edition"))

    async def validate(
        self, document: dict[str, Any], fixtures: list[dict[str, Any]] | None = None
    ) -> TypedPolicyValidation:
        """Every finding for a candidate document, ordered and complete.

        A document that is refused is still a successful validation: read
        ``success`` and the findings rather than expecting an exception.
        """
        request = TypedAuthoringDocumentRequest(document=document, fixtures=fixtures)
        body = await self._json("POST", "/validate", request.model_dump(exclude_none=True))
        return TypedPolicyValidation.model_validate(body)

    async def publish(
        self, document: dict[str, Any], fixtures: list[dict[str, Any]]
    ) -> TypedPolicyPublication:
        """Publish a document as a signed artifact, pinned by its digest.

        ``fixtures`` are the author-declared cases the publication gauntlet runs;
        a publication without any is refused, since no policy in the document has
        then been shown to do anything.

        Raises:
            TypedPolicyRefusal: 422 with the findings (``publication_refused`` or
                ``document_refused``; an edition boundary or
                ``APPROVER_IS_AUTHOR`` appears as a finding code), 402
                ``tier_limit``, 429 ``artifact_cap``, or 400 for a malformed
                request.
        """
        request = TypedAuthoringDocumentRequest(document=document, fixtures=fixtures)
        body = await self._json("POST", "/publish", request.model_dump(exclude_none=True))
        return TypedPolicyPublication.model_validate(body)

    async def activate(self, digest: str, *, reason: str | None = None) -> TypedPolicyActivation:
        """Promote a published digest to active. The activation is audited and names the caller.

        Raises:
            TypedPolicyRefusal: 409 ``activation_refused`` when the digest is not
                admitted, its version does not advance, its parent is not the
                active digest, or the caller may not activate it. Reload the
                active version and rebase. Rollback and withdraw are customer
                portal operations, not reachable here.
        """
        payload: dict[str, Any] = {"digest": digest}
        if reason is not None:
            payload["reason"] = reason
        return TypedPolicyActivation.model_validate(await self._json("POST", "/activate", payload))

    async def active(self) -> ActiveTypedPolicy | None:
        """The document in force, as the exact bytes that were signed, or ``None`` if nothing is."""
        response = await self._send("GET", f"{TYPED_POLICIES_PATH}/active")
        if response.status_code == 404:  # noqa: PLR2004
            return None
        _raise_for_refusal(response, "/active")
        return ActiveTypedPolicy(source=response.content.decode("utf-8"), document=response.json())

    async def system(self) -> TypedPolicySystemCorpus:
        """The platform's own controls, read-only: the shipped system corpus."""
        body = await self._json("GET", "/system")
        system = body.get("system")
        return TypedPolicySystemCorpus.model_validate(system if isinstance(system, dict) else {})


class SyncTypedPoliciesNamespace:
    """``client.typed_policies`` on the synchronous client; see :class:`TypedPoliciesNamespace`."""

    __slots__ = ("_namespace", "_run")

    def __init__(
        self, namespace: TypedPoliciesNamespace, run: Callable[[Coroutine[Any, Any, Any]], Any]
    ) -> None:
        self._namespace = namespace
        self._run = run

    def edition(self) -> TypedAuthoringEdition:
        """What this deployment may author."""
        return self._run(self._namespace.edition())  # type: ignore[no-any-return]

    def validate(
        self, document: dict[str, Any], fixtures: list[dict[str, Any]] | None = None
    ) -> TypedPolicyValidation:
        """Every finding for a candidate document."""
        return self._run(self._namespace.validate(document, fixtures))  # type: ignore[no-any-return]

    def publish(
        self, document: dict[str, Any], fixtures: list[dict[str, Any]]
    ) -> TypedPolicyPublication:
        """Publish a document as a signed artifact, pinned by its digest."""
        return self._run(self._namespace.publish(document, fixtures))  # type: ignore[no-any-return]

    def activate(self, digest: str, *, reason: str | None = None) -> TypedPolicyActivation:
        """Promote a published digest to active."""
        return self._run(self._namespace.activate(digest, reason=reason))  # type: ignore[no-any-return]

    def active(self) -> ActiveTypedPolicy | None:
        """The document in force, or ``None`` if nothing is."""
        return self._run(self._namespace.active())  # type: ignore[no-any-return]

    def system(self) -> TypedPolicySystemCorpus:
        """The platform's own controls, read-only."""
        return self._run(self._namespace.system())  # type: ignore[no-any-return]
