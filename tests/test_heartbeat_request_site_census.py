"""Every outbound request path must pass the heartbeat trigger.

The heartbeat fires on the client's FIRST OUTBOUND REQUEST
(axonflow-enterprise#3682), which makes "which code paths count as a request"
a correctness question rather than a detail.

A GATE PLACED AT SOME CALLERS IS NOT A GATE ON THE OTHERS. Before the trigger
moved, the constructor pinged, so a method that issued a request without
calling ``_pre_request_hook`` cost nothing. After the move it costs everything:
a process whose only outbound call is such a method never pings at all. The Go
SDK had exactly one of these (``StreamExecutionStatus``, which builds its own
client because SSE needs no timeout); this SDK had TEN, including
``stream_execution_status``, ``mcp_query`` and the whole customer-portal plane.

Fixing the ten and leaving the class open would only defer the next one, so
this test is the census: every raw client call must sit in a function that also
calls the hook, and a new one fails here until its author does something about
it.

WHAT THIS GUARD CAN AND CANNOT SEE. It is a SOURCE SCAN, so it is only as wide
as the syntax it matches: ``self._http_client.<verb>(``,
``self._map_http_client.<verb>(`` and ``stream_client.<verb>(``, which is how
every outbound request in this module is issued today. It would NOT see a
request issued through some future helper that hides the call behind another
name, and it is not a substitute for thinking about the trigger when adding
one. Said plainly rather than left for someone to discover.
"""

from __future__ import annotations

import re
from pathlib import Path

CLIENT = Path(__file__).parent.parent / "axonflow" / "client.py"

#: Modules deliberately OUTSIDE this census, and why.
#:
#: ``axonflow/community.py`` issues one ``httpx.post`` to ``/api/v1/register``
#: from a MODULE-LEVEL function, not a client method: registration is how a
#: tenant is created, so there is no client and no configured endpoint for a
#: heartbeat to describe. Pinging there would report a deployment that does not
#: exist yet. The Go SDK exempts its ``register.go`` for the same reason.
#:
#: Named here rather than left implicit, so "the census only reads client.py"
#: is a decision on the record instead of an accident of scope.
OUT_OF_SCOPE_MODULES = {
    "axonflow/community.py": (
        "module-level tenant registration — no client, no endpoint, nothing for "
        "a heartbeat to describe"
    ),
}

# How an outbound request is spelled in this module.
#
# THE RECEIVER IS PART OF THE PATTERN, and the width was chosen against two
# failure directions rather than one. Too narrow and a bypass hides: the first
# version named only the three known client attributes, so `await httpx.get(...)`
# or a side `httpx.AsyncClient()` inside a hook-less method stayed green. Too
# broad and it cries wolf: a bare `\.get\(` matches `response.headers.get(...)`
# and `params.get(...)`, and a guard that produces false positives trains the
# next reader to add a bogus exemption, after which the census means nothing.
#
# So: any receiver whose name ends in `client`, plus the `httpx` module itself.
#
# DECLARED LIMIT: a client held in a variable NOT named `*client` — say
# `hc.get(u)` — is not matched. That is the price of no false positives, and it
# is stated rather than left to be discovered.
_CALL = re.compile(
    r"(?:\b\w*client|\bhttpx)"
    r"\.(?:get|post|put|patch|delete|request|stream|send)\(",
    re.IGNORECASE,
)

#: Constructing an httpx client is NOT a request, so it is a SEPARATE check.
#:
#: Folding it into ``_CALL`` was wrong and the fixture said so immediately: it
#: flagged the three sites where this SDK legitimately builds its own pooled
#: clients. But a client built on the SIDE, inside a method that never calls the
#: hook, is the specific smell worth watching for — it is how a request path
#: comes to exist outside the wrapper. So the construction sites are ENUMERATED,
#: and a new one has to be justified rather than silently added.
_CLIENT_CONSTRUCTION = re.compile(r"\bhttpx\.(?:Async)?Client\(")

#: Functions allowed to construct an httpx client, and why.
CLIENT_CONSTRUCTION_SITES: dict[str, str] = {
    "_stamp_identity": (
        "builds the client's own pooled AsyncClients at construction — this IS "
        "the client, not a side client, and every request through it goes "
        "through _fetch/_pre_request_hook"
    ),
    "_stamp_derived": (
        "borrows the parent's transport for an as_user()-derived client so the "
        "pool is shared; same pooled client, different identity hook"
    ),
    "stream_execution_status": (
        "SSE needs a client with no read timeout, which the pooled client "
        "cannot express — the same exception the Go SDK's StreamExecutionStatus "
        "carries. It is NOT exempt from the heartbeat trigger: it calls "
        "_pre_request_hook itself, and the census above enforces that. This "
        "entry records that the side client is deliberate, not that the path "
        "is unwatched."
    ),
}
_DEF = re.compile(r"^(\s*)(async )?def (\w+)\s*\(")
_CLASS = re.compile(r"^class (\w+)")

#: Functions that issue a raw request and deliberately do NOT call the hook.
#: Empty today. An entry here is a written exemption, not a shrug — it has to
#: say why the heartbeat must not fire on that path.
EXEMPT: dict[str, str] = {}


def _scan() -> tuple[dict[int, tuple[str, str]], set[str], dict[int, str]]:
    """Return {line: (class, function)} for raw calls, the set of functions
    that call the hook, and {line: function} for client constructions."""
    src = CLIENT.read_text().split("\n")
    calls: dict[int, tuple[str, str]] = {}
    constructions: dict[int, str] = {}
    hook_fns: set[str] = set()
    cls = ""
    fn = ""
    for i, line in enumerate(src, 1):
        m = _CLASS.match(line)
        if m:
            cls = m.group(1)
        m = _DEF.match(line)
        if m:
            fn = m.group(3)
        stripped = line.strip()
        if stripped.startswith("#"):
            # Prose mentioning a call is not a call. A marker string colliding
            # with the comment beside it is its own failure mode.
            continue
        if "self._pre_request_hook()" in line and not stripped.startswith("def "):
            hook_fns.add(f"{cls}.{fn}")
        if _CALL.search(line):
            calls[i] = (cls, fn)
        if _CLIENT_CONSTRUCTION.search(line):
            constructions[i] = fn
    return calls, hook_fns, constructions


def test_every_request_site_passes_the_heartbeat_trigger():
    calls, hook_fns, _constructions = _scan()

    # POSITIVE CONTROL. A scan finding nothing has stopped working — a renamed
    # attribute, a moved file, a changed spelling — and an empty result would
    # otherwise read as "no bypasses", which is the most dangerous way for a
    # source-scanning guard to fail.
    assert calls, (
        "the scan found ZERO request sites in client.py, which cannot be true. "
        "The guard has stopped matching and would report any bypass as clean."
    )
    assert hook_fns, (
        "the scan found ZERO functions calling _pre_request_hook, so every site "
        "below would be reported as a bypass. The guard has stopped matching."
    )

    bypasses: list[str] = []
    for line, (cls, fn) in sorted(calls.items()):
        qualified = f"{cls}.{fn}"
        if qualified in hook_fns or fn in EXEMPT:
            continue
        bypasses.append(f"  client.py:{line}  {qualified}")

    assert not bypasses, (
        "these functions issue an outbound request without calling "
        "_pre_request_hook():\n"
        + "\n".join(bypasses)
        + "\n\nThe telemetry heartbeat fires on the client's FIRST OUTBOUND REQUEST, so a "
        "request path that skips the hook is a path on which the SDK never pings. A "
        "process whose only outbound call is one of these would be invisible.\n\n"
        "Either call self._pre_request_hook() at the top of the function, or add it to "
        "EXEMPT above with a reason the heartbeat must not fire there."
    )


def test_no_side_http_client_is_built_outside_the_known_sites():
    """A client built on the SIDE is how a request path comes to exist outside
    the wrapper — and outside the heartbeat trigger with it.

    Constructing a client is not itself a request, so this is a separate check
    from the census above rather than a wider needle. Folding the two together
    flagged the three sites where the SDK legitimately builds its own pooled
    clients, which is a false positive, and false positives get silenced with
    bogus exemptions.
    """
    _calls, _hooks, constructions = _scan()

    # POSITIVE CONTROL: the SDK really does construct clients, so an empty
    # result means the detector stopped matching.
    assert constructions, (
        "the scan found ZERO httpx client constructions in client.py, which "
        "cannot be true. The detector has stopped matching."
    )

    unexpected = [
        f"  client.py:{line}  {fn}"
        for line, fn in sorted(constructions.items())
        if fn not in CLIENT_CONSTRUCTION_SITES
    ]
    assert not unexpected, (
        "these functions construct an httpx client outside the known sites:\n"
        + "\n".join(unexpected)
        + "\n\nA client built on the side can issue requests that never reach "
        "_pre_request_hook, so the SDK would never ping for a process that only "
        "uses that path. Route it through the pooled client, or add it to "
        "CLIENT_CONSTRUCTION_SITES with a reason."
    )


def test_the_out_of_scope_modules_still_look_the_way_this_census_assumes():
    """``community.py`` is excluded on the grounds that its request is
    module-level and client-free. If that stops being true the exclusion is
    stale, so the premise is asserted rather than trusted.
    """
    community = Path(__file__).parent.parent / "axonflow" / "community.py"
    src = community.read_text()
    assert "axonflow/community.py" in OUT_OF_SCOPE_MODULES
    # The premise: no class holds this request, so there is no client on which
    # a heartbeat gate could be consulted.
    assert "class " not in src.split("def ")[0] or "self._http_client" not in src, (
        "community.py now has client state; the exclusion above may be stale and "
        "the census should cover it"
    )


def test_the_needle_has_no_false_positives():
    """A guard that cries wolf is not a stricter guard.

    Ported from the Go census, where widening the needle to a bare ``.get(``
    on any receiver flagged three sites that issue no request at all
    (``resp.Header.Get(...)``). Nobody exempts those correctly under time
    pressure — they add a bogus entry to make the test pass, and then the
    census means nothing. So the RECEIVER is part of the pattern, and these
    lines must NOT match.
    """
    for not_a_request in [
        '        scope = response.headers.get("X-Axonflow-Read-Scope")',
        '        value = params.get("id")',
        "        cached = self._cache.get(key)",
        # Constructing a client is not issuing a request — it has its own check.
        "        self._http_client = httpx.AsyncClient(timeout=t)",
    ]:
        assert not _CALL.search(not_a_request), (
            f"the needle matches {not_a_request.strip()!r}, which issues no request. "
            "False positives train readers to add bogus exemptions"
        )

    # And it must still match the real spellings.
    for is_a_request in [
        "        response = await self._http_client.post(url, json=body)",
        "        response = await self._map_http_client.request(method, url)",
        '        async with stream_client.stream("GET", url) as response:',
        # The forms the first, narrower needle MISSED — this is what M1 was.
        "        response = await httpx.get(url)",
        "        response = await httpx.post(url, json=body)",
    ]:
        assert _CALL.search(is_a_request), (
            f"the needle MISSES {is_a_request.strip()!r}, an ordinary request spelling"
        )


def test_the_census_can_actually_fail():
    """The guard's own mutation gate, run in-process.

    A census that cannot fail is decorative. This feeds the checker a source
    fragment containing a raw call in a function with no hook, and asserts the
    detector flags it — so "no bypasses" above is a measurement rather than an
    artifact of a regex that matches nothing.
    """
    fragment = [
        "class AxonFlow:",
        "    async def sneaky_bypass(self):",
        "        response = await self._http_client.post('/x', json={})",
        "        return response",
    ]
    found = [i for i, line in enumerate(fragment, 1) if _CALL.search(line)]
    assert found == [3], f"the detector missed a plain raw call; matched lines {found}"

    # And it must NOT match the same text inside a comment.
    commented = "        # self._http_client.post('/x', json={})"
    assert commented.strip().startswith("#"), "fixture premise"
    assert _CALL.search(commented), (
        "fixture premise: the regex DOES match the text; the scanner skips it by "
        "checking for the comment prefix, which is what this asserts is still needed"
    )
