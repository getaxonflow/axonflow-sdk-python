#!/usr/bin/env python3
"""Apply one source mutant, verify it was APPLIED, run tests, report, restore.

    python scripts/mutation-check.py "<label>" <file> "<old>" "<new>" "<-k expr>"

WHY THIS EXISTS AS A COMMITTED SCRIPT RATHER THAN A SHELL ONE-LINER.

A mutation result is only evidence if the mutation actually happened, and the
first version of this harness reported a KILLED mutant as SURVIVED. Two causes,
both silent:

  1. It inferred "applied" from an exit code it did not check, so a pattern that
     no longer matched the file produced a green run and a clean report.
  2. It wrote the mutated source and ran pytest WITHIN THE SAME SECOND, and
     CPython's .pyc validity check is mtime-based with coarse granularity — so
     the tests ran against STALE BYTECODE. The same mutant fails when run by
     hand seconds later.

Both directions matter: a false SURVIVED sends you hunting a test gap that does
not exist, and a false KILLED certifies coverage you do not have.

So this script asserts the file changed on disk, purges __pycache__, sets
PYTHONDONTWRITEBYTECODE, refuses to report on a run where no test executed, and
always restores the original in a finally block — a killed script that left the
tree mutated would be worse than no harness at all.
"""

import os
import pathlib
import shutil
import subprocess
import sys

desc, path, old, new, kexpr = sys.argv[1:6]
p = pathlib.Path(path)
orig = p.read_text(encoding="utf-8")
if old not in orig:
    print(f"NOT-APPLIED  :: {desc}  (pattern absent)")
    sys.exit(3)
mutated = orig.replace(old, new, 1)
if mutated == orig:
    print(f"NOT-APPLIED  :: {desc}  (replace was a no-op)")
    sys.exit(3)
bak = orig
p.write_text(mutated, encoding="utf-8")
try:
    # Positive control: the file on disk really differs now.
    if p.read_text(encoding="utf-8") == bak:
        # The positive control. Reporting on a run where the mutation did not
        # land is how a harness certifies coverage that does not exist.
        print(f"NOT-APPLIED  :: {desc}  (file unchanged on disk)")
        sys.exit(3)
    # PYTHONDONTWRITEBYTECODE + a pycache purge: the harness writes the source
    # and runs pytest within the same second, and CPython's .pyc validity check
    # is mtime-based with coarse granularity — so a stale bytecode cache made a
    # KILLED mutant report as SURVIVED. Proven: the same mutant fails when run
    # by hand seconds later.
    for cache in pathlib.Path("axonflow").rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    r = subprocess.run(  # noqa: S603 - argv is literal; sys.executable is trusted
        [
            ".venv/bin/python",
            "-m",
            "pytest",
            "tests/test_telemetry_adapter_registry.py",
            "-p",
            "no:cacheprovider",
            "--no-cov",
            "-q",
            "-k",
            kexpr,
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "(no output)"
    if "no tests ran" in r.stdout or (
        "deselected" in tail and "passed" not in tail and "failed" not in tail
    ):
        print(f"NO-TESTS-RAN :: {desc}  [{tail}]")
        sys.exit(4)
    if r.returncode != 0:
        print(f"KILLED       :: {desc}  [{tail}]")
    else:
        print(f"SURVIVED(BAD):: {desc}  [{tail}]")
finally:
    p.write_text(bak, encoding="utf-8")
