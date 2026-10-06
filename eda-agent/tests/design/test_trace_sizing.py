# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""IPC-2221 trace-sizing tests (inverse width-from-current)."""

from __future__ import annotations

import pytest

from eda_agent.design.trace_sizing import (
    copper_thickness_mils,
    current_capacity_amps,
    trace_resistance_mohm,
    trace_width_for_current,
)


def test_copper_thickness():
    assert copper_thickness_mils(1.0) == pytest.approx(1.378)
    assert copper_thickness_mils(2.0) == pytest.approx(2.756)
    with pytest.raises(ValueError):
        copper_thickness_mils(0)


def test_inverse_round_trips_forward():
    # width -> current -> width must return the original width.
    for w in (5.0, 12.0, 50.0, 200.0):
        for layer in ("external", "internal"):
            i = current_capacity_amps(w, copper_oz=1.0, delta_t_c=10.0,
                                      layer=layer)
            r = trace_width_for_current(i, copper_oz=1.0, delta_t_c=10.0,
                                        layer=layer, margin=0.0)
            assert r.min_width_mils == pytest.approx(w, rel=1e-9)


def test_known_value_1a_external():
    # 1 A, 1 oz, 10 degC external -> ~11.8 mil (matches IPC-2221 charts).
    r = trace_width_for_current(1.0, copper_oz=1.0, delta_t_c=10.0,
                                layer="external", margin=0.0)
    assert r.min_width_mils == pytest.approx(11.8, abs=0.3)


def test_internal_needs_more_width_than_external():
    # k halves on inner layers -> a much wider track for the same current.
    ext = trace_width_for_current(2.0, layer="external", margin=0.0)
    intl = trace_width_for_current(2.0, layer="internal", margin=0.0)
    assert intl.min_width_mils > ext.min_width_mils
    # Same dT, k ratio 2 -> width ratio 2^(1/0.725) ~ 2.6.
    assert intl.min_width_mils / ext.min_width_mils == pytest.approx(
        2 ** (1 / 0.725), rel=1e-6)


def test_more_current_needs_more_width():
    a = trace_width_for_current(1.0, margin=0.0).min_width_mils
    b = trace_width_for_current(5.0, margin=0.0).min_width_mils
    assert b > a


def test_higher_copper_needs_less_width():
    # 2 oz copper is twice as thick, so half the width for the same current.
    one = trace_width_for_current(3.0, copper_oz=1.0, margin=0.0).min_width_mils
    two = trace_width_for_current(3.0, copper_oz=2.0, margin=0.0).min_width_mils
    assert two == pytest.approx(one / 2.0, rel=1e-9)


def test_margin_and_rounding():
    r = trace_width_for_current(1.0, copper_oz=1.0, delta_t_c=10.0,
                                layer="external", margin=0.2)
    # Recommended is >= min*(1.2) and lands on a 0.1 mil grid.
    assert r.recommended_width_mils >= r.min_width_mils * 1.2 - 1e-9
    assert round(r.recommended_width_mils * 10) == r.recommended_width_mils * 10


def test_resistance_and_drop_with_length():
    r = trace_width_for_current(2.0, copper_oz=1.0, delta_t_c=10.0,
                                length_mils=1000.0, margin=0.2)
    assert r.resistance_mohm is not None
    # V = I * R; vdrop in mV, res in mOhm, current in A.
    assert r.voltage_drop_mv == pytest.approx(2.0 * r.resistance_mohm)
    # Cross-check resistance against the direct formula.
    assert r.resistance_mohm == pytest.approx(
        trace_resistance_mohm(r.recommended_width_mils, 1.0, 1000.0))


def test_no_length_no_resistance():
    r = trace_width_for_current(2.0)
    assert r.resistance_mohm is None
    assert r.voltage_drop_mv is None


def test_validations():
    with pytest.raises(ValueError):
        trace_width_for_current(0)
    with pytest.raises(ValueError):
        trace_width_for_current(1.0, delta_t_c=0)
    with pytest.raises(ValueError):
        trace_width_for_current(1.0, layer="middle")
    with pytest.raises(ValueError):
        trace_width_for_current(1.0, margin=-0.1)
