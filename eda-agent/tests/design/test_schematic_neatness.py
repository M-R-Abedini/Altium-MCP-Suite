# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the interpretable schematic-neatness report (pure diagnostic)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eda_agent.design.plan import DesignPlan
from eda_agent.design.benchmark import SyntheticSymbolExtractor
from eda_agent.design.pipeline import build_best_canvas_from_plan
from eda_agent.design.schematic_neatness import neatness_report, NeatnessReport

_PLANS = Path(__file__).resolve().parents[1] / "benchmarks" / "plans"


def _canvas(name):
    plan = DesignPlan.model_validate(json.loads((_PLANS / f"{name}.json").read_text()))
    ex = SyntheticSymbolExtractor(plan)
    return build_best_canvas_from_plan(plan, ex).canvas, plan


@pytest.mark.parametrize("name", ["blinker555", "buck", "mcu"])
def test_report_is_well_formed(name):
    canvas, plan = _canvas(name)
    r = neatness_report(canvas, plan)
    assert isinstance(r, NeatnessReport)
    # spread is a real, positive bbox
    assert r.spread_w_mils > 0 and r.spread_h_mils > 0
    # fractions are in range
    assert 0.0 <= r.label_fallback_frac <= 1.0
    assert r.detour_ratio >= 0.0
    assert r.bends_per_signal_net >= 0.0
    assert r.bends_per_power_net >= 0.0
    # the engine routes orthogonally
    assert r.diagonal_wires == 0
    # every rail net has an entry in the fragmentation map
    rails = {n.name for n in plan.nets if n.is_power or n.is_ground}
    assert set(r.port_fragmentation) == rails


def test_detour_ratio_near_or_below_one_signal():
    # Signal routing is near-optimal (label fallback can push it <1); a huge
    # detour would mean a router regression.
    canvas, plan = _canvas("blinker555")
    r = neatness_report(canvas, plan)
    assert r.detour_ratio <= 1.6


def test_summary_renders_all_metrics():
    canvas, plan = _canvas("buck")
    s = neatness_report(canvas, plan).summary()
    for token in ("spread", "wire length", "label soup", "bends/net",
                  "ports/rail", "straddles"):
        assert token in s


def test_labeled_nets_consistent_with_fraction():
    canvas, plan = _canvas("blinker555")
    r = neatness_report(canvas, plan)
    multi = [n for n in plan.nets
             if len(n.pins) >= 2 and not (n.is_power or n.is_ground)]
    assert r.label_fallback_frac == pytest.approx(len(r.labeled_nets) / len(multi))


def test_bends_stay_within_the_professional_bound():
    """Both bend budgets stay inside this module's own thresholds.

    This replaces an earlier ``signal <= power`` comparison. That form
    was a proxy: it held only while power routing was the bendy part, so
    IMPROVING power routing broke it. It had already been relaxed once
    (``<`` to ``<=``) when signal routing caught up, and the repair-port
    stub upgrade broke it again by pulling the 555 board's power bends
    from 2.0 to 1.0 -- which is the direction ``flags()`` explicitly asks
    for ("with straight stubs ~1", "straight stubs would cut this").

    A test that fails when the code gets better is measuring the wrong
    thing, so this asserts the property directly instead: each bend
    budget within the bound the module itself uses to decide something is
    wrong. Absolute bounds cannot be satisfied by degrading the other
    side of a comparison.

    No absolute POWER bound is asserted here. Under the shrunk FD sweep
    the test conftest uses for speed, buck settles at 4.67 bends/rail-net
    where the production sweep gives 3.67, so any bound tight enough to
    be meaningful would be failing on a fixture artifact rather than on
    the code. Power bends are covered directly instead by
    ``test_stub_upgrade_*`` in test_pipeline, which measures the same
    board with and without the pass.
    """
    for name in ("buck", "blinker555"):
        canvas, plan = _canvas(name)
        r = neatness_report(canvas, plan)
        # The same number flags() uses, so the test and the reported
        # quality bar cannot drift apart.
        assert r.bends_per_signal_net <= 3.0, (
            f"{name}: signal nets no longer route near-minimally "
            f"({r.bends_per_signal_net} bends/net)")


def test_deterministic():
    canvas, plan = _canvas("buck")
    assert neatness_report(canvas, plan) == neatness_report(canvas, plan)


# --- flags() prioritised diagnosis (constructed reports, no builds) ----------

def _report(**kw):
    base = dict(
        spread_w_mils=1000, spread_h_mils=1000, spread_aspect=1.0,
        signal_wire_mils=100, power_wire_mils=100, detour_ratio=1.0,
        label_fallback_frac=0.0, labeled_nets=(),
        bends_per_signal_net=1.0, bends_per_power_net=1.0, diagonal_wires=0,
        port_fragmentation={}, straddle_nets=0, four_way_junctions=0,
        duplicate_wires=0)
    base.update(kw)
    return NeatnessReport(**base)


def test_flags_empty_when_clean():
    assert _report().flags() == []


def test_flags_diagonal_is_highest_severity():
    f = _report(diagonal_wires=2, duplicate_wires=1).flags()
    assert f[0].startswith("non-orthogonal")          # severity 9 first
    assert any("duplicate" in m for m in f)            # severity 1 present, later


def test_flags_power_spoke_clutter_detected():
    f = _report(bends_per_power_net=13.5).flags()
    assert any("power L-spoke" in m for m in f)


def test_flags_straddle_and_labels():
    f = _report(straddle_nets=2, label_fallback_frac=0.25,
                labeled_nets=("THR",)).flags()
    assert any("pin-straddle" in m for m in f)
    assert any("label fallback" in m for m in f)


def test_flags_ordered_by_severity():
    # 4-way (severity 7) should rank above label fallback (severity 3)
    f = _report(four_way_junctions=1, label_fallback_frac=0.5,
                labeled_nets=("A",)).flags()
    i_4way = next(i for i, m in enumerate(f) if "4-way" in m)
    i_label = next(i for i, m in enumerate(f) if "label fallback" in m)
    assert i_4way < i_label
