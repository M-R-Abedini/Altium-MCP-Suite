# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Offline tests for the KiCad .kicad_mod footprint writer."""

from __future__ import annotations

from eda_agent.export.kicad_footprint import format_kicad_footprint

# Two SMD pads (an 0805-ish chip) at +/-X, plus one through-hole pad.
_SMD_PADS = [
    {"name": "1", "x_mils": -40, "y_mils": 0, "size_x_mils": 50,
     "size_y_mils": 60, "shape": "Rectangular", "layer": "top", "hole_mils": 0,
     "rotation": 0},
    {"name": "2", "x_mils": 40, "y_mils": 0, "size_x_mils": 50,
     "size_y_mils": 60, "shape": "Rectangular", "layer": "top", "hole_mils": 0,
     "rotation": 0},
]
_THT_PAD = {
    "name": "3", "x_mils": 0, "y_mils": 100, "size_x_mils": 60,
    "size_y_mils": 60, "shape": "Round", "layer": "multi", "hole_mils": 30,
    "rotation": 0,
}


def test_header_and_smd_attr() -> None:
    out = format_kicad_footprint("R0805", _SMD_PADS)
    assert out.startswith('(footprint "R0805"')
    assert "(attr smd)" in out
    assert '(layer "F.Cu")' in out


def test_pad_count_and_names() -> None:
    out = format_kicad_footprint("R0805", _SMD_PADS)
    assert out.count("(pad ") == 2
    assert '(pad "1" smd rect' in out
    assert '(pad "2" smd rect' in out


def test_mil_to_mm_and_y_flip() -> None:
    out = format_kicad_footprint("R0805", _SMD_PADS)
    # pad 1 at x=-40 mil -> -1.016 mm; y stays 0.
    assert "(at -1.0160 0.0000)" in out
    # size 50x60 mil -> 1.27 x 1.524 mm.
    assert "(size 1.2700 1.5240)" in out


def test_y_is_negated() -> None:
    # A pad at +100 mil y (Altium, up) becomes -2.54 mm (KiCad, down).
    out = format_kicad_footprint("X", [_THT_PAD])
    assert "(at 0.0000 -2.5400" in out


def test_through_hole_pad_gets_drill_and_attr() -> None:
    out = format_kicad_footprint("X", [_THT_PAD])
    assert "(attr through_hole)" in out
    assert '(pad "3" thru_hole circle' in out
    assert "(drill 0.7620)" in out          # 30 mil
    assert '(layers "*.Cu" "*.Mask")' in out


def test_smd_pad_layer_set_top_vs_bottom() -> None:
    top = format_kicad_footprint("X", [_SMD_PADS[0]])
    assert '(layers "F.Cu" "F.Paste" "F.Mask")' in top
    bottom_pad = dict(_SMD_PADS[0], layer="bottom")
    bottom = format_kicad_footprint("X", [bottom_pad])
    assert '(layers "B.Cu" "B.Paste" "B.Mask")' in bottom


def test_roundrect_gets_rratio() -> None:
    pad = dict(_SMD_PADS[0], shape="RoundRectangle")
    out = format_kicad_footprint("X", [pad])
    assert "roundrect" in out
    assert "(roundrect_rratio 0.25)" in out


def test_rotation_is_emitted_and_flipped() -> None:
    pad = dict(_SMD_PADS[0], rotation=90)
    out = format_kicad_footprint("X", [pad])
    # 90 deg, negated with the y-flip -> 270.
    assert "270" in out


def test_value_text_is_footprint_name() -> None:
    out = format_kicad_footprint("SOT23", _SMD_PADS)
    assert '(fp_text value "SOT23"' in out
    assert '(fp_text reference "REF**"' in out


def test_trailing_newline_and_closed_paren() -> None:
    out = format_kicad_footprint("X", _SMD_PADS)
    assert out.endswith(")\n")
