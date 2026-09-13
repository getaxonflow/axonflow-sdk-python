"""The v11.0.0 platform wire: decision provenance, frozen legacy writes, deprecated routes.

A v11 platform reports what decided each governed request (``engine``,
``subject_type``, ``policy_bundle``, ``legacy_validators``, and on ``/decide`` the
matched policies by name, the add-on packs and the organization document's
version). It freezes the static- and dynamic-policy write routes with
``409 LEGACY_POLICY_WRITE_FROZEN`` and stamps its legacy policy routes as
deprecated. These tests pin how the SDK surfaces each of those, and that a
v10-shaped response still parses with every new field ``None``.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from pytest_httpx import HTTPXMock

from axonflow import (
    AxonFlow,
    AxonFlowError,
    DecideRequest,
    LegacyPolicyWriteFrozenError,
    PlatformRouteDeprecationWarning,
)
from axonflow.policies import CreateDynamicPolicyRequest, CreateStaticPolicyRequest

BASE = "https://test.axonflow.com"

PROVENANCE: dict[str, Any] = {
    "engine": "anchored",
    "subject_type": "client",
    "policy_bundle": "sha256:4f2a",
    "legacy_validators": [{"validator": "indonesia_pii", "action": "masked"}],
}

FROZEN_BODY = {
    "error": {
        "code": "LEGACY_POLICY_WRITE_FROZEN",
        "message": "legacy policy writes are frozen; author policy at /api/v1/typed-policies",
    }
}

STAMP_BEFORE_TAG = {
    "X-AxonFlow-Removed-In": "v11.1",
    "Link": '</api/v1/typed-policies>; rel="successor-version"',
}


def _assert_provenance(obj: Any) -> None:
    assert obj.engine == "anchored"
    assert obj.subject_type == "client"
    assert obj.policy_bundle == "sha256:4f2a"
    assert obj.legacy_validators is not None
    assert [(v.validator, v.action) for v in obj.legacy_validators] == [("indonesia_pii", "masked")]


def _assert_no_provenance(obj: Any) -> None:
    assert obj.engine is None
    assert obj.subject_type is None
    assert obj.policy_bundle is None
    assert obj.legacy_validators is None


# ---------------------------------------------------------------------------
# Decision provenance on every governed response
# ---------------------------------------------------------------------------


async def test_decide_carries_the_v11_provenance(client: AxonFlow, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/api/v1/decide",
        json={
            "verdict": "allow",
            "decision_id": "dec_1",
            "obligations": [],
            "evaluated_policies": ["sys_pii_ktp", "org_pol_7"],
            **PROVENANCE,
            "policy_identities": [
                {"id": "sys_pii_ktp", "name": "Indonesian KTP", "source": "shipped"},
                {"id": "org_pol_7", "name": "Card data", "source": "organization", "version": 3},
            ],
            "policy_packs": ["fincrime"],
            "document_version": 3,
        },
    )
    resp = await client.decide(DecideRequest(stage="tool", query="hi"))
    _assert_provenance(resp)
    assert resp.policy_packs == ["fincrime"]
    assert resp.document_version == 3
    assert resp.policy_identities is not None
    assert [(p.id, p.name, p.source, p.version) for p in resp.policy_identities] == [
        ("sys_pii_ktp", "Indonesian KTP", "shipped", None),
        ("org_pol_7", "Card data", "organization", 3),
    ]


async def test_a_v10_decide_response_parses_with_every_new_field_none(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/api/v1/decide",
        json={"verdict": "allow", "decision_id": "dec_1", "obligations": []},
    )
    resp = await client.decide(DecideRequest(stage="tool", query="hi"))
    _assert_no_provenance(resp)
    assert resp.policy_identities is None
    assert resp.policy_packs is None
    assert resp.document_version is None


async def test_pre_check_carries_the_decision_id_verdict_and_provenance(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/api/policy/pre-check",
        json={
            "context_id": "ctx_1",
            "approved": False,
            "expires_at": "2026-09-13T06:00:00Z",
            "block_reason": "blocked by policy",
            "decision_id": "dec_9",
            "verdict": "deny",
            **PROVENANCE,
        },
    )
    result = await client.get_policy_approved_context(user_token="u", query="q")
    assert result.decision_id == "dec_9"
    assert result.verdict == "deny"
    _assert_provenance(result)


async def test_a_v10_pre_check_parses_with_every_new_field_none(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/api/policy/pre-check",
        json={"context_id": "ctx_1", "approved": True, "expires_at": "2026-09-13T06:00:00Z"},
    )
    result = await client.get_policy_approved_context(user_token="u", query="q")
    assert result.decision_id is None
    assert result.verdict is None
    _assert_no_provenance(result)


async def test_request_carries_the_provenance(client: AxonFlow, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/api/request",
        json={"success": True, "blocked": False, "data": {"answer": "ok"}, **PROVENANCE},
    )
    resp = await client.proxy_llm_call(user_token="u", query="q", request_type="chat")
    _assert_provenance(resp)


async def test_mcp_check_output_carries_the_provenance(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/api/v1/mcp/check-output",
        json={"allowed": True, "policies_evaluated": 2, **PROVENANCE},
    )
    resp = await client.mcp_check_output(connector_type="postgres", message="ok")
    _assert_provenance(resp)


async def test_mcp_query_maps_the_provenance_onto_its_connector_response(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/mcp/resources/query",
        json={"success": True, "data": [], **PROVENANCE},
    )
    resp = await client.mcp_query("postgres", "SELECT 1")
    _assert_provenance(resp)


async def test_query_connector_maps_the_request_provenance_onto_its_connector_response(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/api/request",
        json={"success": True, "data": {"rows": []}, **PROVENANCE},
    )
    resp = await client.query_connector(user_token="u", connector_name="postgres", operation="q")
    _assert_provenance(resp)


# ---------------------------------------------------------------------------
# Frozen legacy writes
# ---------------------------------------------------------------------------


async def test_a_frozen_static_policy_write_raises_the_typed_error(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/api/v1/static-policies", method="POST", status_code=409, json=FROZEN_BODY
    )
    with pytest.raises(LegacyPolicyWriteFrozenError) as info:
        await client.create_static_policy(
            CreateStaticPolicyRequest(name="n", category="security-sqli", pattern="x")
        )
    assert info.value.code == "LEGACY_POLICY_WRITE_FROZEN"
    assert "/api/v1/typed-policies" in info.value.message


async def test_a_frozen_dynamic_policy_write_raises_the_typed_error(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/api/v1/dynamic-policies", method="POST", status_code=409, json=FROZEN_BODY
    )
    with pytest.raises(LegacyPolicyWriteFrozenError):
        await client.create_dynamic_policy(
            CreateDynamicPolicyRequest(name="n", type="cost", conditions=[], actions=[])
        )


async def test_a_different_409_on_the_same_route_is_not_the_frozen_error(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/api/v1/static-policies",
        method="POST",
        status_code=409,
        json={"error": {"code": "DUPLICATE_NAME", "message": "exists"}},
    )
    with pytest.raises(AxonFlowError) as info:
        await client.create_static_policy(
            CreateStaticPolicyRequest(name="n", category="security-sqli", pattern="x")
        )
    assert not isinstance(info.value, LegacyPolicyWriteFrozenError)


# ---------------------------------------------------------------------------
# Deprecated routes
# ---------------------------------------------------------------------------


async def test_the_pre_tag_stamp_warns_naming_the_successor_and_the_removal(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/api/v1/static-policies", json={"policies": []}, headers=STAMP_BEFORE_TAG
    )
    with pytest.warns(PlatformRouteDeprecationWarning) as record:
        await client.list_static_policies()
    warning = record[0].message
    assert isinstance(warning, PlatformRouteDeprecationWarning)
    assert warning.route == "GET /api/v1/static-policies"
    assert warning.successor == "/api/v1/typed-policies"
    assert warning.removed_in == "v11.1"
    assert warning.deprecation is None


async def test_the_rfc9745_deprecation_header_alone_warns(
    client: AxonFlow, httpx_mock: HTTPXMock
) -> None:
    httpx_mock.add_response(
        url=f"{BASE}/api/v1/dynamic-policies",
        json={"policies": []},
        headers={"Deprecation": "@1789603200"},
    )
    with pytest.warns(PlatformRouteDeprecationWarning) as record:
        await client.list_dynamic_policies()
    warning = record[0].message
    assert isinstance(warning, PlatformRouteDeprecationWarning)
    assert warning.deprecation == "@1789603200"
    assert warning.successor is None


async def test_an_unstamped_response_does_not_warn(client: AxonFlow, httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=f"{BASE}/api/v1/static-policies", json={"policies": []})
    with warnings.catch_warnings():
        warnings.simplefilter("error", PlatformRouteDeprecationWarning)
        await client.list_static_policies()
