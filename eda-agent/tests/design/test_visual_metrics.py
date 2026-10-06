# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Unit tests for the perceptual placement metrics.

These cover the geometry the metrics read off a placement -- cluster spread,
the tight-pack target, quadrant whitespace balance, and the combined penalty
-- independently of the SA engine, so they are fast and deterministic.
"""

from __future__ import annotations

import math

from eda_agent.design import visual_metrics as vm
from eda_agent.placement import PlaceComp, PlaceNet, PlacePin
from eda_agent.placement.autoplace import BoardRegion


def _comp(ref, w=40, h=40):
    return PlaceComp(ref=ref, w=w, h=h, cx=0, cy=0)


def test_cluster_spread_is_max_pairwise_distance():
    cen = {"A": (0.0, 0.0), "B": (300.0, 0.0), "C": (0.0, 400.0)}
    # max pairwise is B-C = hypot(300, 400) = 500
    assert abs(vm.cluster_spread(cen, ["A", "B", "C"]) - 500.0) < 1e-6
    # fewer than two present -> 0
    assert vm.cluster_spread(cen, ["A"]) == 0.0
    assert vm.cluster_spread(cen, ["A", "Z"]) == 0.0


def test_tight_pack_diag_scales_with_area_and_floors_on_biggest():
    # Three small caps: area-driven diagonal, but never below biggest*1.4.
    small = [(40.0, 40.0), (40.0, 40.0)]
    diag = vm.tight_pack_diag(small)
    assert diag >= 40.0 * 1.4 - 1e-9
    # A single large part dominates via the floor.
    big = [(400.0, 400.0)]
    assert vm.tight_pack_diag(big) >= 400.0 * 1.4 - 1e-9


def test_group_compactness_flags_scattered_not_tight():
    comps = [_comp("Y1", 120, 80), _comp("C6"), _comp("C7")]
    groups = {"Y1": "g", "C6": "g", "C7": "g"}
    tight = {"Y1": (0.0, 0.0), "C6": (60.0, 0.0), "C7": (-60.0, 0.0)}
    sp_t, ex_t = vm.group_compactness(comps, tight, groups)
    # 120 mils spread, target for a 120x80 + two 40x40 ~ floor 168 -> excess 0
    assert ex_t["g"] == 0.0
    scattered = {"Y1": (0.0, 0.0), "C6": (900.0, 0.0), "C7": (-60.0, 0.0)}
    sp_s, ex_s = vm.group_compactness(comps, scattered, groups)
    assert sp_s["g"] > sp_t["g"]
    assert ex_s["g"] > 0.0


def test_whitespace_cv_balanced_is_low_lopsided_is_high():
    region = BoardRegion(x1=0.0, y1=0.0, x2=1000.0, y2=1000.0)
    comps = [_comp("A"), _comp("B"), _comp("C"), _comp("D")]
    balanced = {"A": (250.0, 250.0), "B": (750.0, 250.0),
                "C": (250.0, 750.0), "D": (750.0, 750.0)}
    lopsided = {"A": (100.0, 100.0), "B": (150.0, 150.0),
                "C": (120.0, 180.0), "D": (200.0, 120.0)}
    assert vm.whitespace_cv(comps, balanced, region) < 1e-6
    assert vm.whitespace_cv(comps, lopsided, region) > 1.0


def test_visual_report_penalty_rises_with_excess_and_crossings():
    comps = [_comp("Y1", 120, 80), _comp("C6"), _comp("C7")]
    groups = {"Y1": "g", "C6": "g", "C7": "g"}
    region = BoardRegion(x1=0.0, y1=0.0, x2=1000.0, y2=1000.0)
    tight = {"Y1": (480.0, 480.0), "C6": (540.0, 480.0), "C7": (420.0, 480.0)}
    scattered = {"Y1": (480.0, 480.0), "C6": (60.0, 60.0),
                 "C7": (940.0, 940.0)}
    r_tight = vm.visual_report(comps, tight, region, groups=groups, crossings=0)
    r_scat = vm.visual_report(comps, scattered, region, groups=groups,
                              crossings=0)
    assert r_scat.penalty > r_tight.penalty
    # crossings add to the penalty independently of geometry.
    r_cross = vm.visual_report(comps, tight, region, groups=groups, crossings=5)
    assert r_cross.penalty > r_tight.penalty
    assert abs(r_cross.penalty - r_tight.penalty - 5 * vm.W_CROSSING) < 1e-6


def test_visual_report_defaults_groups_to_match_group_attr():
    comps = [_comp("Y1", 120, 80), _comp("C6"), _comp("C7")]
    for c in comps:
        c.match_group = "g"
    region = BoardRegion(x1=0.0, y1=0.0, x2=1000.0, y2=1000.0)
    scattered = {"Y1": (480.0, 480.0), "C6": (60.0, 60.0),
                 "C7": (940.0, 940.0)}
    rep = vm.visual_report(comps, scattered, region)
    assert "g" in rep.group_excess and rep.group_excess["g"] > 0.0
