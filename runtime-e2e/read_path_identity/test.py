"""Real-wire proof of the read-path per-user identity (platform #2922).

Drives the Python SDK's production code path against a LIVE enterprise agent +
orchestrator — no test doubles.

The defect this pins: every SDK carried ``user_token`` as a write-path body
field only, so ``explain_decision`` and ``list_decisions`` asked the platform
anonymously. On an enterprise stack that is not "a caller who sees everything"
— it is a caller the platform cannot scope, so explain answered not-found for
ids that plainly existed and list answered a confident empty page. Both looked
exactly like "there is nothing there".

What this driver asserts, and why each assertion cannot pass vacuously
----------------------------------------------------------------------

1. WRITE       three decisions through the SDK's own ``decide``, as dev-a.
2. LIST        as dev-a: the page must contain AT LEAST the three ids this run
               wrote, each checked BY ID — a floor derived from what this test
               itself wrote. Then DEV-B writes one and dev-a's page must NOT
               grow, which is what separates own-rows from a broken narrowing
               that returns the whole tenant.
3. EXPLAIN     as dev-a: must carry the decision id asked for AND the context
               value THIS RUN chose, so a populated-looking stub cannot satisfy
               it.
4. NO IDENTITY the same list, unscoped: must be REFUSED as a typed
               ReadScopeError with ``identity_missing``, not answered ``[]``.
5. OTHER USER  explain dev-a's decision as dev-b: must be refused, and must NOT
               report a missing identity — dev-b presented one.
6. MALFORMED / EXPIRED / WRONG-ORG tokens: each must fail CLOSED (401), never
               degrade to the tenant credential's visibility.
7. TENANT-WIDE as admin: must see dev-a's decision, which is what makes step 5
               falsifiable — a read broken for everyone also "refuses dev-b".
8. AS_USER     one client, two derived identities: each read must be scoped to
               the identity it was derived for, on a method that takes no
               per-call ``user_token``.
9. NO LEAK     the token must appear in NO captured log record and in NO
               request reaching the telemetry collector this driver hosts. A
               positive control asserts SDK output IS present first, so the
               grep is not run over an empty haystack.
10. OBSERVABLE the platform must leave a record of the unscoped read.

Two traps this driver exists to not fall into
---------------------------------------------

**Identities are minted at @example.com, never @axonflow.local.** The platform
reserves that whole domain (and @axonflow.internal) for SHARED, non-personal
identities and censuses them to nothing before scoping
(``IsSharedSyntheticIdentity``). A perfectly valid developer token minted at
@axonflow.local reads ZERO rows and reports scope ``none`` — identical to
presenting no token at all. Verified live: the same token differing only in
domain yields ``none`` vs ``own-rows``. ``generate-jwt.sh``'s own default
(``demo-user@axonflow.local``) lands in the reserved domain.

**Tokens are minted in-process, not taken from AXONFLOW_USER_TOKEN.** The
scoping assertions need several DISTINCT identities — two developers, an admin,
an expired one, one from another org. A single shared env token cannot express
them, and the setup script's token is ``role=admin``, which short-circuits to
tenant-wide and would make steps 4-8 untestable.

Usage::

    # Enterprise stack, per
    # axonflow-internal-docs/engineering/E2E_EXAMPLES_TESTING_WORKFLOW.md
    (cd /path/to/axonflow-enterprise && ./scripts/setup-e2e-testing.sh enterprise)

    set -a; source /tmp/axonflow-e2e-env.sh; set +a
    export AXONFLOW_AGENT_URL=http://localhost:8080
    python runtime-e2e/read_path_identity/test.py

Exits non-zero on the first failed assertion.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer

from axonflow import AxonFlow
from axonflow.decisions import ListDecisionsOptions
from axonflow.heartbeat import _resolve_stamp_path
from axonflow.read_identity import HEADER_USER_TOKEN, ReadScope, ReadScopeError
from axonflow.types import DecideRequest, DecisionTarget

AGENT_URL = os.environ.get("AXONFLOW_AGENT_URL", "http://localhost:8080")

# Makes every assertion specific to THIS run: the context value below is unique
# per invocation, so "the explanation is populated" becomes "the explanation
# carries the value this process chose".
RUN_TAG = f"s3-py-{time.time_ns()}"

WROTE = 3


def fail(message: str) -> None:
    print(f"FAIL: {message}", file=sys.stderr)
    sys.exit(1)


def must_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        fail(
            f"{name} must be set "
            f"(source /tmp/axonflow-e2e-env.sh after ./scripts/setup-e2e-testing.sh enterprise)"
        )
    return value  # type: ignore[return-value]


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def mint_user_token(secret: str, email: str, org_id: str, role: str, valid_for: int) -> str:
    """The per-user HS256 JWT the platform's own validator requires.

    Same claim set ``scripts/generate-jwt.sh --kind user`` emits
    (iss/sub/email/jti/org_id/exp). Minted here rather than shelled out to
    because the scoping assertions need SEVERAL distinct identities.
    """
    now = int(time.time())
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64url(
        json.dumps(
            {
                "iss": "axonflow-user-token-mint",
                "sub": email,
                "email": email,
                "user_id": email,
                "tenant_id": org_id,
                "org_id": org_id,
                "role": role,
                "region": "local",
                "jti": f"{RUN_TAG}-{uuid.uuid4()}",
                "permissions": ["query", "llm", "mcp_query"],
                "iat": now - 60,
                "nbf": now - 60,
                "exp": now + valid_for,
            },
            separators=(",", ":"),
        ).encode()
    )
    signing = f"{header}.{payload}"
    signature = _b64url(hmac.new(secret.encode(), signing.encode(), hashlib.sha256).digest())
    return f"{signing}.{signature}"


class _Collector(BaseHTTPRequestHandler):
    """A real listener standing in for the telemetry checkpoint — a THIRD PARTY.

    allow-mocks-here: this is not a stand-in for the system under test. It is
    the far end of a request the SDK sends on its own initiative, and the
    assertion is about what actually arrives there, which cannot be observed at
    all without owning that end.
    """

    seen: list[str] = []  # noqa: RUF012

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode(errors="replace")
        _Collector.seen.append(str(self.headers) + body)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, *args: object) -> None:  # noqa: ARG002
        return


def start_collector() -> str:
    server = HTTPServer(("127.0.0.1", 0), _Collector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{server.server_address[1]}/telemetry"


def park_heartbeat_stamp() -> Callable[[], None]:
    """Move the 7-day telemetry stamp aside for this run, and hand back a
    function that puts it back.

    PARKED, not deleted. The stamp lives in the developer's real user cache
    dir; deleting it would make their next unrelated SDK run fire a genuine
    ping at the production checkpoint — a test reaching outside its own sandbox
    to change the machine's state.

    And parked rather than redirected via HOME/XDG_CACHE_HOME: the path is
    resolved when the heartbeat state is constructed, which can be before this
    function could set an environment variable. The symptom of getting this
    wrong is a silently empty collector and a step 9 that asserts nothing —
    which is why step 9 fails loudly on an empty collector rather than passing.
    """
    stamp = _resolve_stamp_path()
    if stamp is None or not stamp.exists():
        return lambda: None
    parked = stamp.with_suffix(stamp.suffix + ".s3-parked")
    stamp.rename(parked)
    return lambda: parked.rename(stamp)


def client(client_id: str, secret: str, user_token: str | None) -> AxonFlow:
    return AxonFlow(
        endpoint=AGENT_URL,
        client_id=client_id,
        client_secret=secret,
        user_token=user_token or None,
        # Debug is ON deliberately, and step 9 depends on it: every log record
        # is behind this flag, so with it off the "the token does not appear in
        # the log" grep runs against a stream containing no SDK output at all —
        # a negative assertion over an empty haystack, true of every string.
        debug=True,
        timeout=30.0,
    )


async def decide_as(client_id: str, secret: str, user_token: str, index: int) -> str:
    """Drive the real /decide plane THROUGH THE SDK, as a given identity.

    Through ``client.decide`` rather than a hand-rolled POST, because a driver
    that hand-posts the write leg is testing httpx on that leg. It is also the
    evidence for the "inert on the write path" claim: /api/v1/decide is NOT
    proxied, so the X-User-Token a client stamps is genuinely ignored there and
    attribution comes from the BODY's user_token. Hence a client with no
    client-level identity.
    """
    async with client(client_id, secret, None) as writer:
        response = await writer.decide(
            DecideRequest(
                stage="llm",
                query=f"summarize support ticket {index} for run {RUN_TAG}",
                user_token=user_token,
                target=DecisionTarget(type="llm", model="gpt-4", provider="openai"),
                context={"x-session-id": RUN_TAG, "x-ai-agent": "read-path-identity-e2e"},
            )
        )
    if not response.decision_id:
        fail(f"the /decide response carried no decision_id (verdict={response.verdict!r})")
    return response.decision_id


async def wait_for_visible(reader: AxonFlow, decision_id: str) -> None:
    """Poll until the asynchronous audit write has landed.

    So a later assertion fails on SCOPE rather than on timing.
    """
    deadline = time.time() + 45
    while time.time() < deadline:
        try:
            await reader.explain_decision(decision_id)
        except Exception:  # noqa: BLE001 - any failure means "not yet"
            await asyncio.sleep(2)
        else:
            return
    fail(
        f"the decision {decision_id} never became visible to the identity that wrote it within "
        f"45s — the audit write did not land, so every read assertion below would be about "
        f"timing, not scope"
    )


def assert_platform_recorded_the_unscoped_read() -> None:
    """The platform's own account of step 4.

    A fail-closed read the platform leaves no trace of is a read nobody can
    audit; "it failed closed" is only half the property.
    """
    container = os.environ.get("AXONFLOW_ORCH_CONTAINER", "axonflow-orchestrator")
    try:
        out = subprocess.run(  # noqa: S603
            ["docker", "logs", "--tail", "500", container],  # noqa: S607
            capture_output=True,
            check=True,
            timeout=30,
        )
    except Exception as err:  # noqa: BLE001 - loudly inconclusive, never a silent pass
        fail(
            f"step 10: could not read {container}'s logs to confirm the platform recorded the "
            f"unscoped read ({err}). Set AXONFLOW_ORCH_CONTAINER, or run where the stack's logs "
            f"are reachable — an unverified observability claim is not evidence"
        )
        return
    if b"[read-scope]" not in out.stdout + out.stderr:
        fail(
            "step 10: the orchestrator logged no [read-scope] line for the unscoped read in "
            "step 4. The read failed closed but left no platform-side record of having done so"
        )
    print("step 10 PASS: the orchestrator recorded the unscoped read ([read-scope] present)")


async def main() -> None:  # noqa: C901, PLR0912, PLR0915
    client_id = must_env("AXONFLOW_CLIENT_ID")
    secret = must_env("AXONFLOW_CLIENT_SECRET")
    jwt_secret = os.environ.get("AXONFLOW_JWT_SECRET") or must_env("JWT_SECRET")

    # Capture everything the SDK logs, for step 9, for the whole run — so a
    # leak anywhere is caught, not just around the call we suspected.
    captured = logging.StreamHandler(stream=(log_stream := __import__("io").StringIO()))
    captured.setLevel(logging.DEBUG)
    logging.getLogger().addHandler(captured)
    logging.getLogger().setLevel(logging.DEBUG)

    os.environ["AXONFLOW_CHECKPOINT_URL"] = start_collector()
    os.environ["AXONFLOW_TELEMETRY"] = "on"
    restore_stamp = park_heartbeat_stamp()

    dev_a = mint_user_token(
        jwt_secret, f"dev-a-{RUN_TAG}@example.com", client_id, "developer", 3600
    )
    dev_b = mint_user_token(
        jwt_secret, f"dev-b-{RUN_TAG}@example.com", client_id, "developer", 3600
    )
    admin = mint_user_token(jwt_secret, f"admin-{RUN_TAG}@example.com", client_id, "admin", 3600)
    expired = mint_user_token(
        jwt_secret, f"old-{RUN_TAG}@example.com", client_id, "developer", -3600
    )
    wrong_org = mint_user_token(
        jwt_secret, f"out-{RUN_TAG}@example.com", f"other-org-{RUN_TAG}", "admin", 3600
    )
    malformed = "not.a.jwt"  # noqa: S105

    # ------------------------------------------------------------- 1. WRITE
    # Three, not one: the floor in step 2 is "at least the number this run
    # wrote", and a floor of one is satisfied by almost any page.
    written = [await decide_as(client_id, secret, dev_a, i) for i in range(WROTE)]
    print(f"step 1 PASS: wrote {len(written)} decisions as dev-a: {written}")

    async with client(client_id, secret, dev_a) as as_dev_a:
        await wait_for_visible(as_dev_a, written[0])

        # -------------------------------------------------------- 2. LIST
        rows = await as_dev_a.list_decisions(ListDecisionsOptions(limit=50))
        if len(rows) < WROTE:
            fail(
                f"step 2: dev-a's page has {len(rows)} rows, want at least the {WROTE} this run "
                f"wrote — a page smaller than what we just wrote cannot be a correctly-scoped read"
            )
        seen_ids = {r.decision_id for r in rows}
        for decision_id in written:
            if decision_id not in seen_ids:
                fail(f"step 2: dev-a's page does not contain {decision_id}, which dev-a wrote")

        # The floor alone cannot tell own-rows from tenant-wide: a broken
        # narrowing returning the WHOLE tenant would clear it comfortably.
        await decide_as(client_id, secret, dev_b, 99)
        await asyncio.sleep(3)
        rows_after = await as_dev_a.list_decisions(ListDecisionsOptions(limit=50))
        if len(rows_after) != len(rows):
            fail(
                f"step 2: dev-a's page grew from {len(rows)} to {len(rows_after)} rows after "
                f"DEV-B wrote one — the read is not narrowed to dev-a's own rows, so every "
                f"scoping assertion below is vacuous"
            )
        print(
            f"step 2 PASS: dev-a's page ({len(rows)} rows) is exactly its own; dev-b's write did not appear"
        )

        # ----------------------------------------------------- 3. EXPLAIN
        explanation = await as_dev_a.explain_decision(written[0])
        if explanation.decision_id != written[0]:
            fail(
                f"step 3: explanation decision_id = {explanation.decision_id!r}, want {written[0]!r}"
            )
        # A field THIS RUN controls. "Non-empty" would pass on any stub.
        if (explanation.context or {}).get("x_session_id") != RUN_TAG:
            fail(
                f"step 3: explanation context[x_session_id] = "
                f"{(explanation.context or {}).get('x_session_id')!r}, want {RUN_TAG!r} — the "
                f"explanation must carry the value this run wrote, not merely be non-empty"
            )
        print(
            f"step 3 PASS: explanation for {written[0]} is populated and carries this run's "
            f"context (x_session_id={RUN_TAG!r}, decision={explanation.decision!r})"
        )

    # ------------------------------------------------------ 4. NO IDENTITY
    async with client(client_id, secret, None) as anon:
        try:
            anon_rows = await anon.list_decisions(ListDecisionsOptions(limit=50))
        except ReadScopeError as err:
            if not err.identity_missing:
                fail(f"step 4: the unscoped list was refused with scope {err.scope!r}, want 'none'")
            print(f"step 4 PASS: the unscoped list is refused, not answered empty: {err}")
        else:
            if anon_rows:
                fail(
                    f"step 4: the unscoped list returned {len(anon_rows)} rows — this stack is "
                    f"not enforcing role-scoped reads, so every scoping assertion is vacuous. "
                    f"Check DEPLOYMENT_MODE=enterprise"
                )
            fail(
                "step 4: the unscoped list returned 0 rows and NO error. That is the defect: the "
                "read could not have returned a row, and reporting it as an empty page is a "
                "confident lie"
            )

    # ------------------------------------------------------- 5. OTHER USER
    async with client(client_id, secret, dev_b) as as_dev_b:
        try:
            await as_dev_b.explain_decision(written[0])
        except ReadScopeError as err:
            if err.identity_missing:
                fail(
                    f"step 5: dev-b's refusal reports a MISSING identity; dev-b presented one. "
                    f"Reporting the wrong cause is the confidently-wrong-diagnosis class "
                    f"(scope={err.scope!r})"
                )
            if err.scope != ReadScope.OWN_ROWS:
                fail(f"step 5: dev-b's refusal reports scope {err.scope!r}, want 'own-rows'")
            print(f"step 5 PASS: dev-b is refused dev-a's decision, with the RIGHT cause: {err}")
        else:
            fail(
                f"step 5: dev-b explained dev-a's decision {written[0]} — that is the cross-user "
                f"leak #2922 closed"
            )

    # ------------------------------- 6. MALFORMED / EXPIRED / WRONG-ORG
    # The common real-world state, not the exception. Each must fail CLOSED: a
    # rejected token must never degrade into "no token", which would hand the
    # caller the tenant credential's visibility.
    for name, bad in (("malformed", malformed), ("expired", expired), ("another org", wrong_org)):
        async with client(client_id, secret, bad) as as_bad:
            try:
                await as_bad.list_decisions(ListDecisionsOptions(limit=5))
            except Exception as err:  # noqa: BLE001 - any refusal, then checked
                if "401" not in str(err):
                    fail(
                        f"step 6 ({name}): want a 401 (the platform rejecting the token), got: {err}"
                    )
                if bad in str(err):
                    fail(f"step 6 ({name}): the error message echoes the rejected credential")
                print(f"step 6 PASS ({name}): rejected fail-closed with 401, credential not echoed")
            else:
                fail(
                    f"step 6 ({name}): a rejected per-user token produced a SUCCESSFUL read. A "
                    f"present-but-invalid identity must fail closed, never degrade to the "
                    f"unscoped path"
                )

    # ------------------------------------------------------ 7. TENANT-WIDE
    # Without this, step 5 is unfalsifiable: a read broken for everyone would
    # also "refuse dev-b".
    async with client(client_id, secret, admin) as as_admin:
        admin_explanation = await as_admin.explain_decision(written[0])
        if admin_explanation.decision_id != written[0]:
            fail(f"step 7: admin explanation decision_id = {admin_explanation.decision_id!r}")
        print(
            "step 7 PASS: an admin identity reads tenant-wide — step 5's refusal is scoping, not breakage"
        )

        # ------------------------------------------------------- 8. AS_USER
        # A derived client must be scoped to the identity it was derived FOR,
        # on a method that takes no per-call user_token.
        for_dev_b = as_admin.as_user(dev_b)
        try:
            await for_dev_b.explain_decision(written[0])
        except ReadScopeError as err:
            if err.scope != ReadScope.OWN_ROWS:
                fail(f"step 8: as_user(dev-b) reported scope {err.scope!r}, want 'own-rows'")
            print(
                "step 8 PASS: as_user(dev-b) is scoped to dev-b, not to the admin it derived from"
            )
        else:
            fail(
                "step 8: as_user(dev-b) read dev-a's decision — the derived client kept the "
                "ADMIN identity, which is the silent widening as_user exists to prevent"
            )
        if as_admin._config.user_token != admin:  # noqa: SLF001
            fail("step 8: as_user mutated the client it was derived from")

    # -------------------------------------------------------- 9. NO LEAK
    await asyncio.sleep(1)
    log_text = log_stream.getvalue()
    # POSITIVE CONTROL. Without it the greps below are a negative assertion
    # over a haystack that may be empty, which passes for every string.
    if "axonflow" not in log_text.lower():
        fail(
            f"step 9: the captured log contains no SDK output at all ({len(log_text)} chars), so "
            f"asserting the token is absent from it asserts nothing. Debug must be on."
        )
    for name, token in (("dev-a", dev_a), ("dev-b", dev_b), ("admin", admin)):
        if token in log_text:
            fail(f"step 9: the {name} token appears in the SDK's log output")
        for i, request in enumerate(_Collector.seen):
            if token in request:
                fail(f"step 9: the {name} token reached the telemetry collector in request {i}")
        if HEADER_USER_TOKEN.lower() in "".join(_Collector.seen).lower():
            fail("step 9: the identity header itself reached the telemetry collector")
    if not _Collector.seen:
        fail(
            "step 9: the telemetry collector received NOTHING, so its leak assertions asserted "
            "nothing. AXONFLOW_TELEMETRY must be on and the heartbeat must have fired."
        )
    print(
        f"step 9 PASS: no token in {len(log_text)} captured log chars (SDK output present) or in "
        f"any of {len(_Collector.seen)} telemetry requests"
    )

    restore_stamp()

    # ----------------------------------------------------- 10. OBSERVABLE
    assert_platform_recorded_the_unscoped_read()

    print("\nALL PASS: read-path identity verified end to end through the Python SDK runtime")


if __name__ == "__main__":
    asyncio.run(main())
