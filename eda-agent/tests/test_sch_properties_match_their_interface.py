# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A schematic property must only be read off a type that has it.

Reported as issue #22 by zacky0904 on AD25: obj_query with
``object_type="ePort"`` and ``Orientation`` among the properties opened an
"Undeclared identifier: Orientation" dialog and stalled the polling loop
instead of returning a tool error.

Text and Orientation are not on the base ISch_GraphicalObject. ISch_Port
names itself with Name, and carries its direction in Style. The scripting
reference corroborates that twice: PlaceAPort.pas builds a Port from Name,
Style, IOType, Alignment and Width and never touches Text or Orientation,
and ReplaceSchObjects.pas reads a cross-sheet connector's Orientation
precisely in order to MAP it onto Port.Style.

WHY IT CANNOT BE CAUGHT. An undeclared identifier is surfaced by the
script engine as a modal before any surrounding Try/Except runs. The same
engine behaviour defeated Try/Except around StrToFloat and around the
response-file create. Guarding after the fact is not available here, so
the access has to be gated before it happens.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

GENERIC = Path(__file__).parent.parent / "scripts" / "altium" / "Generic.pas"
MAIN = Path(__file__).parent.parent / "scripts" / "altium" / "Main.pas"


def _source() -> str:
    return GENERIC.read_text(encoding="utf-8", errors="replace")


def _decommented(text: str) -> str:
    text = re.sub(r"\{[^}]*\}", " ", text, flags=re.S)
    return re.sub(r"//.*", " ", text)


@pytest.mark.parametrize("prop", ["Text", "Orientation"])
def test_the_access_is_gated_by_type(prop):
    """Every read and write of these goes through a type check."""
    code = _decommented(_source())
    guard = {"Text": "SchObjectHasText", "Orientation": "SchObjectHasOrientation"}[prop]

    # \bObj\. and not just Obj\. : PowerObj.Orientation is a TYPED local
    # where the access is already correct, and matching it flagged code
    # that was never in question. Same substring trap as the undeclared
    # local lint rule.
    accesses = [m.start()
                for m in re.finditer(r"\bObj\." + prop + r"\b", code)]
    assert accesses, f"no Obj.{prop} access found; has the getter moved?"

    for at in accesses:
        window = code[max(0, at - 400):at]
        assert guard in window or "ObjectId" in window, (
            f"Obj.{prop} at offset {at} is reached without a type check. "
            f"On a type that lacks it this raises an undeclared identifier, "
            f"which is a modal that stalls the polling loop and which "
            f"Try/Except cannot contain")


@pytest.mark.parametrize("guard", ["SchObjectHasText", "SchObjectHasOrientation"])
def test_a_port_is_excluded(guard):
    """The reported type. Both properties are absent on ISch_Port."""
    code = _source()
    start = code.index(f"Function {guard}")
    body = code[start:code.index("\nEnd;", start)]
    assert "ePort" in body, (
        f"{guard} does not exclude ePort, which is the type the report "
        f"reproduced against")


def test_the_connection_point_is_pin_only():
    """ConnectionX/ConnectionY read Orientation too.

    Added while fixing a different bug and carrying the same fault: a
    connection point is a pin idea, and the Try around the read cannot
    save a type that has no Orientation.
    """
    code = _decommented(_source())
    for prop in ("ConnectionX", "ConnectionY"):
        at = code.index(f"PropName = '{prop}'")
        window = code[at:at + 400]
        assert "ObjectId <> ePin" in window, (
            f"{prop} does not restrict itself to pins before reading "
            f"Orientation")


def test_the_net_highlight_path_uses_name_for_a_port():
    """It iterated ePort and read Obj.Text on everything but a sheet entry."""
    code = _decommented(_source())
    at = code.index("ObjNet :=")
    window = code[max(0, at - 600):at + 400]
    assert "SchObjectHasText" in window, (
        "the net-highlight path still decides between Name and Text by "
        "checking only for a sheet entry, so a Port takes the Text branch")


# ---------------------------------------------------------------------------
# Fault 1: the constant AD25 refuses.
# ---------------------------------------------------------------------------

def test_the_sentinel_is_not_the_32_bit_boundary():
    """AD25 rejects 2147483647 outright with "Invalid constant"."""
    code = _decommented(MAIN.read_text(encoding="utf-8", errors="replace"))
    assert "2147483647" not in code, (
        "the 32-bit boundary literal is back. AD25 refuses it while "
        "compiling and the loop never starts")

    m = re.search(r"MAX_INT\s*=\s*(\d+)\s*;", code)
    assert m, "MAX_INT is gone; DelphiScript does not predefine MaxInt"
    value = int(m.group(1))
    assert value < 2147483647
    # It is a "larger than anything real" sentinel for internal units,
    # where 1 unit is 1/10000 mil. It has to clear a realistic board.
    assert value >= 1000000000, (
        f"MAX_INT is {value}, which is under 100 inches in internal units "
        f"and could be reached by a real coordinate")


def test_no_hardcoded_boundary_literals_elsewhere():
    """Two sentinels bypassed the constant and carried the literal."""
    offenders = []
    for path in (GENERIC.parent).glob("*.pas"):
        if path.name == "Altium_MCP.pas":
            continue
        code = _decommented(path.read_text(encoding="utf-8", errors="replace"))
        for i, line in enumerate(code.splitlines(), 1):
            if "2147483647" in line:
                offenders.append(f"{path.name}:{i}")
    assert not offenders, (
        f"32-bit boundary literals outside the constant: {offenders}")
