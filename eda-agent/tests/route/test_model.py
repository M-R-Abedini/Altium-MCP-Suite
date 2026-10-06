# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the routing problem model: rules parsing, geometry
distance helpers, pad extents, and the grid obstacle map built from a
board geometry dict (all mils)."""

from __future__ import annotations

import math

import pytest

from eda_agent.route.model import (
    RouteRules,
    RoutingProblem,
    _pad_half_extents,
    dist_point_rect,
    dist_point_seg,
    dist_seg_rect,
    dist_seg_seg,
    rules_from_dict,
)


def _pad(x, y, net="", layer="TopLayer", size=40, rotation=0,
         x_size=None, y_size=None):
    return {
        "x": x, "y": y,
        "x_size": size if x_size is None else x_size,
        "y_size": size if y_size is None else y_size,
        "shape": "Rectangular", "layer": layer, "net": net,
        "rotation": rotation,
    }


def _geom(pads, bbox=(0, 0, 1000, 600), tracks=None, vias=None):
    g = {"pads": pads, "tracks": tracks or [], "vias": vias or []}
    if bbox is not None:
        g["bbox"] = {"x1": bbox[0], "y1": bbox[1],
                     "x2": bbox[2], "y2": bbox[3]}
    return g


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


class TestRules:
    def test_defaults(self):
        r = rules_from_dict(None)
        assert r.clearance_mils == 10
        assert r.track_width_mils["default"] == 10
        assert r.via_size_mils == 50
        assert r.via_drill_mils == 28
        assert r.layers == ("TopLayer", "BottomLayer")

    def test_scalar_width_becomes_default(self):
        r = rules_from_dict({"track_width_mils": 12})
        assert r.track_width_mils == {"default": 12}

    def test_per_class_widths_keep_default(self):
        r = rules_from_dict({"track_width_mils": {"power": 25}})
        assert r.width_for_class("power") == 25
        assert r.width_for_class("signal") == 10  # injected default

    def test_max_track_halfwidth(self):
        r = rules_from_dict(
            {"track_width_mils": {"default": 10, "power": 30}})
        assert r.max_track_halfwidth == 15

    def test_via_key_aliases(self):
        r = rules_from_dict({"via_size": 40, "via_drill": 20})
        assert (r.via_size_mils, r.via_drill_mils) == (40, 20)

    @pytest.mark.parametrize("bad", [
        {"clearance_mils": -1},
        {"track_width_mils": {"default": 0}},
        {"track_width_mils": "wide"},
        {"via_size_mils": 0},
        {"via_drill_mils": 60},          # drill > size
        {"layers": []},
        {"layers": ["TopLayer", "TopLayer"]},
    ])
    def test_invalid_rules_raise(self, bad):
        with pytest.raises(ValueError):
            rules_from_dict(bad)


# ---------------------------------------------------------------------------
# Distance helpers
# ---------------------------------------------------------------------------


class TestDistances:
    def test_point_seg(self):
        assert dist_point_seg(0, 10, -5, 0, 5, 0) == 10
        assert dist_point_seg(10, 0, -5, 0, 5, 0) == 5      # past the end
        assert dist_point_seg(3, 0, -5, 0, 5, 0) == 0        # on it
        assert dist_point_seg(1, 1, 0, 0, 0, 0) == pytest.approx(math.sqrt(2))

    def test_point_rect(self):
        assert dist_point_rect(0, 0, 0, 0, 10, 5) == 0       # inside
        assert dist_point_rect(15, 0, 0, 0, 10, 5) == 5
        assert dist_point_rect(13, 9, 0, 0, 10, 5) == 5      # corner

    def test_seg_seg_crossing_is_zero(self):
        assert dist_seg_seg(-10, 0, 10, 0, 0, -10, 0, 10) == 0

    def test_seg_seg_parallel(self):
        assert dist_seg_seg(0, 0, 10, 0, 0, 7, 10, 7) == 7

    def test_seg_seg_collinear_touching(self):
        assert dist_seg_seg(0, 0, 10, 0, 10, 0, 20, 0) == 0

    def test_seg_rect(self):
        assert dist_seg_rect(-20, 0, 20, 0, 0, 0, 5, 5) == 0  # through it
        assert dist_seg_rect(-20, 9, 20, 9, 0, 0, 5, 5) == 4
        assert dist_seg_rect(0, 0, 1, 0, 100, 0, 5, 5) == 94


# ---------------------------------------------------------------------------
# Pad extents
# ---------------------------------------------------------------------------


class TestPadExtents:
    def test_axis_aligned(self):
        assert _pad_half_extents(_pad(0, 0, x_size=60, y_size=20)) == (30, 10)

    def test_rot_90_swaps(self):
        p = _pad(0, 0, x_size=60, y_size=20, rotation=90)
        assert _pad_half_extents(p) == (10, 30)

    def test_rot_270_swaps(self):
        p = _pad(0, 0, x_size=60, y_size=20, rotation=270)
        assert _pad_half_extents(p) == (10, 30)

    def test_rot_45_uses_aabb(self):
        p = _pad(0, 0, x_size=40, y_size=40, rotation=45)
        hw, hh = _pad_half_extents(p)
        expect = 20 * math.sqrt(2)
        assert hw == pytest.approx(expect)
        assert hh == pytest.approx(expect)


# ---------------------------------------------------------------------------
# Problem construction
# ---------------------------------------------------------------------------


class TestFromGeometry:
    def test_terminals_grouped_and_sorted(self):
        g = _geom([
            _pad(900, 300, "A"), _pad(100, 300, "A"), _pad(500, 100, "B"),
        ])
        prob = RoutingProblem.from_geometry(g, None)
        assert sorted(prob.terminals) == ["A", "B"]
        assert [(t.x, t.y) for t in prob.terminals["A"]] == [
            (100, 300), (900, 300)]

    def test_pad_blocks_other_net_passes_own(self):
        g = _geom([_pad(500, 300, "A")])
        prob = RoutingProblem.from_geometry(g, None)
        cell = prob.snap_cell(500, 300)
        assert prob.passable(0, *cell, "A")
        assert not prob.passable(0, *cell, "B")

    def test_unnetted_pad_blocks_everyone(self):
        g = _geom([_pad(500, 300, "")])
        prob = RoutingProblem.from_geometry(g, None)
        cell = prob.snap_cell(500, 300)
        assert not prob.passable(0, *cell, "A")
        assert not prob.passable(0, *cell, "")

    def test_top_pad_blocks_top_only(self):
        g = _geom([_pad(500, 300, "A", layer="TopLayer")])
        prob = RoutingProblem.from_geometry(g, None)
        cell = prob.snap_cell(500, 300)
        assert not prob.passable(0, *cell, "B")   # TopLayer
        assert prob.passable(1, *cell, "B")       # BottomLayer free

    def test_multilayer_pad_blocks_all_and_reaches_all(self):
        g = _geom([_pad(500, 300, "A", layer="MultiLayer")])
        prob = RoutingProblem.from_geometry(g, None)
        cell = prob.snap_cell(500, 300)
        assert not prob.passable(0, *cell, "B")
        assert not prob.passable(1, *cell, "B")
        assert prob.terminals["A"][0].layers == (0, 1)

    def test_keepout_blocks_all_nets_no_terminal(self):
        g = _geom([_pad(500, 300, "A", layer="KeepOutLayer")])
        prob = RoutingProblem.from_geometry(g, None)
        cell = prob.snap_cell(500, 300)
        assert not prob.passable(0, *cell, "A")
        assert not prob.passable(1, *cell, "A")

    def test_silkscreen_pad_ignored(self):
        g = _geom([_pad(500, 300, "A", layer="TopOverlay")])
        prob = RoutingProblem.from_geometry(g, None)
        cell = prob.snap_cell(500, 300)
        assert prob.passable(0, *cell, "B")
        assert "A" not in prob.terminals

    def test_existing_track_blocks_its_layer(self):
        g = _geom([], tracks=[{
            "x1": 500, "y1": 0, "x2": 500, "y2": 600,
            "width": 20, "layer": "TopLayer", "net": "",
        }])
        prob = RoutingProblem.from_geometry(g, None)
        cell = prob.snap_cell(500, 300)
        assert not prob.passable(0, *cell, "A")
        assert prob.passable(1, *cell, "A")

    def test_existing_via_blocks_all_layers(self):
        g = _geom([], vias=[{"x": 500, "y": 300, "size": 40,
                             "hole_size": 20, "net": ""}])
        prob = RoutingProblem.from_geometry(g, None)
        cell = prob.snap_cell(500, 300)
        assert not prob.passable(0, *cell, "A")
        assert not prob.passable(1, *cell, "A")

    def test_via_map_is_wider_than_track_map(self):
        # 25 mils from a pad edge: a 10 mil track centerline fits
        # (needs 10 + 5 = 15) but a 50 mil via barrel does not
        # (needs 10 + 25 = 35).
        g = _geom([_pad(500, 300, "A", size=40)])
        prob = RoutingProblem.from_geometry(g, None)
        cell = prob.snap_cell(550, 300)   # 30 mils from copper edge
        assert prob.passable(0, *cell, "B")
        assert not prob.via_ok(*cell, "B")

    def test_inflation_clearance_plus_halfwidth(self):
        # Pad edge at x=520; margin = clearance 10 + half-width 5 = 15.
        # Cell at x=525 (5 from edge) blocked; x=550 (30 away) free.
        g = _geom([_pad(500, 300, "A", size=40)])
        prob = RoutingProblem.from_geometry(g, None)
        assert not prob.passable(0, *prob.snap_cell(525, 300), "B")
        assert prob.passable(0, *prob.snap_cell(550, 300), "B")

    def test_bbox_fallback_to_pads(self):
        g = _geom([_pad(100, 100, "A"), _pad(400, 400, "A")], bbox=None)
        prob = RoutingProblem.from_geometry(g, None)
        assert prob.x0 <= -100 and prob.y0 <= -100  # 200 mil apron

    def test_no_bbox_no_pads_raises(self):
        with pytest.raises(ValueError):
            RoutingProblem.from_geometry({"pads": []}, None)

    def test_bad_pitch_raises(self):
        with pytest.raises(ValueError):
            RoutingProblem.from_geometry(
                _geom([_pad(100, 100, "A")]), None, grid_pitch_mils=0)

    def test_oversize_grid_raises(self):
        g = _geom([_pad(0, 0, "A")], bbox=(0, 0, 10_000_000, 10_000_000))
        with pytest.raises(ValueError):
            RoutingProblem.from_geometry(g, None, grid_pitch_mils=1)

    def test_snap_cell_clamps(self):
        prob = RoutingProblem.from_geometry(_geom([_pad(100, 100, "A")]),
                                            None)
        assert prob.snap_cell(-5000, -5000) == (0, 0)
        assert prob.snap_cell(50_000, 50_000) == (prob.nx - 1, prob.ny - 1)

    def test_width_for_net_uses_class(self):
        rules = {"track_width_mils": {"default": 10, "power": 25}}
        prob = RoutingProblem.from_geometry(
            _geom([_pad(100, 100, "VCC")]), rules,
            net_classes={"VCC": "power"})
        assert prob.width_for_net("VCC") == 25
        assert prob.width_for_net("SIG") == 10

    def test_route_rules_instance_accepted(self):
        prob = RoutingProblem.from_geometry(
            _geom([_pad(100, 100, "A")]), RouteRules())
        assert prob.rules.clearance_mils == 10
