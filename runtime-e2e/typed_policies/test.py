"""Real-stack proof of typed policy authoring through the SDK.

Drives ``client.typed_policies`` against a real agent and orchestrator and
asserts, on a fresh stack:

1. Nothing is active yet: ``active()`` answers ``None`` from the platform's 404.
2. ``edition()`` reports the deployment's boundary, and ``system()`` the shipped
   controls with their digest.
3. The document the platform's own route test proves publishable validates
   clean, publishes to a digest, and activates.
4. ``active()`` returns that document as the exact signed source, with the
   AUTHOR overwritten by the platform: the document deliberately names
   ``someone-else``, and the platform signs the caller the agent resolved.
5. Activating the same digest again is refused as a typed 409
   (``activation_refused``): activation promotes, and the version does not
   advance.
6. Publishing with no fixtures is refused as a typed 422
   (``publication_refused``) whose message names the missing fixtures.
7. A document naming an action the registry does not hold validates with the
   platform's rejecting finding (``ACTION_NOT_REGISTERED``), and publishing it
   is refused as a typed 422 (``document_refused``) carrying that finding.

The document is ``tests/fixtures/typed_policy_publish_body.json``, marshalled by
the platform's own types, with its ``document_id`` made unique per run.

Run against a FRESH stack (nothing active on the organization)::

    AXONFLOW_AGENT_URL=http://localhost:8080 \\
    AXONFLOW_CLIENT_ID=runtime-e2e AXONFLOW_CLIENT_SECRET=runtime-e2e-secret \\
    python runtime-e2e/typed_policies/test.py
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from axonflow import AxonFlow, AxonFlowError, TypedPolicyRefusal  # noqa: E402

ENDPOINT = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")
CLIENT_ID = os.environ.get("AXONFLOW_CLIENT_ID", "runtime-e2e")
CLIENT_SECRET = os.environ.get("AXONFLOW_CLIENT_SECRET", "runtime-e2e-secret")

BODY = json.loads((ROOT / "tests" / "fixtures" / "typed_policy_publish_body.json").read_text())

FAILURES: list[str] = []


def check(ok: bool, description: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'}: {description}")
    if not ok:
        FAILURES.append(description)


async def run(client: AxonFlow) -> None:
    typed = client.typed_policies
    document = copy.deepcopy(BODY["document"])
    document_id = f"sdk-python-e2e-{uuid.uuid4().hex[:12]}"
    document["metadata"]["document_id"] = document_id
    fixtures = BODY["fixtures"]

    print("== nothing active yet")
    check(await typed.active() is None, "active() is None before any activation")

    print("== edition and system")
    edition = await typed.edition()
    print(
        f"  edition: catalog={edition.catalog} root={edition.root} "
        f"max_documents={edition.max_documents} persistence={edition.persistence} "
        f"constructs.edition={edition.constructs.edition if edition.constructs else None}"
    )
    check(edition.success and edition.root == "organization", "edition() reports the root")
    check(edition.constructs is not None, "edition() reports the construct boundary")
    system = await typed.system()
    print(
        f"  system: root={system.root} version={system.version} digest={system.digest} "
        f"controls={len(system.controls)} assurance_counts={system.assurance_counts}"
    )
    check(bool(system.digest) and len(system.controls) > 0, "system() returns the shipped corpus")

    print("== validate, publish, activate")
    validation = await typed.validate(document, fixtures)
    print(
        f"  validate: success={validation.success} findings={[f.code for f in validation.findings]}"
    )
    check(validation.success, "the document validates clean")
    published = await typed.publish(document, fixtures)
    print(f"  publish: digest={published.digest} version={published.version}")
    check(bool(published.digest), "publish() returns the artifact digest")
    activation = await typed.activate(published.digest, reason="sdk-python runtime proof")
    print(f"  activate: success={activation.success} activation={activation.activation}")
    check(activation.success, "activate() promotes the digest")

    print("== the document in force")
    active = await typed.active()
    check(active is not None, "active() returns the document in force")
    if active is not None:
        metadata = active.document.get("metadata", {})
        author = metadata.get("author", {})
        print(f"  active: document_id={metadata.get('document_id')} author={author}")
        check(metadata.get("document_id") == document_id, "active() is the document just activated")
        check(
            json.loads(active.source) == active.document,
            "active().source is the signed source the document parses from",
        )
        check(
            author.get("local") != "someone-else",
            "the platform signed the caller as author, not the name in the request",
        )

    print("== typed refusals")
    try:
        await typed.activate(published.digest)
        check(False, "re-activating the active digest is refused")
    except TypedPolicyRefusal as refusal:
        print(
            f"  re-activate: status={refusal.status} reason={refusal.reason} error={refusal.message}"
        )
        check(
            refusal.status == 409 and refusal.reason == "activation_refused",  # noqa: PLR2004
            "re-activating the active digest is a typed 409 activation_refused",
        )
    try:
        await typed.publish(document, [])
        check(False, "publishing with no fixtures is refused")
    except TypedPolicyRefusal as refusal:
        print(
            f"  publish without fixtures: status={refusal.status} reason={refusal.reason} "
            f"error={refusal.message}"
        )
        check(
            refusal.status == 422  # noqa: PLR2004
            and refusal.reason == "publication_refused"
            and "declares no fixtures" in refusal.message,
            "publishing with no fixtures is a typed 422 publication_refused naming the cause",
        )

    print("== a document the save-time checks reject")
    unregistered = copy.deepcopy(document)
    unregistered["metadata"]["document_id"] = f"{document_id}-unregistered"
    unregistered["policy"]["policies"][0]["actions"]["actions"][0]["local"] = "tool.not_registered"
    expected = ("ACTION_NOT_REGISTERED", "reject", "grant.refund")
    rejected = await typed.validate(unregistered, fixtures)
    found = [(f.code, f.severity, f.policy_id) for f in rejected.findings]
    print(f"  validate: success={rejected.success} findings={found}")
    check(
        not rejected.success and expected in found,
        "validate() answers an unregistered action with the platform's rejecting finding",
    )
    try:
        await typed.publish(unregistered, fixtures)
        check(False, "publishing a document the save-time checks reject is refused")
    except TypedPolicyRefusal as refusal:
        found = [(f.code, f.severity, f.policy_id) for f in refusal.findings]
        print(f"  publish: status={refusal.status} reason={refusal.reason} findings={found}")
        check(
            refusal.status == 422  # noqa: PLR2004
            and refusal.reason == "document_refused"
            and expected in found,
            "publishing it is a typed 422 document_refused carrying that finding",
        )


async def main() -> int:
    print(f"agent: {ENDPOINT}")
    async with AxonFlow(
        endpoint=ENDPOINT, client_id=CLIENT_ID, client_secret=CLIENT_SECRET
    ) as client:
        try:
            await run(client)
        except AxonFlowError as e:
            check(False, f"the run raised {type(e).__name__}: {e}")
    if FAILURES:
        print(f"\nFAIL: typed_policies ({len(FAILURES)} assertion(s))")
        return 1
    print("\nPASS: typed_policies")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
