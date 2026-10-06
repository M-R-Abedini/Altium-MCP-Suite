# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the transmission-line / termination calculations.

Cross-checked against published values: stripline FR4 (Er=4.2) ~= 174 ps/in and
microstrip FR4 ~= 138-148 ps/in; the famous "~1 inch per nanosecond" critical
length; the classic 100/100 ohm Thevenin pair on a 50 ohm line at 3.3 V.
"""

import math

import pytest

from eda_agent.design.signal_integrity import (
    effective_dielectric_constant,
    propagation_delay_ns_per_inch,
    flight_time_ns,
    critical_length,
    is_electrically_long,
    series_termination,
    parallel_termination,
    thevenin_termination,
    ac_termination,
    recommend_termination,
)


# --------------------------------------------------------------------------- #
# Effective dielectric constant
# --------------------------------------------------------------------------- #
def test_stripline_er_eff_is_bulk_er():
    assert effective_dielectric_constant(4.2, "stripline") == 4.2


def test_microstrip_er_eff_below_bulk():
    # Part of the field is in air, so Er_eff < Er.
    er_eff = effective_dielectric_constant(4.2, "microstrip")
    assert 1.0 < er_eff < 4.2
    assert er_eff == pytest.approx(0.475 * 4.2 + 0.67)  # Brooks approximation


def test_microstrip_hammerstad_uses_geometry():
    # With width/height known, the Hammerstad form is used (geometry-dependent).
    narrow = effective_dielectric_constant(4.2, "microstrip",
                                           width_mils=6, height_mils=6)
    wide = effective_dielectric_constant(4.2, "microstrip",
                                         width_mils=12, height_mils=6)
    # A wider trace pulls more field into the dielectric -> higher Er_eff.
    assert wide > narrow
    assert 1.0 < narrow < 4.2 and 1.0 < wide < 4.2


def test_er_eff_rejects_bad_input():
    with pytest.raises(ValueError):
        effective_dielectric_constant(0, "microstrip")
    with pytest.raises(ValueError):
        effective_dielectric_constant(4.2, "coax")


# --------------------------------------------------------------------------- #
# Propagation delay
# --------------------------------------------------------------------------- #
def test_stripline_fr4_delay_matches_published():
    # Stripline FR4 (Er=4.2): published ~174 ps/in.
    t_pd = propagation_delay_ns_per_inch(4.2)
    assert t_pd * 1000 == pytest.approx(174, abs=2)


def test_microstrip_fr4_delay_is_brooks_model_in_published_band():
    er_eff = effective_dielectric_constant(4.2, "microstrip")
    t_pd = propagation_delay_ns_per_inch(er_eff)
    # The Brooks Er_eff gives ~138 ps/in (a regression pin on the model, not an
    # independently published figure); honesty check: it must land inside the
    # published microstrip-FR4 band of roughly 130-170 ps/in.
    assert t_pd * 1000 == pytest.approx(138, abs=3)
    assert 130 < t_pd * 1000 < 175


def test_vacuum_delay_is_one_over_c():
    # Er_eff = 1 -> the vacuum speed of light, ~85 ps/in (~11.8 in/ns).
    assert propagation_delay_ns_per_inch(1.0) == pytest.approx(1 / 11.8028, rel=1e-4)


def test_flight_time_scales_with_length():
    er_eff = effective_dielectric_constant(4.2, "stripline")
    assert flight_time_ns(2000, er_eff) == pytest.approx(
        2 * flight_time_ns(1000, er_eff))


# --------------------------------------------------------------------------- #
# Critical length / electrically long
# --------------------------------------------------------------------------- #
def test_critical_length_one_ns_edge_about_an_inch():
    # The canonical rule of thumb: a 1 ns edge on FR4 microstrip is critical at
    # roughly an inch (the 1/6 rule gives ~1.2 in).
    er_eff = effective_dielectric_constant(4.2, "microstrip")
    cl = critical_length(1.0, er_eff)
    assert cl.length_inch == pytest.approx(1.205, abs=0.02)
    assert cl.length_mils == pytest.approx(cl.length_inch * 1000)
    assert cl.length_mm == pytest.approx(cl.length_inch * 25.4)


def test_faster_edge_shorter_critical_length():
    er_eff = effective_dielectric_constant(4.2, "microstrip")
    slow = critical_length(2.0, er_eff).length_mils
    fast = critical_length(0.5, er_eff).length_mils
    assert fast < slow
    assert fast == pytest.approx(slow / 4, rel=1e-6)  # linear in rise time


def test_looser_fraction_allows_longer_net():
    er_eff = effective_dielectric_constant(4.2, "microstrip")
    strict = critical_length(1.0, er_eff, fraction=1 / 6).length_mils
    loose = critical_length(1.0, er_eff, fraction=1 / 2).length_mils
    assert loose == pytest.approx(3 * strict, rel=1e-6)


def test_short_net_not_electrically_long():
    er_eff = effective_dielectric_constant(4.2, "microstrip")
    el = is_electrically_long(200, 1.0, er_eff)
    assert not el.electrically_long
    assert el.delay_ratio < 1


def test_long_net_is_electrically_long():
    er_eff = effective_dielectric_constant(4.2, "microstrip")
    el = is_electrically_long(5000, 1.0, er_eff)
    assert el.electrically_long
    assert el.delay_ratio > 1
    assert el.flight_time_ns > 0


def test_critical_length_rejects_bad_input():
    with pytest.raises(ValueError):
        critical_length(0, 4.2)
    with pytest.raises(ValueError):
        critical_length(1.0, 4.2, fraction=1.5)


# --------------------------------------------------------------------------- #
# Termination values
# --------------------------------------------------------------------------- #
def test_series_termination_subtracts_driver_impedance():
    st = series_termination(50, 10)
    assert st.r_series == 40
    assert st.r_series_e24 == 39  # nearest E24 to 40 (39 is closer than 43)


def test_series_termination_clamps_at_zero():
    # A driver already matched to (or above) Z0 needs no series R.
    st = series_termination(50, 60)
    assert st.r_series == 0


def test_parallel_termination_equals_z0():
    pt = parallel_termination(50)
    assert pt.r_parallel == 50
    assert pt.r_parallel_e24 == 51  # nearest E24 to 50 (51 is closer than 47)


def test_thevenin_classic_100_100_at_mid_rail():
    # 50 ohm line, 3.3 V, mid-rail bias -> 100/100, parallel 50.
    tt = thevenin_termination(50, 3.3)
    assert tt.r_pullup == pytest.approx(100)
    assert tt.r_pulldown == pytest.approx(100)
    assert tt.r_thevenin == pytest.approx(50)
    assert tt.v_bias == pytest.approx(1.65)
    # Parallel of the two resistors must equal Z0.
    par = tt.r_pullup * tt.r_pulldown / (tt.r_pullup + tt.r_pulldown)
    assert par == pytest.approx(50)
    # Static power Vcc^2 / (R1+R2).
    assert tt.static_power_w == pytest.approx(3.3 ** 2 / 200)


def test_thevenin_asymmetric_bias():
    # Bias toward Vcc -> stiffer pull-up (smaller R_pullup), still parallel Z0.
    tt = thevenin_termination(50, 5.0, v_bias=3.0)
    par = tt.r_pullup * tt.r_pulldown / (tt.r_pullup + tt.r_pulldown)
    assert par == pytest.approx(50)
    assert tt.v_bias == pytest.approx(3.0)
    assert tt.r_pullup < tt.r_pulldown  # closer to Vcc => smaller pull-up


def test_thevenin_rejects_bias_out_of_range():
    with pytest.raises(ValueError):
        thevenin_termination(50, 3.3, v_bias=3.3)
    with pytest.raises(ValueError):
        thevenin_termination(50, 3.3, v_bias=0)


def test_ac_termination_r_is_z0_and_cap_reasonable():
    er_eff = effective_dielectric_constant(4.2, "stripline")
    at = ac_termination(50, 2000, er_eff)
    assert at.r_parallel == 50
    # RC = 3 * one-way flight time, so C = 3*Td/Z0; a 2000-mil FR4 stripline
    # net (~0.35 ns one-way) lands around 20 pF.
    td = flight_time_ns(2000, er_eff)
    assert at.capacitance_f == pytest.approx(3 * td * 1e-9 / 50)
    assert 5e-12 < at.capacitance_f < 100e-12


# --------------------------------------------------------------------------- #
# Aggregator
# --------------------------------------------------------------------------- #
def test_recommend_short_net_no_termination():
    adv = recommend_termination(200, 1.0, 50, 4.2, geometry="microstrip")
    assert not adv.needs_termination
    assert adv.recommended == "none"
    assert adv.series is None


def test_recommend_long_point_to_point_picks_series():
    adv = recommend_termination(5000, 0.5, 50, 4.2, geometry="microstrip",
                                driver_impedance=10)
    assert adv.needs_termination
    assert adv.recommended == "series"
    assert adv.series is not None and adv.series.r_series == 40
    # All applicable options are offered.
    assert adv.parallel is not None and adv.ac is not None


def test_recommend_long_multiload_picks_thevenin():
    adv = recommend_termination(5000, 0.5, 50, 4.2, geometry="microstrip",
                                vcc=3.3, multi_load=True)
    assert adv.needs_termination
    assert adv.recommended == "thevenin"
    assert adv.thevenin is not None
    assert adv.thevenin.r_pullup == pytest.approx(100)


def test_recommend_multiload_without_vcc_falls_back_to_parallel():
    adv = recommend_termination(5000, 0.5, 50, 4.2, geometry="microstrip",
                                multi_load=True)
    assert adv.recommended == "parallel"
    assert adv.thevenin is None


def test_recommend_is_deterministic():
    a = recommend_termination(5000, 0.5, 50, 4.2, driver_impedance=10, vcc=3.3)
    b = recommend_termination(5000, 0.5, 50, 4.2, driver_impedance=10, vcc=3.3)
    assert a == b
