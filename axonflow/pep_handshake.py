"""The PEP capability handshake: what this enforcement point can discharge.

A v11 platform lets an enforcement point (a PEP) declare, on each governed
call, the exact obligation types and schema versions it can carry out. The
declaration rides the ``X-Axonflow-PEP-Handshake`` header as the unpadded
base64url encoding of a JSON document::

    {"profile_version": 1, "pep_id": "...", "audience": "...",
     "capabilities": [{"type": "field_redact", "version": 1}]}

On an Enterprise deployment, an allow verdict carrying a mandatory obligation
the declared set cannot discharge becomes a deny, so the enforcement point is
never handed an instruction it would drop. A Community deployment records the
declaration and does not deny on it. A capability in a family the deployment's
edition does not issue is dropped from the declaration, counted and logged; the
request proceeds.

WHERE IT IS SENT. Four request planes read the header: ``decide``
(``/api/v1/decide``), the AuthZEN evaluation route (``evaluate`` and
``evaluate_all``), the MCP check routes (``mcp_check_input``,
``mcp_check_output`` and their ``check_tool_*`` aliases, which the fulfilment
helpers also use) and the gateway pre-check (``get_policy_approved_context``
and ``pre_check``). ``proxy_llm_call`` (``/api/request``) and the
OpenAI-compatible route do not read it, and the client never sends it there.

ABSENT IS NOT EMPTY. A client with no declaration sends no header, and the
platform behaves exactly as it did before the handshake existed. There is no
default declaration: only the caller knows what its enforcement point can
discharge. ``capabilities=()`` is a declaration that it discharges nothing,
which on Enterprise turns every allow that carries a mandatory obligation into
a deny. Omitting ``capabilities`` is refused.

The rules below are the platform's own. A declaration this module accepts is
one the platform's decoder accepts, and one it would refuse fails here, at
construction, naming the member, instead of as a 400 on the first governed
call.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import ClassVar, Final

from axonflow.authzen_types_gen import AUTHZEN_OBLIGATION_TYPE_VALUES
from axonflow.exceptions import PEPHandshakeError

__all__ = [
    "MAX_PEP_HANDSHAKE_BYTES",
    "MAX_PEP_HANDSHAKE_CAPABILITIES",
    "PEP_HANDSHAKE_HEADER",
    "PEP_HANDSHAKE_PROFILE_V1",
    "PEPCapability",
    "PEPHandshake",
]

PEP_HANDSHAKE_HEADER: Final = "X-Axonflow-PEP-Handshake"
#: The only handshake profile the platform reads. Matched exactly, never as a floor.
PEP_HANDSHAKE_PROFILE_V1: Final = 1
#: The longest header value the platform reads, in bytes of base64.
MAX_PEP_HANDSHAKE_BYTES: Final = 4096
#: The most capabilities one declaration may carry. A surplus is refused, not truncated.
MAX_PEP_HANDSHAKE_CAPABILITIES: Final = 64
_MAX_IDENTIFIER_BYTES: Final = 128

# Applied with fullmatch, never with a trailing "$": Python's "$" also matches
# before a trailing newline and the platform's does not, so "edge\n" would pass
# here and be refused there. Both patterns are ASCII-only, so once one matches,
# len() is the byte length the platform bounds.
#
# The pep_id excludes ":" because the platform builds the enforcement point's
# identifier as "client:<authenticated credential>:<pep_id>"; the audience is
# composed into nothing, and admits ":" and "/" so a URI is usable as one.
_PEP_ID_PATTERN: Final = re.compile(r"[a-z0-9][a-z0-9._-]*")
_AUDIENCE_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")

# The obligation types this build can name, from the vendored AuthZEN surface.
# A capability naming anything else is refused, as the platform refuses it: a
# capability neither side can identify is one neither side can match.
_OBLIGATION_TYPES: Final = frozenset(AUTHZEN_OBLIGATION_TYPE_VALUES)


def _refuse(detail: str, *, pointer: str) -> PEPHandshakeError:
    where = f"{pointer}: " if pointer else ""
    return PEPHandshakeError(f"{PEP_HANDSHAKE_HEADER}: {where}{detail}", pointer=pointer)


@dataclass(frozen=True, order=True)
class PEPCapability:
    """One obligation type, at one schema version, that the enforcement point can discharge.

    Matching is exact on both members. Instances order by ``(type, version)``,
    which is the platform's canonical order.

    Raises:
        PEPHandshakeError: ``type`` is not an obligation type this build
            declares, or ``version`` is not a positive integer.
    """

    type: str
    version: int

    def __post_init__(self) -> None:
        if not isinstance(self.type, str) or self.type not in _OBLIGATION_TYPES:
            msg = (
                f"names obligation type {self.type!r}, which is not one of "
                f"{sorted(_OBLIGATION_TYPES)}"
            )
            raise _refuse(msg, pointer="/capabilities")
        # bool is an int subclass and would encode as true, which the platform
        # refuses as not an integer. A version of 0 would match only an
        # obligation whose version was never set.
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version <= 0:
            msg = (
                f"declares {self.type!r} at version {self.version!r}; "
                "a version is a positive integer"
            )
            raise _refuse(msg, pointer="/capabilities")


@dataclass(frozen=True)
class PEPHandshake:
    """A capability declaration, validated and encoded once.

    Pass one to :class:`~axonflow.AxonFlow` as ``pep_handshake=`` to declare it
    on every call to a plane that reads it, or to one of those methods as
    ``pep_handshake=`` to declare it on that call only. One process can be two
    enforcement points (a request path and a response path discharging
    different obligations), and the per-call form is how each presents its own.

    Attributes:
        pep_id: Names this enforcement point within the client's credential:
            lower-case letters, digits, ``.``, ``_`` and ``-``, starting with a
            letter or digit, at most 128 bytes. The platform prefixes it with the
            authenticated credential, so it cannot name another client's
            enforcement point.
        audience: The audience this enforcement point expects a decision proof
            to be bound to, at most 128 bytes; a URI is the usual form. It is
            recorded and bound, and authorises nothing.
        capabilities: The exact set this enforcement point can discharge, stored
            in canonical order. Required; an empty sequence declares that it
            discharges nothing.
        header_value: The ``X-Axonflow-PEP-Handshake`` value this declaration is
            sent as. Two declarations of the same set in a different order
            encode to the same bytes.

    Raises:
        PEPHandshakeError: a member the platform would refuse. ``pointer``
            names it (``/pep_id``, ``/audience`` or ``/capabilities``), or is
            empty when the whole document encodes past the header's size limit.
    """

    PROFILE_VERSION: ClassVar[int] = PEP_HANDSHAKE_PROFILE_V1

    pep_id: str
    audience: str
    capabilities: Sequence[PEPCapability]
    header_value: str = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _require_identifier(self.pep_id, _PEP_ID_PATTERN, pointer="/pep_id")
        _require_identifier(self.audience, _AUDIENCE_PATTERN, pointer="/audience")
        capabilities = _canonical_capabilities(self.capabilities)
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "header_value", self._encode())

    def _encode(self) -> str:
        document = {
            "profile_version": self.PROFILE_VERSION,
            "pep_id": self.pep_id,
            "audience": self.audience,
            "capabilities": [{"type": c.type, "version": c.version} for c in self.capabilities],
        }
        raw = json.dumps(document, separators=(",", ":")).encode("ascii")
        encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
        if len(encoded) > MAX_PEP_HANDSHAKE_BYTES:
            msg = (
                f"encodes to {len(encoded)} bytes; "
                f"the header carries at most {MAX_PEP_HANDSHAKE_BYTES}"
            )
            raise _refuse(msg, pointer="")
        return encoded


def _require_identifier(value: object, pattern: re.Pattern[str], *, pointer: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > _MAX_IDENTIFIER_BYTES
        or pattern.fullmatch(value) is None
    ):
        msg = (
            f"{value!r} is not of the form {pattern.pattern} "
            f"with at most {_MAX_IDENTIFIER_BYTES} bytes"
        )
        raise _refuse(msg, pointer=pointer)


def _canonical_capabilities(value: object) -> tuple[PEPCapability, ...]:
    # A str is a Sequence and a Mapping iterates its keys; neither is a list of
    # capabilities, and accepting one would declare something nobody wrote.
    if value is None or isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Sequence):
        msg = (
            "is absent or not a sequence; a handshake exists to declare capabilities, "
            "and an enforcement point that discharges nothing declares an empty one"
        )
        raise _refuse(msg, pointer="/capabilities")
    if len(value) > MAX_PEP_HANDSHAKE_CAPABILITIES:
        msg = (
            f"declares {len(value)} capabilities; "
            f"the platform reads at most {MAX_PEP_HANDSHAKE_CAPABILITIES}"
        )
        raise _refuse(msg, pointer="/capabilities")
    seen: set[PEPCapability] = set()
    for capability in value:
        if not isinstance(capability, PEPCapability):
            msg = f"holds {capability!r}; every entry is a PEPCapability"
            raise _refuse(msg, pointer="/capabilities")
        if capability in seen:
            msg = (
                f"declares {capability.type!r} at version {capability.version} more than once; "
                "the platform refuses a repeated capability"
            )
            raise _refuse(msg, pointer="/capabilities")
        seen.add(capability)
    return tuple(sorted(value))
