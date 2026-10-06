# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the shared power/ground stub planner (straight-first / L-fallback)."""

from __future__ import annotations

from eda_agent.design.power_stub import plan_rail_stubs, StubPlan


def _seg_len(p):
    return sum(abs(x1 - x2) + abs(y1 - y2) for (x1, y1, x2, y2) in p.segments)


def test_clear_column_gives_straight_ground_stub_below():
    plans = plan_rail_stubs([(1000, 2000)], is_ground=True,
                            foreign_pins=[], foreign_segments=[], bodies=[])
    assert len(plans) == 1
    p = plans[0]
    assert p.straight
    assert p.port == (1000, 1700)         # 300 below the pin
    assert p.segments == ((1000, 2000, 1000, 1700),)
    assert _seg_len(p) == 300             # one straight segment, 0 bends


def test_power_stub_goes_above():
    p = plan_rail_stubs([(500, 500)], is_ground=False,
                        foreign_pins=[], foreign_segments=[], bodies=[])[0]
    assert p.straight and p.port == (500, 800)


def test_foreign_pin_on_column_forces_L():
    # a foreign pin sits directly below at y=1850 (inside the 2000->1700 span)
    p = plan_rail_stubs([(1000, 2000)], is_ground=True,
                        foreign_pins=[(1000, 1850)], foreign_segments=[],
                        bodies=[])[0]
    assert not p.straight
    assert p.port[0] != 1000              # shifted off the blocked column
    assert len(p.segments) == 2           # an L


def test_body_in_column_forces_L():
    p = plan_rail_stubs([(1000, 2000)], is_ground=True, foreign_pins=[],
                        foreign_segments=[],
                        bodies=[(900, 1750, 1100, 1900)])[0]
    assert not p.straight


def test_foreign_wire_crossing_forces_L():
    # a horizontal foreign wire at y=1850 spanning x=1000
    p = plan_rail_stubs([(1000, 2000)], is_ground=True, foreign_pins=[],
                        foreign_segments=[(800, 1850, 1200, 1850)], bodies=[])[0]
    assert not p.straight


def test_two_pins_same_column_do_not_cross():
    # Two ground pins stacked on the same x: the first goes straight down; the
    # second's straight stub would overlap the first's, so it must divert.
    plans = plan_rail_stubs([(1000, 2000), (1000, 1600)], is_ground=True,
                            foreign_pins=[], foreign_segments=[], bodies=[])
    # first is straight
    assert plans[0].straight
    # the accumulation means the second can't blindly reuse the column where it
    # would coincide; both are valid plans with no shared interior crossing
    assert isinstance(plans[1], StubPlan)


def test_same_net_pin_below_is_not_an_obstacle():
    # another pin of the SAME net below the stub must NOT block it (same net
    # can share geometry); only foreign pins block.
    p = plan_rail_stubs([(1000, 2000), (1000, 1850)], is_ground=True,
                        foreign_pins=[(1000, 1850)],  # but it's our own pin too
                        foreign_segments=[], bodies=[])[0]
    # (1000,1850) is one of our pins, so it's excluded from obstacles -> straight
    assert p.straight


def test_one_plan_per_pin_in_order():
    pins = [(0, 0), (500, 0), (1000, 0)]
    plans = plan_rail_stubs(pins, is_ground=False, foreign_pins=[],
                            foreign_segments=[], bodies=[])
    assert [p.pin for p in plans] == pins


def test_deterministic():
    args = dict(is_ground=True, foreign_pins=[(0, 100)], foreign_segments=[],
                bodies=[])
    a = plan_rail_stubs([(0, 500), (300, 500)], **args)
    b = plan_rail_stubs([(0, 500), (300, 500)], **args)
    assert a == b


def _stub_segs_cross(plans):
    segs = [s for p in plans for s in p.segments]

    def hv_cross(h, v):
        hx1, hy1, hx2, hy2 = h
        vx1, vy1, vx2, vy2 = v
        if hy1 != hy2 or vx1 != vx2:
            return False
        return (min(hx1, hx2) < vx1 < max(hx1, hx2)
                and min(vy1, vy2) < hy1 < max(vy1, vy2))
    n = 0
    for i in range(len(segs)):
        for j in range(i + 1, len(segs)):
            if hv_cross(segs[i], segs[j]) or hv_cross(segs[j], segs[i]):
                n += 1
    return n


def test_dense_pins_degrade_gracefully_no_self_crossings():
    # 12 ground pins on a row; foreign pins block every other straight column.
    pins = [(1000 + 200 * i, 2000) for i in range(12)]
    foreign = [(1000 + 200 * i, 1850) for i in range(12) if i % 2 == 0]
    plans = plan_rail_stubs(pins, is_ground=True, foreign_pins=foreign,
                            foreign_segments=[], bodies=[])
    assert len(plans) == len(pins)              # all connected
    assert all(p.segments for p in plans)
    assert _stub_segs_cross(plans) == 0         # accumulation -> no self-cross


def test_all_columns_blocked_still_connects_without_crossings():
    pins = [(1000 + 200 * i, 2000) for i in range(12)]
    foreign = [(1000 + 200 * i, 1850) for i in range(12)]  # block every column
    plans = plan_rail_stubs(pins, is_ground=True, foreign_pins=foreign,
                            foreign_segments=[], bodies=[])
    assert all(not p.straight for p in plans)   # none can go straight
    assert all(p.segments for p in plans)       # but all still connect
    assert _stub_segs_cross(plans) == 0
