#!/usr/bin/env python3
"""Regenerate tests/fixtures/wire_shape_baseline.json.

Usage:
    python scripts/refresh_wire_shape_baseline.py <specs_dir> [--sha <SHA>]

Arguments:
    specs_dir   Path to a directory holding the platform's ``docs/api``
                specs - normally the committed snapshot,
                ``tests/fixtures/openapi`` (see its README), or a checkout
                of the getaxonflow/axonflow community mirror's docs/api.
                The specs there are the authoritative wire contract.
    --sha       Platform commit the specs were taken from. For the committed
                snapshot it is read from the files' own headers (see
                scripts/snapshot_openapi_schemas.py), and a --sha that
                disagrees with them is refused. A directory that is not a
                generated snapshot, such as a platform checkout's docs/api,
                names no commit, so --sha is required for it.

When to run:
    - After a deliberate spec change that should be acknowledged as the
      new baseline (e.g. a legitimate schema divergence, or burn-down of
      an existing drift entry).
    - Never just because the gate failed. Read the failure first;
      regenerating to silence a failure hides the bug.

Output:
    Writes JSON to tests/fixtures/wire_shape_baseline.json with:
      - openapi_specs_sha  : pinned commit the baseline was built against
      - cross_spec_duplicates : {schema_name: {spec_filename: [fields]}}
      - registered_models  : the list of models currently mapped to a
                             schema, for the rename-escape guard
      - per_model_drift    : {model_name: {sdk_only, spec_only}} frozen
                             at the time of generation

Running this script requires the SDK package to be importable (so the
pydantic model walker can find every BaseModel subclass).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_MODULE_PATH = REPO_ROOT / "tests" / "test_wire_shape.py"
SNAPSHOT_SCRIPT_PATH = REPO_ROOT / "scripts" / "snapshot_openapi_schemas.py"
BASELINE_PATH = REPO_ROOT / "tests" / "fixtures" / "wire_shape_baseline.json"

# Bind ``import axonflow`` to THIS repo's package, ahead of any installed
# (or editable-installed-from-elsewhere) copy on sys.path. Without this,
# ``python scripts/refresh_wire_shape_baseline.py`` puts scripts/ (not the
# repo root) at sys.path[0], so a stale editable install pointing at a
# DIFFERENT checkout silently wins and the regenerated baseline records
# that other tree's models - observed in practice (#3254 batch 2): a
# sibling checkout's pre-fix masfeat parser produced a wrong-but-plausible
# drift entry with no error.
sys.path.insert(0, str(REPO_ROOT))


def _load_test_helpers():
    spec = importlib.util.spec_from_file_location("_ws", TEST_MODULE_PATH)
    if spec is None or spec.loader is None:
        msg = f"Could not load helpers from {TEST_MODULE_PATH}"
        raise RuntimeError(msg)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_snapshot_script():
    spec = importlib.util.spec_from_file_location("_snapshot", SNAPSHOT_SCRIPT_PATH)
    if spec is None or spec.loader is None:
        msg = f"Could not load {SNAPSHOT_SCRIPT_PATH}"
        raise RuntimeError(msg)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def resolve_specs_sha(specs_dir: Path, explicit: str | None) -> str:
    """The platform commit to pin the baseline to. Raises ValueError.

    A generated snapshot names its commit in every file's header, and that is
    the commit: --sha may repeat it but not contradict it. Any other directory
    names none, so the caller must. There is no fallback to a git checkout's
    HEAD: for the snapshot, which lives inside this repository, that is the
    SDK's own commit, and a baseline pinned to it names a spec it never read.
    """
    named = _load_snapshot_script().snapshot_commit(specs_dir)
    if named is not None:
        if explicit is not None and explicit != named:
            msg = (
                f"--sha {explicit} disagrees with {specs_dir}, whose generated "
                f"headers name platform commit {named}"
            )
            raise ValueError(msg)
        return named
    if not explicit:
        msg = (
            f"{specs_dir} is not a generated snapshot, so it names no platform "
            "commit; pass --sha <commit> for the platform checkout its specs came from"
        )
        raise ValueError(msg)
    return explicit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("specs_dir", type=Path, help="Path to docs/api directory")
    parser.add_argument(
        "--sha",
        type=str,
        default=None,
        help="Platform commit; read from a generated snapshot's headers, required otherwise",
    )
    args = parser.parse_args()

    specs_dir: Path = args.specs_dir
    if not specs_dir.is_dir():
        print(f"error: {specs_dir} is not a directory", file=sys.stderr)
        return 2

    # Resolve the SHA before touching any models or schemas, so a bad one
    # fails fast and never writes a poisoned baseline.
    try:
        sha = resolve_specs_sha(specs_dir, args.sha)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    helpers = _load_test_helpers()
    merged, duplicates_by_spec = helpers._load_all_schemas(specs_dir)
    models = helpers._discover_models()

    # Preserve `note` annotations from the previous baseline so a regen
    # doesn't silently strip the human-authored burn-down rationale.
    # Notes are carried forward verbatim — when a model's drift changes,
    # the note may need updating, and that is a reviewer's call. The
    # gate itself only reads sdk_only/spec_only; `note` is informational.
    existing_notes: dict[str, str] = {}
    if BASELINE_PATH.is_file():
        with BASELINE_PATH.open() as f:
            old = json.load(f) or {}
        for name, entry in (old.get("per_model_drift") or {}).items():
            note = entry.get("note") if isinstance(entry, dict) else None
            if isinstance(note, str) and note:
                existing_notes[name] = note

    registered: list[str] = []
    drift: dict[str, dict[str, Any]] = {}

    def _record(name: str, sdk_fields: list[str]) -> None:
        if name not in merged:
            return
        registered.append(name)
        spec_fields = merged[name]
        if sdk_fields == spec_fields:
            return
        entry: dict[str, Any] = {
            "sdk_only": sorted(set(sdk_fields) - set(spec_fields)),
            "spec_only": sorted(set(spec_fields) - set(sdk_fields)),
        }
        if name in existing_notes:
            entry["note"] = existing_notes[name]
        drift[name] = entry

    for name, model in models.items():
        _record(name, helpers._wire_fields(model))

    # #3262: masfeat dataclass bindings (parser-consumed wire keys) join
    # the baseline on the same terms as pydantic models, so a pin bump
    # regen recomputes their drift instead of silently dropping it.
    for name, consumed in helpers._masfeat_dataclass_bindings().items():
        _record(name, consumed)

    cross_spec: dict[str, dict[str, list[str]]] = {
        name: {spec: list(fields) for spec, fields in sorted(decls.items())}
        for name, decls in sorted(duplicates_by_spec.items())
    }

    out = {
        "_comment": (
            "Baseline of KNOWN wire-shape drift between the Python SDK and "
            "the OpenAPI specs. Generated by "
            "scripts/refresh_wire_shape_baseline.py. The CI gate fails on "
            "drift OUTSIDE this baseline. Entries here should be burned "
            "down over time via targeted fix PRs. See axonflow-"
            "enterprise#1704 for the tracking issue."
        ),
        "openapi_specs_sha": sha,
        "cross_spec_duplicates": cross_spec,
        "registered_models": sorted(registered),
        "per_model_drift": drift,
    }

    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with BASELINE_PATH.open("w") as f:
        json.dump(out, f, indent=2, sort_keys=True)
        f.write("\n")

    print(f"Wrote baseline: {BASELINE_PATH}")
    print(f"  openapi_specs_sha: {sha}")
    print(f"  cross_spec_duplicates: {len(cross_spec)}")
    print(f"  registered_models:     {len(registered)}")
    print(f"  per_model_drift:       {len(drift)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
