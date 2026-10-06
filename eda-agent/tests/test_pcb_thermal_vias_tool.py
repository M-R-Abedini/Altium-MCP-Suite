# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Wiring tests for the pcb_calc_thermal_vias tool. Pure math, no Altium; the
formulas are covered in tests/design/test_thermal_vias.py."""

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
    return cap["pcb_calc_thermal_vias"]


@pytest.mark.asyncio
async def test_solve_count_from_power_budget(monkeypatch):
    out = await _tool(monkeypatch)(0.3, 1.6, power_w=2.0, delta_t_c=20.0)
    assert out["ok"] is True
    assert out["via_count"] == 20
    assert out["array_k_per_w"] <= 10.0
    assert out["temp_rise_c"] <= 20.0


@pytest.mark.asyncio
async def test_solve_count_from_target(monkeypatch):
    out = await _tool(monkeypatch)(0.3, 1.6, target_k_per_w=10.0)
    assert out["via_count"] == 20
    assert out["temp_rise_c"] is None


@pytest.mark.asyncio
async def test_score_explicit_count(monkeypatch):
    out = await _tool(monkeypatch)(0.3, 1.6, via_count=9, power_w=2.0)
    assert out["via_count"] == 9
    assert out["temp_rise_c"] == pytest.approx(2.0 * out["array_k_per_w"], abs=0.1)


@pytest.mark.asyncio
async def test_copper_fill_needs_fewer_vias(monkeypatch):
    barrel = await _tool(monkeypatch)(0.3, 1.6, target_k_per_w=10.0)
    filled = await _tool(monkeypatch)(0.3, 1.6, target_k_per_w=10.0,
                                      filled_copper=True)
    assert filled["via_count"] < barrel["via_count"]


@pytest.mark.asyncio
async def test_no_target_returns_reason(monkeypatch):
    out = await _tool(monkeypatch)(0.3, 1.6)
    assert out["ok"] is False
    assert "reason" in out
