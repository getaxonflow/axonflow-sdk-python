#!/usr/bin/env python3
"""Lint check: no falsey-clobber on `.get(...) or X` / `obj.attr or X`.

The wire-shape audit caught `response.result or data_dict.get("result")`
in `generate_plan` — the `or` falls through on every falsy value, not
just None, so legitimate empty results (0, False, "", [], {}) get
silently replaced with the fallback. The Python audit also found this
same pattern in the `started_at` slot of `create_workflow`.

This script walks each Python file with the stdlib `ast` module and
flags `BoolOp(Or)` where the **left** operand is one of:

- `Call` whose function is an `Attribute` ending in `.get`
  (e.g. `data.get("key") or fallback`).
- `Attribute` access on a name (e.g. `response.field or fallback`).
- `Subscript` (e.g. `data["key"] or fallback`).

These are the common transformer-code patterns that bit us. Pure
boolean control-flow `if a or b:` and `while x or y:` are NOT flagged
(BoolOp inside a test position is allowed).

Baseline mode (mirrors the wire-shape contract gate pattern):

A JSON baseline file at `.lint_baselines/falsey_clobber.json`
captures the set of findings that exist on `main` at the time of
introduction. CI uses `--baseline` to fail only on findings NOT in
the baseline — i.e., new code is blocked from introducing the
pattern, but pre-existing instances can burn down incrementally.

Without `--baseline`, every finding fails the run.

Usage:
    # Lint a path, fail on any finding:
    python3 scripts/lint_no_falsey_clobber.py axonflow/

    # Lint with baseline tolerance (CI):
    python3 scripts/lint_no_falsey_clobber.py axonflow/ \\
        --baseline .lint_baselines/falsey_clobber.json

    # Regenerate baseline (after intentional sweep):
    python3 scripts/lint_no_falsey_clobber.py axonflow/ \\
        --write-baseline .lint_baselines/falsey_clobber.json

Exit codes:
    0 — no findings (or all findings baselined)
    1 — at least one new falsey-clobber pattern found
    2 — script error (file not parseable, etc.)

False-positive escape hatch:
    Add `# noqa: falsey-clobber` at end of the offending line.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

NOQA_MARKER = "noqa: falsey-clobber"


class FalseyClobberFinder(ast.NodeVisitor):
    def __init__(self, source_lines: list[str], path: Path) -> None:
        self.source_lines = source_lines
        self.path = path
        self.findings: list[tuple[int, int, str]] = []
        # Stack of "in test position" — when True, `or` is being used
        # for control flow (if/while/assert/comprehension if-clause).
        # We don't flag `or` in those contexts; they're not data
        # transformer expressions.
        self._test_depth = 0

    # ------- track test-position scopes -------

    def visit_If(self, node: ast.If) -> None:
        self._test_depth += 1
        self.visit(node.test)
        self._test_depth -= 1
        for stmt in node.body:
            self.visit(stmt)
        for stmt in node.orelse:
            self.visit(stmt)

    def visit_While(self, node: ast.While) -> None:
        self._test_depth += 1
        self.visit(node.test)
        self._test_depth -= 1
        for stmt in node.body:
            self.visit(stmt)
        for stmt in node.orelse:
            self.visit(stmt)

    def visit_Assert(self, node: ast.Assert) -> None:
        self._test_depth += 1
        self.visit(node.test)
        self._test_depth -= 1
        if node.msg is not None:
            self.visit(node.msg)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        # Ternary: walk test in test position; body+orelse outside.
        self._test_depth += 1
        self.visit(node.test)
        self._test_depth -= 1
        self.visit(node.body)
        self.visit(node.orelse)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.visit(node.iter)
        self._test_depth += 1
        for if_clause in node.ifs:
            self.visit(if_clause)
        self._test_depth -= 1

    # ------- the actual check -------

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        if self._test_depth > 0:
            self.generic_visit(node)
            return
        if not isinstance(node.op, ast.Or):
            self.generic_visit(node)
            return
        # The bug pattern is when the LEFT operand is a wire-field-like
        # access. Multi-operand `a or b or c` evaluates left-to-right;
        # we flag any operand other than the last whose type matches.
        for operand in node.values[:-1]:
            if self._is_wire_field_access(operand) and not self._has_noqa(operand):
                lineno = operand.lineno
                col = operand.col_offset
                snippet = (
                    self.source_lines[lineno - 1].rstrip()
                    if lineno - 1 < len(self.source_lines)
                    else ""
                )
                self.findings.append(
                    (
                        lineno,
                        col,
                        (
                            f"falsey-clobber: `or` falls through on every falsy "
                            f"value (0, False, '', [], {{}}), not just None. "
                            f"Use `... if X is not None else fallback`. "
                            f"Line: {snippet.strip()}"
                        ),
                    )
                )
        self.generic_visit(node)

    @staticmethod
    def _is_wire_field_access(node: ast.expr) -> bool:
        """Return True if the expression is the kind of wire-field
        access that's likely to take a meaningful falsy value."""
        # `data.get("key")` or `obj.method()` — flag when method is `.get`.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            return node.func.attr == "get"
        # `obj.attr` — wire access on a parsed response object.
        # `data["key"]` — direct subscript.
        return isinstance(node, (ast.Attribute, ast.Subscript))

    def _has_noqa(self, node: ast.expr) -> bool:
        line_idx = node.lineno - 1
        if line_idx < 0 or line_idx >= len(self.source_lines):
            return False
        return NOQA_MARKER in self.source_lines[line_idx]


def check_file(path: Path) -> list[tuple[Path, int, int, str]]:
    try:
        source = path.read_text()
    except OSError as e:
        print(f"error: could not read {path}: {e}", file=sys.stderr)
        return []
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        print(f"error: could not parse {path}: {e}", file=sys.stderr)
        return []
    finder = FalseyClobberFinder(source.splitlines(), path)
    finder.visit(tree)
    return [(path, lineno, col, msg) for lineno, col, msg in finder.findings]


def walk(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix == ".py" else []
    return sorted(p for p in root.rglob("*.py") if p.is_file())


def _finding_key(path: Path, lineno: int, col: int) -> str:
    """Stable key for baseline membership.

    File-relative path + line + column. Code shifts within a file
    (e.g. unrelated edits in the same file) move the line number,
    which is why baselines need to be regenerated after a sweep —
    same as the wire-shape gate's per-line baseline.
    """
    return f"{path}:{lineno}:{col}"


def _load_baseline(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"error: could not read baseline {path}: {e}", file=sys.stderr)
        sys.exit(2)
    raw = data.get("findings", [])
    if not isinstance(raw, list):
        print(f"error: baseline {path} has malformed `findings` list", file=sys.stderr)
        sys.exit(2)
    return set(raw)


def _write_baseline(path: Path, findings: list[tuple[Path, int, int, str]]) -> None:
    keys = sorted({_finding_key(p, line, col) for p, line, col, _ in findings})
    payload = {
        "_comment": (
            "Pre-existing falsey-clobber findings. Generated by "
            "scripts/lint_no_falsey_clobber.py --write-baseline. "
            "CI fails on any finding NOT listed here. Burn this list "
            "down via targeted PRs that fix each pattern (or add "
            "# noqa: falsey-clobber if the `or` is intentional)."
        ),
        "findings": keys,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="Files or directories to scan")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help=(
            "JSON file listing pre-existing findings to tolerate. "
            "Run is GREEN if every finding is in the baseline; "
            "RED if any finding is NOT in the baseline."
        ),
    )
    parser.add_argument(
        "--write-baseline",
        type=Path,
        default=None,
        help="Write the current set of findings to the given path and exit 0.",
    )
    args = parser.parse_args()

    findings: list[tuple[Path, int, int, str]] = []
    for raw in args.paths:
        path = Path(raw)
        if not path.exists():
            print(f"error: {path} does not exist", file=sys.stderr)
            return 2
        for f in walk(path):
            findings.extend(check_file(f))

    if args.write_baseline is not None:
        _write_baseline(args.write_baseline, findings)
        print(f"Wrote {len(findings)} finding(s) to {args.write_baseline}")
        return 0

    if not findings:
        print("no falsey-clobber findings.")
        return 0

    findings.sort()
    if args.baseline is not None:
        baselined = _load_baseline(args.baseline)
        new_findings = [
            (p, line, col, msg)
            for p, line, col, msg in findings
            if _finding_key(p, line, col) not in baselined
        ]
        # Also catch cases where the baseline references a finding
        # that's no longer present — burn-down protection.
        observed_keys = {_finding_key(p, line, col) for p, line, col, _ in findings}
        stale_baseline = [k for k in baselined if k not in observed_keys]
        if not new_findings and not stale_baseline:
            print(
                f"falsey-clobber: {len(findings)} finding(s), all baselined. "
                f"Burndown queue size: {len(findings)}."
            )
            return 0
        if new_findings:
            print(f"falsey-clobber: {len(new_findings)} NEW finding(s) (not in baseline):")
            for path, lineno, col, msg in new_findings:
                print(f"  {path}:{lineno}:{col}: {msg}")
            print()
            print("Each new finding is an `or` whose left operand is a wire-field")
            print("access. Use `value if value is not None else fallback`.")
            print("Add `# noqa: falsey-clobber` at end of line if the `or` is")
            print("intentional (rare in transformer code).")
        if stale_baseline:
            print()
            print(
                f"falsey-clobber: {len(stale_baseline)} stale baseline entry(ies) — "
                f"finding(s) burned down but baseline still lists them. "
                f"Re-run with --write-baseline to refresh."
            )
            for k in stale_baseline:
                print(f"  {k}")
        return 1

    print(f"falsey-clobber: {len(findings)} finding(s):")
    for path, lineno, col, msg in findings:
        print(f"  {path}:{lineno}:{col}: {msg}")
    print()
    print("Each finding flags an `or` whose left operand is a wire-field")
    print("access. Use `value if value is not None else fallback` instead.")
    print("Add `# noqa: falsey-clobber` at end of line if the `or` is")
    print("intentional (rare in transformer code).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
