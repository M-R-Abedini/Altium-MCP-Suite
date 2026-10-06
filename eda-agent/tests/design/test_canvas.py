# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for design.canvas: SchematicCanvas, SymbolInstance pin_world math.

Pin-world rotation is the core math the pipeline depends on; getting
the sign wrong on the rotation matrix would silently break every
schematic. These tests pin the convention down.
"""

from __future__ import annotations

import pytest

from eda_agent.design.canvas import (
    Junction,
    NetLabel,
    PowerPort,
    SchematicCanvas,
    Sheet,
    SymbolInstance,
    WireSegment,
)
from eda_agent.design.symbols import SymbolBBox, SymbolModel, SymbolPin


def _two_pin_horizontal() -> SymbolModel:
    """Horizontal 2-pin passive. Pin coords are the BODY-ATTACH ends
    (SchLib convention); the electrical end is body_attach + length *
    orientation_vector. With body x = -50..50 and length 100:
      - pin 1 body-attach at x=-50, orient=2 (left), elec at x=-150
      - pin 2 body-attach at x=50,  orient=0 (right), elec at x=150
    """
    return SymbolModel(
        lib_path="/x.SchLib", lib_ref="R10k",
        pins=(
            SymbolPin(designator="1", name="1", x=-50, y=0,
                      orientation=2, length=100,
                      electrical_type="passive"),
            SymbolPin(designator="2", name="2", x=50, y=0,
                      orientation=0, length=100,
                      electrical_type="passive"),
        ),
        body_bbox=SymbolBBox(x_min=-50, y_min=-30, x_max=50, y_max=30),
    )


def test_pin_world_rot_0():
    """Rotation 0: pin_world returns the electrical end in world coords.
    Pin 1 elec at local (-150, 0); pin 2 elec at local (150, 0)."""
    sym = _two_pin_horizontal()
    inst = SymbolInstance(refdes="R1", symbol=sym, x=1000, y=500, rotation=0)
    p1 = inst.pin_world("1")
    p2 = inst.pin_world("2")
    assert (p1.x, p1.y) == (850, 500)
    assert p1.orientation == 2  # left, unchanged
    assert (p2.x, p2.y) == (1150, 500)
    assert p2.orientation == 0


def test_pin_world_rot_90_ccw():
    """Rotation 90: CCW; (-150, 0) -> (0, -150); orientation rotates 2 -> 3."""
    sym = _two_pin_horizontal()
    inst = SymbolInstance(refdes="R1", symbol=sym, x=1000, y=500, rotation=90)
    p1 = inst.pin_world("1")
    p2 = inst.pin_world("2")
    # CCW rotation of (-150, 0) by 90 -> (0, -150).
    assert (p1.x, p1.y) == (1000, 350)
    # orientation 2 (left) + 1 (90 deg step) = 3 (down).
    assert p1.orientation == 3
    # CCW rotation of (150, 0) by 90 -> (0, 150).
    assert (p2.x, p2.y) == (1000, 650)
    assert p2.orientation == 1  # (0 + 1) mod 4


def test_pin_world_rot_180():
    sym = _two_pin_horizontal()
    inst = SymbolInstance(refdes="R1", symbol=sym, x=1000, y=500, rotation=180)
    p1 = inst.pin_world("1")
    p2 = inst.pin_world("2")
    # 180 rotates each pin to the opposite side.
    assert (p1.x, p1.y) == (1150, 500)
    assert p1.orientation == 0  # left -> right
    assert (p2.x, p2.y) == (850, 500)
    assert p2.orientation == 2  # right -> left


def test_pin_world_rot_270_ccw():
    sym = _two_pin_horizontal()
    inst = SymbolInstance(refdes="R1", symbol=sym, x=1000, y=500, rotation=270)
    p1 = inst.pin_world("1")
    p2 = inst.pin_world("2")
    # CCW 270 = CW 90: (-150, 0) -> (0, 150).
    assert (p1.x, p1.y) == (1000, 650)
    assert p1.orientation == 1
    assert (p2.x, p2.y) == (1000, 350)
    assert p2.orientation == 3


def test_pin_world_missing_returns_none():
    sym = _two_pin_horizontal()
    inst = SymbolInstance(refdes="R1", symbol=sym, x=0, y=0, rotation=0)
    assert inst.pin_world("99") is None


def test_world_bbox_rotates_with_instance():
    """A taller-than-wide body should become wider-than-tall after 90 rot."""
    sym = SymbolModel(
        lib_path="/x.SchLib", lib_ref="Tall",
        pins=(),
        body_bbox=SymbolBBox(x_min=-100, y_min=-300, x_max=100, y_max=300),
    )
    inst0 = SymbolInstance(refdes="U1", symbol=sym, x=0, y=0, rotation=0)
    inst90 = SymbolInstance(refdes="U2", symbol=sym, x=0, y=0, rotation=90)
    bb0 = inst0.world_bbox()
    bb90 = inst90.world_bbox()
    assert bb0.width == 200 and bb0.height == 600
    assert bb90.width == 600 and bb90.height == 200


def test_canvas_add_instance_rejects_duplicate_refdes():
    sym = _two_pin_horizontal()
    canvas = SchematicCanvas()
    canvas.add_sheet(Sheet(name="main"))
    canvas.add_instance(SymbolInstance(refdes="R1", symbol=sym, x=0, y=0, rotation=0))
    with pytest.raises(ValueError, match="R1"):
        canvas.add_instance(
            SymbolInstance(refdes="R1", symbol=sym, x=100, y=0, rotation=0)
        )


def test_canvas_pin_world_unknown_refdes_returns_none():
    canvas = SchematicCanvas()
    assert canvas.pin_world("R1", "1") is None


def test_canvas_per_sheet_filtering():
    """Wires/labels/ports/junctions belong to one sheet; queries filter."""
    sym = _two_pin_horizontal()
    canvas = SchematicCanvas()
    canvas.add_sheet(Sheet(name="main"))
    canvas.add_sheet(Sheet(name="aux"))
    canvas.add_instance(
        SymbolInstance(refdes="R1", symbol=sym, x=0, y=0, rotation=0, sheet="main")
    )
    canvas.add_instance(
        SymbolInstance(refdes="R2", symbol=sym, x=0, y=0, rotation=0, sheet="aux")
    )
    canvas.add_wires([
        WireSegment(x1=0, y1=0, x2=100, y2=0, sheet="main", net="N1"),
        WireSegment(x1=0, y1=0, x2=100, y2=0, sheet="aux", net="N2"),
    ])
    canvas.add_labels([NetLabel(text="A", x=0, y=0, orientation=0, sheet="main")])
    canvas.add_power_ports([PowerPort(text="VCC", x=0, y=0, style="bar", sheet="aux")])
    canvas.add_junctions([Junction(x=0, y=0, sheet="main")])

    assert {i.refdes for i in canvas.instances_on("main")} == {"R1"}
    assert {i.refdes for i in canvas.instances_on("aux")} == {"R2"}
    assert len(canvas.wires_on("main")) == 1
    assert len(canvas.wires_on("aux")) == 1
    assert canvas.labels_on("aux") == []
    assert canvas.power_ports_on("main") == []
    assert canvas.junctions_on("aux") == []


def test_canvas_to_dict_includes_lib_keys_not_full_symbols():
    """to_dict() should serialise symbols by (lib_path, lib_ref) reference,
    not inline -- a 50-pin IC shouldn't blow up the snapshot size."""
    sym = _two_pin_horizontal()
    canvas = SchematicCanvas()
    canvas.add_sheet(Sheet(name="main"))
    canvas.add_instance(SymbolInstance(refdes="R1", symbol=sym, x=0, y=0, rotation=0))
    d = canvas.to_dict()
    assert "pins" not in d["instances"][0]
    assert d["instances"][0]["lib_path"] == sym.lib_path
    assert d["instances"][0]["lib_ref"] == sym.lib_ref


def test_sheet_size_drives_dimensions():
    """The size string sets the drawing area: A4 keeps the inner default,
    larger sizes scale up (previously the size was decorative -> always A4)."""
    assert (Sheet(name="s", size="A4").width_mils,
            Sheet(name="s", size="A4").height_mils) == (11500, 7600)
    a3 = Sheet(name="s", size="A3")
    assert a3.width_mils > 11500 and a3.height_mils > 7600
    a2 = Sheet(name="s", size="A2")
    assert a2.width_mils > a3.width_mils and a2.height_mils > a3.height_mils


def test_sheet_explicit_dimensions_preserved():
    """An explicit, non-default width/height is not overwritten by size."""
    s = Sheet(name="s", size="A3", width_mils=9000, height_mils=6000)
    assert s.width_mils == 9000 and s.height_mils == 6000


def test_sheet_unknown_size_falls_back_to_a4():
    s = Sheet(name="s", size="weird")
    assert s.width_mils == 11500 and s.height_mils == 7600
