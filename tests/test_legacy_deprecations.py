"""How the SDK reports the platform's deprecation of its legacy policy routes.

A v11 platform stamps every response from its deprecated policy surface, and the
client reports each stamped route ONCE per client through
``PlatformRouteDeprecationWarning``, as the TypeScript and Go SDKs do: keyed by
method and path without the query string, and shared with clients derived through
``as_user``. The stamp below is exactly what the platform's
``policypath.StampDeprecation`` writes at 857455033, with the header names as Go
canonicalises them (as a live capture showed): ``X-Axonflow-Removed-In`` and
the successor ``Link``, with ``Deprecation`` omitted until release prep sets the
tag date.

Every count is taken with ``simplefilter("always")``, so Python's own warning
filter cannot hide a repeat: the counts are what the SDK emits.
"""

from __future__ import annotations

import warnings
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from axonflow import (
    AxonFlow,
    AxonFlowError,
    LegacyPolicyWriteFrozenError,
    PlatformRouteDeprecationWarning,
)
from axonflow.policies import CreatePolicyOverrideRequest, ListStaticPoliciesOptions, OverrideAction

BASE = "https://test.axonflow.com"
STATIC = f"{BASE}/api/v1/static-policies"
STAMP = {
    "X-Axonflow-Removed-In": "v11.1",
    "Link": '</api/v1/typed-policies>; rel="successor-version"',
}
FROZEN = {
    "error": {
        "code": "LEGACY_POLICY_WRITE_FROZEN",
        "message": "legacy policy writes are frozen; author policy through /api/v1/typed-policies",
    }
}


@contextmanager
def reports() -> Iterator[list[PlatformRouteDeprecationWarning]]:
    found: list[PlatformRouteDeprecationWarning] = []
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        yield found
    found.extend(
        w.message for w in caught if isinstance(w.message, PlatformRouteDeprecationWarning)
    )


def stamped_list(httpx_mock, calls: int = 1, url: str = STATIC) -> None:
    for _ in range(calls):
        httpx_mock.add_response(url=url, json={"policies": []}, headers=STAMP)


class TestOncePerRoute:
    @pytest.mark.asyncio
    async def test_the_first_call_reports_the_route(self, client: AxonFlow, httpx_mock) -> None:
        stamped_list(httpx_mock)
        with reports() as found:
            await client.list_static_policies()
        assert [(w.route, w.removed_in, w.successor) for w in found] == [
            ("GET /api/v1/static-policies", "v11.1", "/api/v1/typed-policies")
        ]

    @pytest.mark.asyncio
    async def test_a_second_call_reports_nothing_new(self, client: AxonFlow, httpx_mock) -> None:
        stamped_list(httpx_mock, calls=2)
        with reports() as found:
            await client.list_static_policies()
            await client.list_static_policies()
        assert len(found) == 1

    @pytest.mark.asyncio
    async def test_a_derived_client_shares_the_memory(self, client: AxonFlow, httpx_mock) -> None:
        stamped_list(httpx_mock, calls=4)
        # The parent reports, and a client derived from it does not report again...
        with reports() as found:
            await client.list_static_policies()
            await client.as_user("user-token").list_static_policies()
        assert len(found) == 1

        # ...and the other way round: a derived client's report covers its parent.
        async with AxonFlow(
            endpoint=BASE, client_id="test-client", client_secret="test-secret"
        ) as parent:
            with reports() as found:
                await parent.as_user(None).list_static_policies()
                await parent.list_static_policies()
        assert len(found) == 1

    @pytest.mark.asyncio
    async def test_a_different_route_reports_again(self, client: AxonFlow, httpx_mock) -> None:
        stamped_list(httpx_mock)
        httpx_mock.add_response(url=f"{STATIC}/effective", json={"static": []}, headers=STAMP)
        with reports() as found:
            await client.list_static_policies()
            await client.get_effective_static_policies()
        assert [w.route for w in found] == [
            "GET /api/v1/static-policies",
            "GET /api/v1/static-policies/effective",
        ]

    @pytest.mark.asyncio
    async def test_the_query_is_not_part_of_the_route(self, client: AxonFlow, httpx_mock) -> None:
        stamped_list(httpx_mock, url=f"{STATIC}?enabled=true")
        stamped_list(httpx_mock, url=f"{STATIC}?enabled=false")
        with reports() as found:
            await client.list_static_policies(ListStaticPoliciesOptions(enabled=True))
            await client.list_static_policies(ListStaticPoliciesOptions(enabled=False))
        assert [w.route for w in found] == ["GET /api/v1/static-policies"]

    @pytest.mark.asyncio
    async def test_a_refused_stamped_request_is_still_reported_once(
        self, client: AxonFlow, httpx_mock
    ) -> None:
        for _ in range(2):
            httpx_mock.add_response(
                url=f"{STATIC}/pol_missing",
                status_code=404,
                json={"error": "not found"},
                headers=STAMP,
            )
        with reports() as found:
            for _ in range(2):
                with pytest.raises(AxonFlowError):
                    await client.get_static_policy("pol_missing")
        assert [w.route for w in found] == ["GET /api/v1/static-policies/pol_missing"]

    def test_the_sync_client_reports_once(self, sync_client, httpx_mock) -> None:
        stamped_list(httpx_mock, calls=2)
        with reports() as found:
            sync_client.list_static_policies()
            sync_client.list_static_policies()
        assert len(found) == 1


class TestTheDocumentedDeprecations:
    @pytest.mark.parametrize(
        ("method", "route"),
        [
            ("simulate_policies", "POST /api/v1/policies/simulate"),
            ("get_policy_impact_report", "POST /api/v1/policies/impact-report"),
            ("detect_policy_conflicts", "POST /api/v1/policies/conflicts"),
        ],
    )
    def test_the_simulation_family_documents_its_deprecation(
        self, method: str, route: str, client: AxonFlow, sync_client
    ) -> None:
        for owner in (type(client), type(sync_client)):
            doc = " ".join((getattr(owner, method).__doc__ or "").split())
            assert f"Deprecated: the platform deprecates ``{route}`` in v11.0.0" in doc, owner
            assert "removes it in v11.1" in doc, owner
            assert "``/api/v1/typed-policies``" in doc, owner
            assert "PlatformRouteDeprecationWarning" in doc, owner

    @pytest.mark.parametrize("method", ["create_policy_override", "delete_policy_override"])
    def test_the_override_writes_document_the_freeze(
        self, method: str, client: AxonFlow, sync_client
    ) -> None:
        for owner in (type(client), type(sync_client)):
            doc = " ".join((getattr(owner, method).__doc__ or "").split())
            assert "``409 LEGACY_POLICY_WRITE_FROZEN``" in doc, owner
            assert "LegacyPolicyWriteFrozenError" in doc, owner


class TestTheOverrideFreeze:
    @pytest.mark.asyncio
    async def test_creating_an_override_is_refused_as_the_typed_error(
        self, client: AxonFlow, httpx_mock
    ) -> None:
        httpx_mock.add_response(
            method="POST", url=f"{STATIC}/pol_1/override", status_code=409, json=FROZEN
        )
        with pytest.raises(LegacyPolicyWriteFrozenError) as caught:
            await client.create_policy_override(
                "pol_1",
                CreatePolicyOverrideRequest(
                    action_override=OverrideAction.WARN, override_reason="migration"
                ),
            )
        assert caught.value.message == FROZEN["error"]["message"]

    @pytest.mark.asyncio
    async def test_deleting_an_override_is_refused_as_the_typed_error(
        self, client: AxonFlow, httpx_mock
    ) -> None:
        httpx_mock.add_response(
            method="DELETE", url=f"{STATIC}/pol_1/override", status_code=409, json=FROZEN
        )
        with pytest.raises(LegacyPolicyWriteFrozenError):
            await client.delete_policy_override("pol_1")


SIMULATION = f"{BASE}/api/v1/policies"
# policypath.StampDeprecation at 857455033, and from the v11.0.0 tag, when release
# prep sets DeprecatedSince and Deprecation carries its RFC 9745 date.
STAMPED_TODAY = STAMP
STAMPED_AT_THE_TAG = {**STAMP, "Deprecation": "@1788220800"}


class TestTheSimulationFamily:
    """The routes are Evaluation+ only; the stamps are the platform's own (policypath.go)."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "stamp", [STAMPED_TODAY, STAMPED_AT_THE_TAG], ids=["today", "at-the-tag"]
    )
    async def test_each_simulation_route_is_reported_once(
        self, stamp: dict[str, str], client: AxonFlow, httpx_mock
    ) -> None:
        for _ in range(2):
            httpx_mock.add_response(url=f"{SIMULATION}/simulate", json={}, headers=stamp)
            httpx_mock.add_response(url=f"{SIMULATION}/conflicts", json={}, headers=stamp)
            # An impact report on a policy a fresh organization cannot have answers
            # 500 at 857455033 (enterprise #4223); the stamp rides the refusal.
            httpx_mock.add_response(
                url=f"{SIMULATION}/impact-report",
                status_code=500,
                json={
                    "code": "INTERNAL_ERROR",
                    "error": "INTERNAL_ERROR",
                    "message": "Failed to evaluate input 0",
                },
                headers=stamp,
            )
        with reports() as found:
            for _ in range(2):
                await client.simulate_policies("hello")
                await client.detect_policy_conflicts()
                with pytest.raises(AxonFlowError):
                    await client.get_policy_impact_report("pol_missing", [{"query": "hello"}])
        assert [(w.route, w.removed_in, w.successor, w.deprecation) for w in found] == [
            (
                f"POST /api/v1/policies/{route}",
                "v11.1",
                "/api/v1/typed-policies",
                stamp.get("Deprecation"),
            )
            for route in ("simulate", "conflicts", "impact-report")
        ]
