"""Typed policy authoring through the agent: ``client.typed_policies``.

Each operation is asserted on the wire (the route, the method, the exact body)
and on its answer, and so is every refusal the platform documents. The publish
body is the one the platform's own route test proves publishable, marshalled by
the platform's own types: tests/fixtures/typed_policy_publish_body.json.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from axonflow import (
    AuthenticationError,
    AxonFlow,
    AxonFlowError,
    TypedPoliciesNamespace,
    TypedPolicyRefusal,
)

BASE = "http://localhost:8080"
ROUTE = f"{BASE}/api/v1/typed-policies"
BODY = json.loads(
    (Path(__file__).parent / "fixtures" / "typed_policy_publish_body.json").read_text()
)
DOCUMENT, FIXTURES = BODY["document"], BODY["fixtures"]
DIGEST = "sha256:5d41402abc4b2a76b9719d911017c592"


def _client() -> AxonFlow:
    return AxonFlow(endpoint=BASE, client_id="test-client", client_secret="test-secret")


def _sent(httpx_mock) -> tuple[str, str, object]:
    request = httpx_mock.get_requests()[-1]
    body = json.loads(request.content) if request.content else None
    return request.method, str(request.url), body


class TestTheOperations:
    @pytest.mark.asyncio
    async def test_edition(self, httpx_mock):
        httpx_mock.add_response(
            url=f"{ROUTE}/edition",
            json={
                "success": True,
                "catalog": "deployment",
                "root": "organization",
                "max_documents": 20,
                "constructs": {
                    "edition": "community",
                    "obligation_families": ["disclosure"],
                    "attribute_namespaces": ["args"],
                    "group_scope": False,
                    "separation_of_duties": False,
                    "tier_established": True,
                    "reserved": ["step_up"],
                },
                "persistence": "database",
                "signing_key_custody": "process",
            },
        )
        edition = await _client().typed_policies.edition()
        assert _sent(httpx_mock)[:2] == ("GET", f"{ROUTE}/edition")
        assert edition.max_documents == 20
        assert edition.persistence == "database"
        assert edition.constructs is not None
        assert edition.constructs.edition == "community"
        assert edition.constructs.group_scope is False
        assert edition.constructs.reserved == ["step_up"]

    @pytest.mark.asyncio
    async def test_validate_sends_the_document_and_fixtures_and_returns_every_finding(
        self, httpx_mock
    ):
        httpx_mock.add_response(
            url=f"{ROUTE}/validate",
            json={
                "success": False,
                "findings": [
                    {
                        "code": "ACTION_NOT_REGISTERED",
                        "severity": "reject",
                        "policy_id": "grant.refund",
                        "summary": (
                            "The action selector names an action "
                            "that is not in the action registry."
                        ),
                        "detail": "Action::tool.cal",
                    }
                ],
            },
        )
        validation = await _client().typed_policies.validate(DOCUMENT, FIXTURES)
        assert _sent(httpx_mock) == ("POST", f"{ROUTE}/validate", BODY)
        # A refused document is a successful validation: an answer, not an error.
        assert validation.success is False
        assert validation.findings[0].code == "ACTION_NOT_REGISTERED"
        assert validation.findings[0].policy_id == "grant.refund"

    @pytest.mark.asyncio
    async def test_validate_without_fixtures_sends_none(self, httpx_mock):
        httpx_mock.add_response(url=f"{ROUTE}/validate", json={"success": True, "findings": []})
        await _client().typed_policies.validate(DOCUMENT)
        assert _sent(httpx_mock)[2] == {"document": DOCUMENT}

    @pytest.mark.asyncio
    async def test_publish(self, httpx_mock):
        httpx_mock.add_response(
            url=f"{ROUTE}/publish",
            json={"success": True, "digest": DIGEST, "version": 1, "findings": []},
        )
        published = await _client().typed_policies.publish(DOCUMENT, FIXTURES)
        assert _sent(httpx_mock) == ("POST", f"{ROUTE}/publish", BODY)
        assert (published.digest, published.version) == (DIGEST, 1)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("reason", "sent"),
        [("baseline", {"digest": DIGEST, "reason": "baseline"}), (None, {"digest": DIGEST})],
        ids=["with-a-reason", "without"],
    )
    async def test_activate(self, httpx_mock, reason, sent):
        httpx_mock.add_response(
            url=f"{ROUTE}/activate",
            json={"success": True, "activation": {"digest": DIGEST, "actor": "admin"}},
        )
        activation = await _client().typed_policies.activate(DIGEST, reason=reason)
        assert _sent(httpx_mock) == ("POST", f"{ROUTE}/activate", sent)
        assert activation.activation["digest"] == DIGEST

    @pytest.mark.asyncio
    async def test_active_keeps_the_exact_bytes_that_were_signed(self, httpx_mock):
        # Deliberately not the way json.dumps would render it: the source is the
        # signed bytes, and re-serialising would change what a digest covers.
        signed = b'{"api_version": "v1",  "metadata":{"document_id":"org-baseline"}}'
        httpx_mock.add_response(url=f"{ROUTE}/active", content=signed)
        active = await _client().typed_policies.active()
        assert active is not None
        assert active.source.encode("utf-8") == signed
        assert active.document["metadata"]["document_id"] == "org-baseline"

    @pytest.mark.asyncio
    async def test_nothing_active_is_none(self, httpx_mock):
        httpx_mock.add_response(
            url=f"{ROUTE}/active",
            status_code=404,
            json={"success": False, "reason": "nothing_active", "error": "nothing is active"},
        )
        assert await _client().typed_policies.active() is None

    @pytest.mark.asyncio
    async def test_system(self, httpx_mock):
        httpx_mock.add_response(
            url=f"{ROUTE}/system",
            json={
                "success": True,
                "system": {
                    "root": "system",
                    "version": 3,
                    "digest": "sha256:system",
                    "authority": "shipped_corpus",
                    "controls": [
                        {
                            "id": "sys.pii.ssn",
                            "authority": "constraint",
                            "assurance": "enforcement",
                            "mandatory": True,
                            "description": "SSN",
                            "obligations": [{"type": "field_redact"}],
                        }
                    ],
                    "assurance_counts": {"enforcement": 1},
                    "document": {"api_version": "v1"},
                },
            },
        )
        system = await _client().typed_policies.system()
        assert (system.root, system.version, system.digest) == ("system", 3, "sha256:system")
        assert system.controls[0].assurance == "enforcement"
        assert system.assurance_counts == {"enforcement": 1}


def _refusal(status, reason, **extra):
    return {"success": False, "reason": reason, "error": f"refused: {reason}", **extra}


APPROVER = {
    "code": "APPROVER_IS_AUTHOR",
    "severity": "reject",
    "summary": "the author may not approve their own publication",
}

REFUSALS = [
    pytest.param(
        "publish",
        422,
        _refusal(422, "publication_refused", findings=[APPROVER]),
        {},
        id="publish-422-separation-of-duties",
    ),
    pytest.param(
        "publish",
        422,
        _refusal(422, "document_refused", findings=[]),
        {},
        id="publish-422-document",
    ),
    pytest.param(
        "publish",
        402,
        _refusal(402, "tier_limit", code="ERR_TIER_LIMIT_ORG_ROOT_POLICY"),
        {},
        id="publish-402-tier-limit",
    ),
    pytest.param(
        "publish",
        402,
        _refusal(402, "tier_limit", code="ERR_TIER_LIMIT_ORG_ROOT_POLICY"),
        {"Retry-After": "30"},
        id="publish-402-ledger-outage",
    ),
    pytest.param("publish", 429, _refusal(429, "artifact_cap"), {}, id="publish-429-artifact-cap"),
    pytest.param("publish", 400, _refusal(400, "document_id_required"), {}, id="publish-400"),
    pytest.param("activate", 409, _refusal(409, "activation_refused"), {}, id="activate-409"),
    pytest.param("validate", 503, _refusal(503, "catalog_not_configured"), {}, id="validate-503"),
    pytest.param("edition", 404, _refusal(404, "no_such_endpoint"), {}, id="edition-404"),
]


async def _call(namespace: TypedPoliciesNamespace, operation: str):
    if operation == "publish":
        return await namespace.publish(DOCUMENT, FIXTURES)
    if operation == "validate":
        return await namespace.validate(DOCUMENT, FIXTURES)
    if operation == "activate":
        return await namespace.activate(DIGEST)
    return await getattr(namespace, operation)()


class TestTheRefusals:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("operation", "status", "body", "headers"), REFUSALS)
    async def test_every_documented_refusal_is_typed(
        self, httpx_mock, operation, status, body, headers
    ):
        httpx_mock.add_response(
            url=f"{ROUTE}/{operation}", status_code=status, json=body, headers=headers
        )
        with pytest.raises(TypedPolicyRefusal) as caught:
            await _call(_client().typed_policies, operation)
        refusal = caught.value
        assert (refusal.status, refusal.reason, refusal.code) == (
            status,
            body["reason"],
            body.get("code"),
        )
        assert refusal.message == body["error"]
        assert [f.code for f in refusal.findings] == [f["code"] for f in body.get("findings", [])]
        assert refusal.retry_after == (int(headers["Retry-After"]) if headers else None)

    @pytest.mark.asyncio
    async def test_a_401_is_an_authentication_error_carrying_the_platform_text(self, httpx_mock):
        httpx_mock.add_response(
            url=f"{ROUTE}/system",
            status_code=401,
            json=_refusal(401, "org_not_stamped"),
        )
        with pytest.raises(AuthenticationError, match="refused: org_not_stamped"):
            await _client().typed_policies.system()

    @pytest.mark.asyncio
    async def test_a_refusal_without_a_json_body_still_names_its_status(self, httpx_mock):
        httpx_mock.add_response(url=f"{ROUTE}/edition", status_code=502, content=b"bad gateway")
        with pytest.raises(TypedPolicyRefusal) as caught:
            await _client().typed_policies.edition()
        assert (caught.value.status, caught.value.reason) == (502, None)
        assert caught.value.message == "HTTP 502 from /edition"

    @pytest.mark.asyncio
    async def test_a_success_whose_body_is_not_an_object_is_an_error(self, httpx_mock):
        httpx_mock.add_response(url=f"{ROUTE}/edition", json=["not", "an", "object"])
        with pytest.raises(AxonFlowError, match="not an object"):
            await _client().typed_policies.edition()


# What the platform sends for a collection it holds as a nil Go slice or map:
# JSON null, not [] or {}. A real stack's clean validation answered
# "findings": null; these are every such field the platform declares without
# omitempty.
NULL_COLLECTIONS = [
    pytest.param(
        "validate",
        {"success": True, "findings": None},
        lambda r: r.findings,
        [],
        id="validate-findings",
    ),
    pytest.param(
        "publish",
        {"success": True, "digest": DIGEST, "version": 1, "findings": None},
        lambda r: r.findings,
        [],
        id="publish-findings",
    ),
    pytest.param(
        "edition",
        {
            "success": True,
            "constructs": {
                "edition": "community",
                "obligation_families": None,
                "attribute_namespaces": None,
            },
        },
        lambda r: (r.constructs.obligation_families, r.constructs.attribute_namespaces),
        ([], []),
        id="edition-constructs",
    ),
    pytest.param(
        "system",
        {
            "success": True,
            "system": {
                "root": "system",
                "controls": None,
                "assurance_counts": None,
                "document": None,
            },
        },
        lambda r: (r.controls, r.assurance_counts, r.document),
        ([], {}, {}),
        id="system-corpus",
    ),
]


class TestNullCollections:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("operation", "body", "read", "expected"), NULL_COLLECTIONS)
    async def test_a_nil_go_collection_reads_as_empty(
        self, httpx_mock, operation, body, read, expected
    ):
        httpx_mock.add_response(url=f"{ROUTE}/{operation}", json=body)
        assert read(await _call(_client().typed_policies, operation)) == expected


class TestTheClients:
    def test_the_sync_client(self, httpx_mock):
        httpx_mock.add_response(
            url=f"{ROUTE}/publish",
            json={"success": True, "digest": DIGEST, "version": 1, "findings": []},
        )
        client = AxonFlow.sync(BASE, "test-client", "test-secret")
        assert client.typed_policies.publish(DOCUMENT, FIXTURES).digest == DIGEST
        assert _sent(httpx_mock) == ("POST", f"{ROUTE}/publish", BODY)

    def test_the_namespace_is_built_once(self):
        client = _client()
        assert client.typed_policies is client.typed_policies

    def test_a_derived_client_gets_its_own_namespace_bound_to_itself(self):
        # A namespace copied from the parent would send through the PARENT's
        # transport, with the parent's identity.
        parent = _client()
        parent_namespace = parent.typed_policies
        derived = parent.as_user("user-token")
        assert derived.typed_policies is not parent_namespace
        assert derived.typed_policies._send.__self__ is derived
