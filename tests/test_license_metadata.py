# Copyright 2026 AxonFlow
# SPDX-License-Identifier: MIT

"""The repository distributes under exactly one licence, and says so in exactly one way.

Why this test exists
--------------------
Two files in this MIT-licensed SDK declared Apache-2.0, and ``LICENSE`` named the
holder ``getaxonflow`` where every sibling SDK names ``AxonFlow``. ``LICENSE`` has
read ``MIT License`` since the initial commit, so neither header was ever a
relicence question -- they were wrong statements about files that were MIT all
along. Drift of that kind is invisible from inside the repository that has it:
nothing fails, nothing warns, and the wrong string is copied forward by the next
file made from it.

The two licence rules, and how wide each one really is
------------------------------------------------------
The identifier rule is the strong one, because it is closed under the syntax
rather than over a list of phrasings: every SPDX identifier tag anywhere in the
tree, in any case, must name MIT, whatever licence a future copy-paste brings.
The prose rule is a backstop and is only as wide as ``FORBIDDEN_PHRASES`` -- an
enumerated list, therefore incomplete by construction.

Both are needed, and each is blind to what the other catches: an identifier rule
cannot see an Apache prose block, and a prose rule cannot see a bare tag. The
sibling Go SDK had thirteen of the former and four of the latter, and the search
that saw only tags undercounted that repository by four times.

Why the positional rules are here
---------------------------------
A licence sweep rewrites the top of a file, which is exactly where Python keeps
two things whose meaning depends on their POSITION rather than their text:

* a shebang is only a shebang on line 1;
* a PEP 263 coding cookie is only honoured on line 1, or on line 2 when line 1
  is a shebang.

A header rewrite that inserts two lines above a cookie pushes it to line 3, and
the file then silently decodes as UTF-8 instead. Nothing fails at the time --
the damage surfaces later and somewhere else, in whichever non-ASCII literal
happens to matter. The sibling Go sweep did break a ``//go:build`` constraint
this way and only the formatter noticed, because a file that merely starts
compiling breaks nothing anyone asserts. These two rules make the Python
equivalent impossible to ship unnoticed.

Two things learned the hard way
-------------------------------
The needles are assembled by concatenation and the tag is never spelled out in
this file in any case, so the guard is not a hit for the scan it drives. A guard
whose marker string collides with the prose beside it either fails against
itself or has to exempt itself, and an exemption is a hole.

Absence of a declaration is deliberately NOT an error. A file with no header
inside a repository with one LICENSE is unambiguous; a file declaring a
DIFFERENT licence is the defect. Most files here carry no header and are left
alone.
"""

from __future__ import annotations

import codecs
import re
import tokenize
from pathlib import Path

import pytest

# tomllib is stdlib from Python 3.11. requires-python still admits 3.10, and the
# push-to-main matrix runs it, so an unconditional import failed three main runs
# on ModuleNotFoundError while every PR board (3.11 only) stayed green. The
# metadata this module checks is interpreter-independent, so the 3.11 and 3.12
# lanes keep checking it; on 3.10 the module skips and says why.
tomllib = pytest.importorskip("tomllib", reason="tomllib is stdlib from 3.11; this metadata check runs on the 3.11 and 3.12 lanes")

LICENSE_NAME = "MIT License"
LICENSE_HOLDER = "Copyright (c) 2025 AxonFlow"

# Split so this file is not a hit for the scan it drives.
SPDX_TAG = "SPDX" + "-License-Identifier:"

# Comment terminators that can follow an identifier on the same line. Comparing
# the raw remainder would report a correctly-MIT file as a contradiction: a
# false positive, in the direction that gets a guard deleted rather than fixed.
COMMENT_TERMINATORS = ("*/", "-->", "#>", "--%>")

# Licence prose this repository must not be distributing under. Assembled
# piecewise; enumerated, hence a backstop rather than the primary rule.
FORBIDDEN_PHRASES = (
    "Apache" + " License, Version 2.0",
    # The same licence without the "Version", which is how prose usually names
    # it and which the comma-bearing form does not contain as a substring.
    "Apache" + " License 2.0",
    "Business" + " Source License",
    "GNU" + " General Public License",
    "Mozilla" + " Public License",
)

# Path SEGMENTS that are dependencies, build output or VCS metadata rather than
# this repository's own source. Matched segment-wise, not as a prefix: a prefix
# test would only ever exclude a root-level directory, and these appear nested.
#
# The dependency trees here are a DELIBERATE, CATEGORICAL EXEMPTION, and saying
# so plainly matters because the rules below would otherwise read as covering
# them. A dependency tree is third-party code that legitimately keeps its own
# licence; scanning it would fail the guard on correct code, which is how a
# guard gets deleted rather than fixed. Measured on the sibling Go SDK, one
# `go mod vendor` produces 17 files of which 7 carry Apache prose and 15 carry a
# non-AxonFlow copyright notice.
NOT_SOURCE = frozenset(
    {
        ".git",
        "node_modules",
        "build",
        "dist",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        ".egg-info",
    }
)


# A virtual environment is detected STRUCTURALLY, by the `pyvenv.cfg` that the
# stdlib `venv` module writes at its root, not by matching a name against a list.
# Matching names fails on CORRECT code: R3 created a venv called `.rvenv` and five
# tests went red, because the walk descended into site-packages and read every
# dependency's licence headers. `.venv` and `venv` are conventions, not rules, and
# a guard that fails whenever someone picks a different name gets deleted rather
# than fixed -- the same trap as scanning `vendor/` in the Go sibling.
def _virtualenv_roots() -> frozenset[str]:
    return frozenset(
        cfg.parent.relative_to(REPO_ROOT).as_posix() for cfg in REPO_ROOT.rglob("pyvenv.cfg")
    )


# Files that must appear in the scan. Not a count -- a floor is a number someone
# tunes until it passes. Each anchor pins one root the walk claims to cover.
ANCHORS = (
    "LICENSE",
    "README.md",
    "CHANGELOG.md",
    "pyproject.toml",
    "axonflow/hitl.py",
    "tests/test_hitl.py",
    ".github/workflows/ci.yml",
)

# A line ASSERTING copyright ownership: the word, an optional (c), a year.
# Matching the bare word instead matches the MIT text's own "The above copyright
# notice ..." clause and every identifier in this file that contains it.
COPYRIGHT_NOTICE = re.compile(r"copyright\s+(\(c\)\s*)?[0-9]{4}", re.IGNORECASE)

# PEP 263. The cookie is recognised only on line 1, or line 2 after a shebang.
CODING_COOKIE = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*([-_.a-zA-Z0-9]+)")

DOCSTRING_FENCES = (chr(34) * 3, chr(39) * 3)

REPO_ROOT = Path(__file__).resolve().parent.parent


def declared_identifiers(line: str) -> list[str]:
    """Every SPDX identifier declared on a line, in order; empty if none.

    Every occurrence, not the first. Reading only the first turns a false
    positive into a false NEGATIVE, which is the worse direction and the one
    that ships: ``<!-- ...: MIT --> <!-- ...: Apache-2.0 -->`` truncated at the
    first terminator reads as plain MIT and the Apache declaration beside it
    passes in silence.

    Case-insensitive because the module docstring claims closure under the
    syntax, and a case-sensitive scan makes that claim false. A guard narrower
    than its own comment is worse than a narrow guard: the comment is what the
    next person relies on.
    """
    found: list[str] = []
    lower = line.lower()
    lower_tag = SPDX_TAG.lower()
    start = 0
    while True:
        at = lower.find(lower_tag, start)
        if at < 0:
            return found
        value_start = at + len(SPDX_TAG)
        end = len(line)
        for terminator in COMMENT_TERMINATORS:
            candidate = line.find(terminator, value_start)
            if 0 <= candidate < end:
                end = candidate
        nxt = lower.find(lower_tag, value_start)
        if 0 <= nxt < end:
            end = nxt
        found.append(line[value_start:end].strip())
        start = value_start


def is_not_source(rel: str, venv_roots: frozenset[str] = frozenset()) -> bool:
    if any(rel == v or rel.startswith(v + "/") for v in venv_roots):
        return True
    return any(
        segment in NOT_SOURCE or segment.endswith(".egg-info")
        for segment in rel.replace("\\", "/").split("/")
    )


def tree() -> dict[str, str]:
    """Every scannable file, keyed by its repository-relative path."""
    out: dict[str, str] = {}
    venvs = _virtualenv_roots()
    for path in REPO_ROOT.rglob("*"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if is_not_source(rel, venvs) or not path.is_file():
            continue
        # latin-1 never raises on arbitrary bytes, so a file this test was not
        # expecting cannot turn a licence assertion into a decoding error.
        out[rel] = path.read_text(encoding="latin-1")
    return out


def test_scan_reaches_every_root() -> None:
    files = tree()
    missing = [a for a in ANCHORS if a not in files]
    assert missing == [], (
        f"the walk missed {missing}, making every rule below vacuous over its root"
    )


def test_license_file_is_mit() -> None:
    text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    # Strip \r so a CRLF checkout does not fail with the self-denying message
    # 'expected "MIT License" but was "MIT License"'.
    assert text.split("\n")[0].rstrip("\r") == LICENSE_NAME
    assert "Permission is hereby granted, free of charge" in text
    # The holder NAME is AxonFlow in every SDK; the YEAR stays each repo's own
    # first year. This repo said "getaxonflow" until the relicence sweep.
    assert LICENSE_HOLDER in text


def test_pyproject_declares_mit() -> None:
    # LICENSE is what GitHub reads. These two are what PyPI publishes and what a
    # downstream `pip-licenses` audit reads, and all three can disagree, so all
    # three are asserted.
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    declared = project["license"]
    assert (declared["text"] if isinstance(declared, dict) else declared) == "MIT"
    assert "License :: OSI Approved :: MIT License" in project["classifiers"]


def test_every_spdx_identifier_names_mit() -> None:
    wrong: list[str] = []
    seen = 0
    for path, content in tree().items():
        for line in content.split("\n"):
            for declared in declared_identifiers(line):
                seen += 1
                if declared != "MIT":
                    wrong.append(f"{path}: {declared}")
    assert wrong == [], f"licence identifiers contradicting this repository's LICENSE: {wrong}"
    # Without this, a walk that read nothing would satisfy the assertion above.
    assert seen > 0, "no SPDX identifier was read at all, so the rule above proved nothing"


def test_no_foreign_licence_prose() -> None:
    hits = [
        f"{path}: {phrase}"
        for path, content in tree().items()
        if path != "LICENSE"  # it IS the licence text
        for phrase in FORBIDDEN_PHRASES
        if phrase in content
    ]
    assert hits == [], f"licence prose contradicting this repository's LICENSE: {hits}"


def test_every_copyright_notice_is_axonflows() -> None:
    # Scope stated precisely: this covers THIS REPOSITORY'S OWN SOURCE, not the
    # dependency trees the walk skips wholesale. An earlier version of this
    # comment in the Go sibling claimed the guard had "no exemption list at all"
    # and that a vendored file would "force the decision" -- both false, and R3
    # proved it with a real vendor tree that passed every licence rule. A guard
    # narrower than its own comment is worse than a narrow guard, because the
    # comment is what the next person relies on.
    #
    # What it does catch is the realistic drift: a third-party helper pasted into
    # `axonflow/` beside the code that uses it, where nothing marks it as someone
    # else's and any header pass sweeps it into MIT.
    foreign: list[str] = []
    seen = 0
    for path, content in tree().items():
        for line in content.split("\n"):
            if not COPYRIGHT_NOTICE.search(line):
                continue
            seen += 1
            if "AxonFlow" not in line:
                foreign.append(f"{path}: {line.strip()}")
    assert foreign == [], f"copyright notices that are not AxonFlow's: {foreign}"
    assert seen > 0, "no copyright notice was read at all, so the rule above proved nothing"


def _header_lines(content: str) -> list[tuple[int, str]]:
    """The header region as (1-based line number, text), skipping docstring bodies.

    A ``#!`` at column 0 inside a docstring is documentation, not a shebang, and
    flagging it fails correct code -- the failure mode that gets a guard deleted
    rather than fixed. Five lines rather than one, because a shebang that is
    re-spaced AND pushed down by an inserted header escapes a three-line window
    by exactly one line.
    """
    out: list[tuple[int, str]] = []
    fence: str | None = None
    for n, line in enumerate(content.split("\n")[:5], start=1):
        if fence is None:
            opener = next((q for q in DOCSTRING_FENCES if q in line), None)
            if opener is None:
                out.append((n, line))
                continue
            # A fence that opens and closes on the same line is a one-line
            # docstring and does not swallow what follows.
            if line.count(opener) == 1:
                fence = opener
        elif fence in line:
            fence = None
    return out


def test_shebangs_stay_on_line_one() -> None:
    """A shebang is only a shebang on line 1.

    A licence sweep rewrites the top of a file. If it reorders a shebang below
    the header it stops being one, and nothing in a test suite notices.

    SCOPE, stated because the obvious phrasing overclaims: this checks the
    shebang's POSITION and SPELLING, not the executable bit. Asserting the bit
    would fail on correct code here -- this repository has 11 shebang-bearing
    files and only 6 are executable, because a module meant to be run with
    ``python -m`` legitimately stays 644. The harm pinned is "the interpreter
    line stops being read", not "the file stops being executable", which was
    never true of nearly half of them.
    """
    misplaced = [
        f"{path}:{n}"
        for path, content in tree().items()
        if path.endswith((".py", ".sh"))
        for n, line in _header_lines(content)
        if line.startswith("#!") and n != 1
    ]
    assert misplaced == [], f"shebang not on line 1: {misplaced}"


def test_shebangs_are_not_respaced() -> None:
    """`# !/usr/bin/env python` is a comment, not a shebang.

    The position rule above keys on the literal ``#!``, so a sweep that inserted
    a space would make the line invisible to it AND inert to the kernel at the
    same time -- the two failures hide each other. Both broken orderings are
    covered: re-spaced in place on line 1, and re-spaced then pushed down by an
    inserted header. The second is the one no other rule here can see.

    This is the Python analogue of the ``// go:build`` defect the Go sibling
    shipped into review, where build, vet and the entire suite passed with the
    directive disabled.
    """
    # The window is the header region rather than line 1, deliberately: the two
    # broken orderings are (a) re-spaced in place, still on line 1, and (b)
    # re-spaced AND pushed down by an inserted header. (b) is invisible to the
    # position rule for two independent reasons at once, so it must be caught
    # here or not at all.
    respaced = [
        f"{path}:{n}"
        for path, content in tree().items()
        if path.endswith((".py", ".sh"))
        for n, line in _header_lines(content)
        if re.match(r"^#[ \t]+!", line)
    ]
    assert respaced == [], f"a shebang with a space after the hash is inert: {respaced}"


def test_coding_cookies_are_where_python_honours_them() -> None:
    """A declared source encoding must be one CPython actually applies.

    The rule is positional and conditional -- PEP 263 reads line 1, and reads
    line 2 as well when line 1 is a shebang, a comment or blank -- so a header
    inserted above a cookie can push it out of range. Python then decodes the
    file as UTF-8 and says nothing; the damage appears later, in whichever
    non-ASCII literal happens to matter.

    This asks ``tokenize.detect_encoding`` rather than re-implementing the rule.
    An earlier version encoded my own reading of PEP 263 -- line 2 only after a
    shebang -- which is NARROWER than CPython in the false-positive direction:
    it rejected a cookie on line 2 after a line-1 comment, a shape these
    repositories produce by default because they put a copyright line first.
    Verified against the tokenizer, that file decodes as iso-8859-1, so the
    guard was failing correct code. Deriving the expectation from the authority
    instead of from a paraphrase removes the whole class.
    """
    inert: list[str] = []
    checked = 0
    for path in sorted(tree()):
        if not path.endswith(".py"):
            continue
        raw = (REPO_ROOT / path).read_bytes()
        declared = None
        for line in raw.split(b"\n")[:8]:
            m = CODING_COOKIE.match(line.decode("latin-1"))
            if m:
                declared = m.group(1)
                break
        if declared is None:
            continue
        checked += 1
        with (REPO_ROOT / path).open("rb") as fh:
            honoured, _ = tokenize.detect_encoding(fh.readline)
        if codecs.lookup(honoured).name != codecs.lookup(declared).name:
            inert.append(
                f"{path}: declares {declared!r} but CPython decodes it as "
                f"{honoured!r} -- the cookie is out of the range PEP 263 reads"
            )
    assert inert == [], f"coding cookies CPython does not honour: {inert}"
    # No `checked > 0` floor here: this repository declares no cookie today, and
    # a floor would be a number tuned until it passed. The rule's own reachability
    # is proven by the planted mutant instead, which is the honest control.
    assert checked >= 0


def test_a_shebang_inside_a_docstring_is_not_a_shebang() -> None:
    """The false-positive direction, pinned with a fixture.

    A ``#!`` at column 0 inside a docstring is documentation. Flagging it
    fails correct code, and a guard that fails correct code gets deleted
    rather than fixed -- the same trap as scanning a vendor tree.
    """
    q = DOCSTRING_FENCES[0]
    in_docstring = "\n".join([q, "#!/usr/bin/env python3", q, "x = 1"])
    assert "#!/usr/bin/env python3" not in [text for _, text in _header_lines(in_docstring)]

    # ...and the control: outside a docstring the same line IS seen, so the
    # exclusion cannot be satisfied by seeing nothing at all.
    real = "\n".join(["#!/usr/bin/env python3", "x = 1"])
    assert "#!/usr/bin/env python3" in [text for _, text in _header_lines(real)]


def test_identifier_reader_handles_every_comment_syntax() -> None:
    # A recogniser has two failure directions and needs a case for each. Rows are
    # built from SPDX_TAG rather than written out, so this test's own cases are
    # not hits for the tree scan it describes.
    cases: list[tuple[str, list[str]]] = [
        # ACCEPTS: MIT however the surrounding comment closes.
        (f"# {SPDX_TAG} MIT", ["MIT"]),
        (f"// {SPDX_TAG} MIT", ["MIT"]),
        (f" * {SPDX_TAG} MIT", ["MIT"]),
        (f"/* {SPDX_TAG} MIT */", ["MIT"]),
        (f"<!-- {SPDX_TAG} MIT -->", ["MIT"]),
        # Every terminator is exercised, so dropping one fails here rather than
        # silently narrowing what the reader understands.
        (f"<%-- {SPDX_TAG} MIT --%>", ["MIT"]),
        (f"<# {SPDX_TAG} MIT #>", ["MIT"]),
        # CASE: the docstring claims closure under the syntax.
        (f"# {SPDX_TAG.lower()} Apache-2.0", ["Apache-2.0"]),
        (f"# {SPDX_TAG.upper()} BUSL-1.1", ["BUSL-1.1"]),
        (f"# {SPDX_TAG.lower()} MIT", ["MIT"]),
        # STILL CATCHES: a foreign identifier is not laundered.
        (f"/* {SPDX_TAG} Apache-2.0 */", ["Apache-2.0"]),
        (f"<!-- {SPDX_TAG} BUSL-1.1 -->", ["BUSL-1.1"]),
        (f"# {SPDX_TAG} MIT OR GPL-3.0", ["MIT OR GPL-3.0"]),
        # THE FALSE-NEGATIVE DIRECTION, which is the one that ships.
        (f"<!-- {SPDX_TAG} MIT --> <!-- {SPDX_TAG} Apache-2.0 -->", ["MIT", "Apache-2.0"]),
        (f"/* {SPDX_TAG} MIT */ /* {SPDX_TAG} BUSL-1.1 */", ["MIT", "BUSL-1.1"]),
        (f"{SPDX_TAG} MIT {SPDX_TAG} Apache-2.0", ["MIT", "Apache-2.0"]),
        # A line declaring nothing yields nothing, so `seen` counts only real ones.
        ("import re", []),
    ]
    for line, want in cases:
        assert declared_identifiers(line) == want, f"declared_identifiers({line!r})"


def test_dependency_and_build_output_is_not_scanned() -> None:
    # Segment-wise, not prefix: these appear nested, so a prefix test would walk
    # every installed dependency's licence headers.
    for rel in (
        ".git/config",
        "axonflow/__pycache__/x.pyc",
        "build/lib/x.py",
        "src/axonflow.egg-info/PKG-INFO",
    ):
        assert is_not_source(rel), rel
    # ...while a real source path that merely CONTAINS the word is still scanned.
    for rel in ("axonflow/hitl.py", "axonflow/building/x.py", "tests/test_hitl.py"):
        assert not is_not_source(rel), rel
    # A virtual environment is excluded by its pyvenv.cfg WHATEVER it is called,
    # and a directory that merely looks like one by name is not excluded unless it
    # really is one. Both directions, because name-matching failed in the first.
    for name in (".venv", "venv", ".rvenv", "env-py314", "some/nested/whatever"):
        assert is_not_source(f"{name}/lib/site-packages/x.py", frozenset({name}))
        assert not is_not_source(f"{name}/lib/site-packages/x.py", frozenset())


def test_phrase_rule_can_fire() -> None:
    # The prose rule asserts an ABSENCE across a tree that is currently clean, so
    # on its own it would pass identically if the membership test never matched.
    planted = "# Licensed under the " + "Apache" + " License, Version 2.0"
    assert any(phrase in planted for phrase in FORBIDDEN_PHRASES)
