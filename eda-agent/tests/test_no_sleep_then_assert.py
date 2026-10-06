# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A fixed sleep followed by an assertion is a race, not a wait.

The simulator answers requests on a background thread. A test that
writes a request, sleeps a fixed 0.1 seconds and then asserts the file
is gone is betting that the thread gets scheduled inside that window.
It wins on an idle machine and loses under load, which produces the
least useful kind of failure: red at 94% of a full run, green when run
alone.

That is not theoretical here. ``test_bad_request_still_removed`` did
exactly this, and the file that still holds a legitimate poll loop
carries a comment about an earlier version failing "roughly 1 run in
25" for the same reason.

Use ``wait_until`` from ``tests/conftest.py``. It returns as soon as
the condition holds, so it is faster than the sleep it replaces, and
fails only after a timeout long enough that a failure means the
behaviour is wrong rather than the machine busy.

Sleeping is still fine in two shapes, and neither is flagged:

* inside a poll loop, where the sleep is the backoff and the assertion
  comes after the loop
* to simulate timing that is the subject of the test, as
  ``test_stress.py`` does when it writes a file in two parts to imitate
  a reader catching a Pascal write mid-flight

The rule is only about a sleep whose next act is an assertion.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent

#: How many statements after the sleep still count as "immediately".
#: Two, not more. At three, a sleep that simulates timing and is
#: followed by the work and then a check on the work reads as a race
#: when it is not; test_stress.py has exactly that shape.
_LOOKAHEAD = 2


def _is_sleep_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return False
    func = node.value.func
    name = (func.attr if isinstance(func, ast.Attribute)
            else getattr(func, "id", ""))
    return name == "sleep"


def _offenders_in(tree: ast.AST) -> list[int]:
    """Line numbers of sleeps whose next statements assert, outside loops.

    Works on statement LISTS rather than a flat walk, so "the next
    statement" is a real sibling and a sleep at the end of a loop body
    is not paired with an assertion that follows the loop.
    """
    found: list[int] = []

    def visit(node: ast.AST, in_loop: bool) -> None:
        for field, value in ast.iter_fields(node):
            if not isinstance(value, list):
                if isinstance(value, ast.AST):
                    visit(value, in_loop)
                continue
            statements = [s for s in value if isinstance(s, ast.stmt)]
            for index, stmt in enumerate(statements):
                if not in_loop and _is_sleep_call(stmt):
                    ahead = statements[index + 1:index + 1 + _LOOKAHEAD]
                    if any(isinstance(s, ast.Assert) for s in ahead):
                        found.append(stmt.lineno)
            for item in value:
                if isinstance(item, ast.AST):
                    visit(item, in_loop or isinstance(node, (ast.For,
                                                             ast.While)))

    visit(tree, False)
    return found


def _scan() -> tuple[list[str], int]:
    offenders, scanned = [], 0
    for path in sorted(TESTS.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8",
                                            errors="replace"))
        except SyntaxError:
            continue
        scanned += 1
        for lineno in _offenders_in(tree):
            offenders.append(f"{path.relative_to(TESTS).as_posix()}:{lineno}")
    return offenders, scanned


def test_no_test_sleeps_then_asserts():
    offenders, scanned = _scan()
    assert scanned > 50, (
        f"only parsed {scanned} test modules; the scan is not seeing the "
        f"suite and this guard proves nothing")
    assert not offenders, (
        "these sleep for a fixed time and then assert, which races "
        "whatever they are waiting for:\n  " + "\n  ".join(offenders)
        + "\nReplace the sleep with wait_until() from tests/conftest.py.")


def test_the_scan_finds_a_planted_offender():
    """A detector that matches nothing would pass forever."""
    tree = ast.parse(
        "import time\n"
        "def t():\n"
        "    time.sleep(0.1)\n"
        "    assert True\n"
    )
    assert _offenders_in(tree) == [3]


def test_a_sleep_inside_a_poll_loop_is_allowed():
    """The legitimate shape must not be reported.

    Without this the guard would push people away from the very pattern
    it is trying to encourage.
    """
    tree = ast.parse(
        "import time\n"
        "def t():\n"
        "    while cond():\n"
        "        time.sleep(0.01)\n"
        "    assert done()\n"
    )
    assert _offenders_in(tree) == []


def test_a_sleep_with_no_following_assert_is_allowed():
    """Sleeping to simulate timing is not what this rule is about."""
    tree = ast.parse(
        "import time\n"
        "def t():\n"
        "    time.sleep(0.05)\n"
        "    write_second_half()\n"
        "    result = read_it()\n"
        "    assert result\n"
    )
    assert _offenders_in(tree) == []
