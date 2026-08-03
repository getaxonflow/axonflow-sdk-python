"""Wire-shape contract test: OpenAPI spec ↔ pydantic SDK types.

This test catches camelCase / snake_case drift and missing-field drift
between the authoritative OpenAPI specs and the SDK's pydantic models.
It is the Python arm of QF-15 (see axonflow-enterprise#1699).

Data flow:
- Load all `*.yaml` OpenAPI specs from AXONFLOW_OPENAPI_SPECS_DIR (set by
  CI after cloning the community repo). Collect every schema that has
  concrete ``properties``.
- Walk the ``axonflow`` package, find every pydantic ``BaseModel``
  subclass, compute its wire-shape field names (``alias`` if set, else
  the Python attribute name).
- For every pydantic model whose class name matches a schema name,
  diff the sorted property-name sets and fail on mismatch.

Unmapped models (no schema with the same name) are counted but not
failed — many SDK models are client-side-only (config, interceptor
request wrappers, etc.). Unmapped schemas (spec shape without a
matching SDK model) are also counted, surfacing coverage gaps.

The test is opt-in via the ``wire_shape`` marker. The dedicated CI job
runs it by specifying ``-m wire_shape`` after setting the specs dir.
Regular ``pytest`` runs skip it cleanly when the specs dir is missing.
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import pkgutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel

import axonflow

# Classes we deliberately exclude even if they match a schema name.
# Add here only with a reason. Each entry must be cross-referenced to
# an issue or ADR.
EXCLUDED_MODELS: dict[str, str] = {}

# Baseline of KNOWN drift and cross-spec duplicates at the time this
# gate was installed. The CI gate fails on drift that is NOT in the
# baseline; baseline entries are intended to be burned down over time.
BASELINE_PATH = Path(__file__).parent / "fixtures" / "wire_shape_baseline.json"

# Module namespaces under ``axonflow`` whose ``ImportError`` is expected
# when the corresponding optional third-party dependency is not
# installed. Both packages themselves and their submodules are covered.
# Any ImportError OUTSIDE these namespaces surfaces as a test failure
# so real regressions (a moved symbol, a broken internal import) cannot
# silently drop whole swathes of the SDK out of wire-shape enforcement.
#
# Current entries:
# - axonflow.adapters: langchain, langgraph, langgraph_wrapper, tool_wrapper,
#   computer_use — each pulls in langchain-core / langgraph / others.
# - axonflow.interceptors: openai, anthropic, bedrock, gemini, ollama —
#   each pulls in its respective provider SDK.
OPTIONAL_DEP_MODULE_PREFIXES: tuple[str, ...] = (
    "axonflow.adapters",
    "axonflow.interceptors",
)


pytestmark = pytest.mark.wire_shape


def _specs_dir() -> Path | None:
    """Return the OpenAPI specs directory, or None when the env var is unset.

    Unset AXONFLOW_OPENAPI_SPECS_DIR is the designed local-dev skip. A
    variable that IS set - but empty, or pointing at a missing directory
    or a non-directory - is a misconfiguration and fails loudly instead:
    previously those cases produced 7 silent skips and exit 0, so a
    broken CI checkout read as green. Set-but-empty fails (rather than
    reading as unset) because a CI consumer wiring the var from an
    expression that evaluates empty is exactly the broken-checkout class.
    """
    env = os.environ.get("AXONFLOW_OPENAPI_SPECS_DIR")
    if env is None:
        return None
    if not env.strip():
        pytest.fail(
            "AXONFLOW_OPENAPI_SPECS_DIR is set but empty. Refusing to treat "
            "it as unset: an empty value usually means the CI expression "
            "that was supposed to produce the specs path evaluated to "
            "nothing, and a silent skip would make that read as a green "
            "wire-shape gate. Fix the expression, or unset the variable to "
            "skip locally."
        )
    p = Path(env)
    if not p.is_dir():
        pytest.fail(
            f"AXONFLOW_OPENAPI_SPECS_DIR is set to {env!r} but that is not an "
            "existing directory. Refusing to skip: with the variable set, a "
            "silent skip would make a broken specs checkout read as a green "
            "wire-shape gate. Fix the path, or unset the variable to skip "
            "locally."
        )
    return p


def _wire_fields(model: type[BaseModel]) -> list[str]:
    """Return sorted wire-shape property names for a pydantic model.

    The wire name is what the SDK actually sends/receives on the wire,
    which in pydantic V2 is resolved with this precedence:
      1. ``serialization_alias`` if set — explicit outgoing name
      2. ``alias`` if set — default for both directions
      3. ``validation_alias`` if set — explicit incoming name; used as
         a fallback because if only the incoming alias is declared,
         that is the wire contract the field is written against
      4. The Python attribute name

    ``AliasChoices`` and ``AliasPath`` are resolved to their canonical
    string: the first string choice for ``AliasChoices``, or the first
    string segment for ``AliasPath``. This keeps the check strict even
    as pydantic features get adopted in the SDK.
    """
    from pydantic import AliasChoices, AliasPath  # noqa: PLC0415

    def _canonical(alias_obj: Any) -> str | None:  # noqa: PLR0911
        if alias_obj is None:
            return None
        if isinstance(alias_obj, str):
            return alias_obj
        if isinstance(alias_obj, AliasChoices):
            for choice in alias_obj.choices:
                if isinstance(choice, str):
                    return choice
                if isinstance(choice, AliasPath) and choice.path:
                    head = choice.path[0]
                    if isinstance(head, str):
                        return head
            return None
        if isinstance(alias_obj, AliasPath) and alias_obj.path:
            head = alias_obj.path[0]
            if isinstance(head, str):
                return head
        return None

    names: list[str] = []
    for field_name, field_info in model.model_fields.items():
        wire = (
            _canonical(field_info.serialization_alias)
            or _canonical(field_info.alias)
            or _canonical(field_info.validation_alias)
            or field_name
        )
        names.append(wire)
    return sorted(names)


def _schema_fields(schema: dict[str, Any]) -> list[str] | None:
    """Return sorted property names for an OpenAPI schema, or None
    if the schema has no concrete ``properties`` (pure $ref / allOf /
    freeform additionalProperties).
    """
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        return None
    return sorted(props.keys())


def _load_all_schemas(
    spec_dir: Path,
) -> tuple[dict[str, list[str]], dict[str, dict[str, list[str]]]]:
    """Load every `*.yaml` in spec_dir and collect schemas with properties.

    Returns ``(merged_schemas, duplicates_by_spec)``:

    - ``merged_schemas``: ``{schema_name: sorted_fields}`` with last-loaded
      declaration winning on name collision. This is what SDK models are
      diffed against.
    - ``duplicates_by_spec``: ``{schema_name: {spec_filename: sorted_fields}}``
      — every declaration of a schema name that appears in more than one
      spec, keyed by the file it came from. The test compares this to the
      baseline fingerprint so that baselined divergences can't quietly
      drift further (e.g. an already-acknowledged collision adding a
      field on one side still fails the gate).
    """
    schemas: dict[str, list[str]] = {}
    all_declarations: dict[str, dict[str, list[str]]] = {}
    for spec_file in sorted(spec_dir.glob("*.yaml")):
        with spec_file.open() as f:
            doc = yaml.safe_load(f) or {}
        components = (doc.get("components") or {}).get("schemas") or {}
        for name, schema in components.items():
            if not isinstance(schema, dict):
                continue
            fields = _schema_fields(schema)
            if fields is None:
                continue
            all_declarations.setdefault(name, {})[spec_file.name] = fields
            schemas[name] = fields
    # Only schemas whose declarations DIFFER across specs are tracked as
    # divergences. Redundant identical declarations are benign.
    duplicates_by_spec = {
        name: decls
        for name, decls in all_declarations.items()
        if len({tuple(f) for f in decls.values()}) > 1
    }
    return schemas, duplicates_by_spec


def _discover_models() -> dict[str, type[BaseModel]]:
    """Find every pydantic BaseModel subclass reachable under ``axonflow``.

    Modules in ``OPTIONAL_DEP_MODULES`` are allowed to raise ``ImportError``
    silently when their optional third-party dep is missing. Any other
    ``ImportError`` is a real regression and is re-raised, so broken
    internal imports can't quietly remove a swath of the SDK from gate
    enforcement while the rest of the test happily says "some models
    matched, good enough".
    """
    models: dict[str, type[BaseModel]] = {}
    pkg_root = Path(axonflow.__file__).parent
    for mod_info in pkgutil.walk_packages([str(pkg_root)], prefix="axonflow."):
        try:
            mod = importlib.import_module(mod_info.name)
        except ImportError as exc:
            if any(
                mod_info.name == prefix or mod_info.name.startswith(prefix + ".")
                for prefix in OPTIONAL_DEP_MODULE_PREFIXES
            ):
                continue
            msg = (
                f"Unexpected ImportError importing {mod_info.name!r}: {exc}. "
                f"If {mod_info.name!r} legitimately requires an optional "
                f"third-party dep, add its namespace prefix to "
                f"OPTIONAL_DEP_MODULE_PREFIXES. Otherwise fix the broken "
                f"import — silently skipping would remove this module's "
                f"models from wire-shape enforcement."
            )
            raise ImportError(msg) from exc
        for _, obj in inspect.getmembers(mod, inspect.isclass):
            if obj is BaseModel:
                continue
            if not issubclass(obj, BaseModel):
                continue
            # Only capture classes defined in the axonflow package.
            if not (obj.__module__ or "").startswith("axonflow."):
                continue
            models[obj.__name__] = obj
    return models


@pytest.fixture(scope="module")
def loaded_specs() -> tuple[dict[str, list[str]], dict[str, dict[str, list[str]]]]:
    spec_dir = _specs_dir()
    if spec_dir is None:
        pytest.skip(
            "AXONFLOW_OPENAPI_SPECS_DIR not set; wire-shape contract tests "
            "skipped. The dedicated CI job clones "
            "https://github.com/getaxonflow/axonflow and exports the specs "
            "dir before running this file."
        )
    return _load_all_schemas(spec_dir)


@pytest.fixture(scope="module")
def openapi_schemas(
    loaded_specs: tuple[dict[str, list[str]], dict[str, dict[str, list[str]]]],
) -> dict[str, list[str]]:
    return loaded_specs[0]


@pytest.fixture(scope="module")
def sdk_models() -> dict[str, type[BaseModel]]:
    return _discover_models()


@pytest.fixture(scope="module")
def baseline() -> dict[str, Any]:
    if not BASELINE_PATH.is_file():
        return {"cross_spec_duplicates": [], "per_model_drift": {}}
    with BASELINE_PATH.open() as f:
        return json.load(f)


def test_specs_dir_is_non_empty(openapi_schemas: dict[str, list[str]]) -> None:
    assert openapi_schemas, "No OpenAPI schemas with properties were loaded."


def test_no_new_cross_spec_schema_divergence(
    loaded_specs: tuple[dict[str, list[str]], dict[str, dict[str, list[str]]]],
    baseline: dict[str, Any],
) -> None:
    """Fail if a schema name is declared with different shapes in two
    specs, AND either the name or the per-spec shapes differ from
    what's in the baseline.

    Platform-side spec inconsistencies — the SDK side can only pick one
    version when the name collides. The baseline records the **expected
    per-spec shape** for each acknowledged duplicate so the gate still
    fails when a duplicate widens (e.g. one side adds a new field after
    the divergence was baselined). Previously the baseline was a flat
    list of names, which allowed already-known collisions to drift
    further without tripping the gate.
    """
    baseline_dups = baseline.get("cross_spec_duplicates", {})
    # Back-compat: if the baseline is still a flat list, treat it as
    # "names acknowledged but shapes not pinned" and degrade to a
    # name-only check with a loud warning printed. This lets the test
    # pass a grace period and reminds the reader to regenerate.
    if isinstance(baseline_dups, list):
        baseline_dups = dict.fromkeys(baseline_dups)
        print(
            "\nWARNING: baseline cross_spec_duplicates is a flat list; "
            "regenerate the baseline to pin per-spec shapes so the gate "
            "can catch drift-after-acknowledgement."
        )

    observed = loaded_specs[1]
    problems: list[str] = []

    for name, observed_decls in observed.items():
        if name not in baseline_dups:
            problems.append(
                f"  {name}: NEW cross-spec divergence (not in baseline).\n"
                + "\n".join(
                    f"    {spec}: {fields}" for spec, fields in sorted(observed_decls.items())
                )
            )
            continue
        expected = baseline_dups[name]
        if expected is None:
            # Flat-list legacy entry — accept without shape check this run.
            continue
        if not isinstance(expected, dict):
            problems.append(
                f"  {name}: baseline entry malformed — expected a "
                f"{{spec_file: [fields]}} object, got {type(expected).__name__}."
            )
            continue
        observed_norm = {spec: list(fields) for spec, fields in observed_decls.items()}
        expected_norm = {spec: list(fields) for spec, fields in expected.items()}
        if observed_norm != expected_norm:
            diff_lines = [f"  {name}: divergence drifted from baseline."]
            all_specs = sorted(set(observed_norm) | set(expected_norm))
            for spec in all_specs:
                exp = expected_norm.get(spec)
                obs = observed_norm.get(spec)
                if exp == obs:
                    continue
                diff_lines.append(f"    {spec}:")
                diff_lines.append(f"      baseline: {exp}")
                diff_lines.append(f"      observed: {obs}")
            problems.append("\n".join(diff_lines))

    if problems:
        lines = [
            "",
            "Cross-spec schema divergence gate failed:",
            "",
            *problems,
            "",
            (
                "Fix: reconcile in the axonflow-enterprise specs (rename one, "
                "or merge into a shared supertype). If the divergence is "
                "intentional and must stand, regenerate "
                "tests/fixtures/wire_shape_baseline.json with the new "
                "per-spec shapes via scripts/refresh_wire_shape_baseline.py."
            ),
        ]
        pytest.fail("\n".join(lines))


def test_no_new_sdk_vs_spec_drift(
    openapi_schemas: dict[str, list[str]],
    sdk_models: dict[str, type[BaseModel]],
    baseline: dict[str, Any],
) -> None:
    """The core QF-15 gate. Fails if an SDK model has ANY drift against
    its matching OpenAPI schema that is not covered by the baseline.

    New drift = introduced by this PR = blocked merge. Burning down
    baseline entries is a separate workflow: fix a model, re-run the
    extraction, shrink the baseline JSON.
    """
    expected_drift = baseline.get("per_model_drift", {})
    new_drift: list[tuple[str, list[str], list[str], list[str], list[str]]] = []
    matched = 0

    for name, model in sdk_models.items():
        if name in EXCLUDED_MODELS:
            continue
        if name not in openapi_schemas:
            continue  # unmapped models are tracked by a coverage test below
        matched += 1
        sdk_fields = _wire_fields(model)
        spec_fields = openapi_schemas[name]
        only_sdk = sorted(set(sdk_fields) - set(spec_fields))
        only_spec = sorted(set(spec_fields) - set(sdk_fields))

        expected = expected_drift.get(name, {})
        allowed_sdk_only = set(expected.get("sdk_only", []))
        allowed_spec_only = set(expected.get("spec_only", []))

        unexpected_sdk = sorted(set(only_sdk) - allowed_sdk_only)
        unexpected_spec = sorted(set(only_spec) - allowed_spec_only)

        if unexpected_sdk or unexpected_spec:
            new_drift.append((name, only_sdk, only_spec, unexpected_sdk, unexpected_spec))

    if new_drift:
        lines = [
            "",
            "NEW wire-shape drift detected (not covered by baseline):",
            "",
        ]
        for name, only_sdk, only_spec, unexpected_sdk, unexpected_spec in new_drift:
            lines.append(f"  {name}:")
            if unexpected_sdk:
                lines.append(f"    NEW, only in SDK model:  {unexpected_sdk}")
            if unexpected_spec:
                lines.append(f"    NEW, only in OpenAPI:    {unexpected_spec}")
            if only_sdk and set(only_sdk) != set(unexpected_sdk):
                lines.append(
                    f"    (baseline, only in SDK):  {sorted(set(only_sdk) - set(unexpected_sdk))}"
                )
            if only_spec and set(only_spec) != set(unexpected_spec):
                lines.append(
                    f"    (baseline, only in spec): {sorted(set(only_spec) - set(unexpected_spec))}"
                )
        lines.append("")
        lines.append(
            "Fix: align the pydantic field name (or its alias) with the "
            "OpenAPI property name, OR update the spec if the SDK is the "
            "source of truth. Do not widen the baseline to hide the drift "
            "without a tracking issue."
        )
        pytest.fail("\n".join(lines))

    assert matched > 0, "No SDK models matched any OpenAPI schema by name — check discovery."


def test_baseline_has_not_grown_stale(
    openapi_schemas: dict[str, list[str]],
    sdk_models: dict[str, type[BaseModel]],
    baseline: dict[str, Any],
) -> None:
    """Informational: when a baseline entry's drift has been partially or
    fully resolved, print that fact so the baseline can be shrunk. Does
    not fail — baselines are always allowed to be larger than needed.
    """
    expected_drift = baseline.get("per_model_drift", {})
    stale_entries: list[tuple[str, list[str], list[str]]] = []

    for name, expected in expected_drift.items():
        if name not in sdk_models or name not in openapi_schemas:
            stale_entries.append((name, ["<model or schema no longer exists>"], []))
            continue
        model = sdk_models[name]
        sdk_fields = _wire_fields(model)
        spec_fields = openapi_schemas[name]
        only_sdk = set(sdk_fields) - set(spec_fields)
        only_spec = set(spec_fields) - set(sdk_fields)

        stale_sdk = sorted(set(expected.get("sdk_only", [])) - only_sdk)
        stale_spec = sorted(set(expected.get("spec_only", [])) - only_spec)

        if stale_sdk or stale_spec:
            stale_entries.append((name, stale_sdk, stale_spec))

    if stale_entries:
        print("\nBaseline entries that no longer match observed drift (safe to shrink):")
        for name, stale_sdk, stale_spec in stale_entries:
            print(f"  {name}:")
            if stale_sdk:
                print(f"    sdk_only entries no longer drifting: {stale_sdk}")
            if stale_spec:
                print(f"    spec_only entries no longer drifting: {stale_spec}")


def test_registered_models_still_map(
    openapi_schemas: dict[str, list[str]],
    sdk_models: dict[str, type[BaseModel]],
    baseline: dict[str, Any],
) -> None:
    """Rename-escape guard. Every model name listed in
    ``baseline.registered_models`` must still match (a) an SDK pydantic
    class and (b) an OpenAPI schema with concrete properties.

    Without this gate, renaming either side silently drops the model out
    of the matching loop and the drift check passes trivially. Fail
    loudly instead so the rename is either done on both sides or the
    baseline list is updated deliberately.
    """
    registered = baseline.get("registered_models", [])
    if not registered:
        pytest.skip(
            "baseline has no registered_models list; rename-escape guard "
            "is disabled until the baseline is regenerated with that key."
        )
    missing_model: list[str] = []
    missing_schema: list[str] = []
    for name in registered:
        if name not in sdk_models:
            missing_model.append(name)
        if name not in openapi_schemas:
            missing_schema.append(name)
    if missing_model or missing_schema:
        lines = [
            "",
            "Registered-model mapping broken — rename-escape guard fired:",
            "",
        ]
        if missing_model:
            lines.append(f"  No matching SDK pydantic class for: {missing_model}")
        if missing_schema:
            lines.append(f"  No matching OpenAPI schema for: {missing_schema}")
        lines.append("")
        lines.append(
            "Fix: either revert the rename, do it on both sides, or update "
            "tests/fixtures/wire_shape_baseline.json::registered_models "
            "(and mirror the rename in baseline.per_model_drift entries)."
        )
        pytest.fail("\n".join(lines))


def test_unmapped_sdk_models_are_tracked(
    openapi_schemas: dict[str, list[str]], sdk_models: dict[str, type[BaseModel]]
) -> None:
    """Surface SDK models with no matching schema. This is informational
    — many internal wrappers correctly have no spec shape — but a large
    number is worth noticing. This test always passes; run with -v to
    see the list."""
    unmapped = sorted(
        name for name in sdk_models if name not in openapi_schemas and name not in EXCLUDED_MODELS
    )
    print(
        f"\n{len(unmapped)} SDK model(s) have no matching OpenAPI schema (internal / client-side):"
    )
    for name in unmapped:
        print(f"  - {name}")


def test_unmapped_spec_schemas_are_tracked(
    openapi_schemas: dict[str, list[str]], sdk_models: dict[str, type[BaseModel]]
) -> None:
    """Surface OpenAPI schemas with no matching SDK model. Coverage gap
    for the Python SDK — each missing schema is either (a) an endpoint
    the Python SDK doesn't expose yet, or (b) a rename that escaped the
    diff. This test always passes; run with -v to see the list."""
    unmapped = sorted(name for name in openapi_schemas if name not in sdk_models)
    print(f"\n{len(unmapped)} OpenAPI schema(s) have no matching Python SDK model:")
    for name in unmapped:
        print(f"  - {name}")


# ---------------------------------------------------------------------------
# _specs_dir misconfiguration guard (#3254 review follow-up)
# ---------------------------------------------------------------------------
# These do not use the loaded_specs fixture, so they run in the regular
# suite too (the module's other tests skip there via the fixture).


def test_specs_dir_unset_keeps_designed_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env unset -> None, which the loaded_specs fixture turns into the
    designed local-dev skip."""
    monkeypatch.delenv("AXONFLOW_OPENAPI_SPECS_DIR", raising=False)
    assert _specs_dir() is None


def test_specs_dir_set_but_missing_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Env SET but pointing at a missing directory must FAIL, not skip.

    Before this guard, that misconfiguration produced 7 silent skips and
    exit 0 - a broken CI specs checkout read as a green gate.
    """
    missing = tmp_path / "no-such-specs-dir"
    monkeypatch.setenv("AXONFLOW_OPENAPI_SPECS_DIR", str(missing))
    with pytest.raises(pytest.fail.Exception, match="AXONFLOW_OPENAPI_SPECS_DIR"):
        _specs_dir()


def test_specs_dir_set_but_empty_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Env SET but EMPTY must FAIL, not read as unset.

    Choice (of fail-on-empty vs empty-as-unset): fail. A CI consumer
    wiring the variable from an expression that evaluates empty is the
    same broken-checkout class as a missing directory - treating it as
    the designed local-dev skip would green-light a gate that checked
    nothing. Whitespace-only counts as empty.
    """
    monkeypatch.setenv("AXONFLOW_OPENAPI_SPECS_DIR", "")
    with pytest.raises(pytest.fail.Exception, match="set but empty"):
        _specs_dir()
    monkeypatch.setenv("AXONFLOW_OPENAPI_SPECS_DIR", "   ")
    with pytest.raises(pytest.fail.Exception, match="set but empty"):
        _specs_dir()


def test_specs_dir_set_to_a_file_fails_loudly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Env SET but pointing at a FILE (not a directory) must FAIL.

    Behaviorally covered by the is_dir() check; pinned here so a future
    refactor to exists() cannot silently reopen the class.
    """
    a_file = tmp_path / "specs.yaml"
    a_file.write_text("openapi: 3.0.0\n")
    monkeypatch.setenv("AXONFLOW_OPENAPI_SPECS_DIR", str(a_file))
    with pytest.raises(pytest.fail.Exception, match="not an existing directory"):
        _specs_dir()


def test_specs_dir_set_and_present_resolves(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Env set to an existing directory resolves to that path."""
    monkeypatch.setenv("AXONFLOW_OPENAPI_SPECS_DIR", str(tmp_path))
    assert _specs_dir() == tmp_path
