#!/usr/bin/env python3
"""Regenerate tests/fixtures/wire_shape_baseline.json.

Usage:
    python scripts/refresh_wire_shape_baseline.py <specs_dir> [--sha <SHA>]

Arguments:
    specs_dir   Path to a local clone of the ``docs/api`` directory from
                the getaxonflow/axonflow community mirror. The specs
                there are the authoritative wire contract.
    --sha       Optional commit SHA of the community repo at the time of
                generation. When omitted, the script tries ``git -C
                <parent-of-specs_dir> rev-parse HEAD`` if it's a git
                checkout; otherwise records an empty string.

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
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_MODULE_PATH = REPO_ROOT / "tests" / "test_wire_shape.py"
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


def _git_head_sha(spec_dir: Path) -> str:
    """Best-effort: read the commit SHA of the git repo containing spec_dir.

    We invoke git via its absolute path (if resolvable) on a caller-supplied
    directory argument. The script is developer-local tooling — run in CI
    we pin the SHA explicitly via --sha and never rely on this path.
    """
    import shutil  # noqa: PLC0415

    git = shutil.which("git")
    if git is None:
        return ""
    try:
        result = subprocess.run(  # noqa: S603 - git invoked with absolute path and fixed args
            [git, "-C", str(spec_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return ""
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("specs_dir", type=Path, help="Path to docs/api directory")
    parser.add_argument("--sha", type=str, default=None, help="Commit SHA to pin")
    args = parser.parse_args()

    specs_dir: Path = args.specs_dir
    if not specs_dir.is_dir():
        print(f"error: {specs_dir} is not a directory", file=sys.stderr)
        return 2

    # Resolve SHA before touching any models / schemas so a bad SHA fails
    # fast without wasted work AND without writing a poisoned baseline.
    sha = args.sha if args.sha is not None else _git_head_sha(specs_dir)
    if not sha:
        print(
            "error: could not determine OpenAPI specs commit SHA.\n"
            "  Either run this script against a specs_dir that sits inside a git\n"
            "  checkout of the getaxonflow/axonflow community mirror, or pass\n"
            "  --sha <commit-sha> explicitly. An empty SHA would poison\n"
            "  tests/fixtures/wire_shape_baseline.json and break the next CI\n"
            "  wire-shape-contract run at bootstrap.",
            file=sys.stderr,
        )
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
    print(f"  openapi_specs_sha: {sha or '(unknown — pass --sha or run inside a git checkout)'}")
    print(f"  cross_spec_duplicates: {len(cross_spec)}")
    print(f"  registered_models:     {len(registered)}")
    print(f"  per_model_drift:       {len(drift)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
