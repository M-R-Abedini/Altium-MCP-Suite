# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for datasheet-derived land patterns + the footprint checker."""

import math

import pytest

from eda_agent.design.footprints import (
    Pad,
    two_pin_chip,
    dual_row,
    pattern_bbox,
    row_pitch,
)
from eda_agent.design.footprint_check import check_footprint


# --------------------------------------------------------------------------- #
# Two-pin chip
# --------------------------------------------------------------------------- #
def test_chip_from_span():
    lp = two_pin_chip(1.0, 0.8, span=2.0)
    assert lp.pad_count == 2
    assert [p.designator for p in lp.pads] == ["1", "2"]
    # cx = span/2 - pad_w/2 = 1.0 - 0.5 = 0.5
    assert lp.pads[0].x == pytest.approx(-0.5)
    assert lp.pads[1].x == pytest.approx(0.5)
    assert lp.pads[0].y == 0.0 and lp.pads[0].height == 0.8


def test_chip_from_inner_gap():
    lp = two_pin_chip(1.0, 0.8, inner_gap=0.6)
    # cx = gap/2 + pad_w/2 = 0.3 + 0.5 = 0.8
    assert lp.pads[1].x == pytest.approx(0.8)


def test_chip_from_center():
    lp = two_pin_chip(1.0, 0.8, center=1.5)
    assert lp.pads[1].x == pytest.approx(0.75)


def test_chip_span_and_gap_agree():
    # span = inner_gap + 2*pad_w, so both must give the same pad centre.
    pad_w, gap = 1.0, 0.6
    a = two_pin_chip(pad_w, 0.8, inner_gap=gap)
    b = two_pin_chip(pad_w, 0.8, span=gap + 2 * pad_w)
    assert a.pads[1].x == pytest.approx(b.pads[1].x)


def test_chip_rejects_bad_input():
    with pytest.raises(ValueError):
        two_pin_chip(1.0, 0.8)                       # no dimension
    with pytest.raises(ValueError):
        two_pin_chip(1.0, 0.8, span=2.0, center=1.5)  # two dimensions
    with pytest.raises(ValueError):
        two_pin_chip(1.0, 0.8, span=0.9)             # span <= pad width
    with pytest.raises(ValueError):
        two_pin_chip(-1.0, 0.8, span=2.0)            # negative pad


# --------------------------------------------------------------------------- #
# Dual row (SOIC / SOP)
# --------------------------------------------------------------------------- #
def test_dual_row_soic8_geometry_and_numbering():
    lp = dual_row(4, 1.27, 1.5, 0.6, center=5.0)
    assert lp.pad_count == 8
    by = {p.designator: p for p in lp.pads}
    # cx = center/2 = 2.5; y_top = (4-1)/2 * 1.27 = 1.905
    # Left row top->bottom: pins 1..4 at x=-2.5
    assert by["1"].x == pytest.approx(-2.5) and by["1"].y == pytest.approx(1.905)
    assert by["4"].x == pytest.approx(-2.5) and by["4"].y == pytest.approx(-1.905)
    # Right row bottom->top: pins 5..8 at x=+2.5 (CCW from pin 1)
    assert by["5"].x == pytest.approx(2.5) and by["5"].y == pytest.approx(-1.905)
    assert by["8"].x == pytest.approx(2.5) and by["8"].y == pytest.approx(1.905)
    # Pin 1 and pin 8 are diagonally opposite top corners.
    assert by["1"].y == pytest.approx(by["8"].y)


def test_dual_row_span_and_center_agree():
    # toe-to-toe span = centre span + pad width.
    a = dual_row(4, 1.27, 1.5, 0.6, center=5.0)
    b = dual_row(4, 1.27, 1.5, 0.6, span=5.0 + 1.5)
    assert a.pads[0].x == pytest.approx(b.pads[0].x)


def test_dual_row_pitch_is_recovered():
    lp = dual_row(4, 1.27, 1.5, 0.6, center=5.0)
    assert row_pitch(lp) == pytest.approx(1.27)


def test_chip_has_no_row_pitch():
    assert row_pitch(two_pin_chip(1.0, 0.8, span=2.0)) is None


def test_dual_row_rejects_bad_input():
    with pytest.raises(ValueError):
        dual_row(0, 1.27, 1.5, 0.6, center=5.0)
    with pytest.raises(ValueError):
        dual_row(4, 0.0, 1.5, 0.6, center=5.0)


# --------------------------------------------------------------------------- #
# Courtyard / bbox
# --------------------------------------------------------------------------- #
def test_courtyard_is_pad_bbox_plus_margin():
    lp = two_pin_chip(1.0, 0.8, span=2.0, courtyard_margin=0.25)
    x1, y1, x2, y2 = pattern_bbox(lp)
    # pads span x in [-1.0, 1.0] (cx=0.5 +/- pad_w/2=0.5), y in [-0.4, 0.4]
    assert (x1, y1, x2, y2) == pytest.approx((-1.0, -0.4, 1.0, 0.4))
    assert lp.courtyard == pytest.approx((-1.25, -0.65, 1.25, 0.65))


# --------------------------------------------------------------------------- #
# Checker
# --------------------------------------------------------------------------- #
def _pads(lp):
    return list(lp.pads)


def test_check_identical_passes():
    lp = dual_row(4, 1.27, 1.5, 0.6, center=5.0)
    rep = check_footprint(_pads(lp), lp)
    assert rep.passed and not rep.issues


def test_check_is_origin_invariant():
    # A footprint whose origin is the corner (all pads shifted) still matches:
    # the checker centroid-aligns first.
    lp = dual_row(4, 1.27, 1.5, 0.6, center=5.0)
    shifted = [Pad(p.designator, p.x + 7.3, p.y - 2.1, p.width, p.height)
               for p in lp.pads]
    assert check_footprint(shifted, lp).passed


def test_check_flags_moved_pad():
    lp = two_pin_chip(1.0, 0.8, span=2.0)
    bad = _pads(lp)
    bad[1] = Pad("2", bad[1].x + 0.3, 0.0, 1.0, 0.8)  # pad 2 shifted 0.3 mm
    rep = check_footprint(bad, lp, pos_tol_mm=0.05)
    assert not rep.passed
    assert any(i.code == "pad_position" and i.designator == "2"
               for i in rep.errors)


def test_check_small_perturbation_within_tolerance():
    lp = two_pin_chip(1.0, 0.8, span=2.0)
    bad = _pads(lp)
    bad[1] = Pad("2", bad[1].x + 0.02, 0.0, 1.0, 0.8)  # 0.02 mm < 0.05 tol
    assert check_footprint(bad, lp, pos_tol_mm=0.05).passed


def test_check_flags_wrong_pad_size():
    lp = two_pin_chip(1.0, 0.8, span=2.0)
    bad = _pads(lp)
    bad[0] = Pad("1", bad[0].x, 0.0, 1.3, 0.8)  # pad 1 too wide
    rep = check_footprint(bad, lp, size_tol_mm=0.05)
    assert any(i.code == "pad_size" and i.designator == "1" for i in rep.errors)


def test_check_flags_missing_and_count():
    lp = dual_row(4, 1.27, 1.5, 0.6, center=5.0)
    bad = _pads(lp)[:-1]  # drop pad 8
    rep = check_footprint(bad, lp)
    codes = {i.code for i in rep.errors}
    assert "pad_count" in codes and "missing_pad" in codes
    assert any(i.designator == "8" for i in rep.errors if i.code == "missing_pad")


def test_check_flags_extra_pad():
    lp = two_pin_chip(1.0, 0.8, span=2.0)
    bad = _pads(lp) + [Pad("3", 0.0, 2.0, 1.0, 0.8)]
    rep = check_footprint(bad, lp)
    assert any(i.code == "extra_pad" and i.designator == "3" for i in rep.errors)


def test_check_rejects_empty():
    lp = two_pin_chip(1.0, 0.8, span=2.0)
    with pytest.raises(ValueError):
        check_footprint([], lp)
