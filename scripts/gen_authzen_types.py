#!/usr/bin/env python3
"""Emit this SDK's AuthZEN wire types from the platform's canonical artifact.

Why the types are generated rather than written
-----------------------------------------------
The AuthZEN surface ships in five SDKs. Hand-transcribing the same twenty
shapes five times produces five slightly different opinions about which fields
are optional, and the resulting drift does not look like a bug: it looks like
one SDK marking a field required that the others mark optional, discovered by a
customer whose request is rejected by a server another SDK talks to happily.
The platform reduces its canonical JSON Schema to
``platform/decision/surface/authzen-surface.json``; every SDK vendors that one
file and generates from it, and every SDK's CI regenerates and diffs.

The generated file is committed
-------------------------------
A consumer running ``pip install axonflow`` must receive working types without
running a generator, so the output is committed. A committed generated file is
only worth anything if something proves it is the output of the current input,
which ``tests/test_authzen_generator.py`` does: it regenerates in memory and
compares bytes, so editing either the artifact or the generated file without
the other fails CI.

Usage::

    python3 scripts/gen_authzen_types.py            # write the module
    python3 scripts/gen_authzen_types.py --check    # fail if it is out of date
"""

from __future__ import annotations

import argparse
import hashlib
import json
import keyword
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SURFACE_PATH = REPO_ROOT / "tests" / "fixtures" / "authzen-surface.json"
OUTPUT_PATH = REPO_ROOT / "axonflow" / "authzen_types_gen.py"

# The artifact name and format version this emitter understands. A format
# change is a deliberate migration, not something to generate through: types
# that look right and describe a different contract are worse than a build
# failure.
SUPPORTED_ARTIFACT = "axonflow-authzen-surface"
SUPPORTED_ARTIFACT_VERSION = 1

# The sha256 of the vendored artifact FILE, verified byte-for-byte against
# `platform/decision/surface/authzen-surface.json` on axonflow-enterprise main
# (commit afff5d1a0) and against the copy in axonflow-sdk-go.
#
# This is the only control that pins FIDELITY. The regeneration gate answers
# "is the committed module the output of the committed artifact"; it says
# nothing about whether the committed artifact is the platform's, and an edit
# to the vendored file plus a regeneration satisfies it completely. The
# artifact's own `source_schema_sha256` cannot close that gap either: it is a
# string inside the file, so it moves with any edit that bothers to change it.
# Only a digest recorded OUTSIDE the file does, which is what this is.
#
# Bumping it is the deliberate act of re-vendoring: re-copy from the platform,
# put the new digest here, regenerate, and say in the PR which platform commit
# it came from.
VENDORED_ARTIFACT_SHA256 = "7f768b8ad0d6278d3531e1410decad172459808ebda627da44dca5bb4c9f36f8"

# Kinds the emitter can render. Anything else is refused rather than defaulted
# to ``Any`` — a silently permissive field compiles, ships, and accepts values
# the server refuses.
SCALAR_KINDS = {"string": "str", "bool": "bool", "int": "int"}


class SurfaceError(Exception):
    """The artifact is not something this emitter can generate from."""


# Python keywords plus the names pydantic reserves on a model. A field called
# `class` is a SyntaxError; one called `model_config` generates "successfully"
# and ships a package that raises on import.
_RESERVED_FIELD_NAMES = frozenset(keyword.kwlist) | {
    "model_config",
    "model_fields",
    "model_computed_fields",
    "model_extra",
    "model_fields_set",
}

# What a doc string may contain before it stops being safe to paste into a
# docstring or a comment. The artifact is first-party, so this is
# defence-in-depth rather than a live threat -- but "the emitter refuses what it
# cannot render" is this file's own claim, and a stray `"""` in a platform doc
# comment would otherwise emit a module that does not parse, or worse, one that
# parses into something nobody wrote.
_UNSAFE_IN_DOC = ('"""', "\\")


def _check_identifier(where: str, name: str) -> None:
    """Refuse a name that cannot become a Python attribute."""
    if not name.isidentifier():
        msg = f"{where}: {name!r} is not a valid Python identifier, so it cannot become a field"
        raise SurfaceError(msg)
    if name in _RESERVED_FIELD_NAMES:
        msg = (
            f"{where}: {name!r} is reserved by Python or by pydantic; a model declaring it "
            f"would fail to import rather than fail to generate"
        )
        raise SurfaceError(msg)
    if name.startswith("_"):
        # The fail-open the identifier check was written to prevent, hiding
        # inside it: `__init__` IS a valid identifier and is not a keyword, so
        # it passed - and pydantic treats a leading-underscore attribute as
        # private, so the member VANISHED from the model with no error
        # anywhere. No wire member starts with an underscore, so refusing the
        # whole shape is both correct and the only version of this check that
        # cannot be walked around.
        msg = (
            f"{where}: {name!r} starts with an underscore; pydantic treats such an "
            f"attribute as private, so the member would be silently dropped from the "
            f"generated model rather than generated wrongly"
        )
        raise SurfaceError(msg)


def _check_doc(where: str, doc: str) -> None:
    """Refuse doc text this emitter cannot safely place in a docstring."""
    for token in _UNSAFE_IN_DOC:
        if token in doc:
            msg = (
                f"{where}: the doc text contains {token!r}, which cannot be rendered into a "
                f"Python docstring without changing what the emitted module means"
            )
            raise SurfaceError(msg)


def _check_literal(where: str, value: str) -> None:
    """Refuse a value this emitter cannot safely place in a string literal."""
    if any(ch in value for ch in ("'", '"', "\\", "\n", "\r")):
        msg = (
            f"{where}: the value {value!r} carries a quote, a backslash or a newline, which "
            f"this emitter will not put inside a generated string literal"
        )
        raise SurfaceError(msg)


@dataclass(frozen=True)
class TypeRef:
    """A field's type."""

    kind: str
    ref: str = ""
    enum: str = ""
    items: TypeRef | None = None
    value: TypeRef | None = None


@dataclass(frozen=True)
class Field:
    """One member of a type."""

    name: str
    required: bool
    type: TypeRef
    doc: str = ""
    min_items: int = 0
    min_length: int = 0
    requires_members: tuple[str, ...] = ()
    const: str = ""


@dataclass(frozen=True)
class Type:
    """One object shape."""

    name: str
    fields: tuple[Field, ...]
    doc: str = ""
    exactly_one_of: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class Enum:
    """A closed set of string values."""

    name: str
    values: tuple[str, ...]
    doc: str = ""


@dataclass(frozen=True)
class Surface:
    """The whole artifact."""

    artifact: str
    artifact_version: int
    profile: str
    contract_schema_version: str
    source_schema_id: str
    source_schema_sha256: str
    enums: tuple[Enum, ...] = field(default=())
    types: tuple[Type, ...] = field(default=())


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

_SURFACE_MEMBERS = {
    "artifact",
    "artifact_version",
    "profile",
    "contract_schema_version",
    "source_schema_id",
    "source_schema_sha256",
    "enums",
    "types",
}
_ENUM_MEMBERS = {"name", "doc", "values"}
_TYPE_MEMBERS = {"name", "doc", "fields", "exactly_one_of"}
_FIELD_MEMBERS = {
    "name",
    "doc",
    "required",
    "type",
    "min_items",
    "min_length",
    "requires_members",
    "const",
}
_TYPEREF_MEMBERS = {"kind", "ref", "enum", "items", "value"}


def _reject_unknown(where: str, obj: dict[str, object], known: set[str]) -> None:
    """Refuse an artifact member this emitter does not understand.

    Strictness is the point. A member the platform added and this emitter
    ignores is a construct this SDK would silently omit — the
    declared-but-never-emitted class, arriving through the generator built to
    prevent it. Failing here costs one obvious CI error; ignoring it costs a
    field four other SDKs have and this one does not.
    """
    unknown = sorted(set(obj) - known)
    if unknown:
        msg = (
            f"{where}: the artifact carries {unknown}, which this emitter does not "
            f"understand. Generating around it would silently drop it from this SDK."
        )
        raise SurfaceError(msg)


def _parse_typeref(where: str, raw: object) -> TypeRef:
    if not isinstance(raw, dict):
        msg = f"{where}: a type must be an object, got {type(raw).__name__}"
        raise SurfaceError(msg)
    _reject_unknown(where, raw, _TYPEREF_MEMBERS)
    kind = raw.get("kind")
    if not isinstance(kind, str) or not kind:
        msg = f"{where}: a type must name a kind"
        raise SurfaceError(msg)
    items = _parse_typeref(f"{where}[]", raw["items"]) if "items" in raw else None
    value = _parse_typeref(f"{where}{{}}", raw["value"]) if "value" in raw else None
    return TypeRef(
        kind=kind,
        ref=str(raw.get("ref", "")),
        enum=str(raw.get("enum", "")),
        items=items,
        value=value,
    )


def _parse_field(where: str, raw: object) -> Field:
    if not isinstance(raw, dict):
        msg = f"{where}: a field must be an object"
        raise SurfaceError(msg)
    _reject_unknown(where, raw, _FIELD_MEMBERS)
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        msg = f"{where}: a field must be named"
        raise SurfaceError(msg)
    _check_identifier(f"{where}.{name}", name)
    doc = str(raw.get("doc", ""))
    _check_doc(f"{where}.{name}", doc)
    const = str(raw.get("const", ""))
    if const:
        _check_literal(f"{where}.{name}.const", const)
    # `required` is read STRICTLY. `bool("false")` is True, so a coerced read
    # makes the string "false" mean required here and optional in the sibling
    # SDK, which reads `=== true`. One artifact, two opposite answers, and the
    # regeneration gate is green in both.
    required = raw.get("required", False)
    if not isinstance(required, bool):
        msg = f"{where}.{name}: `required` must be a JSON boolean, got {required!r}"
        raise SurfaceError(msg)
    field_type = _parse_typeref(f"{where}.{name}", raw.get("type"))
    min_items = _parse_bound(f"{where}.{name}", "min_items", raw)
    min_length = _parse_bound(f"{where}.{name}", "min_length", raw)
    # A bound the emitter would silently drop is worse than one it cannot
    # render: a `min_length` on a bool looks like a live constraint in the
    # artifact and enforces nothing in the SDK.
    if min_items and field_type.kind != "array":
        msg = (
            f"{where}.{name}: `min_items` is declared on a {field_type.kind} field; it is "
            f"only meaningful on an array, and emitting nothing for it would leave a "
            f"constraint the artifact declares and no SDK enforces"
        )
        raise SurfaceError(msg)
    if min_length and field_type.kind not in {"string", "enum"}:
        msg = (
            f"{where}.{name}: `min_length` is declared on a {field_type.kind} field; it is "
            f"only meaningful on a string, and emitting nothing for it would leave a "
            f"constraint the artifact declares and no SDK enforces"
        )
        raise SurfaceError(msg)
    requires_members = tuple(str(m) for m in raw.get("requires_members", ()) or ())
    if len(set(requires_members)) != len(requires_members):
        msg = f"{where}.{name}: `requires_members` names the same member twice"
        raise SurfaceError(msg)
    return Field(
        name=name,
        required=required,
        type=field_type,
        doc=doc,
        min_items=min_items,
        min_length=min_length,
        requires_members=requires_members,
        const=const,
    )


def _parse_bound(where: str, member: str, raw: dict[str, object]) -> int:
    """Read a numeric bound, refusing one that would silently do nothing."""
    if member not in raw:
        return 0
    value = raw[member]
    if not isinstance(value, int) or isinstance(value, bool):
        msg = f"{where}: `{member}` must be a JSON integer, got {value!r}"
        raise SurfaceError(msg)
    if value < 0:
        msg = (
            f"{where}: `{member}` is {value}; a negative bound reads as a constraint and "
            f"disables one"
        )
        raise SurfaceError(msg)
    return value


def _parse_enum(raw: object, seen: set[str]) -> Enum:
    if not isinstance(raw, dict):
        msg = "an enum must be an object"
        raise SurfaceError(msg)
    _reject_unknown("enum", raw, _ENUM_MEMBERS)
    name = str(raw.get("name", ""))
    values = tuple(str(v) for v in raw.get("values", ()) or ())
    if not name:
        msg = "an enum must be named"
        raise SurfaceError(msg)
    if name in seen:
        msg = f"the artifact declares the enum {name!r} twice"
        raise SurfaceError(msg)
    if not values:
        msg = f"enum {name!r} has no values"
        raise SurfaceError(msg)
    if len(set(values)) != len(values):
        msg = f"enum {name!r} repeats a value"
        raise SurfaceError(msg)
    seen.add(name)
    doc = str(raw.get("doc", ""))
    _check_doc(f"enum {name}", doc)
    for value in values:
        _check_literal(f"enum {name}", value)
    return Enum(name=name, values=values, doc=doc)


def _parse_exactly_one_of(type_name_: str, raw: object, field_names: list[str]) -> tuple[str, ...]:
    members = tuple(str(m) for m in raw)  # type: ignore[union-attr]
    if len(members) < 2:  # noqa: PLR2004
        msg = f"type {type_name_!r} has an exactly-one-of group with {len(members)} members"
        raise SurfaceError(msg)
    if len(set(members)) != len(members):
        msg = f"type {type_name_!r} names the same member twice in an exactly-one-of group"
        raise SurfaceError(msg)
    for member in members:
        if member not in field_names:
            msg = (
                f"type {type_name_!r} names {member!r} in an exactly-one-of group "
                f"but has no such field"
            )
            raise SurfaceError(msg)
    return members


def _parse_type(raw: object, seen: set[str]) -> Type:
    if not isinstance(raw, dict):
        msg = "a type must be an object"
        raise SurfaceError(msg)
    _reject_unknown("type", raw, _TYPE_MEMBERS)
    name = str(raw.get("name", ""))
    if not name:
        msg = "a type must be named"
        raise SurfaceError(msg)
    if name in seen:
        msg = f"the artifact declares the type {name!r} twice"
        raise SurfaceError(msg)
    seen.add(name)
    fields = tuple(_parse_field(name, f) for f in raw.get("fields", ()) or ())
    if not fields:
        msg = f"type {name!r} has no fields"
        raise SurfaceError(msg)
    field_names = [f.name for f in fields]
    if len(set(field_names)) != len(field_names):
        msg = f"type {name!r} declares a field twice"
        raise SurfaceError(msg)
    groups = tuple(
        _parse_exactly_one_of(name, group, field_names)
        for group in raw.get("exactly_one_of", ()) or ()
    )
    doc = str(raw.get("doc", ""))
    _check_doc(f"type {name}", doc)
    return Type(name=name, fields=fields, doc=doc, exactly_one_of=groups)


def parse_surface(raw_bytes: bytes) -> Surface:
    """Decode the artifact strictly and check that it hangs together.

    Every reference must resolve inside the document. A dangling one would
    otherwise become a Python name that does not exist, and the failure would
    surface as an ImportError in generated code rather than as a statement
    about the artifact.
    """
    try:
        doc = json.loads(raw_bytes)
    except ValueError as exc:
        msg = f"the surface artifact is not valid JSON: {exc}"
        raise SurfaceError(msg) from exc
    if not isinstance(doc, dict):
        msg = "the surface artifact must be a JSON object"
        raise SurfaceError(msg)
    _reject_unknown("artifact", doc, _SURFACE_MEMBERS)

    seen_enums: set[str] = set()
    enums = tuple(_parse_enum(raw, seen_enums) for raw in doc.get("enums", ()) or ())
    seen_types: set[str] = set()
    types = tuple(_parse_type(raw, seen_types) for raw in doc.get("types", ()) or ())

    for member in (
        "artifact",
        "profile",
        "contract_schema_version",
        "source_schema_id",
        "source_schema_sha256",
    ):
        # The class, not the enumerated sites. The first pass swept type, field
        # and enum docs and enum values; these five are copied into code by the
        # header emitter and were left unchecked, so a quote in `profile` still
        # emitted a module that does not parse.
        _check_literal(f"artifact.{member}", str(doc.get(member, "")))
    surface = Surface(
        artifact=str(doc.get("artifact", "")),
        artifact_version=int(doc.get("artifact_version", 0) or 0),
        profile=str(doc.get("profile", "")),
        contract_schema_version=str(doc.get("contract_schema_version", "")),
        source_schema_id=str(doc.get("source_schema_id", "")),
        source_schema_sha256=str(doc.get("source_schema_sha256", "")),
        enums=enums,
        types=types,
    )
    _check_references(surface, seen_types, seen_enums)
    _check_consts_are_enforced(surface)
    return surface


def _check_consts_are_enforced(surface: Surface) -> None:
    """Refuse a ``const`` no enforcement site covers.

    The emitter deliberately renders no check for ``const``: the only one the
    contract declares is the response context's profile, and the hand-written
    client refuses an unreadable profile with a message that names the version
    it does speak. Two checks put the generated one in front of the actionable
    one and made the actionable one dead code.

    That is right for the profile and wrong for anything else, and the emitter's
    own rule for bounds says why: a constraint the artifact declares and no SDK
    enforces is worse than one the emitter cannot render, because it looks
    enforced. So a ``const`` whose value is the profile is allowed through to
    the client's check, and any OTHER const fails here - loudly, at the moment
    the platform adds it, rather than silently in five SDKs.
    """
    for type_ in surface.types:
        for field_ in type_.fields:
            if field_.const and field_.const != surface.profile:
                msg = (
                    f"{type_.name}.{field_.name} declares const {field_.const!r}, which no "
                    f"enforcement site covers. The one const this emitter generates through "
                    f"is the negotiated profile, which the client refuses by name; any other "
                    f"would be a constraint the artifact declares and no SDK enforces. Teach "
                    f"the emitter to render it, or teach the client to check it, before "
                    f"adding it to the contract."
                )
                raise SurfaceError(msg)


def _check_references(surface: Surface, types: set[str], enums: set[str]) -> None:
    for type_ in surface.types:
        for field_ in type_.fields:
            _check_ref(f"{type_.name}.{field_.name}", field_.type, types, enums)
            for member in field_.requires_members:
                target = field_.type.ref
                if field_.type.kind != "ref" or not target:
                    msg = (
                        f"{type_.name}.{field_.name} declares requires_members on a "
                        f"non-reference field; there is no type to require them of"
                    )
                    raise SurfaceError(msg)
                referenced = next(t for t in surface.types if t.name == target)
                if member not in {f.name for f in referenced.fields}:
                    msg = (
                        f"{type_.name}.{field_.name} requires the member {member!r} of "
                        f"{target!r}, which has no such field"
                    )
                    raise SurfaceError(msg)


def _check_container_item(where: str, item: TypeRef) -> None:
    """Refuse a container nested inside a container.

    The artifact declares none today. Refusing rather than rendering keeps the
    two SDKs in step: the sibling emitter cannot render a nested container
    either, and a construct one SDK generates for and the other refuses is a
    release where four SDKs ship and one does not build.
    """
    if item.kind in {"array", "map"}:
        msg = (
            f"{where} nests a {item.kind} inside a container; no SDK emitter renders that "
            f"yet, and generating for it in one language and not another is how a "
            f"five-SDK release becomes a four-SDK release"
        )
        raise SurfaceError(msg)


def _check_ref(where: str, ref: TypeRef, types: set[str], enums: set[str]) -> None:
    if ref.kind == "ref":
        if ref.ref not in types:
            msg = f"{where} references the type {ref.ref!r}, which the artifact does not define"
            raise SurfaceError(msg)
    elif ref.kind == "enum":
        if ref.enum not in enums:
            msg = f"{where} references the enum {ref.enum!r}, which the artifact does not define"
            raise SurfaceError(msg)
    elif ref.kind == "array":
        if ref.items is None:
            msg = f"{where} is an array with no item type"
            raise SurfaceError(msg)
        _check_container_item(f"{where}[]", ref.items)
        _check_ref(f"{where}[]", ref.items, types, enums)
    elif ref.kind == "map":
        if ref.value is None:
            msg = f"{where} is a map with no value type"
            raise SurfaceError(msg)
        _check_container_item(f"{where}{{}}", ref.value)
        _check_ref(f"{where}{{}}", ref.value, types, enums)
    elif ref.kind == "object":
        pass
    elif ref.kind not in SCALAR_KINDS:
        msg = f"{where} has the unsupported type kind {ref.kind!r}"
        raise SurfaceError(msg)


# --------------------------------------------------------------------------
# Naming
# --------------------------------------------------------------------------
#
# Every generated name carries the AuthZEN prefix. It is not decoration: this
# package already exports `Obligation`-shaped names from the PEP surface, and a
# generated type of the same name would shadow one of them on `from axonflow
# import *`. Prefixing everything, rather than only what collides today, keeps
# the rule mechanical — a future collision does not require inventing a new
# convention under time pressure.
#
# NOTE ON PLURALISATION: nothing here appends "s" to a derived name. The
# per-enum value tuple is `<ENUM>_VALUES`, not `All<Type>s()`, because a naive
# pluraliser produces names like `AllAuthZENCategorys` — and every generated
# name is a public compatibility commitment through v11, so a name that reads
# as a typo is one this SDK would have to carry for two major releases.


def _pascal(text: str) -> str:
    parts = [p for p in text.replace(".", "_").replace("-", "_").split("_") if p]
    return "".join(p[:1].upper() + p[1:] for p in parts)


def type_name(artifact_name: str) -> str:
    """Map an artifact type name onto this SDK's exported class name."""
    return "AuthZEN" + _pascal(artifact_name.removeprefix("authzen_"))


def enum_type_name(artifact_name: str) -> str:
    """Map an artifact enum name onto this SDK's exported type-alias name."""
    return "AuthZEN" + _pascal(artifact_name.removeprefix("authzen_"))


def enum_values_name(artifact_name: str) -> str:
    """The module constant holding every value of an enum."""
    return "AUTHZEN_" + artifact_name.removeprefix("authzen_").upper() + "_VALUES"


def enum_const_name(artifact_name: str, value: str) -> str:
    """The module constant for one enum value.

    The value's own word boundaries are preserved rather than collapsed: the
    artifact spells values either lower_snake (``not_permitted``) or upper
    (``ALLOW``), and upper-casing in place renders both correctly. Routing them
    through a PascalCase helper first would emit
    ``AUTHZEN_ERROR_CODE_MALFORMEDENVELOPE`` — a public name, unreadable, and
    frozen for two major releases.
    """
    stem = artifact_name.removeprefix("authzen_").upper()
    normalised = value.upper().replace("-", "_").replace(".", "_")
    return f"AUTHZEN_{stem}_{normalised}"


def field_name(wire_name: str) -> str:
    """Python attribute name for a wire member.

    The artifact's members are already lower_snake_case, which is Python's own
    convention, so the wire name IS the attribute name. Keeping them identical
    means a reader never has to hold a translation table, and a JSON Pointer
    from a server refusal names the attribute the caller wrote.
    """
    return wire_name


# --------------------------------------------------------------------------
# Emission
# --------------------------------------------------------------------------

# The column limit this repository lints at (`[tool.ruff] line-length = 100`).
# Generated code is linted and formatted like every other module in the
# package, so the emitter has to respect it: a generated file nobody may
# hand-edit cannot be fixed by hand when it fails E501.
_MAX_COLUMNS = 100
# Prose is wrapped narrower than the hard limit so comments and docstrings read
# like the rest of the package rather than running to the margin.
_LINE_WIDTH = 88


def _wrap(text: str, width: int, indent: str, first_indent: str | None = None) -> list[str]:
    words = text.split()
    if not words:
        return []
    lead = first_indent if first_indent is not None else indent
    lines: list[str] = []
    current = lead + words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) > width:
            lines.append(current)
            current = indent + word
        else:
            current += " " + word
    lines.append(current)
    return lines


def _docstring(doc: str, indent: str) -> list[str]:
    """Render an artifact doc string as a Python docstring block."""
    if not doc:
        return []
    body = _wrap(doc, _LINE_WIDTH, indent)
    if len(body) == 1:
        return [f'{indent}"""{body[0].strip()}"""']
    out = [f'{indent}"""{body[0].strip()}']
    out.extend(body[1:])
    out.append(f'{indent}"""')
    return out


def _annotation(surface: Surface, ref: TypeRef, *, optional: bool) -> str:
    """Render one field's type annotation.

    Enums are annotated ``str`` rather than a ``Literal``. A newer server may
    send a value added after this SDK was built, and a ``Literal`` would make
    that a decode failure on the response path — turning a decision this build
    could still fail closed on into an exception with no decision at all. The
    generated ``*_VALUES`` tuple is how a caller tells a known value from one
    this build does not know.
    """
    inner = _bare_annotation(surface, ref)
    return f"{inner} | None" if optional else inner


def _bare_annotation(surface: Surface, ref: TypeRef) -> str:
    if ref.kind in SCALAR_KINDS:
        return SCALAR_KINDS[ref.kind]
    if ref.kind == "enum":
        # The alias IS `str`, so this changes no runtime behaviour. It changes
        # what a reader and an IDE see: `code: AuthZENErrorCode` names the
        # closed set the value is drawn from, where a bare `str` names nothing.
        return enum_type_name(ref.enum)
    if ref.kind == "object":
        return "dict[str, Any]"
    if ref.kind == "ref":
        return type_name(ref.ref)
    if ref.kind == "array":
        assert ref.items is not None  # noqa: S101 - parse_surface refuses items-less arrays
        return f"list[{_bare_annotation(surface, ref.items)}]"
    if ref.kind == "map":
        assert ref.value is not None  # noqa: S101 - parse_surface refuses value-less maps
        return f"dict[str, {_bare_annotation(surface, ref.value)}]"
    msg = f"unsupported type kind {ref.kind!r}"
    raise SurfaceError(msg)


def _emit_enum(out: list[str], enum: Enum) -> None:
    alias = enum_type_name(enum.name)
    values_const = enum_values_name(enum.name)
    out.append(f"# {alias}: a closed set of values the server may send.")
    out.append("#")
    out.extend(
        _wrap(
            "It is a plain str alias rather than an Enum so an unrecognised value from a "
            "newer server round-trips instead of raising on decode or landing on a "
            f"neighbouring constant. Use {values_const} to tell a value this build knows "
            "from one it does not.",
            _LINE_WIDTH,
            "# ",
        )
    )
    out.append(f"{alias}: TypeAlias = str")
    out.append("")
    for value in enum.values:
        _emit_assignment(
            out, "", f"{enum_const_name(enum.name, value)}: Final[{alias}]", f'"{value}"'
        )
    out.append("")
    out.append(f"# Every value of {enum.name} this build knows, in the artifact's order.")
    out.append(f"{values_const}: Final[tuple[{alias}, ...]] = (")
    for value in enum.values:
        out.append(f"    {enum_const_name(enum.name, value)},")
    out.append(")")
    out.append("")
    out.append("")


def _emit_type(out: list[str], surface: Surface, type_: Type) -> None:
    name = type_name(type_.name)
    out.append(f"class {name}(_AuthZENModel):")
    doc = type_.doc or f"{name} is part of the AuthZEN wire surface."
    out.extend(_docstring(doc, "    "))
    out.append("")
    for field_ in type_.fields:
        if field_.doc:
            for line in _wrap(field_.doc, _LINE_WIDTH, "    # "):
                out.append(line)
        if field_.const:
            out.append(f"    # The only value the server sends is {field_.const!r}.")
        annotation = _annotation(surface, field_.type, optional=not field_.required)
        if field_.required:
            out.append(f"    {field_name(field_.name)}: {annotation}")
        else:
            out.append(f"    {field_name(field_.name)}: {annotation} = None")
        out.append("")
    _emit_validate(out, type_)
    out.append("")


def _emit_assignment(out: list[str], indent: str, target: str, literal: str) -> None:
    """Emit ``<target> = <literal>``, parenthesising it when it will not fit.

    This mirrors what the repository's formatter would do to an over-long
    assignment, which is what keeps the generated file stable under
    ``ruff format --check``: the emitter has to produce the ALREADY-formatted
    shape, because a file the formatter would rewrite is a file the
    regeneration gate reports as drift on every unrelated pull request until
    somebody deletes the gate.
    """
    single = f"{indent}{target} = {literal}"
    if len(single) <= _MAX_COLUMNS:
        out.append(single)
        return
    out.append(f"{indent}{target} = (")
    out.append(f"{indent}    {literal}")
    out.append(f"{indent})")


def _emit_message(
    out: list[str],
    indent: str,
    text: str,
    *,
    trailing_expr: str | None = None,
) -> None:
    """Emit ``msg = ...`` at a width the formatter will not rewrite.

    Three shapes, chosen by length, because the formatter joins an implicit
    concatenation whose joined form fits and splits an assignment whose single
    line does not. Emitting the shape it would have chosen means it has nothing
    to change:

    1. it fits on one line          -> ``msg = "..."``
    2. it fits on one INDENTED line -> ``msg = (\\n    "..."\\n)``
    3. neither                      -> a wrapped implicit concatenation, whose
       joined form provably exceeds the limit, so it is not re-joined.
    """
    body = text if trailing_expr is None else text + trailing_expr
    prefix = "" if trailing_expr is None else "f"
    if len(f'{indent}msg = {prefix}"{body}"') <= _MAX_COLUMNS:
        out.append(f'{indent}msg = {prefix}"{body}"')
        return
    if len(f'{indent}    {prefix}"{body}"') <= _MAX_COLUMNS:
        out.append(f"{indent}msg = (")
        out.append(f'{indent}    {prefix}"{body}"')
        out.append(f"{indent})")
        return
    # Wrapped. Each chunk carries the `f` prefix only when it contains the
    # placeholder: an `f` on a chunk without one is an F541 finding on a file
    # that cannot be hand-fixed.
    chunks = _wrap(text, _MAX_COLUMNS - len(indent) - 7, "")
    if trailing_expr is not None:
        chunks.append(trailing_expr)
    out.append(f"{indent}msg = (")
    for i, chunk in enumerate(chunks):
        last = i == len(chunks) - 1
        chunk_prefix = "f" if (trailing_expr is not None and last) else ""
        space = "" if last else " "
        out.append(f'{indent}    {chunk_prefix}"{chunk}{space}"')
    out.append(f"{indent})")


def _emit_validate(out: list[str], type_: Type) -> None:
    """Render the checks the type system cannot carry.

    Pydantic already enforces required-ness and the annotated types. What it
    cannot express is the envelope's exactly-one-of rule, the singular member's
    own required set, a minimum item count, and a minimum string length — and a
    model without those validates happily and builds requests the server
    refuses, which is the least useful place for a caller to find out.

    The ``const`` on ``authzen_response_context.profile`` is deliberately NOT
    enforced here. The client refuses a profile it cannot read with a message
    that names the version it does speak and tells the caller to upgrade; a
    second enforcement site would fire first with a worse message, and two
    copies of one rule are how the two drift.
    """
    checks: list[str] = []

    for group in type_.exactly_one_of:
        checks.append("        present = sum(")
        checks.append("            1")
        checks.append("            for value in (")
        for member in group:
            checks.append(f"                self.{field_name(member)},")
        checks.append("            )")
        checks.append("            if value is not None")
        checks.append("        )")
        checks.append("        if present != 1:")
        _emit_message(
            checks,
            "            ",
            f"{type_.name}: exactly one of {' or '.join(group)} must be set, ",
            trailing_expr="{present} are",
        )
        checks.append("            raise ValueError(msg)")

    for field_ in type_.fields:
        attr = field_name(field_.name)
        # Guaranteed to be an array: the parser refuses `min_items` anywhere
        # else, so this branch cannot silently apply a list bound to a string.
        if field_.min_items > 0 and field_.type.kind == "array":
            plural = "entry" if field_.min_items == 1 else "entries"
            checks.append(
                f"        if self.{attr} is not None and len(self.{attr}) < {field_.min_items}:"
            )
            _emit_message(
                checks,
                "            ",
                f"{type_.name}: {field_.name} needs at least {field_.min_items} {plural}",
            )
            checks.append("            raise ValueError(msg)")
        # min_length is enforced on the VALUE, not on presence. A REQUIRED
        # member with min_length 1 is already non-None; the case a
        # required-only check misses entirely is an OPTIONAL one
        # (obligation.target) that is present and blank, which the server
        # refuses and a caller would otherwise learn about from a 422.
        if field_.min_length > 0 and field_.type.kind in {"string", "enum"}:
            checks.append(
                f"        if self.{attr} is not None and len(self.{attr}) < {field_.min_length}:"
            )
            _emit_message(
                checks,
                "            ",
                f"{type_.name}: {field_.name} must be at least {field_.min_length} "
                f"character(s); it is present but too short",
            )
            checks.append("            raise ValueError(msg)")
        for member in field_.requires_members:
            target_attr = field_name(member)
            checks.append(
                f"        if self.{attr} is not None and self.{attr}.{target_attr} is None:"
            )
            _emit_message(
                checks,
                "            ",
                f"{type_.name}: {field_.name} has no {member}; it has no shared base to "
                f"inherit one from",
            )
            checks.append("            raise ValueError(msg)")

    if not checks:
        return

    out.append('    @model_validator(mode="after")')
    out.append(f"    def _check_{type_.name}(self) -> {type_name(type_.name)}:")
    out.extend(
        _docstring(
            "Enforce the artifact rules pydantic's own annotations cannot carry. "
            "Nested models are validated by pydantic before this runs, so a violation "
            "deeper in the tree is reported at the member that carries it.",
            "        ",
        )
    )
    out.extend(checks)
    out.append("        return self")
    out.append("")


def _check_emittable(surface: Surface) -> None:
    """Refuse an artifact this emitter must not generate from.

    The empty check is the anti-vacuity one: a generator that renders an empty
    surface produces a file that compiles, imports, exports nothing, and passes
    every byte-comparison test written against it.
    """
    if not surface.types or not surface.enums:
        msg = (
            f"the artifact describes {len(surface.types)} types and {len(surface.enums)} "
            f"enums; generating from an empty surface would silently produce an empty SDK"
        )
        raise SurfaceError(msg)
    if surface.artifact != SUPPORTED_ARTIFACT:
        msg = (
            f"{SURFACE_PATH.name} is not an AuthZEN surface artifact "
            f"(artifact={surface.artifact!r})"
        )
        raise SurfaceError(msg)
    if surface.artifact_version != SUPPORTED_ARTIFACT_VERSION:
        msg = (
            f"artifact format version {surface.artifact_version} is not supported by this "
            f"emitter; a format change is a deliberate migration, not something to "
            f"generate through"
        )
        raise SurfaceError(msg)


def _emit_header(out: list[str], surface: Surface) -> None:
    out.append('"""AuthZEN wire types. GENERATED FILE — DO NOT EDIT.')
    out.append("")
    out.append("Source: tests/fixtures/authzen-surface.json")
    out.append(f"  artifact:        {surface.artifact} v{surface.artifact_version}")
    out.append(f"  profile:         {surface.profile}")
    out.append(f"  contract schema: {surface.contract_schema_version}")
    out.append(f"  schema digest:   {surface.source_schema_sha256}")
    out.append("")
    out.append("Regenerate with::")
    out.append("")
    out.append("    python3 scripts/gen_authzen_types.py")
    out.append("")
    out.append("Editing this file by hand is pointless: tests/test_authzen_generator.py")
    out.append("regenerates it in memory and compares bytes, so a hand edit fails CI on the")
    out.append("next run.")
    out.append('"""')
    out.append("")
    out.append("from __future__ import annotations")
    out.append("")
    out.append("from typing import Any, Final, TypeAlias")
    out.append("")
    out.append("from pydantic import BaseModel, ConfigDict, model_validator")
    out.append("")
    out.append("")
    out.append("class _AuthZENModel(BaseModel):")
    out.extend(
        _docstring(
            'Base for every generated AuthZEN model. extra="forbid" is the decode-side '
            "half of the surface's central rule: an unknown member in a decision is a "
            "server speaking a profile this build does not understand, and quietly "
            "dropping it would mean acting on a partial reading of an authorization "
            "decision. On the request side it catches a member the caller invented "
            "before it becomes a 422.",
            "    ",
        )
    )
    out.append("")
    out.append('    model_config = ConfigDict(extra="forbid")')
    out.append("")
    out.append("")
    out.append("# The profile a Policy Enforcement Point negotiates to receive anything beyond")
    out.append("# the boolean decision. AuthZEN 1.0's response is a bare boolean; the")
    out.append("# four-valued state, the obligations, the approval challenge and the safe reason")
    out.append("# code all ride in the response context and are returned ONLY to a caller that")
    out.append("# asked for them by version.")
    _emit_assignment(out, "", "AUTHZEN_PROFILE_V1: Final", f'"{surface.profile}"')
    out.append("")
    out.append("# The contract version these types were generated from. It is the value the")
    out.append("# server echoes in AuthZENResponseContext.schema_version.")
    _emit_assignment(
        out, "", "AUTHZEN_CONTRACT_SCHEMA_VERSION: Final", f'"{surface.contract_schema_version}"'
    )
    out.append("")
    out.append("# The digest of the JSON Schema the artifact was reduced from. It is carried so")
    out.append("# a support conversation can establish which contract a deployed SDK was built")
    out.append("# against without reading its dependency tree.")
    _emit_assignment(
        out, "", "AUTHZEN_SOURCE_SCHEMA_SHA256: Final", f'"{surface.source_schema_sha256}"'
    )
    out.append("")
    out.append("")


def _emit_all(out: list[str], surface: Surface) -> None:
    out.append("__all__ = [")
    exported = [
        "AUTHZEN_CONTRACT_SCHEMA_VERSION",
        "AUTHZEN_PROFILE_V1",
        "AUTHZEN_SOURCE_SCHEMA_SHA256",
    ]
    for enum in surface.enums:
        exported.append(enum_type_name(enum.name))
        exported.append(enum_values_name(enum.name))
        exported.extend(enum_const_name(enum.name, v) for v in enum.values)
    exported.extend(type_name(t.name) for t in surface.types)
    for name in sorted(exported):
        out.append(f'    "{name}",')
    out.append("]")


def emit(surface: Surface) -> str:
    """Render the whole module.

    Deterministic by construction: everything iterated here is a tuple built in
    the artifact's own order, and the only sort is over a list of names. If any
    ordering leaked from a set or a dict, the regeneration gate would fail on
    unrelated pull requests until somebody deleted it as flaky —
    ``tests/test_authzen_generator.py`` re-emits repeatedly to pin that.
    """
    _check_emittable(surface)

    out: list[str] = []
    _emit_header(out, surface)
    for enum in surface.enums:
        _emit_enum(out, enum)
    for type_ in surface.types:
        _emit_type(out, surface, type_)
    _emit_all(out, surface)

    rendered = "\n".join(out)
    # Collapse the blank-line runs the emitters produce at block boundaries so
    # the output survives `ruff format --check` without a post-processing pass
    # that the -check gate could not reproduce.
    while "\n\n\n\n" in rendered:
        rendered = rendered.replace("\n\n\n\n", "\n\n\n")
    rendered = rendered.rstrip("\n") + "\n"

    # The emitter is the authority on its own layout, and it fails rather than
    # emit a line the linter would reject. A generated file that fails
    # `ruff check` cannot be fixed by hand -- the header forbids it -- so the
    # only repair is here, and a loud failure at generation time is where a
    # maintainer can act on it. The sibling emitter carries the same guard.
    over = [
        (index + 1, line)
        for index, line in enumerate(rendered.split("\n"))
        if len(line) > _MAX_COLUMNS
    ]
    if over:
        first_line, first_text = over[0]
        msg = (
            f"the emitter produced {len(over)} line(s) over {_MAX_COLUMNS} columns, starting "
            f"at line {first_line}: {first_text.strip()[:60]}..."
        )
        raise SurfaceError(msg)
    return rendered


def verify_vendored_digest(raw_bytes: bytes) -> None:
    """Refuse a vendored artifact that is not the one this SDK was pinned to.

    Without this the regeneration gate is a closed loop: edit the artifact,
    regenerate, and both the CI check and the byte-comparison test are green
    while the SDK now describes a contract the platform never published. This
    is the only check that looks OUTSIDE the file.
    """
    digest = hashlib.sha256(raw_bytes).hexdigest()
    if digest != VENDORED_ARTIFACT_SHA256:
        msg = (
            f"{SURFACE_PATH.name} has sha256 {digest}, not the pinned "
            f"{VENDORED_ARTIFACT_SHA256}. The vendored artifact is a COPY of the "
            f"platform's canonical surface, so editing it here silently forks the "
            f"contract this SDK describes. If you are re-vendoring on purpose: copy the "
            f"file from the platform again, update VENDORED_ARTIFACT_SHA256 in this "
            f"script, regenerate, and name the platform commit in the pull request."
        )
        raise SurfaceError(msg)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero if the committed module is not what the artifact generates",
    )
    args = parser.parse_args(argv)

    raw_bytes = SURFACE_PATH.read_bytes()
    verify_vendored_digest(raw_bytes)
    surface = parse_surface(raw_bytes)
    rendered = emit(surface)

    if args.check:
        if not OUTPUT_PATH.exists():
            print(f"--check: {OUTPUT_PATH} does not exist", file=sys.stderr)
            return 1
        committed = OUTPUT_PATH.read_text(encoding="utf-8")
        if committed != rendered:
            print(
                f"--check: {OUTPUT_PATH.relative_to(REPO_ROOT)} is not what "
                f"{SURFACE_PATH.relative_to(REPO_ROOT)} generates.\n"
                f"Regenerate it in the same change:\n"
                f"  python3 scripts/gen_authzen_types.py",
                file=sys.stderr,
            )
            return 1
        print(f"{OUTPUT_PATH.relative_to(REPO_ROOT)} is current.")
        return 0

    OUTPUT_PATH.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    return 0


def _cli(argv: list[str] | None = None) -> int:
    """Run ``main`` and report a refusal as a message, not a traceback.

    The value of these gates is telling a maintainer what to do about the
    failure. A stack trace in a CI log buries the one sentence that matters
    under frames from this script.
    """
    try:
        return main(argv)
    except SurfaceError as exc:
        print(f"gen_authzen_types: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_cli())
