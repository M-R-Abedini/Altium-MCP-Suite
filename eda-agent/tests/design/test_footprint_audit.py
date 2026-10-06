# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Datasheet land-pattern audit: the deterministic comparison half.

Every test builds the 'footprint' side as the dict shape the bridge's
``library.get_pad_geometry`` returns, so these run fully offline. The
spec side uses the parametric shorthands plus explicit pads. What is
pinned down:

* a faithful footprint passes at any origin offset and any of the four
  library rotations;
* each defect class (count, position, size, shape, drill, numbering
  sequence, extra pad, thermal paste policy) is reported as its own
  check with expected vs actual;
* a mirrored pattern FAILS (mirroring must never be silently
  compensated).
"""

from __future__ import annotations

import pytest

from eda_agent.design.footprint_audit import (
    LandPatternSpec,
    audit_footprint_against_spec,
    expand_spec_pads,
)

_SRC = {
    "datasheet_url": "https://example.invalid/part.pdf",
    "reference": "Figure 9-1, p. 30",
    "part_number": "GENERIC-8",
}


def _soic8_spec(**overrides) -> LandPatternSpec:
    body = {
        "source": _SRC,
        "dual_row": {
            "count": 8, "pitch": 1.27, "span": 5.4,
            "pad_w": 2.0, "pad_h": 0.6, "shape": "rectangular",
        },
    }
    body.update(overrides)
    return LandPatternSpec.model_validate(body)


def _pad(name, x, y, w, h, shape="rectangular", **extra) -> dict:
    d = {
        "name": str(name), "x_mm": x, "y_mm": y, "w_mm": w, "h_mm": h,
        "shape": shape, "corner_pct": 0, "rotation": 0.0,
        "hole_mm": 0.0, "hole_w_mm": 0.0, "hole_type": "round",
        "plated": True, "layer": "top",
        "paste_expansion_mm": 0.0, "paste_expansion_source": "rule",
        "mask_expansion_mm": 0.0, "mask_expansion_source": "rule",
    }
    d.update(extra)
    return d


def _soic8_pads(dx=0.0, dy=0.0, mirror=False):
    pads = []
    for p in expand_spec_pads(_soic8_spec()):
        x = -p.x if mirror else p.x
        pads.append(_pad(p.name, x + dx, p.y + dy, p.w, p.h))
    return pads


def _fp(pads) -> dict:
    return {"name": "FP", "description": "", "pad_count": len(pads),
            "pads": pads}


def test_dual_row_expansion_is_soic_ccw():
    pads = expand_spec_pads(_soic8_spec())
    by_name = {p.name: p for p in pads}
    assert len(pads) == 8
    # Pin 1 top of the LEFT column, pin 4 bottom-left, pin 5 bottom-
    # right, pin 8 top-right: the CCW wrap.
    assert by_name["1"].x < 0 and by_name["1"].y > 0
    assert by_name["4"].x < 0 and by_name["4"].y < 0
    assert by_name["5"].x > 0 and by_name["5"].y < 0
    assert by_name["8"].x > 0 and by_name["8"].y > 0
    # Column pitch.
    assert by_name["1"].y - by_name["2"].y == pytest.approx(1.27)


def test_faithful_footprint_passes_with_offset_origin():
    report = audit_footprint_against_spec(
        _soic8_spec(), _fp(_soic8_pads(dx=13.7, dy=-4.2)))
    assert report["ok"], report["findings"]
    assert report["findings"] == []


def test_faithful_footprint_passes_rotated_90():
    spec = _soic8_spec()
    pads = []
    for p in expand_spec_pads(spec):
        # Rotate the whole pattern +90: (x,y) -> (-y,x); pad body drawn
        # rotated too, so its extents swap.
        pads.append(_pad(p.name, -p.y, p.x, p.h, p.w))
    report = audit_footprint_against_spec(spec, _fp(pads))
    assert report["ok"], report["findings"]
    assert report["rotation_applied_deg"] in (90, 270)


def test_mirrored_pattern_fails():
    report = audit_footprint_against_spec(
        _soic8_spec(), _fp(_soic8_pads(mirror=True)))
    assert not report["ok"]
    checks = {f["check"] for f in report["findings"]}
    assert "pad_sequence" in checks or "pad_position" in checks


def test_wrong_pitch_is_position_errors():
    spec = _soic8_spec()
    pads = []
    for p in expand_spec_pads(_soic8_spec()):
        pads.append(_pad(p.name, p.x, p.y * (1.0 / 1.27), p.w, p.h))
    report = audit_footprint_against_spec(spec, _fp(pads))
    assert not report["ok"]
    assert any(f["check"] == "pad_position" for f in report["findings"])


def test_missing_pad_reports_count():
    pads = _soic8_pads()[:-1]
    report = audit_footprint_against_spec(_soic8_spec(), _fp(pads))
    assert not report["ok"]
    counts = [f for f in report["findings"] if f["check"] == "pad_count"]
    assert counts and counts[0]["expected"] == "8" \
        and counts[0]["actual"] == "7"


def test_extra_pad_reported():
    pads = _soic8_pads() + [_pad("9", 9.9, 9.9, 0.6, 0.6)]
    report = audit_footprint_against_spec(_soic8_spec(), _fp(pads))
    assert any(f["check"] == "pad_extra" and f["pad"] == "9"
               for f in report["findings"])


def test_pad_size_out_of_tolerance():
    pads = _soic8_pads()
    pads[2]["w_mm"] += 0.2  # 0.2 mm over on one pad
    report = audit_footprint_against_spec(_soic8_spec(), _fp(pads))
    sized = [f for f in report["findings"] if f["check"] == "pad_size"]
    assert len(sized) == 1 and sized[0]["pad"] == "3"


def test_pad_size_within_tolerance_passes():
    pads = _soic8_pads()
    pads[2]["w_mm"] += 0.03  # under the 0.05 default
    report = audit_footprint_against_spec(_soic8_spec(), _fp(pads))
    assert report["ok"], report["findings"]


def test_swapped_designators_report_sequence():
    pads = _soic8_pads()
    pads[0]["name"], pads[1]["name"] = pads[1]["name"], pads[0]["name"]
    report = audit_footprint_against_spec(_soic8_spec(), _fp(pads))
    seq = [f for f in report["findings"] if f["check"] == "pad_sequence"]
    assert {f["pad"] for f in seq} == {"1", "2"}


def test_shape_mismatch_is_warning_not_error():
    pads = _soic8_pads()
    pads[0]["shape"] = "roundrectangle"
    report = audit_footprint_against_spec(_soic8_spec(), _fp(pads))
    shapes = [f for f in report["findings"] if f["check"] == "pad_shape"]
    assert shapes and shapes[0]["severity"] == "warning"
    assert report["ok"]  # warnings alone leave ok true


def test_unexpected_drill_on_smd_pad():
    pads = _soic8_pads()
    pads[4]["hole_mm"] = 0.3
    report = audit_footprint_against_spec(_soic8_spec(), _fp(pads))
    assert any(f["check"] == "pad_hole" for f in report["findings"])
    assert not report["ok"]


def test_thermal_pad_windowed_paste_policy():
    spec = LandPatternSpec.model_validate({
        "source": _SRC,
        "quad": {"count": 20, "pitch": 0.5, "span_x": 4.8, "span_y": 4.8,
                 "pad_w": 0.25, "pad_h": 0.8, "shape": "rectangular"},
        "thermal_pad": {"name": "21", "x": 0.0, "y": 0.0,
                        "w": 3.2, "h": 3.2, "shape": "rectangular"},
        "thermal_paste": "windowed",
    })
    pads = [
        _pad(p.name, p.x, p.y, p.w, p.h)
        for p in expand_spec_pads(spec)
    ]
    # Full-face rule paste on the EP: must warn.
    report = audit_footprint_against_spec(spec, _fp(pads))
    assert any(f["check"] == "thermal_paste" for f in report["findings"])

    # Manual negative expansion (suppressed full-face paste): no warning.
    pads[-1]["paste_expansion_source"] = "manual"
    pads[-1]["paste_expansion_mm"] = -3.2
    report2 = audit_footprint_against_spec(spec, _fp(pads))
    assert not any(f["check"] == "thermal_paste"
                   for f in report2["findings"])


def test_quad_expansion_count_and_ccw():
    spec = LandPatternSpec.model_validate({
        "source": _SRC,
        "quad": {"count": 16, "pitch": 0.65, "span_x": 4.6, "span_y": 4.6,
                 "pad_w": 0.35, "pad_h": 1.0},
    })
    pads = expand_spec_pads(spec)
    by_name = {p.name: p for p in pads}
    assert len(pads) == 16
    assert by_name["1"].x < 0 and by_name["1"].y > 0      # left side top
    assert by_name["5"].y < 0 and by_name["5"].x < 0      # bottom left
    assert by_name["9"].x > 0 and by_name["9"].y < 0      # right bottom
    assert by_name["13"].y > 0 and by_name["13"].x > 0    # top right
    # Left-side pads are wide along X (rotated relative to bottom row).
    assert by_name["1"].w == pytest.approx(1.0)
    assert by_name["5"].w == pytest.approx(0.35)


def test_spec_requires_source_citation():
    with pytest.raises(Exception):
        LandPatternSpec.model_validate({
            "dual_row": {"count": 8, "pitch": 1.27, "span": 5.4,
                         "pad_w": 2.0, "pad_h": 0.6},
        })
