# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""End-to-end validation of the geometric net solver via the design pipeline.

This closes the loop with committed fixtures only (the benchmark plans): emit
a plan through the offline pipeline, then reconstruct its netlist with the
solver and assert it matches the plan's intended connectivity. Because the
pipeline (the writer) and the solver (the reader) are independent and both
agree with the plan (a third, independent spec), a match validates all three,
including by-name / net-label connectivity on a label-heavy board (the
buck emits 9 labels), which live Altium alone could not cover here.
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

import pytest

from eda_agent.design.benchmark import SyntheticSymbolExtractor
from eda_agent.design.pipeline import build_best_canvas_from_plan
from eda_agent.design.plan import DesignPlan
from eda_agent.fileio.netlist_solver import solve_nets

_PLANS = Path(__file__).resolve().parent / "benchmarks" / "plans"


@functools.lru_cache(maxsize=None)
def _emit_and_solve(plan_name: str):
    plan = DesignPlan.model_validate(
        json.loads((_PLANS / f"{plan_name}.json").read_text()))
    canvas = build_best_canvas_from_plan(
        plan, SyntheticSymbolExtractor(plan)).canvas

    plan_groups: dict[str, set] = {}
    pins = []
    for net in plan.nets:
        for ref in net.pins:
            key = (ref.refdes, str(ref.pin))
            plan_groups.setdefault(net.name, set()).add(f"{key[0]}.{key[1]}")
            ep = canvas.pin_world(key[0], key[1])
            if ep is not None:
                pins.append({"component": key[0], "pin": key[1],
                             "x": ep.x, "y": ep.y})

    solved = solve_nets(
        pins,
        [{"x1": w.x1, "y1": w.y1, "x2": w.x2, "y2": w.y2} for w in canvas.wires],
        [{"x": p.x, "y": p.y, "name": p.text} for p in canvas.power_ports],
        [{"x": j.x, "y": j.y} for j in canvas.junctions],
        [{"x": lb.x, "y": lb.y, "name": lb.text} for lb in canvas.labels])

    solver_groups: dict[str, set] = {}
    for pin_id, net in solved["pin_nets"].items():
        solver_groups.setdefault(net, set()).add(pin_id)
    return plan_groups, solver_groups, solved


# Scoped to the buck: a deterministic, clean emit that exercises the solver's
# full envelope (wire + power port + junction + net-label / by-name) on a
# label-heavy board. The mcu / blinker555 plans are NOT asserted here because
# best_of layout is non-deterministic in which variant wins, and some variants
# leave a signal-net label floating (a known pipeline label-fallback case);
# the solver then correctly reports that net disconnected, so a strict
# plan-equals-solver assertion would be flaky through no fault of the solver.
# (mcu's bus is handled via its per-pin labels; buses need no special support.)
@pytest.mark.parametrize("plan_name", ["buck"])
def test_solver_reconstructs_pipeline_netlist(plan_name):
    plan_groups, solver_groups, solved = _emit_and_solve(plan_name)
    # Compare by pin-membership (grouping), not name: the pipeline leaves some
    # short local nets unlabeled, which the solver auto-names: the grouping
    # is the connectivity that matters.
    plan_sets = {frozenset(v) for v in plan_groups.values()}
    solver_sets = {frozenset(v) for v in solver_groups.values()}
    assert plan_sets == solver_sets, (
        f"{plan_name}: solver groupings diverge from the plan\n"
        f"  only in plan:   {plan_sets - solver_sets}\n"
        f"  only in solver: {solver_sets - plan_sets}")


def test_clean_emit_has_no_shorts():
    # A correctly-emitted board reconstructs with no name conflicts (shorts).
    _, _, solved = _emit_and_solve("buck")
    assert solved["name_conflicts"] == []


def test_named_rails_keep_their_names():
    # Power/label rails the plan names must survive reconstruction by name.
    plan_groups, solver_groups, _ = _emit_and_solve("buck")
    for rail in ("GND", "VIN", "VOUT"):
        assert rail in solver_groups, f"rail {rail} lost its name"
        assert solver_groups[rail] == plan_groups[rail]
