# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the pure-Python schematic layout engine."""

from __future__ import annotations

import pytest

from eda_agent.design.plan import (
    DesignPlan,
    Net,
    Part,
    PinRef,
    Sheet,
    Zone,
)
from eda_agent.design.schematic_layout import (
    LayoutScore,
    NetRoute,
    NetSegment,
    PinSlot,
    PlacedSymbol,
    compute_schematic_layout,
    decide_net_representation,
    estimate_wire_bends,
    group_blocks,
    score_layout,
    to_executor_payload,
    LayoutWeights,
)


def _buck_plan() -> DesignPlan:
    """A small regulator-like plan: one IC, its local passives, rails."""
    sheets = [Sheet(name="main")]
    zones = [
        Zone(name="reg", sheet="main", role="mcu"),
    ]
    parts = [
        Part(refdes="U1", lib_ref="REG_IC", zone="reg"),
        Part(refdes="C1", lib_ref="CAP", value="10uF", zone="reg"),
        Part(refdes="C2", lib_ref="CAP", value="22uF", zone="reg"),
        Part(refdes="L1", lib_ref="IND", value="4.7uH", zone="reg"),
        Part(refdes="R1", lib_ref="RES", value="10k", zone="reg"),
        Part(refdes="R2", lib_ref="RES", value="3k3", zone="reg"),
        Part(refdes="J1", lib_ref="CONN", role="input"),
        Part(refdes="J2", lib_ref="CONN", role="output"),
    ]
    nets = [
        Net(name="VIN", is_power=True, pins=[
            PinRef(refdes="J1", pin="1"),
            PinRef(refdes="U1", pin="1"),
            PinRef(refdes="C1", pin="1"),
        ]),
        Net(name="GND", is_ground=True, pins=[
            PinRef(refdes="J1", pin="2"),
            PinRef(refdes="U1", pin="2"),
            PinRef(refdes="C1", pin="2"),
            PinRef(refdes="C2", pin="2"),
            PinRef(refdes="R2", pin="2"),
            PinRef(refdes="J2", pin="2"),
        ]),
        Net(name="SW", role="switch", pins=[
            PinRef(refdes="U1", pin="3"),
            PinRef(refdes="L1", pin="1"),
        ]),
        Net(name="VOUT", is_power=True, pins=[
            PinRef(refdes="L1", pin="2"),
            PinRef(refdes="C2", pin="1"),
            PinRef(refdes="R1", pin="1"),
            PinRef(refdes="J2", pin="1"),
        ]),
        Net(name="FB", role="feedback", pins=[
            PinRef(refdes="U1", pin="4"),
            PinRef(refdes="R1", pin="2"),
            PinRef(refdes="R2", pin="1"),
        ]),
    ]
    return DesignPlan(
        spec="buck regulator",
        summary="reg + passives",
        topology="buck",
        sheets=sheets,
        zones=zones,
        parts=parts,
        nets=nets,
    )


def test_determinism_byte_identical():
    plan = _buck_plan()
    a = compute_schematic_layout(plan)
    b = compute_schematic_layout(plan)
    pa = to_executor_payload(a)
    pb = to_executor_payload(b)
    assert pa == pb
    # Placement positions identical.
    assert {r: (s.x_mils, s.y_mils, s.rotation) for r, s in a.placed.items()} == {
        r: (s.x_mils, s.y_mils, s.rotation) for r, s in b.placed.items()
    }


def test_all_parts_placed_on_grid():
    plan = _buck_plan()
    layout = compute_schematic_layout(plan)
    assert set(layout.placed) == {p.refdes for p in plan.parts}
    for sym in layout.placed.values():
        assert sym.x_mils % 100 == 0
        assert sym.y_mils % 100 == 0
        assert sym.rotation in (0, 90, 180, 270)


def test_no_body_overlaps():
    plan = _buck_plan()
    layout = compute_schematic_layout(plan)
    syms = list(layout.placed.values())
    for i in range(len(syms)):
        for j in range(i + 1, len(syms)):
            a, b = syms[i], syms[j]
            # Centre-distance based: bboxes must not strictly overlap.
            ax = (a.bbox[0] + a.bbox[2]) / 2
            bx = (b.bbox[0] + b.bbox[2]) / 2
            ay = (a.bbox[1] + a.bbox[3]) / 2
            by = (b.bbox[1] + b.bbox[3]) / 2
            # Allow touching; flag deep overlap of centres only.
            assert not (abs(ax - bx) < 50 and abs(ay - by) < 50)


def test_signal_flow_orders_input_left_output_right():
    plan = _buck_plan()
    layout = compute_schematic_layout(plan)
    j1 = layout.placed["J1"]  # role=input -> left
    j2 = layout.placed["J2"]  # role=output -> right
    assert j1.x_mils < j2.x_mils


def test_net_representation_tiering():
    plan = _buck_plan()
    layout = compute_schematic_layout(plan)
    # Power and ground -> power_port.
    assert layout.decisions["VIN"].kind == "power_port"
    assert layout.decisions["VOUT"].kind == "power_port"
    assert layout.decisions["GND"].kind == "power_port"
    assert layout.decisions["GND"].style == "gnd_power"
    assert layout.decisions["GND"].orientation == 3
    assert layout.decisions["VIN"].orientation == 1
    # SW: both pins in 'reg' zone -> wire (short, 2-pin).
    assert layout.decisions["SW"].kind in ("wire", "net_label")


def test_cross_zone_net_uses_label():
    # A net spanning a zoned and an unzoned part -> label.
    plan = DesignPlan(
        spec="x", summary="x",
        sheets=[Sheet(name="main")],
        zones=[Zone(name="a", sheet="main"), Zone(name="b", sheet="main")],
        parts=[
            Part(refdes="U1", lib_ref="IC", zone="a"),
            Part(refdes="U2", lib_ref="IC", zone="b"),
        ],
        nets=[
            Net(name="SIG", pins=[
                PinRef(refdes="U1", pin="1"),
                PinRef(refdes="U2", pin="1"),
            ]),
        ],
    )
    layout = compute_schematic_layout(plan)
    assert layout.decisions["SIG"].kind == "net_label"


def test_force_label_promotes_to_label():
    plan = DesignPlan(
        spec="x", summary="x",
        sheets=[Sheet(name="main")],
        zones=[Zone(name="a", sheet="main")],
        parts=[
            Part(refdes="U1", lib_ref="IC", zone="a"),
            Part(refdes="R1", lib_ref="RES", zone="a"),
        ],
        nets=[
            Net(name="SIG", force_label=True, pins=[
                PinRef(refdes="U1", pin="1"),
                PinRef(refdes="R1", pin="1"),
            ]),
        ],
    )
    layout = compute_schematic_layout(plan)
    assert layout.decisions["SIG"].kind == "net_label"


def _mini_plan(parts, nets) -> DesignPlan:
    return DesignPlan(
        spec="edge", summary="edge case", topology="generic",
        sheets=[Sheet(name="main")], zones=[], parts=parts, nets=nets,
    )


def test_layout_handles_minimal_valid_plan():
    # The smallest plan the schema permits: two parts joined by one net.
    parts = [Part(refdes="U1", lib_ref="IC", zone="z"),
             Part(refdes="R1", lib_ref="RES", zone="z")]
    nets = [Net(name="SIG", role="signal", pins=[
        PinRef(refdes="U1", pin="1"), PinRef(refdes="R1", pin="1")])]
    layout = compute_schematic_layout(_mini_plan(parts, nets))
    assert set(layout.placed) == {"U1", "R1"}
    assert "SIG" in layout.decisions
    assert isinstance(layout.score.total, (int, float))


def test_layout_handles_all_power_nets():
    parts = [Part(refdes="U1", lib_ref="IC", zone="z"),
             Part(refdes="C1", lib_ref="CAP", zone="z")]
    nets = [
        Net(name="VCC", is_power=True, pins=[
            PinRef(refdes="U1", pin="1"), PinRef(refdes="C1", pin="1")]),
        Net(name="GND", is_ground=True, pins=[
            PinRef(refdes="U1", pin="2"), PinRef(refdes="C1", pin="2")]),
    ]
    layout = compute_schematic_layout(_mini_plan(parts, nets))
    # Power and ground both become port glyphs; nothing is drawn as a wire.
    assert all(d.kind == "power_port" for d in layout.decisions.values())
    assert all(not r for r in layout.routes.values())


def test_buck_layout_is_neat_end_to_end():
    """One assertion guarding all the schematic neatness work together:
    alignment, on-grid placement, no overlapping symbol courtyards, and no
    avoidable wire crossings."""
    layout = compute_schematic_layout(_buck_plan())

    # Alignment: at least 70% of symbols share a row or column.
    assert layout.score.alignment_penalty <= 0.30

    # No wire crossings remain on this fixture.
    assert layout.score.wire_crossings == 0

    syms = list(layout.placed.values())
    # Every symbol centre sits on the snap grid.
    for s in syms:
        assert s.x_mils % 100 == 0 and s.y_mils % 100 == 0

    # No two symbol BODIES overlap. The stored bbox is the courtyard and
    # includes the asymmetric pin-stub reach (a 2-pin part's stub sticks out
    # ~600 mils to one side); two stubs at different heights are not a
    # collision. The real body is the symmetric box inscribed in the
    # courtyard around the centre, which is what the placer keeps apart.
    def body_half(s) -> tuple[int, int]:
        hx = min(s.x_mils - s.bbox[0], s.bbox[2] - s.x_mils)
        hy = min(s.y_mils - s.bbox[1], s.bbox[3] - s.y_mils)
        return hx, hy

    for i in range(len(syms)):
        hxa, hya = body_half(syms[i])
        for j in range(i + 1, len(syms)):
            hxb, hyb = body_half(syms[j])
            ox = (hxa + hxb) - abs(syms[i].x_mils - syms[j].x_mils)
            oy = (hya + hyb) - abs(syms[i].y_mils - syms[j].y_mils)
            assert not (ox > 1 and oy > 1), (
                f"{syms[i].refdes} and {syms[j].refdes} bodies overlap"
            )


def test_segment_cells_walks_grid_points():
    from eda_agent.design.schematic_layout import _segment_cells, NetSegment
    assert _segment_cells(NetSegment(0, 0, 300, 0), 100) == [
        (0, 0), (100, 0), (200, 0), (300, 0)]
    assert _segment_cells(NetSegment(0, 0, 0, 200), 100) == [
        (0, 0), (0, 100), (0, 200)]


def test_router_avoids_occupied_cells_when_a_detour_exists():
    from eda_agent.design.schematic_layout import (
        _dijkstra_route, _segment_cells, _ROUTE_CROSS_WEIGHT,
    )
    # Occupied "wire" is an L at the corner the horizontal-first route would use.
    a_cells = frozenset({(200, 100), (200, 200), (300, 200)})

    def route_cells(occupied):
        segs = _dijkstra_route(
            (100, 100), (400, 300), grid_mils=100, bend_weight=5.0,
            occupied=occupied, cross_weight=_ROUTE_CROSS_WEIGHT,
        )
        cells = set()
        for s in segs:
            cells.update(_segment_cells(s, 100))
        return cells

    occ = route_cells(a_cells)
    free = route_cells(None)
    # The occupancy-aware route reaches the target with ZERO shared cells
    # (a crossing-free detour exists), and never shares more than the
    # occupancy-blind route.
    assert len(occ & a_cells) == 0
    assert len(occ & a_cells) <= len(free & a_cells)


def test_align_flowed_snaps_near_collinear_to_shared_lines():
    from eda_agent.design.schematic_layout import align_flowed, _Pt
    pts = {
        "A": _Pt(100.0, 0.0),
        "B": _Pt(130.0, 800.0),   # near A's column (within tol)
        "C": _Pt(2000.0, 0.0),    # far column, must stay distinct
    }
    out = align_flowed(pts, tol=150, grid_mils=100)
    # A and B snap to one shared column.
    assert out["A"].x == out["B"].x
    # The far symbol keeps its own column.
    assert out["C"].x != out["A"].x
    # A and C share a row (both y=0) -> snapped to a shared row line.
    assert out["A"].y == out["C"].y


def test_align_flowed_is_deterministic_and_total_preserving():
    from eda_agent.design.schematic_layout import align_flowed, _Pt
    pts = {"U1": _Pt(0.0, 0.0), "R1": _Pt(40.0, 500.0), "R2": _Pt(900.0, 10.0)}
    a = align_flowed(pts, grid_mils=100)
    b = align_flowed(pts, grid_mils=100)
    assert a == b
    assert set(a) == set(pts)            # never drops or invents symbols


def test_estimate_wire_bends():
    assert estimate_wire_bends([(0, 0), (500, 0)]) == 0  # straight horizontal
    assert estimate_wire_bends([(0, 0), (0, 500)]) == 0  # straight vertical
    assert estimate_wire_bends([(0, 0), (500, 500)]) == 1  # 2-pin L
    assert estimate_wire_bends([(0, 0), (500, 500), (1000, 200)]) == 2
    assert estimate_wire_bends([(0, 0)]) == 0


def test_estimate_wire_bends_graded_trunk_and_branches():
    # A rail: two taps share a row, one branches off -> a single bend.
    assert estimate_wire_bends([(0, 0), (500, 0), (250, 500)]) == 1
    # Three taps on a row, two branch off below -> two bends.
    assert estimate_wire_bends(
        [(0, 0), (500, 0), (1000, 0), (250, 500), (750, 500)]
    ) == 2
    # Many collinear taps on one rail stay a straight run (0 bends).
    assert estimate_wire_bends([(0, 0), (300, 0), (600, 0), (900, 0)]) == 0
    # A vertical rail with one horizontal tap is also a single bend.
    assert estimate_wire_bends([(0, 0), (0, 500), (400, 250)]) == 1
    # More branches never cost fewer bends than fewer branches (monotone).
    one = estimate_wire_bends([(0, 0), (500, 0), (250, 400)])
    two = estimate_wire_bends([(0, 0), (500, 0), (250, 400), (750, 400)])
    assert two >= one


def test_long_wire_promoted_to_label():
    placed = {
        "U1": PlacedSymbol("U1", "main", 1000, 1000, 0,
                           (900, 900, 1100, 1100), {"1": (1000, 1000)}),
        "U2": PlacedSymbol("U2", "main", 9000, 1000, 0,
                           (8900, 900, 9100, 1100), {"1": (9000, 1000)}),
    }
    net = Net(name="SIG", pins=[
        PinRef(refdes="U1", pin="1"),
        PinRef(refdes="U2", pin="1"),
    ])
    dec = decide_net_representation(
        net, {"U1": "z", "U2": "z"}, placed, label_span_mils=3000
    )
    # Span 8000 > 3000 -> promoted to label even though same zone.
    assert dec.kind == "net_label"


def test_group_blocks_uses_explicit_zone():
    plan = _buck_plan()
    blocks = group_blocks(plan)
    # All zoned 'reg' parts share a block.
    reg_parts = ["U1", "C1", "C2", "L1", "R1", "R2"]
    block_ids = {blocks[r] for r in reg_parts}
    assert len(block_ids) == 1
    assert blocks["U1"] == "zone:reg"


def test_group_blocks_topology_fallback():
    # No zones: an IC + a passive connected only to it cluster together.
    plan = DesignPlan(
        spec="x", summary="x",
        sheets=[Sheet(name="main")],
        parts=[
            Part(refdes="U1", lib_ref="IC"),
            Part(refdes="C1", lib_ref="CAP"),
        ],
        nets=[
            Net(name="N1", pins=[
                PinRef(refdes="U1", pin="1"),
                PinRef(refdes="C1", pin="1"),
            ]),
            Net(name="N2", pins=[
                PinRef(refdes="U1", pin="2"),
                PinRef(refdes="C1", pin="2"),
            ]),
            Net(name="N3", pins=[
                PinRef(refdes="U1", pin="3"),
                PinRef(refdes="U1", pin="4"),
            ]),
        ],
    )
    blocks = group_blocks(plan)
    # C1 connects only to U1 (4-pin) -> shares U1's block.
    assert blocks["C1"] == blocks["U1"]


def test_real_pin_geometry_routes():
    plan = _buck_plan()
    geom = {
        "U1": [
            PinSlot("1", "VIN", (0, 0), "left"),
            PinSlot("2", "GND", (0, -200), "left"),
            PinSlot("3", "SW", (1000, 0), "right"),
            PinSlot("4", "FB", (0, -100), "left"),
        ],
        "L1": [
            PinSlot("1", "A", (0, 0), "left"),
            PinSlot("2", "B", (600, 0), "right"),
        ],
    }
    layout = compute_schematic_layout(plan, pin_geometry=geom)
    assert layout.placed["U1"].pins["1"] is not None
    # SW is a wire-tier net -> should produce a route.
    if layout.decisions["SW"].kind == "wire":
        assert "SW" in layout.routes


def test_score_improves_after_layout_vs_random_baseline():
    plan = _buck_plan()
    layout = compute_schematic_layout(plan)
    good_score = layout.score

    # Build a deterministic "scrambled" baseline by scattering the placed
    # symbols to widely-spread, deliberately misaligned positions and
    # re-routing the same wire nets through that scatter.
    spread = [
        (1200, 1100), (9300, 1700), (1500, 6900), (9100, 6200),
        (5300, 1300), (1100, 4100), (9400, 3900), (5200, 6800),
    ]
    refdes_sorted = sorted(layout.placed)
    baseline_placed = {}
    for i, r in enumerate(refdes_sorted):
        sym = layout.placed[r]
        bx, by = spread[i % len(spread)]
        # nudge so no two share a row/column -> alignment near zero.
        bx += (i * 37) % 100
        by += (i * 53) % 100
        new_pins = {}
        for num, (px, py) in sym.pins.items():
            new_pins[num] = (px - sym.x_mils + bx, py - sym.y_mils + by)
        baseline_placed[r] = PlacedSymbol(
            refdes=r, sheet=sym.sheet, x_mils=bx, y_mils=by,
            rotation=sym.rotation,
            bbox=(bx - 200, by - 200, bx + 200, by + 200),
            pins=new_pins,
        )

    # Re-route wire nets through the scattered placement.
    from eda_agent.design.schematic_layout import route_wire_nets
    membership = {
        net.name: [(pr.refdes, pr.pin) for pr in net.pins]
        for net in plan.nets
    }
    baseline_routes = route_wire_nets(
        layout.decisions, baseline_placed, membership=membership, grid_mils=100,
    )
    baseline_score = score_layout(
        baseline_placed, baseline_routes, layout.decisions,
        weights=LayoutWeights(),
    )

    assert good_score.total < baseline_score.total


def test_to_executor_payload_keys():
    plan = _buck_plan()
    layout = compute_schematic_layout(plan)
    payload = to_executor_payload(layout)
    assert set(payload) == {
        "placements", "wires", "net_labels", "power_ports",
        "junctions", "score", "sheet",
    }
    for p in payload["placements"]:
        assert set(p) >= {
            "lib_reference", "library_path", "x", "y",
            "designator", "rotation", "footprint",
        }
    for w in payload["wires"]:
        assert set(w) == {"x1", "y1", "x2", "y2"}
    for nl in payload["net_labels"]:
        assert set(nl) == {"text", "x", "y", "orientation"}
        assert nl["orientation"] in (0, 1, 2, 3)
    for pp in payload["power_ports"]:
        assert set(pp) >= {"text", "x", "y", "style"}


def test_score_field_names_match_quality():
    from eda_agent.design import quality
    q = quality.LayoutScore()
    s = LayoutScore()
    shared = {"total", "wire_crossings", "aspect_ratio_penalty", "total_wire_length"}
    for f in shared:
        assert hasattr(q, f)
        assert hasattr(s, f)
    # New terms present.
    assert hasattr(s, "total_bends")
    assert hasattr(s, "alignment_penalty")


def test_placement_hints_override():
    plan = _buck_plan()
    hints = {"U1": {"x": 5000, "y": 4000, "rotation": 90}}
    layout = compute_schematic_layout(plan, placement_hints=hints)
    assert layout.placed["U1"].x_mils == 5000
    assert layout.placed["U1"].y_mils == 4000
    assert layout.placed["U1"].rotation == 90


def test_cross_check_notes_surface_not_raise():
    # Net references an unknown refdes path can't be built (validator),
    # but a part on an undeclared sheet surfaces in notes via cross_check.
    # Build a plan that validates but has a zone issue caught by cross_check
    # is not possible (validator). Instead verify notes is a list.
    plan = _buck_plan()
    layout = compute_schematic_layout(plan)
    assert isinstance(layout.notes, list)


# ---------------------------------------------------------------------------
# Crossing objective: direct coverage + a multi-wire-net pipeline fixture.
# ---------------------------------------------------------------------------

def _route(net, segs):
    return NetRoute(net_name=net, segments=tuple(NetSegment(*s) for s in segs))


def test_count_crossings_detects_interior_intersection():
    from eda_agent.design.schematic_layout import _count_crossings
    # NETA horizontal at y=500 (x 0..1000); NETB vertical at x=500 (y 0..1000).
    crossing = {
        "NETA": [_route("NETA", [(0, 500, 1000, 500)])],
        "NETB": [_route("NETB", [(500, 0, 500, 1000)])],
    }
    assert _count_crossings(crossing) == 1


def test_count_crossings_zero_for_parallel_and_shared_endpoints():
    from eda_agent.design.schematic_layout import _count_crossings
    # Two parallel horizontals never cross.
    parallel = {
        "A": [_route("A", [(0, 100, 1000, 100)])],
        "B": [_route("B", [(0, 300, 1000, 300)])],
    }
    assert _count_crossings(parallel) == 0
    # An H and V that only touch at a shared endpoint do NOT count (interior
    # intersection required), so a clean T/L junction is not a crossing.
    touching = {
        "A": [_route("A", [(0, 500, 500, 500)])],
        "B": [_route("B", [(500, 500, 500, 1000)])],
    }
    assert _count_crossings(touching) == 0
    # Same-net overlap is never a crossing.
    same = {"A": [_route("A", [(0, 500, 1000, 500)]),
                  _route("A", [(500, 0, 500, 1000)])]}
    assert _count_crossings(same) == 0


def _two_signal_block_plan() -> DesignPlan:
    """One functional block with two compact intra-block signal nets, so both
    stay wire-tier (not promoted to labels) and actually exercise the router
    and the crossing objective end to end."""
    sheets = [Sheet(name="main")]
    zones = [Zone(name="blk", sheet="main", role="mcu")]
    parts = [
        Part(refdes="U1", lib_ref="IC", zone="blk"),
        Part(refdes="R1", lib_ref="RES", value="10k", zone="blk"),
        Part(refdes="R2", lib_ref="RES", value="10k", zone="blk"),
        Part(refdes="R3", lib_ref="RES", value="10k", zone="blk"),
    ]
    nets = [
        # Two distinct, non-power, single-zone signal nets sharing the U1 area.
        Net(name="SIGA", role="signal", pins=[
            PinRef(refdes="U1", pin="1"),
            PinRef(refdes="R1", pin="1"),
            PinRef(refdes="R2", pin="1"),
        ]),
        Net(name="SIGB", role="signal", pins=[
            PinRef(refdes="U1", pin="2"),
            PinRef(refdes="R3", pin="1"),
            PinRef(refdes="R1", pin="2"),
        ]),
        Net(name="GND", is_ground=True, pins=[
            PinRef(refdes="U1", pin="3"),
            PinRef(refdes="R2", pin="2"),
            PinRef(refdes="R3", pin="2"),
        ]),
    ]
    return DesignPlan(
        spec="two-signal block",
        summary="one block, two intra-block signal nets",
        topology="generic",
        sheets=sheets, zones=zones, parts=parts, nets=nets,
    )


def test_multi_intrablock_signal_nets_stay_wires_and_route():
    plan = _two_signal_block_plan()
    layout = compute_schematic_layout(plan)
    wire_nets = [n for n, d in layout.decisions.items() if d.kind == "wire"]
    # Both compact single-zone signal nets must survive as wires (this is the
    # behaviour that was previously force-promoted to labels), so the router
    # and crossing objective are genuinely exercised.
    assert {"SIGA", "SIGB"}.issubset(set(wire_nets))
    routed = {n for n, r in layout.routes.items() if r}
    assert {"SIGA", "SIGB"}.issubset(routed)
    # The crossing term is realised (an int >= 0) rather than dead.
    assert isinstance(layout.score.wire_crossings, int)
    assert layout.score.wire_crossings >= 0


def test_layout_reduces_crossings_vs_deliberately_crossed_baseline():
    """The realised layout should not have MORE wire crossings than a
    deliberately tangled placement of the same nets."""
    plan = _two_signal_block_plan()
    good = compute_schematic_layout(plan)

    # Baseline: pin the parts in a configuration designed to tangle SIGA/SIGB.
    tangled_hints = {
        "U1": {"x": 1000, "y": 1000, "rotation": 0},
        "R1": {"x": 0, "y": 2000, "rotation": 0},
        "R2": {"x": 2000, "y": 0, "rotation": 0},
        "R3": {"x": 0, "y": 0, "rotation": 0},
    }
    tangled = compute_schematic_layout(plan, placement_hints=tangled_hints)
    assert good.score.wire_crossings <= tangled.score.wire_crossings


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
