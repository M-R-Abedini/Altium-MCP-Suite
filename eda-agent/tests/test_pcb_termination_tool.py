# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Wiring tests for the pcb_calc_termination tool. Pure math, no Altium; the
formulas are covered in tests/design/test_signal_integrity.py."""

from __future__ import annotations

import pytest

from eda_agent.tools import pcb as pcb_module


def _tool(monkeypatch):
    monkeypatch.setattr(pcb_module, "get_bridge", lambda: None)
    cap: dict = {}

    class M:
        def tool(self):
            def d(fn):
                cap[fn.__name__] = fn
                return fn
            return d

    pcb_module.register_pcb_tools(M())
    return cap["pcb_calc_termination"]


@pytest.mark.asyncio
async def test_short_net_needs_no_termination(monkeypatch):
    out = await _tool(monkeypatch)(200, 1.0, z0_ohms=50)
    assert out["ok"] is True
    assert out["needs_termination"] is False
    assert out["recommended"] == "none"
    assert out["options"] == {}


@pytest.mark.asyncio
async def test_long_point_to_point_recommends_series(monkeypatch):
    out = await _tool(monkeypatch)(5000, 0.5, z0_ohms=50,
                                   driver_impedance_ohms=10)
    assert out["ok"] is True
    assert out["needs_termination"] is True
    assert out["recommended"] == "series"
    assert out["options"]["series"]["r_ohms"] == 40
    assert "parallel" in out["options"] and "ac" in out["options"]


@pytest.mark.asyncio
async def test_long_multiload_with_vcc_recommends_thevenin(monkeypatch):
    out = await _tool(monkeypatch)(5000, 0.5, z0_ohms=50, vcc=3.3,
                                   multi_load=True)
    assert out["recommended"] == "thevenin"
    thev = out["options"]["thevenin"]
    assert thev["r_pullup_ohms"] == pytest.approx(100)
    assert thev["r_pulldown_ohms"] == pytest.approx(100)
    assert thev["static_power_w"] == pytest.approx(3.3 ** 2 / 200, abs=1e-4)


@pytest.mark.asyncio
async def test_ac_option_reports_cap(monkeypatch):
    out = await _tool(monkeypatch)(5000, 0.5, z0_ohms=50, geometry="stripline")
    ac = out["options"]["ac"]
    assert ac["r_ohms"] == 50
    assert ac["capacitance_pf"] > 0


@pytest.mark.asyncio
async def test_bad_input_returns_reason(monkeypatch):
    out = await _tool(monkeypatch)(5000, 0.0, z0_ohms=50)
    assert out["ok"] is False
    assert "reason" in out
