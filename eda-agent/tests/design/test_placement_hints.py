# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the agent-in-loop placement_hints API.

The hints API lets an agent (or any caller) override specific refdes
positions after compute_layout has run, without having to re-author
the whole plan. This is the contract every iteration of the
agent-in-loop refinement loop depends on.
"""

from __future__ import annotations

from typing import Optional

import pytest

from eda_agent.design.layout import PlacedPart
from eda_agent.design.pipeline import (
    PipelineNote,
    PipelineResult,
    _apply_placement_hints,
    build_canvas_from_plan,
)
from eda_agent.design.plan import DesignPlan
from eda_agent.design.symbols import (
    SymbolBBox,
    SymbolExtractor,
    SymbolModel,
    SymbolPin,
)


_LIB = "/fake/lib.SchLib"


class MockExtractor(SymbolExtractor):
    def __init__(self, symbols: dict[tuple[str, str], SymbolModel]) -> None:
        self._symbols = symbols

    def extract_one(self, lib_path: str, lib_ref: str) -> Optional[SymbolModel]:
        return self._symbols.get((lib_path, lib_ref))

    def extract_many(self, refs):
        return {
            (lp, lr): self._symbols[(lp, lr)]
            for (lp, lr) in refs
            if (lp, lr) in self._symbols
        }


def _passive(lib_ref: str) -> SymbolModel:
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


# ---------------------- _apply_placement_hints ----------------------


def test_hint_overrides_x_y_rotation():
    placements = [
        PlacedPart(refdes="R1", sheet="main", x_mils=1000, y_mils=1000, rotation=0),
        PlacedPart(refdes="R2", sheet="main", x_mils=2000, y_mils=2000, rotation=0),
    ]
    hints = {"R1": {"x": 5000, "y": 5000, "rotation": 90}}
    result = PipelineResult()
    out = _apply_placement_hints(placements, hints, result)
    by_refdes = {p.refdes: p for p in out}
    assert (by_refdes["R1"].x_mils, by_refdes["R1"].y_mils, by_refdes["R1"].rotation) == (5000, 5000, 90)
    # R2 unchanged.
    assert (by_refdes["R2"].x_mils, by_refdes["R2"].y_mils, by_refdes["R2"].rotation) == (2000, 2000, 0)


def test_partial_hint_preserves_unspecified_fields():
    """Hint with only x specified should keep existing y + rotation."""
    placements = [
        PlacedPart(refdes="R1", sheet="main", x_mils=1000, y_mils=1500, rotation=90),
    ]
    hints = {"R1": {"x": 3000}}  # y and rotation omitted
    result = PipelineResult()
    out = _apply_placement_hints(placements, hints, result)
    p = out[0]
    assert p.x_mils == 3000
    assert p.y_mils == 1500  # preserved
    assert p.rotation == 90  # preserved


def test_hint_snaps_to_100_mil_grid():
    placements = [
        PlacedPart(refdes="R1", sheet="main", x_mils=1000, y_mils=1000, rotation=0),
    ]
    hints = {"R1": {"x": 5047, "y": 5083}}
    result = PipelineResult()
    out = _apply_placement_hints(placements, hints, result)
    assert out[0].x_mils == 5000
    assert out[0].y_mils == 5000


def test_unknown_refdes_hint_logs_warning():
    placements = [
        PlacedPart(refdes="R1", sheet="main", x_mils=1000, y_mils=1000, rotation=0),
    ]
    hints = {"R99": {"x": 5000, "y": 5000}}
    result = PipelineResult()
    out = _apply_placement_hints(placements, hints, result)
    # R1 unchanged.
    assert out[0].x_mils == 1000
    # Warning surfaced.
    warnings = [n for n in result.notes if n.severity == "warning"]
    assert any("R99" in w.text for w in warnings)


def test_no_hints_passes_placements_through():
    placements = [
        PlacedPart(refdes="R1", sheet="main", x_mils=1000, y_mils=1000, rotation=0),
    ]
    result = PipelineResult()
    out = _apply_placement_hints(placements, {}, result)
    assert out == placements
    # No info note added either (zero hints = no-op).
    assert not any("placement hint" in n.text for n in result.notes)


# ---------------------- build_canvas_from_plan integration ----------------------


def test_pipeline_respects_placement_hints():
    """End-to-end: passing placement_hints to build_canvas_from_plan
    anchors the hinted refdes at the requested coordinate on the canvas."""
    symbols = {(_LIB, "RES"): _passive("RES")}
    plan = DesignPlan.model_validate({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": [
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
            {"refdes": "R2", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
        ],
        "nets": [
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "R1", "pin": "1"},
                {"refdes": "R2", "pin": "1"}]},
            {"name": "SIG", "pins": [
                {"refdes": "R1", "pin": "2"},
                {"refdes": "R2", "pin": "2"}]},
        ],
    })
    hints = {"R1": {"x": 4000, "y": 4000, "rotation": 0}}
    result = build_canvas_from_plan(
        plan, MockExtractor(symbols), placement_hints=hints,
    )
    assert result.ok, [f.text for f in result.failures]
    r1 = next(i for i in result.canvas.instances if i.refdes == "R1")
    assert r1.x == 4000
    assert r1.y == 4000
    # And the pipeline noted that hints were applied.
    assert any("placement hint" in n.text.lower() for n in result.notes)
