# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Wiring tests for the design_compute_component_value MCP tool.

The math is covered in tests/design/test_component_values.py; these lock
down the tool's dispatch, response shape and error handling. No Altium.
"""

from __future__ import annotations

import pytest

from eda_agent.tools import design as design_module


def _tool():
    captured = {}

    class DummyMcp:
        def tool(self):
            def decorator(fn):
                captured[fn.__name__] = fn
                return fn
            return decorator

    design_module.register_design_tools(DummyMcp())
    return captured["design_compute_component_value"]


@pytest.mark.asyncio
async def test_nearest():
    out = await _tool()(kind="nearest", value=4690, series="E24")
    assert out["ok"] is True
    assert out["snapped"] == pytest.approx(4700)


@pytest.mark.asyncio
async def test_feedback_divider():
    out = await _tool()(kind="feedback_divider", v_out=3.3, v_ref=0.8,
                        series="E96", r_bottom_ohms=10000)
    assert out["ok"] is True
    assert out["r_top_ohms"] == pytest.approx(31600)
    assert out["v_out"] == pytest.approx(3.328, abs=0.002)
    assert abs(out["error_pct"]) < 1.0


@pytest.mark.asyncio
async def test_led_resistor():
    out = await _tool()(kind="led_resistor", v_supply=5.0, v_forward=2.0,
                        i_led_ma=10.0, series="E24")
    assert out["ok"] is True
    assert out["resistor_ohms"] == pytest.approx(300)
    assert out["current_ma"] == pytest.approx(10.0, rel=1e-6)
    assert out["power_w"] == pytest.approx(0.03, rel=1e-6)


@pytest.mark.asyncio
async def test_rc_lowpass():
    out = await _tool()(kind="rc_lowpass", f_cutoff_hz=1000.0, r_ohms=1600.0,
                        series="E24")
    assert out["ok"] is True
    assert out["c_farads"] == pytest.approx(100e-9)


@pytest.mark.asyncio
async def test_missing_args_returns_error_not_exception():
    out = await _tool()(kind="feedback_divider", v_out=3.3)   # no v_ref
    assert out["ok"] is False
    assert "v_ref" in out["error"]


@pytest.mark.asyncio
async def test_invalid_physics_returns_error():
    # Vout < Vref is impossible for a feedback divider; surfaced, not raised.
    out = await _tool()(kind="feedback_divider", v_out=0.8, v_ref=3.3)
    assert out["ok"] is False
    assert out["error"]


@pytest.mark.asyncio
async def test_unknown_kind():
    out = await _tool()(kind="bogus")
    assert out["ok"] is False
    assert "unknown kind" in out["error"]


@pytest.mark.asyncio
async def test_crystal_load_caps():
    out = await _tool()(kind="crystal_load_caps", c_load_pf=18.0,
                        c_stray_pf=5.0, series="E24")
    assert out["ok"] is True
    assert out["cap_pf"] == pytest.approx(27.0)


@pytest.mark.asyncio
async def test_i2c_pullup_feasible():
    out = await _tool()(kind="i2c_pullup", v_bus=3.3, c_bus_pf=200.0,
                        t_rise_ns=300.0, series="E24")
    assert out["ok"] is True
    assert out["feasible"] is True
    assert out["recommended_ohms"] == pytest.approx(1600)


@pytest.mark.asyncio
async def test_i2c_pullup_infeasible_reports_not_raises():
    out = await _tool()(kind="i2c_pullup", v_bus=3.3, c_bus_pf=2000.0,
                        t_rise_ns=300.0, series="E24")
    assert out["ok"] is True            # the computation succeeded...
    assert out["feasible"] is False     # ...but no value fits the window
    assert out["recommended_ohms"] is None
    assert "infeasible" in out["summary"]


@pytest.mark.asyncio
async def test_divider_tolerance():
    out = await _tool()(kind="divider_tolerance", v_in=5.0, r_top_ohms=4320,
                        r_bottom_ohms=2430, tol_pct=1.0)
    assert out["ok"] is True
    assert out["v_nominal"] == pytest.approx(1.8, abs=1e-3)
    assert out["v_min"] < out["v_nominal"] < out["v_max"]


@pytest.mark.asyncio
async def test_new_kind_missing_args_errors():
    out = await _tool()(kind="i2c_pullup", v_bus=3.3)   # no c_bus/t_rise
    assert out["ok"] is False
    assert "c_bus_pf" in out["error"]


@pytest.mark.asyncio
async def test_opamp_gain_inverting():
    out = await _tool()(kind="opamp_gain", gain=10, config="inverting",
                        series="E96")
    assert out["ok"] is True
    assert out["config"] == "inverting"
    assert out["r_feedback_ohms"] / out["r_input_ohms"] == pytest.approx(10.0)
    assert out["gain"] == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_buck_inductor():
    out = await _tool()(kind="buck_inductor", v_in=12, v_out=5, i_out_a=2,
                        f_sw_khz=500, ripple_pct=30, series="E12")
    assert out["ok"] is True
    assert out["inductance_uh"] == pytest.approx(10.0)
    assert out["peak_current_a"] > 2.0


@pytest.mark.asyncio
async def test_opamp_gain_follower_errors():
    out = await _tool()(kind="opamp_gain", gain=1.0, config="non_inverting")
    assert out["ok"] is False
    assert out["error"]


@pytest.mark.asyncio
async def test_buck_inductor_missing_args():
    out = await _tool()(kind="buck_inductor", v_in=12, v_out=5)   # no i_out/fsw
    assert out["ok"] is False
    assert "i_out_a" in out["error"]


@pytest.mark.asyncio
async def test_capacitor_energy_tool():
    out = await _tool()(kind="capacitor_energy", c_farads=1000e-6, voltage=400)
    assert out["ok"] is True
    assert out["energy_j"] == pytest.approx(80.0)


@pytest.mark.asyncio
async def test_holdup_cap_tool():
    out = await _tool()(kind="holdup_cap", i_load_a=2.0, t_s=20e-3, v_drop=5.0)
    assert out["ok"] is True
    assert out["capacitance_uf"] == pytest.approx(8000.0)


@pytest.mark.asyncio
async def test_discharge_resistor_tool():
    out = await _tool()(kind="discharge_resistor", c_farads=0.47e-6,
                        v_initial=325, v_final=50, t_s=1.0)
    assert out["ok"] is True
    assert out["resistor_ohms"] == pytest.approx(1.137e6, rel=1e-3)


@pytest.mark.asyncio
async def test_holdup_missing_args():
    out = await _tool()(kind="holdup_cap", i_load_a=2.0)
    assert out["ok"] is False
    assert "t_s" in out["error"]


@pytest.mark.asyncio
async def test_junction_temp_tool():
    out = await _tool()(kind="junction_temp", power_w=1.0, theta_ja=50.0,
                        t_ambient=25.0)
    assert out["ok"] is True
    assert out["tj_c"] == pytest.approx(75.0)
    assert out["rise_c"] == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_max_power_tool():
    out = await _tool()(kind="max_power", tj_max=125, theta_ja=50, t_ambient=25)
    assert out["ok"] is True
    assert out["power_w"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_required_theta_ja_tool():
    out = await _tool()(kind="required_theta_ja", power_w=2.0, tj_max=125,
                        t_ambient=25)
    assert out["ok"] is True
    assert out["theta_ja_c_per_w"] == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_thermal_no_headroom_errors():
    out = await _tool()(kind="max_power", tj_max=25, theta_ja=50, t_ambient=25)
    assert out["ok"] is False
    assert out["error"]
