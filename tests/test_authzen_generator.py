"""The AuthZEN type generator, and the gate that keeps its output honest.

``axonflow/authzen_types_gen.py`` is committed so a consumer running
``pip install axonflow`` receives working types without running a generator.
A committed generated file is only worth something if something proves the
bytes are the output of the current input — otherwise "generated" is a claim in
a header comment rather than a fact.
"""

from __future__ import annotations

import importlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import gen_authzen_types as gen  # noqa: E402


@pytest.fixture
def surface() -> gen.Surface:
    return gen.parse_surface(gen.SURFACE_PATH.read_bytes())


def _with_literals_joined(source: str) -> str:
    """Rejoin implicitly concatenated string literals.

    The emitter wraps a message that would break the column limit, so a test
    searching for the message as one run of characters would report it missing
    on exactly the longest — and most informative — messages. Joining the seams
    lets an assertion be about the SENTENCE the generated code raises rather
    than about where the emitter chose to break the line.
    """
    return re.sub(r'"\s*\n\s*f?"', "", source)


class TestTheCommittedFileIsCurrent:
    def test_regenerating_reproduces_the_committed_bytes(self, surface: gen.Surface) -> None:
        """Fails on BOTH edits: changing the artifact without regenerating, and
        hand-editing the generated file.
        """
        want = gen.emit(surface)
        have = gen.OUTPUT_PATH.read_text(encoding="utf-8")
        assert have == want, (
            f"{gen.OUTPUT_PATH.name} is not what {gen.SURFACE_PATH.name} generates.\n"
            f"Regenerate it in the same change:\n  python3 scripts/gen_authzen_types.py"
        )

    def test_the_check_mode_agrees_with_this_test(self) -> None:
        """The CI step and this test must not be able to disagree.

        CI runs ``--check`` in the lint job, where a failure names the fix in
        one line; this test runs in the test job. Two gates over one property
        are only worth having if they cannot report different answers.
        """
        result = subprocess.run(  # noqa: S603
            [sys.executable, str(REPO_ROOT / "scripts" / "gen_authzen_types.py"), "--check"],
            capture_output=True,
            text=True,
            check=False,
            cwd=REPO_ROOT,
        )
        assert result.returncode == 0, result.stderr


class TestGenerationIsDeterministic:
    def test_repeated_emission_is_byte_identical(self, surface: gen.Surface) -> None:
        """Why the check above can be trusted.

        Every type and every field arrives from JSON. If any ordering leaked
        from a set or a dict, the regeneration gate would fail on unrelated
        pull requests until somebody deleted it as flaky — which is how a
        working guard gets removed for being right at the wrong moment.
        """
        first = gen.emit(surface)
        for attempt in range(16):
            assert gen.emit(surface) == first, (
                f"emission {attempt + 1} differs from the first; "
                f"the generator is leaking an ordering"
            )

    def test_parsing_is_deterministic_too(self) -> None:
        """A stable emitter over an unstable parse is still unstable."""
        raw = gen.SURFACE_PATH.read_bytes()
        first = gen.parse_surface(raw)
        for _ in range(8):
            assert gen.parse_surface(raw) == first


class TestTheOutputCoversTheWholeArtifact:
    """The anti-vacuity guard.

    The two tests above compare the generator against itself, so both stay
    green over a generator that emitted an empty file. These assert the output
    actually contains every type, every field and every enum value the artifact
    declares — and that the artifact is not itself empty, which would make
    every assertion here vacuous.
    """

    def test_the_artifact_is_not_empty(self, surface: gen.Surface) -> None:
        assert surface.types, "every coverage assertion below would be vacuous"
        assert surface.enums, "every coverage assertion below would be vacuous"

    def test_every_type_and_field_is_declared(self, surface: gen.Surface) -> None:
        source = gen.OUTPUT_PATH.read_text(encoding="utf-8")
        for type_ in surface.types:
            header = f"class {gen.type_name(type_.name)}(_AuthZENModel):"
            assert header in source, f"the generated file has no {header!r}"
            # Scoped to THIS class's block. A whole-file search reads the
            # envelope's OPTIONAL `evaluations` while checking the bulk's
            # REQUIRED one and reports a defect that is not there — two types
            # legitimately share a field name.
            block = source.split(header, 1)[1].split("\nclass ", 1)[0]
            for field in type_.fields:
                declaration = f"\n    {field.name}: "
                assert declaration in block, (
                    f"{type_.name}.{field.name} is not declared in the generated class"
                )
                line = block.split(declaration, 1)[1].split("\n", 1)[0]
                # Optionality must survive into the annotation, or a client
                # omits a field the server requires — or demands one it does
                # not — and gets a refusal it cannot explain.
                optional = line.endswith(" = None")
                assert optional is (not field.required), (
                    f"{type_.name}.{field.name} is "
                    f"{'required' if field.required else 'optional'} in the artifact "
                    f"but generated as the opposite"
                )

    def test_every_enum_value_is_declared(self, surface: gen.Surface) -> None:
        """Read from the IMPORTED module rather than by matching source text.

        A source-text assertion has to know how the emitter laid a declaration
        out — and the emitter parenthesises one that would exceed the column
        limit, so a literal match silently stops covering exactly the longest
        values. What matters is that the constant exists and holds the
        artifact's value, which is a property of the module, not of its
        formatting.
        """
        module = importlib.import_module("axonflow.authzen_types_gen")
        for enum in surface.enums:
            declared = getattr(module, gen.enum_values_name(enum.name))
            assert declared == tuple(enum.values), (
                f"the {enum.name} value tuple does not match the artifact"
            )
            for value in enum.values:
                const = gen.enum_const_name(enum.name, value)
                assert hasattr(module, const), f"enum {enum.name} is missing the value {value!r}"
                assert getattr(module, const) == value

    def test_the_rules_no_annotation_can_carry_are_emitted(self) -> None:
        """The envelope's exactly-one-of rule, the singular member's own
        required set, the bulk minimum and the min-length checks.

        Without these the types still compile and still build requests the
        server refuses — which is the least useful place for a caller to find
        out.
        """
        source = _with_literals_joined(gen.OUTPUT_PATH.read_text(encoding="utf-8"))
        assert "exactly one of evaluation or evaluations must be set" in source
        assert "it has no shared base to inherit one from" in source
        assert "evaluations needs at least 1 entry" in source
        assert "is present but too short" in source

    def test_no_exported_name_carries_a_naive_plural(self) -> None:
        """Every exported name is a public compatibility commitment through v11.

        A pluraliser that appends "s" to a derived name produces things like
        ``AllAuthZENCategorys``: a name that reads as a typo, in a public API,
        frozen for two major releases. Nothing in this emitter derives a
        plural, and this is what keeps that true as it changes.
        """
        module = importlib.import_module("axonflow.authzen_types_gen")
        offenders = [
            name
            for name in module.__all__
            if re.search(r"(?:[bcdfghjklmnpqrstvwxz]y|s|x|ch|sh)s$", name)
        ]
        assert offenders == [], f"exported names with a naive plural suffix: {offenders}"

    def test_the_generated_file_is_lint_and_format_clean(self) -> None:
        """The emitter, not a post-processing pass, is the authority on layout.

        Generated code is linted and formatted like every other module here, so
        an over-long line fails CI on a file nobody is allowed to hand-edit.
        Running the formatter from the generator instead would make the output
        depend on the formatter's version, and the ``--check`` gate would
        report drift whenever that version moved.
        """
        for command in (
            ["ruff", "check", str(gen.OUTPUT_PATH)],
            ["ruff", "format", "--check", str(gen.OUTPUT_PATH)],
        ):
            result = subprocess.run(  # noqa: S603
                command, capture_output=True, text=True, check=False, cwd=REPO_ROOT
            )
            if result.returncode != 0 and "No such file" in result.stderr:
                pytest.skip("ruff is not installed in this environment")
            assert result.returncode == 0, f"{command}\n{result.stdout}\n{result.stderr}"


class TestAPlantedDriftIsCaught:
    """A guard that cannot fail is worse than none.

    Each case plants a change in a COPY of the artifact and asserts the
    generator's output moves. The first is the one the gate exists for: a
    field whose NAME is unchanged and whose SHAPE is not — the drift a
    name-only comparison, or a human skimming a diff, sails past.
    """

    @staticmethod
    def _drifted(mutate: object) -> gen.Surface:
        doc = json.loads(gen.SURFACE_PATH.read_text(encoding="utf-8"))
        mutate(doc)  # type: ignore[operator]
        return gen.parse_surface(json.dumps(doc).encode())

    def test_a_same_name_field_shape_drift_changes_the_output(self, surface: gen.Surface) -> None:
        def make_subject_id_optional(doc: dict) -> None:
            subject = next(t for t in doc["types"] if t["name"] == "authzen_subject")
            field = next(f for f in subject["fields"] if f["name"] == "id")
            assert field["required"] is True, "the fixture no longer plants a drift"
            field["required"] = False

        drifted = gen.emit(self._drifted(make_subject_id_optional))
        committed = gen.OUTPUT_PATH.read_text(encoding="utf-8")
        assert drifted != committed, (
            "a required->optional flip on a same-named field produced identical "
            "output; the regeneration gate cannot see field shapes"
        )
        # Scoped to the subject's own class block: `request_id: str | None`
        # exists legitimately on AuthZENError, and a whole-file search would
        # report the drift as already present in the committed file.
        header = "class AuthZENSubject(_AuthZENModel):"
        drifted_block = drifted.split(header, 1)[1].split("\nclass ", 1)[0]
        committed_block = committed.split(header, 1)[1].split("\nclass ", 1)[0]
        assert "id: str | None = None" in drifted_block
        assert "id: str | None = None" not in committed_block
        # The control: without the mutation the same pipeline reproduces the
        # committed bytes, so the assertion above is about the drift and not
        # about the copy.
        assert gen.emit(surface) == committed

    def test_a_renamed_field_changes_the_output(self, surface: gen.Surface) -> None:
        def rename(doc: dict) -> None:
            subject = next(t for t in doc["types"] if t["name"] == "authzen_subject")
            next(f for f in subject["fields"] if f["name"] == "id")["name"] = "identifier"

        assert gen.emit(self._drifted(rename)) != gen.emit(surface)

    def test_an_added_enum_value_changes_the_output(self, surface: gen.Surface) -> None:
        def add_value(doc: dict) -> None:
            next(e for e in doc["enums"] if e["name"] == "operational_state")["values"].append(
                "QUARANTINE"
            )

        drifted = gen.emit(self._drifted(add_value))
        assert "QUARANTINE" in drifted
        assert "QUARANTINE" not in gen.emit(surface)

    def test_a_dropped_min_items_changes_the_output(self, surface: gen.Surface) -> None:
        """``min_items`` is the bulk envelope's "at least one entry" rule.

        It is checked separately because it is emitted by a different branch
        from the type and optionality assertions above — a gate that only
        compared field names and types would report clean while the rule that
        stops a zero-entry envelope disappeared.
        """

        def drop(doc: dict) -> None:
            bulk = next(t for t in doc["types"] if t["name"] == "authzen_bulk")
            del next(f for f in bulk["fields"] if f["name"] == "evaluations")["min_items"]

        drifted = gen.emit(self._drifted(drop))
        assert "evaluations needs at least 1 entry" not in drifted
        assert "evaluations needs at least 1 entry" in gen.emit(surface)


class TestTheParserRefusesWhatItCannotGenerate:
    """An artifact member this emitter does not understand is a construct the
    platform added and this SDK would silently omit — the
    declared-but-never-emitted class, arriving through the generator built to
    prevent it.
    """

    VALID = json.dumps(
        {
            "artifact": "axonflow-authzen-surface",
            "artifact_version": 1,
            "profile": "p",
            "contract_schema_version": "v",
            "source_schema_id": "i",
            "source_schema_sha256": "s",
            "enums": [{"name": "e", "values": ["a"]}],
            "types": [
                {
                    "name": "t",
                    "fields": [{"name": "f", "required": True, "type": {"kind": "string"}}],
                }
            ],
        }
    )

    def test_the_control_fixture_is_accepted(self) -> None:
        """Without this every case below could be passing because the fixture
        itself is malformed.
        """
        assert gen.parse_surface(self.VALID.encode()) is not None

    @pytest.mark.parametrize(
        ("name", "old", "new"),
        [
            ("an unknown artifact member", '"enums":', '"transport": "grpc", "enums":'),
            ("an unknown type kind", '{"kind": "string"}', '{"kind": "decimal"}'),
            ("a dangling type reference", '{"kind": "string"}', '{"kind": "ref", "ref": "nope"}'),
            ("a dangling enum reference", '{"kind": "string"}', '{"kind": "enum", "enum": "no"}'),
            ("an array with no item type", '{"kind": "string"}', '{"kind": "array"}'),
            ("a map with no value type", '{"kind": "string"}', '{"kind": "map"}'),
            ("an unknown field member", '"name": "f"', '"name": "f", "widget": true'),
            ("an enum with no values", '"values": ["a"]', '"values": []'),
            ("a repeated enum value", '"values": ["a"]', '"values": ["a", "a"]'),
        ],
    )
    def test_a_construct_this_emitter_cannot_render_is_refused(
        self, name: str, old: str, new: str
    ) -> None:
        document = self.VALID.replace(old, new, 1)
        assert document != self.VALID, f"the {name} fixture planted nothing"
        with pytest.raises(gen.SurfaceError):
            gen.parse_surface(document.encode())

    def test_a_type_with_no_fields_is_refused(self) -> None:
        document = self.VALID.replace(
            '"fields": [{"name": "f", "required": true, "type": {"kind": "string"}}]',
            '"fields": []',
        ).replace(
            '"fields": [{"name": "f", "required": True, "type": {"kind": "string"}}]',
            '"fields": []',
        )
        with pytest.raises(gen.SurfaceError):
            gen.parse_surface(document.encode())

    def test_an_exactly_one_of_naming_a_missing_field_is_refused(self) -> None:
        doc = json.loads(self.VALID)
        doc["types"][0]["exactly_one_of"] = [["f", "g"]]
        with pytest.raises(gen.SurfaceError, match="no such field"):
            gen.parse_surface(json.dumps(doc).encode())

    def test_a_duplicate_type_is_refused(self) -> None:
        doc = json.loads(self.VALID)
        doc["types"].append(doc["types"][0])
        with pytest.raises(gen.SurfaceError, match="twice"):
            gen.parse_surface(json.dumps(doc).encode())


class TestTheEmitterRefusesAnUnsupportedArtifact:
    def test_an_unsupported_format_version_is_refused(self, surface: gen.Surface) -> None:
        """A format change is a deliberate migration. Generating through one
        produces types that look right and describe a different contract.
        """
        import dataclasses

        with pytest.raises(gen.SurfaceError, match="format version"):
            gen.emit(dataclasses.replace(surface, artifact_version=2))

    def test_a_different_artifact_is_refused(self, surface: gen.Surface) -> None:
        import dataclasses

        with pytest.raises(gen.SurfaceError, match="not an AuthZEN surface"):
            gen.emit(dataclasses.replace(surface, artifact="something-else"))

    def test_an_empty_surface_is_refused(self, surface: gen.Surface) -> None:
        import dataclasses

        with pytest.raises(gen.SurfaceError, match="empty surface"):
            gen.emit(dataclasses.replace(surface, types=()))
        with pytest.raises(gen.SurfaceError, match="empty surface"):
            gen.emit(dataclasses.replace(surface, enums=()))


class TestTheVendoredArtifactIsThePinnedOne:
    """R3 round 1 renamed this class.

    It used to be called ``TestTheVendoredArtifactMatchesThePlatform`` while
    asserting only that three strings INSIDE the artifact were copied into the
    emitted module - a property that survives any edit to the artifact, because
    those strings move with it. A reviewer asking "is the vendored copy
    verified?" found the old name and concluded yes.

    The fidelity check is ``verify_vendored_digest``: a sha256 of the FILE,
    recorded outside it, verified byte-for-byte against the platform copy and
    the Go SDK's copy when it was vendored.
    """

    def test_the_vendored_file_matches_the_pinned_digest(self) -> None:
        gen.verify_vendored_digest(gen.SURFACE_PATH.read_bytes())

    def test_an_edited_artifact_is_refused_even_if_everything_regenerates(self) -> None:
        """The guard test.

        Without the digest the regeneration gate is a closed loop: edit the
        artifact, regenerate, and both the CI check and the byte-comparison
        test go green over a contract the platform never published.
        """
        doc = json.loads(gen.SURFACE_PATH.read_text(encoding="utf-8"))
        subject = next(t for t in doc["types"] if t["name"] == "authzen_subject")
        next(f for f in subject["fields"] if f["name"] == "id")["required"] = False
        edited = json.dumps(doc).encode()

        # The edited artifact still PARSES and still GENERATES cleanly - which
        # is exactly why the other gates cannot see it.
        assert gen.emit(gen.parse_surface(edited)) != gen.OUTPUT_PATH.read_text(encoding="utf-8")
        with pytest.raises(gen.SurfaceError, match="not the pinned"):
            gen.verify_vendored_digest(edited)

    def test_the_check_command_runs_the_digest_check(self) -> None:
        """A control on the CI wiring, not on the function.

        The digest check is only worth having if the command CI runs performs
        it; a helper nothing calls is a comment.
        """
        source = (REPO_ROOT / "scripts" / "gen_authzen_types.py").read_text(encoding="utf-8")
        main_body = source[source.index("def main(") :]
        assert "verify_vendored_digest(raw_bytes)" in main_body

    def test_the_artifacts_self_declared_strings_reached_the_types(
        self, surface: gen.Surface
    ) -> None:
        """Named for what it asserts: a COPY check, not a fidelity check.

        It catches an emitter that forgot to carry the provenance through. It
        establishes nothing about where the artifact came from - that is the
        digest above.
        """
        from axonflow.authzen_types_gen import (
            AUTHZEN_CONTRACT_SCHEMA_VERSION,
            AUTHZEN_PROFILE_V1,
            AUTHZEN_SOURCE_SCHEMA_SHA256,
        )

        assert surface.profile == AUTHZEN_PROFILE_V1
        assert surface.contract_schema_version == AUTHZEN_CONTRACT_SCHEMA_VERSION
        assert surface.source_schema_sha256 == AUTHZEN_SOURCE_SCHEMA_SHA256
        assert AUTHZEN_SOURCE_SCHEMA_SHA256.startswith("sha256:")


class TestTheEmitterRefusesWhatItCannotRender:
    """R3 round 1: the emitter interpolated artifact strings with no escaping,
    and never checked that an emitted field name was a usable identifier.

    The artifact is first-party, so the realistic failure is the benign one - a
    ``\"\"\"`` or a backslash arriving in a platform doc comment and emitting a
    module that does not parse. The emitter's own claim is that it refuses what
    it cannot render, and that claim did not hold for any string it copied.
    """

    @staticmethod
    def _mutated(mutate: object) -> bytes:
        doc = json.loads(gen.SURFACE_PATH.read_text(encoding="utf-8"))
        mutate(doc)  # type: ignore[operator]
        return json.dumps(doc).encode()

    def test_a_docstring_terminator_in_a_doc_is_refused(self) -> None:
        def plant(doc: dict) -> None:
            doc["types"][0]["doc"] = 'ends the docstring """ and starts something else'

        with pytest.raises(gen.SurfaceError, match="doc text contains"):
            gen.parse_surface(self._mutated(plant))

    def test_a_backslash_in_a_doc_is_refused(self) -> None:
        def plant(doc: dict) -> None:
            doc["types"][0]["doc"] = "a windows path C:\\temp"

        with pytest.raises(gen.SurfaceError, match="doc text contains"):
            gen.parse_surface(self._mutated(plant))

    def test_a_quote_in_an_enum_value_is_refused(self) -> None:
        def plant(doc: dict) -> None:
            doc["enums"][0]["values"].append("it's")

        with pytest.raises(gen.SurfaceError, match="carries a quote"):
            gen.parse_surface(self._mutated(plant))

    @pytest.mark.parametrize("name", ["class", "model_config", "x-trace", "2fa"])
    def test_a_field_name_that_cannot_be_an_attribute_is_refused(self, name: str) -> None:
        """``class`` is a SyntaxError; ``model_config`` generates cleanly and
        ships a package that raises on import. Both are refused here.
        """

        def plant(doc: dict) -> None:
            doc["types"][0]["fields"][0]["name"] = name

        with pytest.raises(gen.SurfaceError, match=r"identifier|reserved"):
            gen.parse_surface(self._mutated(plant))

    def test_a_bound_on_a_kind_it_cannot_constrain_is_refused(self) -> None:
        """A ``min_length`` on a bool looks like a live constraint in the
        artifact and enforces nothing in the SDK. Silently dropping it is how a
        constraint exists on paper and nowhere else.
        """

        def plant_length(doc: dict) -> None:
            action = next(t for t in doc["types"] if t["name"] == "authzen_action")
            next(f for f in action["fields"] if f["name"] == "properties")["min_length"] = 1

        with pytest.raises(gen.SurfaceError, match="min_length"):
            gen.parse_surface(self._mutated(plant_length))

        def plant_items(doc: dict) -> None:
            action = next(t for t in doc["types"] if t["name"] == "authzen_action")
            next(f for f in action["fields"] if f["name"] == "name")["min_items"] = 2

        with pytest.raises(gen.SurfaceError, match="min_items"):
            gen.parse_surface(self._mutated(plant_items))

    def test_a_negative_bound_is_refused(self) -> None:
        def plant(doc: dict) -> None:
            bulk = next(t for t in doc["types"] if t["name"] == "authzen_bulk")
            next(f for f in bulk["fields"] if f["name"] == "evaluations")["min_items"] = -1

        with pytest.raises(gen.SurfaceError, match="negative bound"):
            gen.parse_surface(self._mutated(plant))

    def test_a_non_boolean_required_is_refused(self) -> None:
        """``bool("false")`` is True. A coerced read makes the string "false"
        mean required here and optional in the sibling SDK, from one artifact,
        with both regeneration gates green.
        """

        def plant(doc: dict) -> None:
            doc["types"][0]["fields"][0]["required"] = "false"

        with pytest.raises(gen.SurfaceError, match="JSON boolean"):
            gen.parse_surface(self._mutated(plant))

    def test_a_container_nested_in_a_container_is_refused(self) -> None:
        """Not because it is unrenderable in Python - it very nearly is - but
        because the sibling emitter refuses it, and a construct one SDK
        generates for and the other rejects is a four-of-five release.
        """

        def plant(doc: dict) -> None:
            bulk = next(t for t in doc["types"] if t["name"] == "authzen_bulk")
            field = next(f for f in bulk["fields"] if f["name"] == "evaluations")
            field["type"] = {
                "kind": "array",
                "items": {"kind": "array", "items": {"kind": "string"}},
            }

        with pytest.raises(gen.SurfaceError, match="nests a"):
            gen.parse_surface(self._mutated(plant))

    def test_a_duplicated_exactly_one_of_member_is_refused(self) -> None:
        """``[["evaluation","evaluation"]]`` emits a type that can never be
        constructed: one member cannot be present exactly twice.
        """

        def plant(doc: dict) -> None:
            envelope = next(t for t in doc["types"] if t["name"] == "authzen_envelope")
            envelope["exactly_one_of"] = [["evaluation", "evaluation"]]

        with pytest.raises(gen.SurfaceError, match="same member twice"):
            gen.parse_surface(self._mutated(plant))

    @pytest.mark.parametrize(
        "member", ["artifact", "profile", "contract_schema_version", "source_schema_sha256"]
    )
    def test_an_unsafe_artifact_level_string_is_refused(self, member: str) -> None:
        """R3 round 2: the first pass swept type, field and enum strings and
        left the artifact's own top-level ones unchecked - so a quote in
        ``profile`` still emitted a module that does not parse. Answering the
        enumerated sites is not answering the class.
        """

        def plant(doc: dict) -> None:
            doc[member] = 'ends the docstring """ here'

        with pytest.raises(gen.SurfaceError, match="carries a quote"):
            gen.parse_surface(self._mutated(plant))

    def test_a_leading_underscore_field_name_is_refused(self) -> None:
        """The fail-open inside the identifier check itself.

        ``__init__`` IS a valid identifier and is not a keyword, so it passed -
        and pydantic treats a leading-underscore attribute as private, so the
        member vanished from the model with no error anywhere.
        """

        def plant(doc: dict) -> None:
            doc["types"][0]["fields"][0]["name"] = "__init__"

        with pytest.raises(gen.SurfaceError, match="starts with an underscore"):
            gen.parse_surface(self._mutated(plant))

    def test_a_const_no_enforcement_site_covers_is_refused(self) -> None:
        """By the emitter's own rule for bounds: a constraint the artifact
        declares and no SDK enforces is worse than one the emitter cannot
        render, because it looks enforced.

        The profile const passes through, because the client refuses an
        unreadable profile by name. Any other fails here.
        """

        def plant(doc: dict) -> None:
            action = next(t for t in doc["types"] if t["name"] == "authzen_action")
            next(f for f in action["fields"] if f["name"] == "name")["const"] = "llm.completion"

        with pytest.raises(gen.SurfaceError, match="no enforcement site covers"):
            gen.parse_surface(self._mutated(plant))

    def test_the_profile_const_the_contract_declares_still_passes(self) -> None:
        """The control: a rule that refused every const would refuse today's
        artifact, and this test would be the only thing to say so.
        """
        assert gen.parse_surface(gen.SURFACE_PATH.read_bytes()) is not None

    def test_an_over_long_line_is_refused_rather_than_emitted(self) -> None:
        """A generated file that fails `ruff check` cannot be fixed by hand -
        the header forbids it - so the emitter fails at generation time, where
        a maintainer can act on it.
        """
        import dataclasses

        surface = gen.parse_surface(gen.SURFACE_PATH.read_bytes())
        long_name = "x" * 200
        widened = dataclasses.replace(
            surface,
            enums=(gen.Enum(name=long_name, values=("a",)), *surface.enums),
        )
        with pytest.raises(gen.SurfaceError, match="columns"):
            gen.emit(widened)
