"""The derived spec snapshot keeps exactly what the wire-shape contract reads.

scripts/snapshot_openapi_schemas.py writes tests/fixtures/openapi/. These tests
pin its three properties on a planted spec: it is lossless for the contract's
loader, it carries no prose (so no foreign licence text reaches this MIT
repository), and it is deterministic.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "snapshot_openapi_schemas.py"

PLANTED_SPEC = {
    "openapi": "3.0.3",
    "info": {
        "title": "Planted",
        "version": "1.0.0",
        "license": {"name": "PLANTED-LICENCE-NAME"},
        "description": "PLANTED-INFO-PROSE",
    },
    "paths": {"/x": {"get": {"description": "PLANTED-PATH-PROSE", "responses": {}}}},
    "components": {
        "schemas": {
            "WithProps": {
                "type": "object",
                "description": "PLANTED-SCHEMA-PROSE",
                "properties": {
                    "b_field": {"type": "string", "description": "PLANTED-FIELD-PROSE"},
                    "a_field": {"type": "integer", "enum": [1, 2]},
                },
            },
            "EmptyProps": {"type": "object", "properties": {}},
            "RefOnly": {"$ref": "#/components/schemas/WithProps"},
            "NotAMapping": "string",
        }
    },
}


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script() -> ModuleType:
    return _load(SCRIPT, "snapshot_openapi_schemas")


@pytest.fixture(scope="module")
def wire_shape() -> ModuleType:
    return _load(REPO_ROOT / "tests" / "test_wire_shape.py", "wire_shape_under_test")


def _run(script: ModuleType, src: Path, out: Path) -> None:
    assert script.main([str(src), str(out), "--source-commit", "c0ffee"]) == 0


def _plant(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "planted-api.yaml").write_text(yaml.safe_dump(PLANTED_SPEC), encoding="utf-8")
    return src


def test_the_contract_loader_reads_the_same_schemas_from_source_and_snapshot(
    tmp_path: Path, script: ModuleType, wire_shape: ModuleType
) -> None:
    src = _plant(tmp_path)
    out = tmp_path / "out"
    _run(script, src, out)
    assert wire_shape._load_all_schemas(out) == wire_shape._load_all_schemas(src)
    # The planted positive: the loader did read something, so equality is not
    # two empty results agreeing.
    assert wire_shape._load_all_schemas(out)[0] == {"WithProps": ["a_field", "b_field"]}


def test_the_snapshot_carries_no_prose(tmp_path: Path, script: ModuleType) -> None:
    src = _plant(tmp_path)
    out = tmp_path / "out"
    _run(script, src, out)
    text = (out / "planted-api.yaml").read_text(encoding="utf-8")
    for planted in (
        "PLANTED-LICENCE-NAME",
        "PLANTED-INFO-PROSE",
        "PLANTED-PATH-PROSE",
        "PLANTED-SCHEMA-PROSE",
        "PLANTED-FIELD-PROSE",
    ):
        assert planted not in text, f"{planted} survived the derivation"
    body = yaml.safe_load(text)
    assert body == {
        "components": {"schemas": {"WithProps": {"properties": {"a_field": {}, "b_field": {}}}}}
    }


def test_the_snapshot_is_deterministic(tmp_path: Path, script: ModuleType) -> None:
    src = _plant(tmp_path)
    first, second = tmp_path / "first", tmp_path / "second"
    _run(script, src, first)
    _run(script, src, second)
    assert (first / "planted-api.yaml").read_bytes() == (second / "planted-api.yaml").read_bytes()


def test_the_header_names_the_source_file_commit_and_digest(
    tmp_path: Path, script: ModuleType
) -> None:
    src = _plant(tmp_path)
    out = tmp_path / "out"
    _run(script, src, out)
    digest = hashlib.sha256((src / "planted-api.yaml").read_bytes()).hexdigest()
    head = (out / "planted-api.yaml").read_text(encoding="utf-8").splitlines()[:3]
    assert head[1] == "# Source: docs/api/planted-api.yaml at platform commit c0ffee"
    assert head[2] == f"# Source sha256: {digest}"


def test_an_empty_source_directory_is_an_error(tmp_path: Path, script: ModuleType) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert script.main([str(empty), str(tmp_path / "out"), "--source-commit", "c0ffee"]) == 1


def test_every_committed_snapshot_file_carries_the_generated_header() -> None:
    """Every committed fixture file carries the generated header, so a hand edit
    that drops it, or a file placed there by hand, fails here."""
    fixture_dir = REPO_ROOT / "tests" / "fixtures" / "openapi"
    files = sorted(fixture_dir.glob("*.yaml"))
    assert files, "tests/fixtures/openapi/ holds no *.yaml"
    for path in files:
        first = path.read_text(encoding="utf-8").splitlines()[0]
        assert first == "# GENERATED by scripts/snapshot_openapi_schemas.py - do not edit.", (
            path.name
        )
