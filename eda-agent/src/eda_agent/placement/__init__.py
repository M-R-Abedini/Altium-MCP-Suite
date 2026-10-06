# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Physical PCB auto-placement.

A connectivity-driven placement planner for real boards. Given the
current component set (with real footprint bounding boxes), the
compiled netlist, and the board outline, it produces an improved set of
component positions that shortens estimated wirelength while keeping
components inside the board and free of overlaps.

The solver is pure Python with no Altium dependency, so it is fully
unit-testable offline. The MCP tool ``pcb_plan_placement`` assembles the
solver's inputs from ``pcb.get_components`` and the compiled netlist,
runs the solver, and returns a dry-run move list (it only mutates the
board when explicitly told to apply).

Algorithm (after Cypress / classical analytical PCB placement, adapted
to a force-directed core):

1. **Global placement** -- star-model spring attraction along nets plus
   axis-aligned bounding-box repulsion, with a cooling schedule. Seeds
   from the board's current positions so it refines rather than
   scrambles.
2. **Legalization** -- a deterministic hard-shove pass that removes
   residual bounding-box overlaps (same layer only) and snaps to grid,
   respecting the board region and any fixed/locked components.

Quality is reported as half-perimeter wirelength (HPWL) before and
after, plus the overlapping-pair count before and after.

This package targets *only* the current project's board; it reads no
cross-project data (NDA isolation -- see :mod:`eda_agent.design`).
"""

from eda_agent.placement.autoplace import (
    BoardRegion,
    PlaceComp,
    PlaceNet,
    PlaceOptions,
    PlacePin,
    PlaceResult,
    hpwl,
    overlap_pair_count,
    pin_hpwl,
    plan_placement,
)
from eda_agent.placement.autoplace import _rotate_offset as rotate_offset

__all__ = [
    "BoardRegion",
    "PlaceComp",
    "PlaceNet",
    "PlaceOptions",
    "PlacePin",
    "PlaceResult",
    "hpwl",
    "overlap_pair_count",
    "pin_hpwl",
    "plan_placement",
    "rotate_offset",
]
