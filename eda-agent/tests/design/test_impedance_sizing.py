# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Inverse controlled-impedance (width-from-Z0) tests."""

from __future__ import annotations

import pytest

from eda_agent.design.impedance_sizing import (
    diff_coupling_factor,
    trace_width_for_impedance,
    z0_microstrip,
    z0_stripline,
)


def test_microstrip_round_trips_forward():
    # width -> Z0 -> width must recover the original width.
    for w in (5.0, 10.0, 20.0):
        z0 = z0_microstrip(w, 8.0, 4.2, copper_oz=1.0)
        r = trace_width_for_impedance(z0, "microstrip", 8.0,
                                      dielectric_constant=4.2)
        assert r.width_mils == pytest.approx(w, rel=1e-9)
        assert r.feasible


def test_stripline_round_trips_forward():
    for w in (5.0, 10.0, 15.0):
        z0 = z0_stripline(w, 20.0, 4.2, copper_oz=1.0)
        r = trace_width_for_impedance(z0, "stripline", 20.0,
                                      dielectric_constant=4.2)
        assert r.width_mils == pytest.approx(w, rel=1e-9)


def test_microstrip_diff_round_trips():
    # Forward: width+spacing -> Zdiff; inverse with that spacing recovers width.
    w, s, h, er = 7.0, 8.0, 8.0, 4.2
    z0 = z0_microstrip(w, h, er)
    k = diff_coupling_factor("microstrip_diff", s, h)
    zdiff = 2.0 * z0 * k
    r = trace_width_for_impedance(zdiff, "microstrip_diff", h,
                                  dielectric_constant=er, spacing_mils=s)
    assert r.width_mils == pytest.approx(w, rel=1e-9)
    assert r.single_ended_z0_ohms == pytest.approx(z0, rel=1e-9)


def test_stripline_diff_round_trips():
    w, s, h, er = 6.0, 10.0, 24.0, 4.2
    z0 = z0_stripline(w, h, er)
    k = diff_coupling_factor("stripline_diff", s, h)
    zdiff = 2.0 * z0 * k
    r = trace_width_for_impedance(zdiff, "stripline_diff", h,
                                  dielectric_constant=er, spacing_mils=s)
    assert r.width_mils == pytest.approx(w, rel=1e-9)


def test_lower_target_impedance_needs_wider_trace():
    # Z0 falls as the trace widens, so a lower target -> a wider trace.
    a = trace_width_for_impedance(75, "microstrip", 8.0).width_mils
    b = trace_width_for_impedance(50, "microstrip", 8.0).width_mils
    assert b > a


def test_50ohm_microstrip_is_a_sane_width():
    # 50 ohm single-ended on FR-4, ~7 mil prepreg -> a few-to-~13 mil trace.
    r = trace_width_for_impedance(50, "microstrip", 7.0,
                                  dielectric_constant=4.2)
    assert r.feasible
    assert 4.0 < r.width_mils < 20.0


def test_infeasible_target_flagged_not_raised():
    # A very HIGH impedance on a thin dielectric needs a trace narrower than the
    # copper is thick -> the formula yields width <= 0; report feasible=False.
    r = trace_width_for_impedance(200, "microstrip", 4.0,
                                  dielectric_constant=4.2)
    assert r.feasible is False
    assert r.width_mils == 0.0


def test_validations():
    with pytest.raises(ValueError):
        trace_width_for_impedance(50, "stripline_diff", 8.0)   # no spacing
    with pytest.raises(ValueError):
        trace_width_for_impedance(0, "microstrip", 8.0)        # bad target
    with pytest.raises(ValueError):
        trace_width_for_impedance(50, "bogus", 8.0)            # bad geometry
    with pytest.raises(ValueError):
        trace_width_for_impedance(50, "microstrip", 0)         # bad height
