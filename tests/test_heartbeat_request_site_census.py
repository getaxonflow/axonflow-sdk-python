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

# How an outbound request is spelled in this module.
_CALL = re.compile(
    r"(self\._http_client|self\._map_http_client|stream_client)"
    r"\.(get|post|put|patch|delete|request|stream)\("
)
_DEF = re.compile(r"^(\s*)(async )?def (\w+)\s*\(")
_CLASS = re.compile(r"^class (\w+)")

#: Functions that issue a raw request and deliberately do NOT call the hook.
#: Empty today. An entry here is a written exemption, not a shrug — it has to
#: say why the heartbeat must not fire on that path.
EXEMPT: dict[str, str] = {}


def _scan() -> tuple[dict[int, tuple[str, str]], set[str]]:
    """Return {line: (class, function)} for raw calls, and the set of
    functions that call the hook."""
    src = CLIENT.read_text().split("\n")
    calls: dict[int, tuple[str, str]] = {}
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
    return calls, hook_fns


def test_every_request_site_passes_the_heartbeat_trigger():
    calls, hook_fns = _scan()

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
        "        client = self._registry.get(name)",
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
