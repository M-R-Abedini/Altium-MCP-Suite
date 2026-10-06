# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Offline tests for the PCB auto-placement solver.

No Altium dependency: the solver is pure Python. These exercise the
quality contract (HPWL drops on a clustered netlist), the safety
contract (no residual overlaps, stays inside the board, fixed parts
pinned), determinism, and edge cases.
"""

from __future__ import annotations

import math

from eda_agent.placement import (
    BoardRegion,
    PlaceComp,
    PlaceNet,
    PlaceOptions,
    PlacePin,
    hpwl,
    overlap_pair_count,
    pin_hpwl,
    plan_placement,
)
from eda_agent.placement.autoplace import _optimize_rotations, _rotate_offset


def _no_overlaps(result, comps) -> bool:
    return overlap_pair_count(comps, result.positions, clearance=0.0) == 0


def _inside(result, comps, region) -> bool:
    rx_lo = min(region.x1, region.x2)
    rx_hi = max(region.x1, region.x2)
    ry_lo = min(region.y1, region.y2)
    ry_hi = max(region.y1, region.y2)
    for c in comps:
        x, y = result.positions[c.ref]
        if not (rx_lo + c.w / 2.0 - 1e-6 <= x <= rx_hi - c.w / 2.0 + 1e-6):
            return False
        if not (ry_lo + c.h / 2.0 - 1e-6 <= y <= ry_hi - c.h / 2.0 + 1e-6):
            return False
    return True


def test_empty_and_single():
    region = BoardRegion(0, 0, 1000, 1000)
    r0 = plan_placement([], [], region)
    assert r0.positions == {}
    assert r0.hpwl_after == 0.0

    one = [PlaceComp("U1", 100, 100, 500, 500)]
    r1 = plan_placement(one, [], region)
    assert r1.positions["U1"] == (500, 500)
    assert r1.moved["U1"] == (0.0, 0.0)


def test_hpwl_drops_on_clustered_netlist():
    """An IC with passives scattered to the four corners should pull in.

    Three 2-pin parts each share a net with the central IC but start in
    far corners. After placement the HPWL must drop substantially.
    """
    region = BoardRegion(0, 0, 4000, 4000)
    comps = [
        PlaceComp("U1", 400, 400, 2000, 2000),
        PlaceComp("C1", 80, 50, 200, 200),
        PlaceComp("C2", 80, 50, 3800, 200),
        PlaceComp("C3", 80, 50, 200, 3800),
        PlaceComp("C4", 80, 50, 3800, 3800),
    ]
    nets = [
        PlaceNet(("U1", "C1"), "N1"),
        PlaceNet(("U1", "C2"), "N2"),
        PlaceNet(("U1", "C3"), "N3"),
        PlaceNet(("U1", "C4"), "N4"),
    ]
    result = plan_placement(comps, nets, region)
    assert result.hpwl_after < result.hpwl_before * 0.6
    assert _no_overlaps(result, comps)
    assert _inside(result, comps, region)


def test_decoupling_caps_cluster_near_ic():
    """Each cap shares a net with the IC; each should end up close."""
    region = BoardRegion(0, 0, 5000, 5000)
    comps = [PlaceComp("U1", 600, 600, 2500, 2500)]
    nets = []
    for i in range(6):
        ref = f"C{i+1}"
        # start far away on a ring
        ang = i * (2 * math.pi / 6)
        comps.append(
            PlaceComp(ref, 60, 40, 2500 + 2200 * math.cos(ang),
                      2500 + 2200 * math.sin(ang))
        )
        nets.append(PlaceNet(("U1", ref), f"N{i}"))
    result = plan_placement(comps, nets, region)
    ux, uy = result.positions["U1"]
    for i in range(6):
        cx, cy = result.positions[f"C{i+1}"]
        # within ~2x the IC half-extent of the IC centroid
        assert math.hypot(cx - ux, cy - uy) < 1600
    assert _no_overlaps(result, comps)


def test_no_overlaps_dense_grid():
    """Many equal parts in a tight board: legalization must separate."""
    region = BoardRegion(0, 0, 2000, 2000)
    comps = []
    for i in range(16):
        # start them all bunched near the centre to force overlap
        comps.append(PlaceComp(f"R{i+1}", 150, 80, 1000 + (i % 4) * 5,
                               1000 + (i // 4) * 5))
    result = plan_placement(comps, [], region, PlaceOptions(iterations=200))
    assert _no_overlaps(result, comps)
    assert _inside(result, comps, region)


def test_fixed_components_do_not_move():
    region = BoardRegion(0, 0, 4000, 4000)
    comps = [
        PlaceComp("J1", 300, 300, 200, 2000, fixed=True),
        PlaceComp("J2", 300, 300, 3800, 2000, fixed=True),
        PlaceComp("U1", 500, 500, 2000, 2000),
        PlaceComp("C1", 80, 50, 2000, 3000),
    ]
    nets = [
        PlaceNet(("J1", "U1"), "A"),
        PlaceNet(("J2", "U1"), "B"),
        PlaceNet(("U1", "C1"), "C"),
    ]
    result = plan_placement(comps, nets, region)
    assert result.positions["J1"] == (200, 2000)
    assert result.positions["J2"] == (3800, 2000)
    assert result.moved["J1"] == (0.0, 0.0)
    assert result.moved["J2"] == (0.0, 0.0)


def test_layer_aware_collision_allows_xy_overlap():
    """A top and a bottom part at the same XY is NOT an overlap."""
    region = BoardRegion(0, 0, 1000, 1000)
    comps = [
        PlaceComp("U1", 200, 200, 500, 500, layer="Top"),
        PlaceComp("U2", 200, 200, 500, 500, layer="Bottom"),
    ]
    assert overlap_pair_count(comps, {"U1": (500, 500), "U2": (500, 500)}) == 0
    result = plan_placement(comps, [], region)
    # both may legitimately sit at the same spot on opposite sides
    assert _no_overlaps(result, comps)


def test_determinism():
    region = BoardRegion(0, 0, 3000, 3000)
    comps = [
        PlaceComp("U1", 400, 400, 1500, 1500),
        PlaceComp("C1", 80, 50, 1505, 1505),  # coincident-ish
        PlaceComp("C2", 80, 50, 1500, 1500),
        PlaceComp("R1", 120, 60, 100, 100),
    ]
    nets = [PlaceNet(("U1", "C1", "C2", "R1"), "N")]
    r1 = plan_placement(comps, nets, region)
    r2 = plan_placement(comps, nets, region)
    assert r1.positions == r2.positions


def test_inputs_not_mutated():
    region = BoardRegion(0, 0, 3000, 3000)
    comps = [
        PlaceComp("U1", 400, 400, 100, 100),
        PlaceComp("C1", 80, 50, 2900, 2900),
    ]
    nets = [PlaceNet(("U1", "C1"), "N")]
    before = [(c.ref, c.cx, c.cy) for c in comps]
    plan_placement(comps, nets, region)
    after = [(c.ref, c.cx, c.cy) for c in comps]
    assert before == after


def test_oversubscribed_board_reports_residual():
    """More copper than fits: solver still returns, flags residual."""
    region = BoardRegion(0, 0, 400, 400)
    comps = [PlaceComp(f"U{i+1}", 300, 300, 200, 200) for i in range(4)]
    result = plan_placement(comps, [], region)
    # Cannot place four 300x300 parts in a 400x400 board without overlap.
    assert result.overlap_pairs_after > 0
    assert any("overlap" in n for n in result.notes)


def test_reseed_grid_mode():
    region = BoardRegion(0, 0, 5000, 5000)
    comps = [PlaceComp(f"R{i+1}", 150, 80, 0, 0) for i in range(9)]
    result = plan_placement(comps, [], region, PlaceOptions(reseed_grid=True))
    assert _no_overlaps(result, comps)
    assert _inside(result, comps, region)


def test_rotate_offset_orthogonal():
    assert _rotate_offset(300, 0, 0) == (300, 0)
    assert _rotate_offset(300, 0, 90) == (0, 300)
    assert _rotate_offset(300, 0, 180) == (-300, 0)
    assert _rotate_offset(300, 0, 270) == (0, -300)
    # 360 wraps to 0
    assert _rotate_offset(123, 45, 360) == (123, 45)


def test_pin_hpwl_falls_back_to_centroid_when_pinless():
    """With no pins, pin_hpwl must equal the plain centroid hpwl."""
    comps = [
        PlaceComp("U1", 400, 400, 0, 0),
        PlaceComp("C1", 80, 50, 1000, 500),
    ]
    nets = [PlaceNet(("U1", "C1"), "N")]
    pos = {"U1": (0.0, 0.0), "C1": (1000.0, 500.0)}
    rot = {"U1": 0.0, "C1": 0.0}
    assert pin_hpwl(comps, pos, rot, nets) == hpwl(pos, nets)


def test_optimize_rotations_picks_aligning_angle():
    """A horizontal 2-pin part between a top and bottom anchor should
    rotate 90 so its pins point at the anchors."""
    r1 = PlaceComp(
        "R1", 700, 200, 1000, 1000, rotatable=True,
        pins=(PlacePin(300, 0, "A"), PlacePin(-300, 0, "B")),
    )
    a1 = PlaceComp("A1", 100, 100, 1000, 2000, fixed=True)  # net A, above
    a2 = PlaceComp("A2", 100, 100, 1000, 0, fixed=True)     # net B, below
    comps = [r1, a1, a2]
    nets = [PlaceNet(("R1", "A1"), "A"), PlaceNet(("R1", "A2"), "B")]
    positions = {c.ref: (c.cx, c.cy) for c in comps}
    rotations = {c.ref: c.rotation for c in comps}
    _optimize_rotations(comps, positions, nets, PlaceOptions(), rotations)
    assert rotations["R1"] == 90.0


def test_optimize_rotations_only_orthogonal_values():
    r1 = PlaceComp(
        "R1", 700, 200, 1000, 1000, rotatable=True,
        pins=(PlacePin(300, 0, "A"), PlacePin(-300, 0, "B")),
    )
    a1 = PlaceComp("A1", 100, 100, 1700, 1100, fixed=True)
    a2 = PlaceComp("A2", 100, 100, 300, 900, fixed=True)
    comps = [r1, a1, a2]
    nets = [PlaceNet(("R1", "A1"), "A"), PlaceNet(("R1", "A2"), "B")]
    positions = {c.ref: (c.cx, c.cy) for c in comps}
    rotations = {c.ref: c.rotation for c in comps}
    _optimize_rotations(comps, positions, nets, PlaceOptions(), rotations)
    assert rotations["R1"] in (0.0, 90.0, 180.0, 270.0)


def test_plan_placement_reports_rotation_and_stays_legal():
    """End-to-end: a rotatable 2-pin part with vertical anchors gets
    re-oriented, the result records it, and nothing overlaps."""
    region = BoardRegion(0, 0, 4000, 4000)
    r1 = PlaceComp(
        "R1", 700, 200, 2000, 2000, rotatable=True,
        pins=(PlacePin(300, 0, "A"), PlacePin(-300, 0, "B")),
    )
    a1 = PlaceComp("A1", 200, 200, 2000, 3500, fixed=True)
    a2 = PlaceComp("A2", 200, 200, 2000, 500, fixed=True)
    comps = [r1, a1, a2]
    nets = [PlaceNet(("R1", "A1"), "A"), PlaceNet(("R1", "A2"), "B")]
    result = plan_placement(comps, nets, region)
    assert "R1" in result.rotated
    assert result.rotations["R1"] in (90.0, 270.0)
    assert result.hpwl_after <= result.hpwl_before
    assert overlap_pair_count(comps, result.positions,
                              rotations=result.rotations) == 0


def test_optimize_rotation_can_be_disabled():
    region = BoardRegion(0, 0, 4000, 4000)
    r1 = PlaceComp(
        "R1", 700, 200, 2000, 2000, rotatable=True,
        pins=(PlacePin(300, 0, "A"), PlacePin(-300, 0, "B")),
    )
    a1 = PlaceComp("A1", 200, 200, 2000, 3500, fixed=True)
    a2 = PlaceComp("A2", 200, 200, 2000, 500, fixed=True)
    comps = [r1, a1, a2]
    nets = [PlaceNet(("R1", "A1"), "A"), PlaceNet(("R1", "A2"), "B")]
    result = plan_placement(comps, nets, region,
                            PlaceOptions(optimize_rotation=False))
    assert result.rotated == {}
    assert result.rotations["R1"] == 0.0


def test_rotation_swaps_bbox_for_legalization():
    """A tall part rotated 90 becomes wide; legalization must account
    for the swapped footprint and still clear its neighbour."""
    region = BoardRegion(0, 0, 6000, 6000)
    # Tall 2-pin part; rotating it 90 makes it 1200 wide.
    r1 = PlaceComp(
        "R1", 200, 1200, 3000, 3000, rotatable=True,
        pins=(PlacePin(0, 500, "A"), PlacePin(0, -500, "B")),
    )
    a1 = PlaceComp("A1", 200, 200, 5500, 3000, fixed=True)  # net A to the right
    a2 = PlaceComp("A2", 200, 200, 500, 3000, fixed=True)   # net B to the left
    comps = [r1, a1, a2]
    nets = [PlaceNet(("R1", "A1"), "A"), PlaceNet(("R1", "A2"), "B")]
    result = plan_placement(comps, nets, region)
    assert overlap_pair_count(comps, result.positions,
                              rotations=result.rotations) == 0


def test_pinned_determinism():
    region = BoardRegion(0, 0, 5000, 5000)
    r1 = PlaceComp(
        "R1", 700, 200, 2500, 2500, rotatable=True,
        pins=(PlacePin(300, 0, "A"), PlacePin(-300, 0, "B")),
    )
    a1 = PlaceComp("A1", 200, 200, 2500, 4000, fixed=True)
    a2 = PlaceComp("A2", 200, 200, 2500, 1000, fixed=True)
    comps = [r1, a1, a2]
    nets = [PlaceNet(("R1", "A1"), "A"), PlaceNet(("R1", "A2"), "B")]
    r_a = plan_placement(comps, nets, region)
    r_b = plan_placement(comps, nets, region)
    assert r_a.positions == r_b.positions
    assert r_a.rotations == r_b.rotations


def test_grid_snap():
    region = BoardRegion(0, 0, 4000, 4000)
    comps = [
        PlaceComp("U1", 400, 400, 1234, 2345),
        PlaceComp("C1", 80, 50, 3000, 3000),
    ]
    nets = [PlaceNet(("U1", "C1"), "N")]
    result = plan_placement(comps, nets, region, PlaceOptions(grid_mils=25))
    for x, y in result.positions.values():
        assert abs(x - round(x / 25) * 25) < 1e-6
        assert abs(y - round(y / 25) * 25) < 1e-6
