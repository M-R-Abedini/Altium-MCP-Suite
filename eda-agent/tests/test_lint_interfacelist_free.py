# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Unit tests for the interfacelist-free lint rule.

Freeing a TInterfaceList that held Altium interface refs releases those
refs through the COM marshaller and faults in oleaut32 (access violation,
read of FFFFFFFF). PCB_SetTrackWidth, PCB_DistributeComponents and
Proj_Annotate all crashed this way on a live board; the working tools
(PCB_Scale, CollectSelectedPCBPrims callers) leave the list to the script
host. The rule flags any ``.Free`` on a local declared ``: TInterfaceList``.

See memory: altium_wrong_api_identifier_family.md.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
LINT_PATH = REPO_ROOT / "scripts" / "altium" / "lint.py"


def _load_lint_module():
    spec = importlib.util.spec_from_file_location("_altium_lint_ifl", LINT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_altium_lint_ifl"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def lint():
    return _load_lint_module()


def _findings(lint, source: str):
    lines = source.split("\n")
    return lint._scan_interfacelist_free("test.pas", lines)


def test_flags_free_on_interfacelist_local(lint):
    src = """Function Foo(Params : String) : String;
Var
    Matches : TInterfaceList;
Begin
    Matches := CreateObject(TInterfaceList);
    Matches.Free;
End;"""
    found = _findings(lint, src)
    assert len(found) == 1
    assert found[0].rule == "interfacelist-free"
    assert found[0].line == 6


def test_flags_free_on_comma_declared_list(lint):
    src = """Procedure Bar;
Var
    A, B : TInterfaceList;
Begin
    B.Free;
End;"""
    found = _findings(lint, src)
    assert len(found) == 1


def test_ignores_free_on_stringlist(lint):
    src = """Function Foo(Params : String) : String;
Var
    Names : TStringList;
Begin
    Names := TStringList.Create;
    Names.Free;
End;"""
    assert _findings(lint, src) == []


def test_ignores_interfacelist_parameter(lint):
    """A TInterfaceList received as a parameter is caller-owned; freeing
    it would be the caller's bug, not this function's -- no flag."""
    src = """Procedure CollectStuff(Board : IPCB_Board;
    L : TInterfaceList);
Var
    Names : TStringList;
Begin
    L.Free;
End;"""
    assert _findings(lint, src) == []


def test_declarations_reset_per_function(lint):
    src = """Function First : String;
Var
    L : TInterfaceList;
Begin
End;

Function Second : String;
Var
    L : TStringList;
Begin
    L.Free;
End;"""
    assert _findings(lint, src) == []


def test_bundled_sources_are_clean(lint):
    pas_dir = REPO_ROOT / "scripts" / "altium"
    for pas in pas_dir.glob("*.pas"):
        if pas.name == "Altium_MCP.pas":
            continue  # generated bundle, mirrors the sources
        lines = pas.read_text(encoding="utf-8", errors="replace").split("\n")
        found = lint._scan_interfacelist_free(pas.name, lines)
        assert found == [], f"{pas.name} frees a TInterfaceList: {found}"
