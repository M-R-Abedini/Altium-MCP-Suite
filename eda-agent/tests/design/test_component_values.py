# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Component-value computation tests (E-series + sizing equations)."""

from __future__ import annotations

import math

import pytest

from eda_agent.design.component_values import (
    feedback_divider,
    led_series_resistor,
    nearest_preferred,
    rc_lowpass,
    resistor_divider,
    series_mantissas,
)


# ---------------------------------------------------------------------------
# E-series tables
# ---------------------------------------------------------------------------


def test_series_lengths():
    assert len(series_mantissas("E6")) == 6
    assert len(series_mantissas("E12")) == 12
    assert len(series_mantissas("E24")) == 24
    assert len(series_mantissas("E48")) == 48
    assert len(series_mantissas("E96")) == 96


def test_series_known_members():
    assert series_mantissas("E6") == [1.0, 1.5, 2.2, 3.3, 4.7, 6.8]
    assert series_mantissas("e12")[:4] == [1.0, 1.2, 1.5, 1.8]   # case-insensitive
    assert 3.16 in series_mantissas("E96")
    assert 9.76 in series_mantissas("E96")


def test_series_unknown_raises():
    with pytest.raises(ValueError):
        series_mantissas("E7")


# ---------------------------------------------------------------------------
# nearest_preferred
# ---------------------------------------------------------------------------


def test_nearest_exact_value_unchanged():
    assert nearest_preferred(4700, "E24") == pytest.approx(4700)
    assert nearest_preferred(1.0, "E24") == pytest.approx(1.0)
    assert nearest_preferred(1_000_000, "E24") == pytest.approx(1_000_000)


def test_nearest_snaps_close_value():
    assert nearest_preferred(4690, "E24") == pytest.approx(4700)
    # 5000 is closer (in ratio) to 5100 than to 4700.
    assert nearest_preferred(5000, "E24") == pytest.approx(5100)


def test_nearest_crosses_decade_boundary():
    # 9.8k is nearer 10k (ratio 1.02) than 9.1k (ratio 1.077).
    assert nearest_preferred(9800, "E24") == pytest.approx(10000)
    # just under 1 should snap up into the next decade's 1.0
    assert nearest_preferred(0.98, "E24") == pytest.approx(1.0)


def test_nearest_e96_precision():
    # 31.25 kOhm (a typical FB divider top) -> 31.6 kOhm in E96.
    assert nearest_preferred(31250, "E96") == pytest.approx(31600)


def test_nearest_rejects_nonpositive():
    with pytest.raises(ValueError):
        nearest_preferred(0, "E24")
    with pytest.raises(ValueError):
        nearest_preferred(-5, "E24")


# ---------------------------------------------------------------------------
# LED series resistor
# ---------------------------------------------------------------------------


def test_led_resistor_textbook():
    # 5 V rail, 2.0 V red LED, 10 mA -> (5-2)/0.01 = 300 ohm exactly (E24).
    res = led_series_resistor(5.0, 2.0, 0.010, series="E24")
    assert res.resistor == pytest.approx(300)
    assert res.current_a == pytest.approx(0.010, rel=1e-6)
    assert res.power_w == pytest.approx(0.03, rel=1e-6)
    assert res.error_pct == pytest.approx(0.0, abs=1e-6)


def test_led_resistor_snaps_and_reports_error():
    # Ideal 272.7 ohm snaps to 270 (E24); current rises slightly above target.
    res = led_series_resistor(5.0, 2.0, 0.011, series="E24")
    assert res.resistor == pytest.approx(270)
    assert res.current_a > 0.011
    assert res.power_w == pytest.approx(res.current_a ** 2 * 270, rel=1e-9)


def test_led_resistor_validates():
    with pytest.raises(ValueError):
        led_series_resistor(2.0, 5.0, 0.01)        # Vf > Vsupply
    with pytest.raises(ValueError):
        led_series_resistor(5.0, 2.0, 0.0)         # zero current


# ---------------------------------------------------------------------------
# Feedback divider (regulator FB pin)
# ---------------------------------------------------------------------------


def test_feedback_divider_fixed_bottom():
    # Vout = Vref(1 + Rt/Rb); 3.3 V from a 0.8 V FB, Rb = 10k.
    # Rt_ideal = 10k*(3.3/0.8 - 1) = 31.25k -> 31.6k (E96).
    res = feedback_divider(3.3, 0.8, series="E96", r_bottom=10_000)
    assert res.r_bottom == pytest.approx(10_000)
    assert res.r_top == pytest.approx(31_600)
    assert res.v_out == pytest.approx(0.8 * (1 + 31600 / 10000), rel=1e-9)
    assert abs(res.error_pct) < 1.0


def test_feedback_divider_search_beats_fixed():
    # Searching the low-side too should find a pair at least as good.
    fixed = feedback_divider(3.3, 0.8, series="E96", r_bottom=10_000)
    searched = feedback_divider(3.3, 0.8, series="E96")
    assert abs(searched.error_pct) <= abs(fixed.error_pct) + 1e-9


def test_feedback_divider_validates():
    with pytest.raises(ValueError):
        feedback_divider(0.8, 3.3)                 # Vout < Vref


# ---------------------------------------------------------------------------
# Unloaded resistor divider
# ---------------------------------------------------------------------------


def test_resistor_divider_ratio_and_error():
    res = resistor_divider(5.0, 3.3, series="E96")
    assert res.v_out == pytest.approx(3.3, abs=0.03)        # within ~1%
    assert abs(res.error_pct) < 1.0
    # Rt/Rb should track (Vin - Vout)/Vout = 1.7/3.3 = 0.515.
    assert res.r_top / res.r_bottom == pytest.approx(0.515, rel=0.1)


def test_resistor_divider_validates():
    with pytest.raises(ValueError):
        resistor_divider(3.3, 5.0)                 # cannot boost
    with pytest.raises(ValueError):
        resistor_divider(5.0, 5.0)                 # no attenuation


# ---------------------------------------------------------------------------
# RC low-pass
# ---------------------------------------------------------------------------


def test_rc_lowpass_given_r():
    # 1 kHz with R = 1.6k -> C ideal 99.5 nF -> 100 nF (E24).
    res = rc_lowpass(1000.0, r=1600.0, series="E24")
    assert res.c == pytest.approx(100e-9)
    assert res.f_cutoff == pytest.approx(1.0 / (2 * math.pi * 1600 * 100e-9))
    assert abs(res.error_pct) < 1.0


def test_rc_lowpass_given_c():
    # 1 kHz with C = 100 nF -> R ideal 1591.5 -> 1.6k (E24).
    res = rc_lowpass(1000.0, c=100e-9, series="E24")
    assert res.r == pytest.approx(1600)
    assert res.f_cutoff == pytest.approx(1.0 / (2 * math.pi * 1600 * 100e-9))


def test_rc_lowpass_requires_exactly_one():
    with pytest.raises(ValueError):
        rc_lowpass(1000.0)                          # neither
    with pytest.raises(ValueError):
        rc_lowpass(1000.0, r=1600, c=100e-9)        # both


# ---------------------------------------------------------------------------
# Crystal load caps
# ---------------------------------------------------------------------------


def test_crystal_load_caps_formula():
    # CL = 18 pF, Cstray = 5 pF -> C = 2*(18-5) = 26 pF -> 27 pF (E24).
    from eda_agent.design.component_values import crystal_load_caps
    res = crystal_load_caps(18e-12, 5e-12, series="E24")
    assert res.cap == pytest.approx(27e-12)
    # The snapped pair presents CL = C/2 + Cstray.
    assert res.c_load_achieved == pytest.approx(27e-12 / 2 + 5e-12)


def test_crystal_load_caps_validates():
    from eda_agent.design.component_values import crystal_load_caps
    with pytest.raises(ValueError):
        crystal_load_caps(4e-12, 5e-12)            # stray exceeds load


# ---------------------------------------------------------------------------
# I2C pull-up window (NXP UM10204)
# ---------------------------------------------------------------------------


def test_i2c_pullup_window():
    from eda_agent.design.component_values import i2c_pullup
    # 3.3 V, 200 pF, fast-mode 300 ns.
    res = i2c_pullup(3.3, 200e-12, 300e-9, series="E24")
    assert res.r_min == pytest.approx((3.3 - 0.4) / 0.003)
    assert res.r_max == pytest.approx(300e-9 / (0.8473 * 200e-12))
    assert res.feasible is True
    # Recommendation is the largest preferred value inside the window.
    assert res.r_min <= res.recommended <= res.r_max
    assert res.recommended == pytest.approx(1600)


def test_i2c_pullup_infeasible_when_bus_cap_too_high():
    from eda_agent.design.component_values import i2c_pullup
    # 2 nF on a 300 ns budget: rise-time ceiling falls below the sink floor.
    res = i2c_pullup(3.3, 2000e-12, 300e-9, series="E24")
    assert res.feasible is False
    assert res.recommended is None
    assert res.r_min > res.r_max


def test_i2c_pullup_validates():
    from eda_agent.design.component_values import i2c_pullup
    with pytest.raises(ValueError):
        i2c_pullup(0.3, 200e-12, 300e-9)           # v_bus below v_ol


# ---------------------------------------------------------------------------
# Divider tolerance window
# ---------------------------------------------------------------------------


def test_divider_tolerance_window():
    from eda_agent.design.component_values import divider_tolerance
    res = divider_tolerance(5.0, 4320, 2430, tol_pct=1.0)
    assert res.v_nominal == pytest.approx(1.8, abs=1e-3)
    # Tolerances stack: max when Rb high & Rt low, min in the opposite corner.
    assert res.v_min < res.v_nominal < res.v_max
    assert res.v_max == pytest.approx(
        5.0 * 2430 * 1.01 / (4320 * 0.99 + 2430 * 1.01))
    assert res.v_min == pytest.approx(
        5.0 * 2430 * 0.99 / (4320 * 1.01 + 2430 * 0.99))


def test_divider_tolerance_zero_tol_is_a_point():
    from eda_agent.design.component_values import divider_tolerance
    res = divider_tolerance(5.0, 4320, 2430, tol_pct=0.0)
    assert res.v_min == pytest.approx(res.v_max) == pytest.approx(res.v_nominal)
    assert res.spread_pct == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Op-amp gain resistors
# ---------------------------------------------------------------------------


def test_opamp_inverting_gain_exact():
    from eda_agent.design.component_values import opamp_gain_resistors
    # |gain| = 10 -> Rf/Rin = 10; 10k/1k is exact in E96.
    r = opamp_gain_resistors(10, config="inverting", series="E96")
    assert r.config == "inverting"
    assert r.r_feedback / r.r_input == pytest.approx(10.0)
    assert r.gain == pytest.approx(10.0)
    assert r.error_pct == pytest.approx(0.0, abs=1e-9)


def test_opamp_noninverting_gain_close():
    from eda_agent.design.component_values import opamp_gain_resistors
    # gain = 1 + Rf/Rg -> Rf/Rg = 9.
    r = opamp_gain_resistors(10, config="non_inverting", series="E96")
    assert r.gain == pytest.approx(10.0, rel=0.01)        # within 1 %
    assert 1.0 + r.r_feedback / r.r_input == pytest.approx(r.gain)


def test_opamp_inverting_accepts_attenuation():
    from eda_agent.design.component_values import opamp_gain_resistors
    # An inverting stage can attenuate (|gain| < 1).
    r = opamp_gain_resistors(0.5, config="inverting", series="E24")
    assert r.gain == pytest.approx(0.5, rel=0.05)


def test_opamp_gain_validates():
    from eda_agent.design.component_values import opamp_gain_resistors
    with pytest.raises(ValueError):
        opamp_gain_resistors(1.0, config="non_inverting")    # follower
    with pytest.raises(ValueError):
        opamp_gain_resistors(0, config="inverting")          # zero gain
    with pytest.raises(ValueError):
        opamp_gain_resistors(5, config="bogus")


# ---------------------------------------------------------------------------
# Buck inductor
# ---------------------------------------------------------------------------


def test_buck_inductor_textbook():
    from eda_agent.design.component_values import buck_inductor
    # 12 -> 5 V, 2 A, 500 kHz, 30 % ripple -> L ideal 9.72 uH -> 10 uH (E12).
    r = buck_inductor(12, 5, 2, 500e3, ripple_fraction=0.3, series="E12")
    assert r.inductance == pytest.approx(10e-6)
    # Actual ripple at 10 uH and the peak current.
    assert r.ripple_current_a == pytest.approx(
        (12 - 5) * 5 / (12 * 500e3 * 10e-6))
    assert r.peak_current_a == pytest.approx(2 + r.ripple_current_a / 2)


def test_buck_inductor_lower_ripple_needs_more_L():
    from eda_agent.design.component_values import buck_inductor
    loose = buck_inductor(12, 5, 2, 500e3, ripple_fraction=0.4)
    tight = buck_inductor(12, 5, 2, 500e3, ripple_fraction=0.1)
    assert tight.inductance_ideal > loose.inductance_ideal


def test_buck_inductor_validates():
    from eda_agent.design.component_values import buck_inductor
    with pytest.raises(ValueError):
        buck_inductor(5, 12, 2, 500e3)            # cannot buck up
    with pytest.raises(ValueError):
        buck_inductor(12, 5, 0, 500e3)            # zero current
    with pytest.raises(ValueError):
        buck_inductor(12, 5, 2, 500e3, ripple_fraction=1.5)


# ---------------------------------------------------------------------------
# Energy storage / timing
# ---------------------------------------------------------------------------


def test_capacitor_energy():
    from eda_agent.design.component_values import capacitor_energy
    # E = 0.5 * C * V^2; 1000 uF @ 400 V = 80 J.
    assert capacitor_energy(1000e-6, 400) == pytest.approx(80.0)
    assert capacitor_energy(0, 100) == 0.0
    with pytest.raises(ValueError):
        capacitor_energy(-1e-6, 5)


def test_holdup_capacitance():
    from eda_agent.design.component_values import holdup_capacitance
    # C = I*t/dV; 2 A, 20 ms, 5 V -> 8000 uF.
    assert holdup_capacitance(2.0, 20e-3, 5.0) == pytest.approx(8000e-6)
    with pytest.raises(ValueError):
        holdup_capacitance(2.0, 20e-3, 0)


def test_discharge_resistor():
    import math
    from eda_agent.design.component_values import discharge_resistor
    # R = t / (C * ln(Vi/Vf)); 0.47 uF, 325 -> 50 V, 1 s.
    r = discharge_resistor(0.47e-6, 325, 50, 1.0)
    assert r == pytest.approx(1.0 / (0.47e-6 * math.log(325 / 50)))
    assert r == pytest.approx(1.137e6, rel=1e-3)
    with pytest.raises(ValueError):
        discharge_resistor(0.47e-6, 50, 325, 1.0)        # Vi < Vf
    with pytest.raises(ValueError):
        discharge_resistor(0.47e-6, 325, 0, 1.0)         # Vf = 0


# ---------------------------------------------------------------------------
# Thermal (junction temperature)
# ---------------------------------------------------------------------------


def test_junction_temperature():
    from eda_agent.design.component_values import junction_temperature
    # Tj = Ta + P*theta; 1 W, 50 C/W, 25 C -> 75 C.
    assert junction_temperature(1.0, 50.0, 25.0) == pytest.approx(75.0)
    assert junction_temperature(0.0, 50.0, 25.0) == pytest.approx(25.0)
    with pytest.raises(ValueError):
        junction_temperature(-1, 50)


def test_max_power_dissipation():
    from eda_agent.design.component_values import max_power_dissipation
    # P = (Tjmax - Ta)/theta; 125, 50 C/W, 25 -> 2 W.
    assert max_power_dissipation(125, 50, 25) == pytest.approx(2.0)
    with pytest.raises(ValueError):
        max_power_dissipation(25, 50, 25)        # no headroom
    with pytest.raises(ValueError):
        max_power_dissipation(125, 0, 25)        # bad theta


def test_required_theta_ja():
    from eda_agent.design.component_values import required_theta_ja
    # theta = (Tjmax - Ta)/P; 2 W, 125, 25 -> 50 C/W.
    assert required_theta_ja(2.0, 125, 25) == pytest.approx(50.0)
    with pytest.raises(ValueError):
        required_theta_ja(0, 125, 25)


def test_thermal_trio_is_self_consistent():
    # The three forms invert each other.
    from eda_agent.design.component_values import (
        junction_temperature, max_power_dissipation, required_theta_ja)
    p = max_power_dissipation(125, 50, 25)
    assert junction_temperature(p, 50, 25) == pytest.approx(125)
    assert required_theta_ja(p, 125, 25) == pytest.approx(50)
