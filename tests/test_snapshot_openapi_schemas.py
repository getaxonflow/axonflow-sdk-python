"""The derived spec snapshot keeps exactly what the wire-shape contract reads.

scripts/snapshot_openapi_schemas.py writes tests/fixtures/openapi/ from the
platform's docs/api. Its own ``--self-test`` checks the derivation on a
planted spec: every schema declaration survives in order (a duplicate and a
property-less one included), no prose survives, and the output is
deterministic. These tests run that self-test, check this repository's loader
against it, and check the committed snapshot is in derived form.
"""

from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "snapshot_openapi_schemas.py"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "openapi"


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


def _plant(tmp_path: Path, script: ModuleType) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "planted-api.yaml").write_text(script.PLANTED, encoding="utf-8")
    return src


def test_the_scripts_own_self_test_passes(script: ModuleType) -> None:
    assert script.self_test() == 0


def test_this_repositorys_loader_reads_the_same_schemas_from_source_and_snapshot(
    tmp_path: Path, script: ModuleType, wire_shape: ModuleType
) -> None:
    src = _plant(tmp_path, script)
    out = tmp_path / "out"
    assert script.main([str(src), str(out), "--source-commit", "c0ffee"]) == 0
    assert wire_shape._load_all_schemas(out) == wire_shape._load_all_schemas(src)
    # The planted positive: the loader did read something, so equality is not
    # two empty results agreeing.
    assert wire_shape._load_all_schemas(out)[0]["WithProps"] == ["a_field", "b_field"]


def test_the_header_names_the_source_file_commit_and_digest(
    tmp_path: Path, script: ModuleType
) -> None:
    src = _plant(tmp_path, script)
    out = tmp_path / "out"
    assert script.main([str(src), str(out), "--source-commit", "c0ffee"]) == 0
    digest = hashlib.sha256((src / "planted-api.yaml").read_bytes()).hexdigest()
    head = (out / "planted-api.yaml").read_text(encoding="utf-8").splitlines()[:3]
    assert head[0] == script.HEADER_FIRST_LINE
    assert head[1] == "# Source: docs/api/planted-api.yaml at platform commit c0ffee"
    assert head[2] == f"# Source sha256: {digest}"


def test_an_empty_source_directory_is_an_error(tmp_path: Path, script: ModuleType) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert script.main([str(empty), str(tmp_path / "out"), "--source-commit", "c0ffee"]) == 1


def test_a_merge_key_under_schemas_is_refused(script: ModuleType) -> None:
    with pytest.raises(ValueError, match="merge key"):
        script.declarations("components:\n  schemas:\n    <<: {A: {}}\n")


def test_the_committed_snapshot_is_exactly_the_scripts_derived_form(script: ModuleType) -> None:
    assert script.check_snapshot(FIXTURE_DIR) == []


def test_a_hand_edited_snapshot_is_refused(tmp_path: Path, script: ModuleType) -> None:
    copy = tmp_path / "openapi"
    copy.mkdir()
    for path in sorted(FIXTURE_DIR.glob("*.yaml")):
        (copy / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    first = sorted(copy.glob("*.yaml"))[0]
    first.write_text(first.read_text(encoding="utf-8") + '    "HandAdded": {}\n', encoding="utf-8")
    assert script.check_snapshot(copy) == [
        f"{first.name}: not in the derived form (edited by hand?)"
    ]
