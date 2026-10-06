# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Regression tests for the routing-safety pieces of the pipeline.

These cover a class of failure where power-port spoke routing
produces a vertical wire that runs through another component's pins,
silently bridging two power nets. Three independent guards now exist:

1. ``_shift_centroid_clear_of_pins`` -- port centroid nudges off any
   column shared with a non-cluster pin.
2. ``_emit_port_cluster`` -- pin-point obstacles passed to the
   L-path router so spokes route around them.
3. ``_detect_routing_shorts`` -- pre-emit catches wires whose paths
   touch a pin on the wrong net.

The first two prevent the bug; the third stops emission if either
preventer misses something. These tests lock the contract.
"""

from __future__ import annotations

import pytest

from eda_agent.design.canvas import (
    PowerPort,
    SchematicCanvas,
    Sheet,
    SymbolInstance,
    WireSegment,
)
from eda_agent.design.pipeline import (
    PipelineNote,
    PipelineResult,
    _detect_routing_shorts,
    _point_on_segment,
    _shift_centroid_clear_of_pins,
)
from eda_agent.design.plan import DesignPlan
from eda_agent.design.symbols import SymbolBBox, SymbolModel, SymbolPin


_LIB = "/fake/lib.SchLib"


def _passive(lib_ref: str) -> SymbolModel:
    """Standard horizontal 2-pin passive (pin 1 left endpoint, pin 2 right)."""
    return SymbolModel(
        lib_path=_LIB, lib_ref=lib_ref,
        pins=(
            SymbolPin(designator="1", name="1", x=-100, y=0,
                      orientation=2, length=100, electrical_type="passive"),
            SymbolPin(designator="2", name="2", x=100, y=0,
                      orientation=0, length=100, electrical_type="passive"),
        ),
        body_bbox=SymbolBBox(x_min=-50, y_min=-30, x_max=50, y_max=30),
    )


# -------------------- _shift_centroid_clear_of_pins --------------------


def test_centroid_unchanged_when_column_free():
    """No forbidden columns -> centroid returned as-is."""
    assert _shift_centroid_clear_of_pins(2700, set()) == 2700


def test_centroid_shifts_off_forbidden_column():
    """When centroid x matches a forbidden pin column, nudge to nearest free."""
    out = _shift_centroid_clear_of_pins(2700, {2700})
    # Closest free columns are 2600 or 2800; both legal, but the function
    # prefers +delta first.
    assert out in (2600, 2800)
    assert out not in {2700}


def test_centroid_walks_past_multiple_forbidden_columns():
    """Forbidden range -> centroid hops to the first clear grid line outside."""
    forbidden = {2700, 2800, 2600, 2900, 2500}
    out = _shift_centroid_clear_of_pins(2700, forbidden)
    assert out not in forbidden


def test_centroid_falls_back_when_everything_taken():
    """Pathological case: every nearby column blocked -> fall back to input."""
    # Forbid everything within ±1000 of 2700, on the grid.
    forbidden = {2700 + 100 * i for i in range(-10, 11)}
    out = _shift_centroid_clear_of_pins(2700, forbidden, max_shift_mils=1000)
    # The function gives up and returns the original; shorts-detector
    # catches whatever lands later.
    assert out == 2700


# -------------------- _point_on_segment --------------------


def test_point_on_horizontal_segment():
    assert _point_on_segment(150, 100, 100, 100, 200, 100)
    # Endpoints count.
    assert _point_on_segment(100, 100, 100, 100, 200, 100)
    assert _point_on_segment(200, 100, 100, 100, 200, 100)
    # Off-axis points don't.
    assert not _point_on_segment(150, 99, 100, 100, 200, 100)
    # Outside the x range.
    assert not _point_on_segment(50, 100, 100, 100, 200, 100)


def test_point_on_vertical_segment():
    assert _point_on_segment(100, 150, 100, 100, 100, 200)
    assert not _point_on_segment(100, 50, 100, 100, 100, 200)


def test_point_off_diagonal_segment_returns_false():
    """Router only emits axis-aligned segments; diagonals shouldn't be a thing."""
    assert not _point_on_segment(150, 150, 100, 100, 200, 200)


# -------------------- _detect_routing_shorts --------------------


def _short_plan() -> DesignPlan:
    """Two-passive plan with two separate nets so we can test cross-net shorts."""
    return DesignPlan.model_validate({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": [
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
            {"refdes": "R2", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
            {"refdes": "R3", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
        ],
        "nets": [
            {"name": "NET_A", "pins": [
                {"refdes": "R1", "pin": "1"},
                {"refdes": "R2", "pin": "1"}]},
            {"name": "NET_B", "pins": [
                {"refdes": "R2", "pin": "2"},
                {"refdes": "R3", "pin": "2"}]},
        ],
    })


def test_shorts_detector_flags_wire_through_foreign_pin():
    """A wire on NET_A that passes through R3's pin (on NET_B) -> failure."""
    plan = _short_plan()
    canvas = SchematicCanvas()
    canvas.add_sheet(Sheet(name="main"))
    sym = _passive("RES")
    # Place R1, R2 along y=1000; place R3 at the SAME y so a horizontal
    # wire could pass through.
    canvas.add_instance(SymbolInstance(refdes="R1", symbol=sym, x=1000, y=1000, rotation=0))
    canvas.add_instance(SymbolInstance(refdes="R2", symbol=sym, x=3000, y=1000, rotation=0))
    canvas.add_instance(SymbolInstance(refdes="R3", symbol=sym, x=2000, y=1000, rotation=0))
    # R3.pin "2" world coord: x=2000+100=2100, y=1000.
    # NET_A wire from R1's right endpoint (900, 1000) to R2's left endpoint
    # (2900, 1000) -- this horizontal wire on y=1000 passes through (2100, 1000),
    # which is R3.pin 2 (on NET_B).
    canvas.add_wires([WireSegment(x1=900, y1=1000, x2=2900, y2=1000,
                                  sheet="main", net="NET_A")])
    result = PipelineResult(canvas=canvas)
    _detect_routing_shorts(plan, canvas, result)
    assert result.failures, "shorts detector should have fired"
    msg = " ".join(f.text for f in result.failures)
    assert "NET_A" in msg
    assert "NET_B" in msg
    assert "R3" in msg


def test_shorts_detector_quiet_when_wires_clean():
    """A wire that only touches its OWN net's pins should not fire.

    Layout: stack R1 above R2 on the same x. A vertical wire on x=900
    connects R1.1 (900, 1000) to R2.1 (900, 2000) -- both on NET_A.
    No other pin sits on x=900 between y=1000 and y=2000, so the
    detector must stay quiet.
    """
    plan = _short_plan()
    canvas = SchematicCanvas()
    canvas.add_sheet(Sheet(name="main"))
    sym = _passive("RES")
    canvas.add_instance(SymbolInstance(refdes="R1", symbol=sym, x=1000, y=1000, rotation=0))
    canvas.add_instance(SymbolInstance(refdes="R2", symbol=sym, x=1000, y=2000, rotation=0))
    canvas.add_instance(SymbolInstance(refdes="R3", symbol=sym, x=5000, y=5000, rotation=0))
    # Vertical NET_A wire from R1.1 to R2.1, along x=900.
    canvas.add_wires([WireSegment(x1=900, y1=1000, x2=900, y2=2000,
                                  sheet="main", net="NET_A")])
    result = PipelineResult(canvas=canvas)
    _detect_routing_shorts(plan, canvas, result)
    assert not result.failures, [f.text for f in result.failures]


def test_shorts_detector_endpoint_on_foreign_pin_flagged():
    """A wire whose ENDPOINT coincides with a foreign pin is a short.

    This is the most common Altium auto-merge failure mode: the wire
    terminates at the foreign pin's world coord, so the two nets share
    that point and Altium silently merges them.
    """
    plan = _short_plan()
    canvas = SchematicCanvas()
    canvas.add_sheet(Sheet(name="main"))
    sym = _passive("RES")
    canvas.add_instance(SymbolInstance(refdes="R1", symbol=sym, x=1000, y=1000, rotation=0))
    canvas.add_instance(SymbolInstance(refdes="R2", symbol=sym, x=3000, y=1000, rotation=0))
    canvas.add_instance(SymbolInstance(refdes="R3", symbol=sym, x=2000, y=1000, rotation=0))
    # R3.pin "1" world coord: x=2000-100=1900, y=1000.
    # NET_A wire terminating exactly at (1900, 1000).
    canvas.add_wires([WireSegment(x1=1100, y1=1000, x2=1900, y2=1000,
                                  sheet="main", net="NET_A")])
    result = PipelineResult(canvas=canvas)
    _detect_routing_shorts(plan, canvas, result)
    assert any("R3" in f.text for f in result.failures)


def test_shorts_detector_ignores_unnamed_wires():
    """A wire with empty net='' (rare but possible during refactors) is skipped."""
    plan = _short_plan()
    canvas = SchematicCanvas()
    canvas.add_sheet(Sheet(name="main"))
    sym = _passive("RES")
    canvas.add_instance(SymbolInstance(refdes="R1", symbol=sym, x=1000, y=1000, rotation=0))
    canvas.add_instance(SymbolInstance(refdes="R3", symbol=sym, x=2000, y=1000, rotation=0))
    canvas.add_wires([WireSegment(x1=900, y1=1000, x2=2100, y2=1000,
                                  sheet="main", net="")])
    result = PipelineResult(canvas=canvas)
    _detect_routing_shorts(plan, canvas, result)
    assert not result.failures


def test_detect_junctions_is_net_aware():
    """A junction dot is an electrical connection, so it must only land where
    SAME-net wires meet. Two different nets that cross (or one ending on the
    other) must NOT get a dot -- that would short them."""
    from eda_agent.design._wiring import _detect_junctions

    # Same net 'A': a horizontal trunk with a vertical stub ending on it (T).
    same_net = [
        (0, 0, 1000, 0, "A"),      # trunk
        (500, 0, 500, 400, "A"),   # stub ends on the trunk interior at (500,0)
    ]
    assert (500, 0) in _detect_junctions(same_net)

    # Cross net: net 'A' horizontal, net 'B' vertical crossing it at (500,0).
    # The crossing point must NOT be a junction.
    cross_net = [
        (0, 0, 1000, 0, "A"),
        (500, -400, 500, 400, "B"),   # B passes THROUGH (500,0)
    ]
    assert (500, 0) not in _detect_junctions(cross_net)

    # Cross net T: net 'B' stub ENDS on net 'A' trunk at (500,0) -> still no dot.
    cross_t = [
        (0, 0, 1000, 0, "A"),
        (500, 0, 500, 400, "B"),
    ]
    assert (500, 0) not in _detect_junctions(cross_t)

    # Backward compat: 4-tuples (no net) behave as one net (per-net callers).
    legacy = [(0, 0, 1000, 0), (500, 0, 500, 400)]
    assert (500, 0) in _detect_junctions(legacy)


def test_pipeline_places_no_cross_net_junction_dots():
    """End to end: across several dense single-sheet designs, every junction
    dot the pipeline emits touches wires of exactly ONE net (no false short)."""
    import random

    from eda_agent.design.pipeline import build_best_canvas_from_plan
    from eda_agent.design.plan import DesignPlan, Net, Part, PinRef, Sheet
    from eda_agent.design.symbols import (
        SymbolBBox, SymbolModel, SymbolPin,
    )

    lib = "/fake/lib.SchLib"

    def passive(ref):
        return SymbolModel(lib_path=lib, lib_ref=ref, pins=(
            SymbolPin(designator="1", name="1", x=-100, y=0, orientation=2,
                      length=100, electrical_type="passive"),
            SymbolPin(designator="2", name="2", x=100, y=0, orientation=0,
                      length=100, electrical_type="passive")),
            body_bbox=SymbolBBox(x_min=-50, y_min=-30, x_max=50, y_max=30))

    def ic(ref, n):
        pins = tuple(SymbolPin(
            designator=str(i + 1), name=f"P{i+1}",
            x=(-300 if i % 2 else 300), y=200 - (i // 2) * 100,
            orientation=(2 if i % 2 else 0), length=100,
            electrical_type="passive") for i in range(n))
        return SymbolModel(lib_path=lib, lib_ref=ref, pins=pins,
                           body_bbox=SymbolBBox(x_min=-200, y_min=-300,
                                                x_max=200, y_max=300))

    syms = {(lib, "RES"): passive("RES"), (lib, "IC"): ic("IC", 8)}

    class Ext:
        def extract_one(self, lp, lr):
            return syms.get((lp, lr))

        def extract_many(self, refs):
            return {k: syms[k] for k in refs if k in syms}

    def wires_at(cv, x, y):
        nets = set()
        for w in cv.wires:
            on = ((w.x1, w.y1) == (x, y) or (w.x2, w.y2) == (x, y)
                  or (w.x1 == w.x2 == x and min(w.y1, w.y2) < y < max(w.y1, w.y2))
                  or (w.y1 == w.y2 == y and min(w.x1, w.x2) < x < max(w.x1, w.x2)))
            if on:
                nets.add(w.net)
        return nets

    for seed in range(6):
        rnd = random.Random(seed)
        parts = [Part(refdes="U1", lib_ref="IC", lib_path=lib,
                      status="existing", sheet="main")]
        parts += [Part(refdes=f"R{i}", lib_ref="RES", lib_path=lib,
                       status="existing", sheet="main") for i in range(1, 7)]
        nets = [Net(name="GND", is_ground=True, pins=(
            [PinRef(refdes="U1", pin="2")]
            + [PinRef(refdes=f"R{i}", pin="2") for i in range(1, 7)]))]
        for i in range(1, 7):
            nets.append(Net(name=f"S{i}", pins=(
                PinRef(refdes="U1", pin=str(rnd.randint(3, 8))),
                PinRef(refdes=f"R{i}", pin="1"))))
        plan = DesignPlan(spec="x", summary="x",
                          sheets=[Sheet(name="main", size="A4")],
                          parts=parts, nets=nets)
        res = build_best_canvas_from_plan(plan, Ext(), n_tries=3,
                                          strict_shorts=False)
        for j in res.canvas.junctions:
            touched = wires_at(res.canvas, j.x, j.y)
            assert len(touched) <= 1, (
                f"seed {seed}: junction ({j.x},{j.y}) bridges nets {touched}")


def test_cross_net_meeting_counts_helper():
    """Detects endpoint coincidence + T-ending across DIFFERENT nets, ignores
    pure crossings and same-net meetings."""
    from eda_agent.design._wiring import _cross_net_meeting_counts

    # Cross-net endpoint coincidence at (500,0).
    assert _cross_net_meeting_counts([
        (0, 0, 500, 0, "A"), (500, 0, 500, 400, "B")]) == {"A": 1, "B": 1}

    # Cross-net T: B endpoint on A's segment interior.
    assert _cross_net_meeting_counts([
        (0, 0, 1000, 0, "A"), (500, 0, 500, 400, "B")]).get("A", 0) >= 1

    # Pure crossing (B passes THROUGH, no endpoint at the cross) -> safe, 0.
    assert _cross_net_meeting_counts([
        (0, 0, 1000, 0, "A"), (500, -400, 500, 400, "B")]) == {}

    # Same-net T -> not a cross-net meeting.
    assert _cross_net_meeting_counts([
        (0, 0, 1000, 0, "A"), (500, 0, 500, 400, "A")]) == {}


def test_pipeline_has_no_cross_net_wire_meetings():
    """End to end: across dense designs the emitted wires contain NO cross-net
    meeting (endpoint coincidence or T) -- offenders fall back to labels, so
    Altium's auto-junction-on-compile can't silently short two nets."""
    import random

    from eda_agent.design._wiring import _cross_net_meeting_counts
    from eda_agent.design.pipeline import build_best_canvas_from_plan
    from eda_agent.design.plan import DesignPlan, Net, Part, PinRef, Sheet
    from eda_agent.design.symbols import (
        SymbolBBox, SymbolModel, SymbolPin,
    )

    lib = "/fake/lib.SchLib"

    def passive(ref):
        return SymbolModel(lib_path=lib, lib_ref=ref, pins=(
            SymbolPin(designator="1", name="1", x=-100, y=0, orientation=2,
                      length=100, electrical_type="passive"),
            SymbolPin(designator="2", name="2", x=100, y=0, orientation=0,
                      length=100, electrical_type="passive")),
            body_bbox=SymbolBBox(x_min=-50, y_min=-30, x_max=50, y_max=30))

    def ic(ref, n):
        pins = tuple(SymbolPin(
            designator=str(i + 1), name=f"P{i+1}",
            x=(-300 if i % 2 else 300), y=200 - (i // 2) * 100,
            orientation=(2 if i % 2 else 0), length=100,
            electrical_type="passive") for i in range(n))
        return SymbolModel(lib_path=lib, lib_ref=ref, pins=pins,
                           body_bbox=SymbolBBox(x_min=-200, y_min=-300,
                                                x_max=200, y_max=300))

    syms = {(lib, "RES"): passive("RES"), (lib, "IC"): ic("IC", 8)}

    class Ext:
        def extract_one(self, lp, lr):
            return syms.get((lp, lr))

        def extract_many(self, refs):
            return {k: syms[k] for k in refs if k in syms}

    for seed in range(8):
        rnd = random.Random(seed)
        parts = [Part(refdes="U1", lib_ref="IC", lib_path=lib,
                      status="existing", sheet="main")]
        parts += [Part(refdes=f"R{i}", lib_ref="RES", lib_path=lib,
                       status="existing", sheet="main") for i in range(1, 7)]
        nets = [Net(name="GND", is_ground=True, pins=(
            [PinRef(refdes="U1", pin="2")]
            + [PinRef(refdes=f"R{i}", pin="2") for i in range(1, 7)]))]
        for i in range(1, 7):
            nets.append(Net(name=f"S{i}", pins=(
                PinRef(refdes="U1", pin=str(rnd.randint(3, 8))),
                PinRef(refdes=f"R{i}", pin="1"))))
        plan = DesignPlan(spec="x", summary="x",
                          sheets=[Sheet(name="main", size="A4")],
                          parts=parts, nets=nets)
        res = build_best_canvas_from_plan(plan, Ext(), n_tries=3,
                                          strict_shorts=False)
        segs = [(w.x1, w.y1, w.x2, w.y2, w.net) for w in res.canvas.wires]
        assert _cross_net_meeting_counts(segs) == {}, f"seed {seed}"


def test_detect_routing_short_flags_label_on_foreign_wire():
    """A net label sitting on a DIFFERENT net's wire merges the two nets in
    Altium -- _detect_routing_shorts must flag it (the wire-vs-pin check alone
    would miss this wire-vs-label short)."""
    from eda_agent.design.canvas import NetLabel

    plan = DesignPlan.model_validate({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": [
            {"refdes": "R1", "lib_ref": "RES", "lib_path": "/fake/l.SchLib",
             "status": "existing", "sheet": "main"},
            {"refdes": "R2", "lib_ref": "RES", "lib_path": "/fake/l.SchLib",
             "status": "existing", "sheet": "main"},
        ],
        "nets": [
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "R1", "pin": "2"}, {"refdes": "R2", "pin": "2"}]},
            {"name": "S1", "pins": [
                {"refdes": "R1", "pin": "1"}, {"refdes": "R2", "pin": "1"}]},
        ],
    })
    canvas = SchematicCanvas()
    canvas.add_sheet(Sheet(name="main", size="A4"))
    # A GND wire, and an S1 label sitting on its interior -> short.
    canvas.add_wires([WireSegment(x1=0, y1=0, x2=1000, y2=0,
                                  sheet="main", net="GND")])
    canvas.add_labels([NetLabel(text="S1", x=500, y=0, orientation=0,
                                sheet="main")])
    result = PipelineResult(canvas=canvas)
    _detect_routing_shorts(plan, canvas, result)
    assert any("net label" in f.text and "S1" in f.text
               for f in result.failures)

    # A same-net label on its own wire is fine (no false positive).
    ok_canvas = SchematicCanvas()
    ok_canvas.add_sheet(Sheet(name="main", size="A4"))
    ok_canvas.add_wires([WireSegment(x1=0, y1=0, x2=1000, y2=0,
                                     sheet="main", net="GND")])
    ok_canvas.add_labels([NetLabel(text="GND", x=500, y=0, orientation=0,
                                   sheet="main")])
    ok_result = PipelineResult(canvas=ok_canvas)
    _detect_routing_shorts(plan, ok_canvas, ok_result)
    assert not any("net label" in f.text for f in ok_result.failures)
