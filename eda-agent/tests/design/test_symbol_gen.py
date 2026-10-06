# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for parametric IC symbol geometry (pure Python)."""

from __future__ import annotations

import pytest

from eda_agent.design.symbol_gen import (
    generate_ic_symbol, SymbolGeometry, PIN_LENGTH, PIN_PITCH,
)


def _pins(names, start=1):
    # designators are unique across the whole symbol (real IC pin numbers),
    # so the right side continues numbering after the left
    return [{"designator": str(i + start), "name": n}
            for i, n in enumerate(names)]


def test_top_left_pin_wire_end_at_origin():
    # Rule 14: top-left pin Location=(200,0), points left -> wire snaps at (0,0).
    g = generate_ic_symbol(_pins(["IN1", "IN2"]), _pins(["OUT"], start=3))
    left0 = g.pins[0]
    assert left0["x"] == PIN_LENGTH and left0["y"] == 0
    assert left0["rotation"] == 180             # points left (orientation 2)
    wire_x = left0["x"] - left0["length"]       # 200 - 200
    assert wire_x == 0


def test_pins_step_down_by_pitch():
    g = generate_ic_symbol(_pins(["A", "B", "C"]), [])
    ys = [p["y"] for p in g.pins]
    assert ys == [0, -PIN_PITCH, -2 * PIN_PITCH]


def test_right_pins_point_right_on_far_edge():
    g = generate_ic_symbol(_pins(["IN"]), _pins(["O1", "O2"], start=2))
    rights = [p for p in g.pins if p["rotation"] == 0]
    assert len(rights) == 2
    bx = g.body["x2"]
    assert all(p["x"] == bx for p in rights)     # right pins on the body's right edge
    assert rights[0]["y"] == 0 and rights[1]["y"] == -PIN_PITCH


def test_body_spans_between_pin_columns():
    g = generate_ic_symbol(_pins(["A"]), _pins(["B"], start=2))
    assert g.body["x1"] == PIN_LENGTH
    assert g.body["x2"] > g.body["x1"]
    assert g.body["y1"] > g.body["y2"]           # top above bottom


def test_everything_on_grid():
    g = generate_ic_symbol(_pins(["VREF", "FB"]), _pins(["SW", "BOOT"], start=3))
    for p in g.pins:
        assert p["x"] % 100 == 0 and p["y"] % 100 == 0
    for k in ("x1", "y1", "x2", "y2"):
        assert g.body[k] % 100 == 0


def test_wider_names_widen_body():
    narrow = generate_ic_symbol(_pins(["A"]), _pins(["B"], start=2))
    wide = generate_ic_symbol(_pins(["VERYLONGPINNAME"]),
                              _pins(["ANOTHERLONGNAME"], start=2))
    assert wide.body["x2"] > narrow.body["x2"]


def test_electrical_type_passed_through():
    g = generate_ic_symbol(
        [{"designator": "1", "name": "VCC", "electrical_type": "power"}],
        [{"designator": "2", "name": "OUT", "electrical_type": "output"}])
    types = {p["name"]: p["electrical_type"] for p in g.pins}
    assert types["VCC"] == "power" and types["OUT"] == "output"


def test_empty_rejected():
    with pytest.raises(ValueError):
        generate_ic_symbol([], [])


def test_duplicate_designator_rejected():
    # left "1" and right "1" -> same pin number twice (invalid component)
    with pytest.raises(ValueError, match="duplicate pin designator"):
        generate_ic_symbol([{"designator": "1", "name": "A"}],
                           [{"designator": "1", "name": "B"}])


def test_missing_designator_rejected():
    with pytest.raises(ValueError, match="non-empty 'designator'"):
        generate_ic_symbol([{"name": "A"}], [{"designator": "2", "name": "B"}])


def test_all_pins_present():
    g = generate_ic_symbol(_pins(["A", "B", "C", "D"]), _pins(["E", "F"], start=5))
    assert len(g.pins) == 6
    assert isinstance(g, SymbolGeometry)


def test_deterministic():
    a = generate_ic_symbol(_pins(["A", "B"]), _pins(["C"], start=3))
    b = generate_ic_symbol(_pins(["A", "B"]), _pins(["C"], start=3))
    assert a == b


# --- passive symbols --------------------------------------------------------

from eda_agent.design.symbol_gen import generate_passive_symbol, PassiveSymbol


def test_passive_two_pins_origin():
    p = generate_passive_symbol("resistor")
    assert isinstance(p, PassiveSymbol)
    assert len(p.pins) == 2
    # pin 1 wire-end at origin, pin 2 wire-end at +400
    p1, p2 = p.pins
    assert p1["x"] - p1["length"] == 0          # left pin (rot 180)
    assert p2["x"] + p2["length"] == 400        # right pin (rot 0)


def test_resistor_is_rectangle():
    p = generate_passive_symbol("r")
    assert len(p.rectangles) == 1 and not p.lines and not p.polygons


def test_capacitor_two_plates():
    p = generate_passive_symbol("c")
    # 2 plate lines (vertical) + 2 leads
    verts = [l for l in p.lines if l["x1"] == l["x2"]]
    assert len(verts) == 2
    assert not p.rectangles


def test_diode_has_triangle_and_bar():
    p = generate_passive_symbol("diode")
    assert len(p.polygons) == 1                 # triangle
    assert len(p.polygons[0]["points"]) == 3
    # a vertical cathode bar line exists
    assert any(l["x1"] == l["x2"] for l in p.lines)


def test_polarized_cap_has_plates_plus_marker():
    p = generate_passive_symbol("polarized_capacitor")
    # two full-height plates (vertical, 200 mils tall) like a plain cap
    plates = [l for l in p.lines
              if l["x1"] == l["x2"] and abs(l["y1"] - l["y2"]) == 200]
    assert len(plates) == 2
    # ...plus a '+' marker = 2 extra short lines over a plain capacitor
    c = generate_passive_symbol("capacitor")
    assert len(p.lines) == len(c.lines) + 2
    assert not p.rectangles
    # alias resolves
    assert generate_passive_symbol("cap_pol").lines


def test_led_is_diode_plus_emission_arrows():
    p = generate_passive_symbol("led")
    # diode triangle + cathode bar, like a diode...
    assert len(p.polygons) == 1 and len(p.polygons[0]["points"]) == 3
    # ...plus extra arrow lines a plain diode does not have
    d = generate_passive_symbol("diode")
    assert len(p.lines) > len(d.lines)
    # an emission arrow rises above the body (y > 100)
    assert any(l["y1"] > 100 or l["y2"] > 100 for l in p.lines)


def test_crystal_has_two_plates_and_resonator():
    p = generate_passive_symbol("crystal")
    plates = [l for l in p.lines if l["x1"] == l["x2"]]
    assert len(plates) == 2                        # two electrodes
    assert len(p.rectangles) == 1                  # resonator body between
    # alias
    assert generate_passive_symbol("xtal").rectangles


def test_fuse_is_rectangle_with_through_lead():
    p = generate_passive_symbol("fuse")
    assert len(p.rectangles) == 1
    # a horizontal lead line through the middle (y==0)
    assert any(l["y1"] == 0 and l["y2"] == 0 for l in p.lines)


def test_passive_unknown_rejected():
    with pytest.raises(ValueError):
        generate_passive_symbol("memristor")


def test_passive_aliases():
    for alias in ("resistor", "r", "res"):
        assert generate_passive_symbol(alias).rectangles
