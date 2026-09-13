"""A static policy's category: known values parse to the enum, unknown ones stay strings.

The platform ships and extends its categories as data, and a v11 platform returns
categories the SDK's enum did not name (``security-dangerous`` on
``GET /api/v1/static-policies/effective``), which failed the whole read. A category
the SDK knows now parses to ``PolicyCategory``; one it does not know yet stays the
platform's string, and the read succeeds.

The SDK's known set is pinned to the categories the platform's shipped posture uses
(``tests/fixtures/shipped_posture_categories.json``, vendored from the platform with
its commit and sha256). The spec's own enum is stale, and is being made a free string
(getaxonflow/axonflow-enterprise#4224); until then the posture is the source this
pin follows, so a category the platform adds shows up here as a failing test
rather than as a read that raises.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from axonflow.policies import PolicyCategory, StaticPolicy

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "shipped_posture_categories.json").read_text()
)
ADDED = [
    "security-dangerous",
    "compliance-euaiact",
    "dangerous_queries",
    "pii_detection",
    "sql_injection",
]


def _policy(category: str) -> dict[str, Any]:
    return {
        "id": "pol_1",
        "name": "a policy",
        "category": category,
        "tier": "system",
        "pattern": "x",
        "created_at": "2026-09-13T00:00:00Z",
        "updated_at": "2026-09-13T00:00:00Z",
    }


def test_a_known_category_parses_to_the_enum() -> None:
    category = StaticPolicy.model_validate(_policy("security-sqli")).category
    assert category is PolicyCategory.SECURITY_SQLI


@pytest.mark.parametrize("value", ADDED)
def test_each_category_the_platform_added_parses_to_the_enum(value: str) -> None:
    category = StaticPolicy.model_validate(_policy(value)).category
    assert isinstance(category, PolicyCategory)
    assert category.value == value


def test_an_unknown_category_stays_the_platforms_string() -> None:
    category = StaticPolicy.model_validate(_policy("a-category-from-a-later-platform")).category
    assert type(category) is str
    assert category == "a-category-from-a-later-platform"


def test_every_category_the_shipped_posture_uses_is_known() -> None:
    known = {c.value for c in PolicyCategory}
    missing = sorted(set(FIXTURE["categories"]) - known)
    assert missing == [], (
        f"the platform's shipped posture at {FIXTURE['platform_commit'][:9]} uses categories "
        f"PolicyCategory lacks: {missing}"
    )


def test_the_fixture_names_its_source() -> None:
    assert FIXTURE["source"] == "platform/decision/pdp/shipped_posture.json"
    assert len(FIXTURE["platform_commit"]) == 40
    assert len(FIXTURE["source_sha256"]) == 64
    assert FIXTURE["categories"] == sorted(set(FIXTURE["categories"]))
