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


pytestmark = pytest.mark.wire_shape


def _specs_dir() -> Path | None:
    """Return the OpenAPI specs directory, or None if not set / missing."""
    env = os.environ.get("AXONFLOW_OPENAPI_SPECS_DIR")
    if not env:
        return None
    p = Path(env)
    return p if p.is_dir() else None


def _wire_fields(model: type[BaseModel]) -> list[str]:
    """Return sorted wire-shape property names for a pydantic model.

    For each field, the wire name is the declared ``alias`` when set,
    otherwise the Python attribute name.
    """
    names: list[str] = []
    for field_name, field_info in model.model_fields.items():
        names.append(field_info.alias or field_name)
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
) -> tuple[dict[str, list[str]], list[tuple[str, str, list[str], str, list[str]]]]:
    """Load every `*.yaml` in spec_dir and collect schemas with properties.

    Returns (merged_schemas, divergences). When the same schema name is
    declared in two specs with different shapes, the later-loaded one
    wins in ``merged_schemas`` and the pair is recorded in ``divergences``
    as ``(name, first_spec, first_fields, second_spec, second_fields)``.
    """
    schemas: dict[str, list[str]] = {}
    seen_in: dict[str, str] = {}
    divergences: list[tuple[str, str, list[str], str, list[str]]] = []
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
            if name in schemas and schemas[name] != fields:
                divergences.append((name, seen_in[name], schemas[name], spec_file.name, fields))
            schemas[name] = fields
            seen_in[name] = spec_file.name
    return schemas, divergences


def _discover_models() -> dict[str, type[BaseModel]]:
    """Find every pydantic BaseModel subclass reachable under ``axonflow``."""
    models: dict[str, type[BaseModel]] = {}
    pkg_root = Path(axonflow.__file__).parent
    for mod_info in pkgutil.walk_packages([str(pkg_root)], prefix="axonflow."):
        try:
            mod = importlib.import_module(mod_info.name)
        except ImportError:
            # Optional dependencies (langchain, langgraph, openai, etc.)
            # may not be installed in the test env.
            continue
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
def loaded_specs() -> tuple[dict[str, list[str]], list[tuple[str, str, list[str], str, list[str]]]]:
    spec_dir = _specs_dir()
    if spec_dir is None:
        pytest.skip(
            "AXONFLOW_OPENAPI_SPECS_DIR not set to an existing directory; "
            "wire-shape contract tests skipped. The dedicated CI job clones "
            "https://github.com/getaxonflow/axonflow and exports the specs "
            "dir before running this file."
        )
    return _load_all_schemas(spec_dir)


@pytest.fixture(scope="module")
def openapi_schemas(
    loaded_specs: tuple[dict[str, list[str]], list[tuple[str, str, list[str], str, list[str]]]],
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
    loaded_specs: tuple[dict[str, list[str]], list[tuple[str, str, list[str], str, list[str]]]],
    baseline: dict[str, Any],
) -> None:
    """Fail if a schema name is declared with different shapes in two
    specs and is NOT in the baseline allowlist.

    Platform-side spec inconsistencies — the SDK side can only pick one
    version when the name collides. Entries already in the baseline are
    tracked for burn-down; newly-introduced divergences block the PR.
    """
    allowed = set(baseline.get("cross_spec_duplicates", []))
    divergences = loaded_specs[1]
    unacknowledged = [d for d in divergences if d[0] not in allowed]
    if unacknowledged:
        lines = ["", "NEW cross-spec schema divergence detected (not in baseline):", ""]
        for name, spec_a, fields_a, spec_b, fields_b in unacknowledged:
            only_a = sorted(set(fields_a) - set(fields_b))
            only_b = sorted(set(fields_b) - set(fields_a))
            lines.append(f"  {name}:")
            lines.append(f"    {spec_a}: {fields_a}")
            lines.append(f"    {spec_b}: {fields_b}")
            if only_a:
                lines.append(f"    Only in {spec_a}: {only_a}")
            if only_b:
                lines.append(f"    Only in {spec_b}: {only_b}")
        lines.append("")
        lines.append(
            "Fix: reconcile in the axonflow-enterprise specs (rename one, "
            "or merge into a shared supertype). If the divergence is "
            "intentional and must stand, add the schema name to "
            "tests/fixtures/wire_shape_baseline.json::cross_spec_duplicates "
            "with a tracking issue."
        )
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
