# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Pin the synthetic benchmark's blind spot so nobody measures against it.

The neatness work is judged largely on synthetic benchmark plans. Those
plans build symbols through ``_synth_symbol``, which splits pins into
left/right columns by INDEX and types every pin ``passive``. It cannot
do better: a DesignPlan carries no pin direction at all (``PinRef`` is
refdes + pin, ``Net.role`` is functional rather than directional).

The consequence is easy to trip over and hard to see. On the 555 plan,
pin 3 (OUT) falls in the LEFT column, so a placer honouring pin sides
correctly draws the output branch to the LEFT of the IC, and
``_count_pin_side_violations`` reports zero -- which is right. Anyone
adding a left-to-right flow metric and evaluating it here would be
scoring the symbol generator, not the layout engine, and would "fix"
the engine until it matched an artefact.

These tests state that invariant out loud. If synthetic symbols ever
gain real directions, they fail, which is the signal to revisit the
benchmarks rather than a regression.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eda_agent.design.benchmark import SyntheticSymbolExtractor, _synth_symbol
from eda_agent.design.plan import DesignPlan

_PLANS = Path(__file__).resolve().parents[1] / "benchmarks" / "plans"


def test_synthetic_pins_carry_no_direction():
    """Every synthetic pin is 'passive', so direction cannot be inferred."""
    sym = _synth_symbol("L.SchLib", "IC8", [str(i) for i in range(1, 9)], "U")
    types = {p.electrical_type for p in sym.pins}
    assert types == {"passive"}, (
        f"synthetic pins now carry {types}; if directions became real, "
        f"the flow-metric caveat in _synth_symbol needs revisiting")


def test_pin_sides_are_index_split_not_direction_split():
    """Columns follow DIP index order, which is what makes flow arbitrary."""
    ids = [str(i) for i in range(1, 9)]
    sym = _synth_symbol("L.SchLib", "IC8", ids, "U")
    by_id = {p.designator: p for p in sym.pins}
    left = {d for d, p in by_id.items() if p.x < 0}
    right = {d for d, p in by_id.items() if p.x > 0}
    assert left == {"1", "2", "3", "4"}
    assert right == {"5", "6", "7", "8"}


def test_555_output_pin_lands_on_the_left_column():
    """The concrete trap: OUT is pin 3, so it faces LEFT here.

    A layout that puts the LED branch left of the IC is CORRECT for this
    symbol. Treating that as a signal-flow defect would be chasing an
    artefact of the generator.
    """
    plan = DesignPlan.model_validate(
        json.loads((_PLANS / "blinker555.json").read_text()))
    ex = SyntheticSymbolExtractor(plan)

    u1 = next(p for p in plan.parts if p.refdes == "U1")
    sym = ex.extract_one(u1.lib_path or "", u1.lib_ref)
    assert sym is not None, "555 symbol missing from the synthetic extractor"

    out_net = next(n for n in plan.nets if n.name == "OUT")
    out_pin_id = next(p.pin for p in out_net.pins if p.refdes == "U1")
    out_pin = next(p for p in sym.pins if p.designator == out_pin_id)
    assert out_pin.x < 0, (
        "OUT now faces right; the benchmark can express signal flow, so "
        "the caveat in _synth_symbol and this test should be revisited")


def test_plan_schema_still_carries_no_pin_direction():
    """The root cause. If PinRef gains a direction, fix the generator."""
    from eda_agent.design.plan import PinRef

    fields = set(PinRef.model_fields)
    assert fields == {"refdes", "pin"}, (
        f"PinRef now has {fields}; if it carries direction, _synth_symbol "
        f"can assign real pin sides and the benchmarks can finally judge "
        f"left-to-right flow")


@pytest.mark.parametrize("plan_name", ["blinker555", "buck", "mcu"])
def test_every_benchmark_shares_the_blind_spot(plan_name):
    """Not a 555 quirk: it applies to every synthetic plan."""
    plan = DesignPlan.model_validate(
        json.loads((_PLANS / f"{plan_name}.json").read_text()))
    ex = SyntheticSymbolExtractor(plan)
    seen = 0
    for part in plan.parts:
        sym = ex.extract_one(part.lib_path or "", part.lib_ref)
        if sym is None:
            continue
        seen += 1
        assert {p.electrical_type for p in sym.pins} == {"passive"}
    assert seen, f"no symbols extracted for {plan_name}"
