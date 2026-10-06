# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The design-engine illustrators must emit valid, non-trivial PNGs."""

from __future__ import annotations

from eda_agent.design import illustrate as ill
from eda_agent.design.plan import (
    DesignPlan, Net, Part, PinRef, Sheet, Zone,
)
from eda_agent.design.schematic_layout import compute_schematic_layout
from eda_agent.design.pcb_placement import (
    ConstructOptions,
    DesignRules,
    construct_placement,
)
from eda_agent.placement import PlaceComp, PlaceNet, PlacePin


def _plan() -> DesignPlan:
    return DesignPlan(
        spec="illustrate", summary="tiny block", topology="generic",
        sheets=[Sheet(name="main")],
        zones=[Zone(name="blk", sheet="main", role="mcu")],
        parts=[
            Part(refdes="U1", lib_ref="IC", zone="blk"),
            Part(refdes="R1", lib_ref="RES", zone="blk"),
            Part(refdes="R2", lib_ref="RES", zone="blk"),
        ],
        nets=[
            Net(name="SIG", role="signal", pins=[
                PinRef(refdes="U1", pin="1"),
                PinRef(refdes="R1", pin="1"),
                PinRef(refdes="R2", pin="1"),
            ]),
            Net(name="GND", is_ground=True, pins=[
                PinRef(refdes="U1", pin="2"),
                PinRef(refdes="R1", pin="2"),
                PinRef(refdes="R2", pin="2"),
            ]),
        ],
    )


def _board():
    u1 = PlaceComp(ref="U1", w=200, h=200, cx=0, cy=0, rotatable=True,
                   pins=(PlacePin(-90, 0, "A"), PlacePin(90, 0, "B")))
    r1 = PlaceComp(ref="R1", w=40, h=40, cx=600, cy=600, rotatable=True,
                   pins=(PlacePin(-20, 0, "A"), PlacePin(20, 0, "C")))
    r2 = PlaceComp(ref="R2", w=40, h=40, cx=-600, cy=600, rotatable=True,
                   pins=(PlacePin(-20, 0, "B"), PlacePin(20, 0, "C")))
    comps = [u1, r1, r2]
    nets = [PlaceNet(refs=("U1", "R1"), name="A"),
            PlaceNet(refs=("U1", "R2"), name="B"),
            PlaceNet(refs=("R1", "R2"), name="C")]
    return comps, nets


def _valid_png(path) -> tuple[int, int]:
    from PIL import Image
    assert path.exists() and path.stat().st_size > 1000
    with Image.open(path) as im:
        im.verify()
    with Image.open(path) as im:
        return im.size


def test_schematic_png_renders(tmp_path):
    layout = compute_schematic_layout(_plan())
    out = tmp_path / "sch.png"
    ill.schematic_png(layout, str(out), title="tiny block")
    w, h = _valid_png(out)
    assert w > 200 and h > 200


def test_placement_png_renders(tmp_path):
    comps, nets = _board()
    res = construct_placement(comps, nets, DesignRules(layers=2),
                              ConstructOptions(seed=3))
    pos = {r: (c[0], c[1]) for r, c in res.centroids.items()}
    out = tmp_path / "pcb.png"
    ill.placement_png(comps, pos, res.region, nets, str(out),
                      title="tiny board", rotations=res.rotations,
                      sides=res.sides, report=res.report)
    w, h = _valid_png(out)
    assert w > 200 and h > 200


def test_placement_png_handles_missing_report(tmp_path):
    comps, nets = _board()
    res = construct_placement(comps, nets, DesignRules(layers=2),
                              ConstructOptions(seed=1))
    pos = {r: (c[0], c[1]) for r, c in res.centroids.items()}
    out = tmp_path / "pcb_noreport.png"
    # report=None must still render (title without the metric line).
    ill.placement_png(comps, pos, res.region, nets, str(out))
    _valid_png(out)


def _canvas():
    from eda_agent.design.canvas import (
        SchematicCanvas, SymbolInstance, WireSegment, NetLabel, PowerPort,
        Junction,
    )
    from eda_agent.design.symbols import SymbolModel, SymbolPin, SymbolBBox

    sym = SymbolModel(
        lib_path="L", lib_ref="RES",
        pins=(SymbolPin(designator="1", name="1", x=-100, y=0, orientation=2,
                        length=100, electrical_type="passive"),
              SymbolPin(designator="2", name="2", x=100, y=0, orientation=0,
                        length=100, electrical_type="passive")),
        body_bbox=SymbolBBox(x_min=-50, y_min=-30, x_max=50, y_max=30))
    cv = SchematicCanvas()
    cv.add_instance(SymbolInstance(refdes="R1", symbol=sym, x=0, y=0, rotation=0))
    cv.add_instance(SymbolInstance(refdes="R2", symbol=sym, x=600, y=0, rotation=0))
    cv.add_wires([WireSegment(x1=100, y1=0, x2=500, y2=0)])
    cv.add_labels([NetLabel(text="SIG", x=300, y=0, orientation=0)])
    cv.add_power_ports([PowerPort(text="GND", x=300, y=-200, style="gnd_power")])
    cv.add_junctions([Junction(x=300, y=0)])
    return cv


def test_canvas_png_renders(tmp_path):
    cv = _canvas()
    out = tmp_path / "canvas.png"
    ill.canvas_png(cv, str(out), title="canvas render")
    w, h = _valid_png(out)
    assert w > 200 and h > 200


def test_canvas_png_empty_canvas(tmp_path):
    from eda_agent.design.canvas import SchematicCanvas
    out = tmp_path / "empty.png"
    # No instances -> defaults to sheet "main", still writes a valid PNG.
    ill.canvas_png(SchematicCanvas(), str(out))
    assert out.exists() and out.stat().st_size > 500
