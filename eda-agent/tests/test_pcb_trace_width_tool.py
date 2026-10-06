# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Wiring tests for the pcb_calc_trace_width_for_current tool (inverse
IPC-2221). Pure math, no Altium; the formula is covered in
tests/design/test_trace_sizing.py."""

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
    return cap["pcb_calc_trace_width_for_current"]


@pytest.mark.asyncio
async def test_basic_width(monkeypatch):
    out = await _tool(monkeypatch)(1.0, copper_oz=1.0, delta_t_c=10.0,
                                   layer="external", margin=0.0)
    assert out["ok"] is True
    assert out["min_width_mils"] == pytest.approx(11.8, abs=0.3)
    assert out["recommended_width_mils"] >= out["min_width_mils"]


@pytest.mark.asyncio
async def test_length_adds_resistance(monkeypatch):
    out = await _tool(monkeypatch)(2.0, length_mils=1000.0)
    assert out["ok"] is True
    assert "resistance_mohm" in out
    # V = I * R; both are independently rounded (4 dp / 3 dp), so compare with
    # an absolute tolerance that covers the rounding granularity.
    assert out["voltage_drop_mv"] == pytest.approx(
        2.0 * out["resistance_mohm"], abs=0.002)


@pytest.mark.asyncio
async def test_invalid_layer_returns_reason(monkeypatch):
    out = await _tool(monkeypatch)(1.0, layer="middle")
    assert out["ok"] is False
    assert "layer" in out["reason"]


@pytest.mark.asyncio
async def test_nonpositive_current_returns_reason(monkeypatch):
    out = await _tool(monkeypatch)(0.0)
    assert out["ok"] is False


def _imp_tool(monkeypatch):
    monkeypatch.setattr(pcb_module, "get_bridge", lambda: None)
    cap: dict = {}

    class M:
        def tool(self):
            def d(fn):
                cap[fn.__name__] = fn
                return fn
            return d

    pcb_module.register_pcb_tools(M())
    return cap["pcb_calc_trace_width_for_impedance"]


@pytest.mark.asyncio
async def test_impedance_width_single_ended(monkeypatch):
    out = await _imp_tool(monkeypatch)(50, "microstrip", 7.0,
                                       dielectric_constant=4.2)
    assert out["ok"] is True and out["feasible"] is True
    assert 4.0 < out["width_mils"] < 20.0


@pytest.mark.asyncio
async def test_impedance_width_diff_round_trips_tool(monkeypatch):
    inv = await _imp_tool(monkeypatch)(90, "microstrip_diff", 7.0,
                                       dielectric_constant=4.2, spacing_mils=6)
    assert inv["ok"] is True and inv["feasible"] is True
    assert inv["width_mils"] > 0


@pytest.mark.asyncio
async def test_impedance_width_missing_spacing(monkeypatch):
    out = await _imp_tool(monkeypatch)(100, "stripline_diff", 8.0)
    assert out["ok"] is False
    assert "spacing" in out["reason"]
