# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Offline tests for the layer-stack CSV report formatter."""

from __future__ import annotations

from eda_agent.export.stackup_csv import (
    format_stackup_csv,
    stackup_total_thickness_mils,
)

# A representative 2-layer stackup as pcb.get_layer_stackup returns it: each
# entry is a copper layer plus the dielectric beneath it.
_TWO_LAYER = {
    "board_name": "demo",
    "layer_count": 2,
    "layers": [
        {"name": "Top Layer", "order": 0, "copper_thickness_mils": 1.4,
         "dielectric_type": "Core", "dielectric_height_mils": 59.0,
         "dielectric_constant": 4.2},
        {"name": "Bottom Layer", "order": 1, "copper_thickness_mils": 1.4,
         "dielectric_type": "none", "dielectric_height_mils": 0,
         "dielectric_constant": 0},
    ],
}


def _rows(csv_text: str) -> list[list[str]]:
    return [line.split(",") for line in csv_text.strip().split("\n")]


def test_header_and_row_shape() -> None:
    rows = _rows(format_stackup_csv(_TWO_LAYER))
    assert rows[0] == [
        "index", "layer", "type", "material",
        "thickness_mil", "thickness_mm", "dielectric_constant",
    ]
    # 2 copper rows + 1 dielectric row (bottom has no dielectric).
    assert len(rows) == 1 + 3


def test_copper_then_dielectric_interleave() -> None:
    rows = _rows(format_stackup_csv(_TWO_LAYER))
    assert rows[1][1:3] == ["Top Layer", "copper"]
    assert rows[2][2] == "dielectric"
    assert rows[2][3] == "Core"
    assert rows[3][1:3] == ["Bottom Layer", "copper"]


def test_mil_to_mm_conversion() -> None:
    rows = _rows(format_stackup_csv(_TWO_LAYER))
    # 59 mil * 0.0254 = 1.4986 mm.
    assert rows[2][5] == "1.4986"
    # 1.4 mil copper -> 0.0356 mm.
    assert rows[1][5] == "0.0356"


def test_no_dielectric_row_when_absent_or_zero() -> None:
    # Bottom layer (dielectric_type none, height 0) emits no dielectric row.
    rows = _rows(format_stackup_csv(_TWO_LAYER))
    types = [r[2] for r in rows[1:]]
    assert types.count("dielectric") == 1


def test_indices_are_sequential() -> None:
    rows = _rows(format_stackup_csv(_TWO_LAYER))
    assert [r[0] for r in rows[1:]] == ["1", "2", "3"]


def test_total_thickness() -> None:
    # 1.4 + 59 + 1.4 + 0 = 61.8 mil.
    assert stackup_total_thickness_mils(_TWO_LAYER) == 61.8


def test_empty_stackup_yields_header_only() -> None:
    out = format_stackup_csv({"layers": []})
    assert out.strip().split("\n") == [
        "index,layer,type,material,thickness_mil,thickness_mm,dielectric_constant"
    ]
    assert stackup_total_thickness_mils({"layers": []}) == 0.0


def test_missing_keys_are_blank_not_error() -> None:
    # A layer with only a name must not raise; missing numerics go blank.
    out = format_stackup_csv({"layers": [{"name": "L1"}]})
    rows = _rows(out)
    assert rows[1][1] == "L1"
    assert rows[1][4] == "" and rows[1][5] == ""


def test_field_with_comma_is_quoted() -> None:
    out = format_stackup_csv(
        {"layers": [{"name": "Top, Inner", "copper_thickness_mils": 1.0}]})
    assert '"Top, Inner"' in out
