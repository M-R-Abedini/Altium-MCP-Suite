# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""No module-level name is defined twice.

Python accepts a second `def` of the same name and silently keeps only
the last, so the first one just stops existing. There is no warning and
no error, and the code that called it goes quiet rather than failing.

Not hypothetical. A second `pytest_configure` was added to
`tests/conftest.py` beside the existing one, which discarded the
existing hook's registration and the new option's effect in one go. The
symptom was a pytest flag that parsed fine and did nothing, while the
suite kept passing.

Cheap to check and it needs no new dependency. A linter's F811 covers
the same ground, but this project has no Python linter in CI, so the
rule lives here where it actually runs.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _redefinitions(path: pathlib.Path) -> list[tuple[str, int, int]]:
    """(name, first line, redefining line) for module-level names.

    Only module level. A name reused inside two different classes or
    functions is a different scope and perfectly legal, so descending
    would produce noise rather than findings.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return []

    seen: dict[str, int] = {}
    out: list[tuple[str, int, int]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
            continue
        # A conditional or decorated redefinition can be deliberate
        # (typing overloads, platform branches). Those live inside an
        # `if` and so are not in tree.body, which is why walking only
        # the top level keeps this free of false positives.
        if node.name in seen:
            out.append((node.name, seen[node.name], node.lineno))
        seen[node.name] = node.lineno
    return out


def _python_files() -> list[pathlib.Path]:
    files: list[pathlib.Path] = []
    for folder in ("src", "tests", "scripts"):
        files.extend((_ROOT / folder).rglob("*.py"))
    return [f for f in files if "__pycache__" not in f.parts]


def test_no_module_level_name_is_defined_twice():
    offenders = []
    for path in _python_files():
        for name, first, again in _redefinitions(path):
            offenders.append(
                f"{path.relative_to(_ROOT)}: {name!r} defined at line "
                f"{first}, redefined at {again}")

    assert not offenders, (
        "a second definition silently replaces the first:\n  "
        + "\n  ".join(offenders))


def test_the_check_actually_detects_a_redefinition(tmp_path):
    """Guard the guard.

    A scan that silently matched nothing would pass forever while
    verifying nothing, which is the failure mode this whole family of
    tests exists to prevent.
    """
    good = tmp_path / "good.py"
    good.write_text("def a():\n    pass\n\n\ndef b():\n    pass\n",
                    encoding="utf-8")
    assert _redefinitions(good) == []

    bad = tmp_path / "bad.py"
    bad.write_text("def a():\n    pass\n\n\ndef a():\n    pass\n",
                   encoding="utf-8")
    found = _redefinitions(bad)
    assert found == [("a", 1, 5)], found


def test_a_name_reused_in_two_scopes_is_not_flagged(tmp_path):
    """Two classes may both define `run`; that is not a redefinition."""
    path = tmp_path / "scopes.py"
    path.write_text(
        "class A:\n    def run(self):\n        pass\n\n\n"
        "class B:\n    def run(self):\n        pass\n",
        encoding="utf-8")
    assert _redefinitions(path) == []


@pytest.mark.parametrize("folder", ["src", "tests", "scripts"])
def test_every_folder_is_actually_scanned(folder):
    """A path typo would empty the scan and pass silently."""
    scanned = {p for p in _python_files()
               if (_ROOT / folder) in p.parents or
               str(p).startswith(str(_ROOT / folder))}
    assert scanned, f"{folder} contributed no files to the scan"


def test_no_extension_handler_is_defined_twice():
    """The JS handler table has Python's redefinition problem exactly.

    `handlers['pcb.save'] = ...` twice keeps the last and discards the
    first, with no warning. The command still answers, so nothing looks
    broken; it just answers with the wrong implementation, and which one
    depends on file order.

    The Python half of this file uses ast. There is no JS parser here,
    and none is needed: the table is assigned by literal key, so the
    keys can be counted directly. What that cannot see is a handler
    assigned through a computed key, and none exists, which the count
    floor below keeps true.
    """
    import collections
    import re

    source = (_ROOT / "extensions" / "easyeda" / "main.js").read_text(
        encoding="utf-8")

    names = re.findall(r"handlers\['([a-z_.0-9]+)'\]\s*=", source)
    assert len(names) > 100, (
        f"only {len(names)} handler assignments found; the pattern has "
        f"drifted and this guard is counting nothing")

    duplicates = sorted(n for n, count in collections.Counter(names).items()
                        if count > 1)
    assert not duplicates, (
        f"these commands have more than one handler: {duplicates}. The "
        f"last assignment wins and the earlier one silently stops "
        f"existing, so the command answers with whichever came last.")

    # A computed key would be invisible to the scan above, so the table
    # must only ever be ASSIGNED by literal.
    #
    # Assignment specifically: the dispatcher READS `handlers[command]`
    # by design, and matching every subscript flagged that as a defect.
    computed = re.findall(r"handlers\[(?!')[^\]]*\]\s*=(?!=)", source)
    assert not computed, (
        f"{len(computed)} handler(s) are assigned through a computed "
        f"key, which the duplicate scan cannot see")


def test_no_extension_helper_is_defined_twice():
    """Same problem one level up.

    A second `function padShape(...)` replaces the first for every
    caller, including the ones written against the original.
    """
    import collections
    import re

    source = (_ROOT / "extensions" / "easyeda" / "main.js").read_text(
        encoding="utf-8")

    functions = re.findall(r"^function (\w+)\(", source, re.M)
    constants = re.findall(r"^const ([A-Za-z_][A-Za-z_0-9]*) =", source, re.M)
    assert len(functions) > 10 and len(constants) > 5, (
        "the function or constant scan has drifted and is finding almost "
        "nothing")

    duplicates = sorted(
        n for n, count in collections.Counter(functions + constants).items()
        if count > 1)
    assert not duplicates, (
        f"these module-level names are defined twice in main.js: "
        f"{duplicates}. In JavaScript a repeated `const` throws at load, "
        f"which takes the whole extension down, and a repeated "
        f"`function` silently keeps the last.")
