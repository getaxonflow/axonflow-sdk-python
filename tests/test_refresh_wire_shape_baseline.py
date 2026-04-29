"""Regression tests for ``scripts/refresh_wire_shape_baseline.py``.

The wire-shape contract gate (``test_wire_shape.py``) only reads
``sdk_only`` and ``spec_only`` from each ``per_model_drift`` entry. The
human-authored ``note`` fields that classify each drift entry are
read by reviewers, not by the gate, so a refactor that "cleans up" the
regenerator and silently strips the ``note`` field would not be caught
by the existing test suite — the burn-down ledger would just disappear
on the next ``python scripts/refresh_wire_shape_baseline.py`` run.

These tests pin the preservation contract.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from textwrap import dedent

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "refresh_wire_shape_baseline.py"
BASELINE_PATH = REPO_ROOT / "tests" / "fixtures" / "wire_shape_baseline.json"


def _load_script_module():
    """Import the regenerator as a module so we can call ``main`` directly."""
    spec = importlib.util.spec_from_file_location("_refresh_baseline", SCRIPT_PATH)
    assert spec is not None, f"Could not load module spec for {SCRIPT_PATH}"
    assert spec.loader is not None, f"Module spec for {SCRIPT_PATH} has no loader"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_minimal_specs(specs_dir: Path) -> None:
    """Write a tiny synthetic OpenAPI spec so the regenerator has something
    to chew on. Two schemas, both shapes the SDK has matching pydantic
    models for (``StaticPolicy``, ``PolicyVersion``)."""
    spec = dedent(
        """\
        openapi: 3.0.0
        info: {title: synthetic, version: 0.0.0}
        components:
          schemas:
            StaticPolicy:
              type: object
              properties:
                id: {type: string}
                policy_id: {type: string}
                name: {type: string}
                description: {type: string}
                category: {type: string}
                tier: {type: string}
                pattern: {type: string}
                severity: {type: string}
                enabled: {type: boolean}
                action: {type: string}
                priority: {type: integer}
                organization_id: {type: string}
                tenant_id: {type: string}
                created_at: {type: string}
                updated_at: {type: string}
                version: {type: integer}
                has_override: {type: boolean}
                override: {type: object}
            PolicyVersion:
              type: object
              properties:
                id: {type: string}
                policy_id: {type: string}
                version: {type: integer}
                changed_by: {type: string}
                changed_at: {type: string}
                change_type: {type: string}
                change_summary: {type: string}
                snapshot: {type: object}
                # Note: spec deliberately omits the deprecated camelCase
                # diff fields the SDK still carries — that's the drift
                # the test exercises.
        """
    )
    (specs_dir / "test-spec.yaml").write_text(spec)


@pytest.fixture
def script_module():
    return _load_script_module()


@pytest.fixture
def isolated_baseline(tmp_path, monkeypatch):
    """Run the regenerator against a temp BASELINE_PATH so the real
    ``tests/fixtures/wire_shape_baseline.json`` is never touched.
    """
    fake_baseline = tmp_path / "baseline.json"
    monkeypatch.setattr(_load_script_module(), "BASELINE_PATH", fake_baseline, raising=False)
    return fake_baseline


def _run_regenerator(script_module, specs_dir: Path, baseline_path: Path, sha: str) -> int:
    """Invoke ``main()`` with the right argv + monkey-patched BASELINE_PATH."""
    script_module.BASELINE_PATH = baseline_path
    argv = [str(SCRIPT_PATH), str(specs_dir), "--sha", sha]
    old_argv = sys.argv
    try:
        sys.argv = argv
        return script_module.main()
    finally:
        sys.argv = old_argv


def test_regenerator_preserves_existing_notes(tmp_path, script_module):
    """A ``note`` field on a drift entry survives a regen.

    This is the load-bearing contract — without it, every routine
    ``refresh_wire_shape_baseline.py`` run silently wipes the burn-down
    rationale and the gate's per-entry classification disappears.
    """
    specs_dir = tmp_path / "docs" / "api"
    specs_dir.mkdir(parents=True)
    _write_minimal_specs(specs_dir)

    fake_baseline = tmp_path / "baseline.json"

    # First run: no existing baseline — regenerator should mint one with
    # at least one drift entry (PolicyVersion's deprecated fields).
    rc = _run_regenerator(script_module, specs_dir, fake_baseline, sha="aaaa1111")
    assert rc == 0, "first regen should succeed"
    initial = json.loads(fake_baseline.read_text())
    assert "per_model_drift" in initial, "regen output missing per_model_drift"
    drift_names = list(initial["per_model_drift"].keys())
    assert drift_names, "synthetic spec should produce at least one drift entry"

    # Author a note on every drift entry, persist, then regen.
    for name in drift_names:
        initial["per_model_drift"][name]["note"] = (
            f"acknowledged-sdk-superset: load-bearing test note for {name} (must survive regen)"
        )
    fake_baseline.write_text(json.dumps(initial, indent=2, sort_keys=True) + "\n")

    rc = _run_regenerator(script_module, specs_dir, fake_baseline, sha="aaaa2222")
    assert rc == 0, "second regen should succeed"
    after = json.loads(fake_baseline.read_text())
    for name in drift_names:
        entry = after["per_model_drift"].get(name)
        assert entry is not None, f"drift entry {name!r} disappeared after regen"
        assert "note" in entry, (
            f"regenerator dropped the note on {name!r} — preservation contract broken"
        )
        assert entry["note"].startswith("acknowledged-sdk-superset:"), (
            f"regenerator mangled the note on {name!r}: {entry['note']!r}"
        )


def test_regenerator_does_not_invent_notes(tmp_path, script_module):
    """A drift entry without a prior note stays without one — the
    regenerator must not synthesise notes from thin air."""
    specs_dir = tmp_path / "docs" / "api"
    specs_dir.mkdir(parents=True)
    _write_minimal_specs(specs_dir)

    fake_baseline = tmp_path / "baseline.json"
    rc = _run_regenerator(script_module, specs_dir, fake_baseline, sha="bbbb1111")
    assert rc == 0
    out = json.loads(fake_baseline.read_text())
    for name, entry in out["per_model_drift"].items():
        assert "note" not in entry, (
            f"regenerator invented a note on {name!r} from a baseline that had none: "
            f"{entry.get('note')!r}"
        )


def test_committed_baseline_has_no_unannotated_drift_entries():
    """Pin the burn-down acceptance bar at the baseline level too.

    The gate test (``test_no_new_sdk_vs_spec_drift``) only fails on NEW
    drift outside the baseline. This test fails if any baselined drift
    entry lacks a ``note`` — preventing a future PR from adding a drift
    entry to the baseline without classifying it.
    """
    if not BASELINE_PATH.is_file():
        pytest.skip("no baseline checked in")
    data = json.loads(BASELINE_PATH.read_text())
    drift = data.get("per_model_drift") or {}
    unannotated = sorted(name for name, entry in drift.items() if "note" not in entry)
    assert not unannotated, (
        "Baseline drift entries missing a `note` classification "
        "(every entry must declare spec-bug-pending / deprecated-pending-"
        "removal / acknowledged-sdk-superset / sdk-aggregation): "
        f"{unannotated}"
    )


def test_committed_baseline_spec_bug_pending_count_under_limit():
    """The burn-down bar caps spec-bug-pending entries at <5 (strict)."""
    if not BASELINE_PATH.is_file():
        pytest.skip("no baseline checked in")
    data = json.loads(BASELINE_PATH.read_text())
    drift = data.get("per_model_drift") or {}
    spec_bug = sorted(
        name
        for name, entry in drift.items()
        if isinstance(entry.get("note"), str) and entry["note"].startswith("spec-bug-pending")
    )
    assert len(spec_bug) < 5, (
        f"Too many spec-bug-pending entries ({len(spec_bug)}); the burn-down "
        f"bar is <5. Current: {spec_bug}. Either fix the SDK to absorb the "
        "drift, escalate the spec PR to close the entry, or re-classify."
    )
