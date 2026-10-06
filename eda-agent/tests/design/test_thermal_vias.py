# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for thermal-via array sizing (Fourier conduction R = L/kA).

Hand-checked reference: a 0.3 mm DRILL with 25 um (1 oz) plating through a
1.6 mm board, unfilled barrel -> annulus ~0.0216 mm^2 -> ~192 K/W per via,
which is in the published ~100-200 K/W range for a single thermal via.

PLATING GROWS INWARD FROM THE DRILL WALL. The drill removes material and
copper is deposited on the wall of the hole it leaves, so the finished
hole is SMALLER than the drill and the barrel lies between
(r_drill - t) and r_drill.

These expectations previously assumed the annulus sat OUTSIDE the drill
radius, r_drill to r_drill + t, which places copper in space the drill
had already cleared. That overstated the barrel by 18 percent and
understated thermal resistance by the same factor, so every via count
came out low: 17 vias where 20 are needed. The numbers here are derived
from the geometry, not read back from the implementation.
"""

import math

import pytest

from eda_agent.design.thermal_vias import (
    via_barrel_area_mm2,
    single_via_thermal_resistance,
    via_array_thermal_resistance,
    vias_for_thermal_resistance,
    assess_thermal_vias,
)


# --------------------------------------------------------------------------- #
# Barrel area
# --------------------------------------------------------------------------- #
def test_barrel_area_is_annulus_when_unfilled():
    a = via_barrel_area_mm2(0.3, 25.0)
    # Copper between the finished hole and the drill wall:
    # pi * (0.15^2 - 0.125^2), r_inner = r_drill - 25um.
    assert a == pytest.approx(math.pi * (0.15 ** 2 - 0.125 ** 2), rel=1e-9)
    assert a == pytest.approx(0.02160, abs=1e-4)


def test_copper_fill_uses_full_circle():
    annulus = via_barrel_area_mm2(0.3, 25.0)
    filled = via_barrel_area_mm2(0.3, 25.0, filled_copper=True)
    # A filled via is copper across the DRILLED hole, not beyond it.
    assert filled == pytest.approx(math.pi * 0.15 ** 2, rel=1e-9)
    assert filled > annulus


def test_more_plating_more_copper():
    assert via_barrel_area_mm2(0.3, 35.0) > via_barrel_area_mm2(0.3, 25.0)


def test_barrel_area_rejects_bad_input():
    with pytest.raises(ValueError):
        via_barrel_area_mm2(0, 25)
    with pytest.raises(ValueError):
        via_barrel_area_mm2(0.3, -1)


# --------------------------------------------------------------------------- #
# Single-via resistance
# --------------------------------------------------------------------------- #
def test_single_via_matches_hand_calc():
    r = single_via_thermal_resistance(0.3, 25.0, 1.6)
    # 1.6e-3 m / (385 W/mK * 2.15984e-8 m^2)
    assert r == pytest.approx(192, abs=3)


def test_thicker_board_higher_resistance():
    # Longer conduction path -> more resistance (linear in length).
    r16 = single_via_thermal_resistance(0.3, 25.0, 1.6)
    r32 = single_via_thermal_resistance(0.3, 25.0, 3.2)
    assert r32 == pytest.approx(2 * r16, rel=1e-9)


def test_copper_fill_lowers_resistance():
    barrel = single_via_thermal_resistance(0.3, 25.0, 1.6)
    filled = single_via_thermal_resistance(0.3, 25.0, 1.6, filled_copper=True)
    assert filled < barrel


def test_single_via_rejects_bad_input():
    with pytest.raises(ValueError):
        single_via_thermal_resistance(0.3, 25.0, 0)
    with pytest.raises(ValueError):
        single_via_thermal_resistance(0.3, 25.0, 1.6, k_cu=0)


# --------------------------------------------------------------------------- #
# Array
# --------------------------------------------------------------------------- #
def test_array_is_parallel():
    r1 = single_via_thermal_resistance(0.3, 25.0, 1.6)
    r9 = via_array_thermal_resistance(9, 0.3, 25.0, 1.6)
    assert r9 == pytest.approx(r1 / 9, rel=1e-9)


def test_array_rejects_zero_count():
    with pytest.raises(ValueError):
        via_array_thermal_resistance(0, 0.3, 25.0, 1.6)


# --------------------------------------------------------------------------- #
# Inverse: count for a target
# --------------------------------------------------------------------------- #
def test_vias_for_target_resistance_rounds_up():
    r1 = single_via_thermal_resistance(0.3, 25.0, 1.6)  # ~163
    n = vias_for_thermal_resistance(10.0, 0.3, 25.0, 1.6)
    assert n == math.ceil(r1 / 10.0)  # 17
    # The resulting array actually meets the target.
    assert via_array_thermal_resistance(n, 0.3, 25.0, 1.6) <= 10.0


def test_looser_target_needs_fewer_vias():
    tight = vias_for_thermal_resistance(5.0, 0.3, 25.0, 1.6)
    loose = vias_for_thermal_resistance(20.0, 0.3, 25.0, 1.6)
    assert loose < tight


def test_vias_for_target_rejects_bad_input():
    with pytest.raises(ValueError):
        vias_for_thermal_resistance(0, 0.3, 25.0, 1.6)


# --------------------------------------------------------------------------- #
# Aggregator
# --------------------------------------------------------------------------- #
def test_assess_from_power_and_delta_t():
    # 2 W, 20 C budget -> target 10 K/W -> 17 vias (~163/10).
    rep = assess_thermal_vias(0.3, 25.0, 1.6, power_w=2.0, delta_t_c=20.0)
    assert rep.target_k_per_w == pytest.approx(10.0)
    assert rep.via_count == 20   # ceil(192.41 / 10)
    assert rep.array_k_per_w <= 10.0
    # The realized rise sits at or under the budget.
    assert rep.temp_rise_c is not None and rep.temp_rise_c <= 20.0


def test_assess_from_explicit_target():
    rep = assess_thermal_vias(0.3, 25.0, 1.6, target_k_per_w=10.0)
    assert rep.via_count == 20   # ceil(192.41 / 10)
    assert rep.temp_rise_c is None  # no power given


def test_assess_scores_an_explicit_count():
    rep = assess_thermal_vias(0.3, 25.0, 1.6, via_count=9, power_w=2.0)
    assert rep.via_count == 9
    assert rep.array_k_per_w == pytest.approx(rep.single_via_k_per_w / 9)
    assert rep.temp_rise_c == pytest.approx(2.0 * rep.array_k_per_w)


def test_assess_composes_with_required_theta_ja():
    # The via-array target can come from required_theta_ja: a part dissipating
    # 1.5 W to stay under Tj=125 C from 25 C ambient needs theta_JA <= 66.7;
    # if the package contributes 40, the board must add <= 26.7 K/W.
    from eda_agent.design.component_values import required_theta_ja
    theta_total = required_theta_ja(1.5, 125.0, 25.0)
    assert theta_total == pytest.approx(66.67, abs=0.1)
    board_budget = theta_total - 40.0  # ~26.67 K/W
    rep = assess_thermal_vias(0.3, 25.0, 1.6, target_k_per_w=board_budget)
    assert rep.array_k_per_w <= board_budget
    assert rep.via_count == 8  # ceil(192.41 / 26.67)


def test_assess_requires_some_target():
    with pytest.raises(ValueError):
        assess_thermal_vias(0.3, 25.0, 1.6)  # no target, no count


def test_assess_is_deterministic():
    a = assess_thermal_vias(0.3, 25.0, 1.6, power_w=2.0, delta_t_c=20.0)
    b = assess_thermal_vias(0.3, 25.0, 1.6, power_w=2.0, delta_t_c=20.0)
    assert a == b
