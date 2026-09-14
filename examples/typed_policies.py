"""Example: typed policy authoring against a running AxonFlow v11.0.0 platform.

A v11.0.0 platform authors policy as a typed document: validated, published as
a signed artifact pinned by its digest, and promoted to active. This example
reads what the deployment may author, validates a document and prints every
finding, and shows the document in force. It publishes and activates only when
``AXONFLOW_TYPED_POLICY_PUBLISH=1``, because that changes the organization's
active policy. Before it activates, it prints the publication's report of the
organization template's controls the document omits: activating a document
that omits them removes them for the organization. A publication or activation
it was asked for and refused fails the run.

Run ``examples/pep_handshake.py`` first. After a document with an
organization-scope constraint is activated, a decide that does not supply the
attribute the constraint conditions on is denied fail-closed with reasons
["unknown_constraint"]; supply the attribute or run this example on a fresh
stack. From v11.0.0 the deny's first reason is that code, followed by one
naming each constraint it could not evaluate and the attribute it needed
(getaxonflow/axonflow-enterprise#4247). The default document is such a
document.

Env vars:

* ``AXONFLOW_AGENT_URL``             (default: http://localhost:8080)
* ``AXONFLOW_CLIENT_ID``             (default: community)
* ``AXONFLOW_CLIENT_SECRET``         (default: empty)
* ``AXONFLOW_TYPED_POLICY_BODY``     a JSON file holding ``{"document": ..., "fixtures": [...]}``
  (default: the repository's ``tests/fixtures/typed_policy_publish_body.json``)
* ``AXONFLOW_TYPED_POLICY_PUBLISH``  set to ``1`` to also publish and activate the document

The default body is found from this file's own location, so it runs from any
directory::

    python examples/typed_policies.py

Exits non-zero if a step fails, so it is usable as a smoke test.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from axonflow import AxonFlow, TypedPolicyPublication, TypedPolicyRefusal
from axonflow.exceptions import AxonFlowError

#: The document and fixtures used when ``AXONFLOW_TYPED_POLICY_BODY`` is unset:
#: the body the platform's own route test proves publishable.
DEFAULT_BODY = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "typed_policy_publish_body.json"
)


def load_body() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The document and fixtures to author: ``AXONFLOW_TYPED_POLICY_BODY``'s, or the default."""
    path = Path(os.environ.get("AXONFLOW_TYPED_POLICY_BODY") or DEFAULT_BODY)
    body = json.loads(path.read_text(encoding="utf-8"))
    return body["document"], body["fixtures"]


def print_refusal(what: str, refusal: TypedPolicyRefusal) -> None:
    """Print a refusal: the platform's reason and any findings that refused it."""
    print(f"{what}: HTTP {refusal.status} {refusal.reason}: {refusal}")
    for f in refusal.findings:
        print(f"  {f.severity} {f.code} {f.policy_id or ''}")


def print_template_omissions(published: TypedPolicyPublication) -> None:
    """Print which of the organization template's controls the document omits."""
    report = published.template_omissions
    if report is not None:
        omitted = ", ".join(report.omitted)
        print(
            f"template omissions: {len(report.omitted)} of {report.of} template controls: {omitted}"
        )
    elif published.template_omissions_unavailable is not None:
        print(f"template omissions: unavailable: {published.template_omissions_unavailable}")
    else:
        print("template omissions: none")


async def main() -> int:
    document, fixtures = load_body()

    failures = 0

    async def step(name: str, fn: Callable[[], Awaitable[None]]) -> None:
        nonlocal failures
        print(f"\n=== {name} ===")
        try:
            await fn()
            print("ok")
        except AxonFlowError as e:
            print(f"FAILED: {e}")
            failures += 1

    async with AxonFlow(
        endpoint=os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080"),
        client_id=os.environ.get("AXONFLOW_CLIENT_ID", "community"),
        client_secret=os.environ.get("AXONFLOW_CLIENT_SECRET", ""),
    ) as client:
        # What this deployment may author: consult it before publishing, rather
        # than learning the edition's boundary from a refusal.
        async def edition() -> None:
            e = await client.typed_policies.edition()
            print(f"root={e.root} max_documents={e.max_documents} persistence={e.persistence}")
            if e.constructs:
                families = ", ".join(e.constructs.obligation_families)
                print(f"edition={e.constructs.edition} obligation families={families}")

        # Validation reports every finding. A document with findings is still a
        # successful call: read success and findings rather than expecting a raise.
        async def validate() -> None:
            validation = await client.typed_policies.validate(document, fixtures)
            print(f"success={validation.success}")
            for f in validation.findings:
                print(f"  {f.severity} {f.code} {f.policy_id or ''}: {f.detail or f.summary or ''}")

        async def publish_and_activate() -> None:
            try:
                published = await client.typed_policies.publish(document, fixtures)
            except TypedPolicyRefusal as refusal:
                # A refusal carries the platform's reason and, for a refused
                # document, the findings that refused it. Publishing was asked
                # for, so a refusal fails the run.
                print_refusal("refused", refusal)
                raise
            print(f"published {published.digest} (version {published.version})")
            # Activating a document that omits the organization template's
            # controls removes them for the organization, so the report comes first.
            print_template_omissions(published)
            try:
                await client.typed_policies.activate(
                    published.digest, reason="examples/typed_policies"
                )
            except TypedPolicyRefusal as refusal:
                # Activation promotes: a digest whose version does not advance
                # past the active one is refused. Activating was asked for, so a
                # refusal fails the run.
                print_refusal("activation refused", refusal)
                raise
            print("activated")

        # The document in force comes back as the exact text that was signed.
        async def in_force() -> None:
            active = await client.typed_policies.active()
            if active is None:
                print("nothing is active")
                return
            document_id = active.document.get("metadata", {}).get("document_id")
            print(f"{len(active.source)} signed characters; document_id={document_id}")

        await step("what this deployment may author", edition)
        await step("validate the document", validate)
        if os.environ.get("AXONFLOW_TYPED_POLICY_PUBLISH") == "1":
            await step("publish and activate", publish_and_activate)
        await step("the document in force", in_force)

    if failures:
        print(f"\n{failures} step(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
