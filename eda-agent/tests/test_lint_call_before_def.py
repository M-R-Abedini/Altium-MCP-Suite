# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Unit tests for the call-before-definition lint rule.

DelphiScript resolves identifiers strictly top-down within a unit and has
NO ``Forward;`` directive, so a locally-defined Function/Procedure called
above its definition raises "Undeclared identifier" -- and only after a full
Altium restart + recompile. The JSON prop builders calling EscapeJsonString
(defined lower in Utils.pas) was the regression that motivated this guard.

See memory: delphiscript_call_before_definition.md.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
LINT_PATH = REPO_ROOT / "scripts" / "altium" / "lint.py"


def _load_lint_module():
    spec = importlib.util.spec_from_file_location("_altium_lint", LINT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_altium_lint"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def lint():
    return _load_lint_module()


def _lines(src: str) -> list[str]:
    return src.split("\n")


def test_custom_forward_reference_is_flagged(lint):
    """A custom helper called above its definition is the real bug."""
    src = (
        "Function JsonStr(Name : String) : String;\n"
        "Begin\n"
        "    Result := EscapeJsonString(Name);\n"   # line 3: call before def
        "End;\n"
        "Function EscapeJsonString(S : String) : String;\n"  # line 5: def
        "Begin Result := S; End;\n"
    )
    findings = lint._scan_call_before_definition("t.pas", _lines(src))
    rules = {(f.rule, f.line) for f in findings}
    assert ("call-before-definition", 3) in rules, findings


def test_builtin_shadow_is_not_flagged(lint):
    """A redefined RTL routine (StrToIntDef) called early binds to the
    built-in, not the not-yet-declared local -- so it must NOT flag."""
    src = (
        "Function StrToPinOrientation(S : String) : Integer;\n"
        "Begin\n"
        "    Result := StrToIntDef(S, 0);\n"   # binds to built-in StrToIntDef
        "End;\n"
        "Function StrToIntDef(S : String; D : Integer) : Integer;\n"
        "Begin Result := D; End;\n"
    )
    findings = lint._scan_call_before_definition("t.pas", _lines(src))
    assert findings == [], f"false positive on built-in shadow: {findings}"


def test_correct_order_is_not_flagged(lint):
    """Callee defined above its caller -- the normal, valid case."""
    src = (
        "Function EscapeJsonString(S : String) : String;\n"
        "Begin Result := S; End;\n"
        "Function JsonStr(Name : String) : String;\n"
        "Begin\n"
        "    Result := EscapeJsonString(Name);\n"
        "End;\n"
    )
    findings = lint._scan_call_before_definition("t.pas", _lines(src))
    assert findings == [], findings


def test_qualified_method_call_is_not_flagged(lint):
    """A ``.Name(`` member call must not collide with a local Name."""
    src = (
        "Function Foo(X : Integer) : Integer;\n"
        "Begin\n"
        "    Result := Obj.Foo(X);\n"   # method call, not the local Foo
        "End;\n"
        "Function Bar : Integer;\n"
        "Begin Result := Foo(1); End;\n"
    )
    findings = lint._scan_call_before_definition("t.pas", _lines(src))
    assert findings == [], findings


def test_recursion_is_not_flagged(lint):
    """A function calling itself is defined above the call (its own body)."""
    src = (
        "Function Fact(N : Integer) : Integer;\n"
        "Begin\n"
        "    If N <= 1 Then Result := 1 Else Result := N * Fact(N - 1);\n"
        "End;\n"
    )
    findings = lint._scan_call_before_definition("t.pas", _lines(src))
    assert findings == [], findings


def test_live_sources_have_no_call_before_def(lint):
    """The shipped .pas files must be clean (regression lock for Utils.pas)."""
    scripts_dir = REPO_ROOT / "scripts" / "altium"
    offenders = []
    scanned, missing = 0, []
    for name in lint.PAS_FILES:
        p = scripts_dir / name
        if not p.exists():
            missing.append(name)
            continue
        raw = p.read_text(encoding="utf-8", errors="replace").split("\n")
        scanned += 1
        offenders += lint._scan_call_before_definition(name, raw)

    # Absence used to be tolerated silently: every file could be
    # renamed and this would still report the sources clean, because
    # the loop simply skipped what it could not find. A regression lock
    # that unlocks itself when the thing it locks moves is worse than
    # none, since it keeps reporting success.
    assert not missing, (
        f"these units are named in PAS_FILES but not on disk, so they "
        f"were never linted: {missing}")
    assert scanned >= 8, (
        f"only {scanned} units were linted; PAS_FILES has shrunk and "
        f"this regression lock covers almost nothing")

    assert not offenders, (
        "call-before-definition in live sources:\n"
        + "\n".join(f"{o.file}:{o.line}  {o.snippet.strip()}" for o in offenders)
    )
