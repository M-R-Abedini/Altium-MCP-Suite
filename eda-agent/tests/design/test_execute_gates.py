# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Pre-emit validation gates on the canvas execute path.

The canvas-based ``execute_plan_via_canvas_from_json`` must enforce the
same gates the standalone validation tool runs -- Pydantic validation
alone lets cross-reference and connectivity faults reach emit. These
tests pin the gate behaviour:

* a needs_creation part halts the execute path (no partial emit), and the
  reported refdes is the part, not a word lifted out of warning text;
* an ERC error (shorted pin, contradictory flags, floating net) halts;
* a structural cross-check problem (unknown refdes, mis-sheeted zone)
  halts.

The gates run BEFORE bridge resolution, so a dummy bridge is enough --
the path returns at the gate without extracting any symbol.
"""

from __future__ import annotations

import json
from typing import Any

from eda_agent.design.orchestrator import execute_plan_via_canvas_from_json

_LIB = "/fake/lib.SchLib"


def _exec(plan: dict[str, Any]) -> dict[str, Any]:
    # A non-None bridge keeps us off the "no bridge" branch; the gates
    # short-circuit before it is ever used.
    return execute_plan_via_canvas_from_json(
        json.dumps(plan), "/tmp/x.PrjPcb", bridge=object()
    )


def _two_resistor_nets() -> list[dict[str, Any]]:
    # A clean, non-floating GND net across two parts (passes ERC).
    return [
        {"name": "N1", "pins": [
            {"refdes": "R1", "pin": "1"}, {"refdes": "R2", "pin": "1"}]},
        {"name": "GND", "is_ground": True, "pins": [
            {"refdes": "R1", "pin": "2"}, {"refdes": "R2", "pin": "2"}]},
    ]


def _base_parts() -> list[dict[str, Any]]:
    return [
        {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
         "status": "existing", "sheet": "main", "zone": "z"},
        {"refdes": "R2", "lib_ref": "RES", "lib_path": _LIB,
         "status": "existing", "sheet": "main", "zone": "z"},
    ]


def test_execute_halts_on_needs_creation() -> None:
    """An unresolved part halts the execute path -- no partial schematic."""
    parts = _base_parts() + [
        {"refdes": "U1", "lib_ref": "STM32G031F", "value": "STM32G031F",
         "status": "needs_creation", "sheet": "main", "zone": "z",
         "rationale": "no stm32 in library yet"},
    ]
    nets = _two_resistor_nets() + [
        # U1 to a free R1 pin -- avoids shorting an already-used pin so the
        # ERC gate passes and the needs_creation gate is the one that trips.
        {"name": "SIG", "pins": [
            {"refdes": "U1", "pin": "1"}, {"refdes": "R1", "pin": "3"}]},
    ]
    out = _exec({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "zones": [{"name": "z", "sheet": "main"}],
        "parts": parts, "nets": nets,
    })
    assert out["ok"] is False
    # Finding #3: the reported refdes is U1, never the literal "skipping".
    assert out["needs_creation"] == ["U1"]
    assert "skipping" not in out["needs_creation"]
    assert any("needs_creation" in n for n in out["notes"])


def test_execute_halts_on_shorted_pin() -> None:
    """A pin on two nets (ERC error) halts before emit."""
    nets = [
        {"name": "A", "pins": [
            {"refdes": "R1", "pin": "1"}, {"refdes": "R2", "pin": "1"}]},
        # R1.2 also appears on B -> shorted with the GND net below.
        {"name": "B", "pins": [
            {"refdes": "R1", "pin": "2"}, {"refdes": "R2", "pin": "2"}]},
        {"name": "GND", "is_ground": True, "pins": [
            {"refdes": "R1", "pin": "2"}, {"refdes": "R2", "pin": "3"}]},
    ]
    out = _exec({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "zones": [{"name": "z", "sheet": "main"}],
        "parts": _base_parts(), "nets": nets,
    })
    assert out["ok"] is False
    assert any("shorted_pin" in n for n in out["notes"])


def test_execute_halts_on_unknown_refdes() -> None:
    """cross_check: a net naming a part that does not exist halts."""
    nets = _two_resistor_nets() + [
        {"name": "SIG", "pins": [
            {"refdes": "R1", "pin": "1"}, {"refdes": "Q9", "pin": "1"}]},
    ]
    out = _exec({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "zones": [{"name": "z", "sheet": "main"}],
        "parts": _base_parts(), "nets": nets,
    })
    assert out["ok"] is False
    assert any("Q9" in n for n in out["notes"])


def test_execute_passes_clean_plan_to_bridge() -> None:
    """A clean plan clears the gates and proceeds far enough to need the
    bridge (which the dummy is not), proving the gate did not block it."""
    out = _exec({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "zones": [{"name": "z", "sheet": "main"}],
        "parts": _base_parts(), "nets": _two_resistor_nets(),
    })
    # The gates passed, so needs_creation stayed empty and no cross-check /
    # erc error note was raised. (It then fails later on the dummy bridge,
    # which is fine -- we are only asserting the gate let it through.)
    assert out["needs_creation"] == []
    assert not any("shorted_pin" in n or "not in sheets" in n
                   for n in out["notes"])
