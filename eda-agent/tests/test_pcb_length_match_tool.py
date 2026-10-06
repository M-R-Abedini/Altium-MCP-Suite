# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Wiring tests for the pcb_calc_length_match tool. Pure math, no Altium; the
formulas are covered in tests/design/test_length_matching.py."""

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
    return cap["pcb_calc_length_match"]


@pytest.mark.asyncio
async def test_tolerance_only_no_lengths(monkeypatch):
    out = await _tool(monkeypatch)(skew_budget_ps=10.0, geometry="stripline")
    assert out["ok"] is True
    assert out["tolerance_mils"] == pytest.approx(57.6, abs=0.5)
    assert "members" not in out


@pytest.mark.asyncio
async def test_budget_from_rise_time(monkeypatch):
    out = await _tool(monkeypatch)(rise_time_ns=0.5, match_fraction=0.1)
    assert out["skew_budget_ps"] == pytest.approx(50.0)


@pytest.mark.asyncio
async def test_group_report_with_lengths(monkeypatch):
    out = await _tool(monkeypatch)(
        lengths={"D0": 1000, "D1": 1200, "D2": 1180}, skew_budget_ps=50.0)
    assert out["ok"] is True
    assert out["target_length_mils"] == 1200
    assert out["all_matched"] is True
    comp = {m["net"]: m["compensation_mils"] for m in out["members"]}
    assert comp == {"D0": 200, "D1": 0, "D2": 20}


@pytest.mark.asyncio
async def test_group_flags_over_budget(monkeypatch):
    out = await _tool(monkeypatch)(
        lengths={"D0": 1000, "D1": 1200}, skew_budget_ps=5.0)
    assert out["all_matched"] is False
    bad = [m["net"] for m in out["members"] if not m["within_tolerance"]]
    assert bad == ["D0"]
    assert "over the" in out["summary"]


@pytest.mark.asyncio
async def test_microstrip_window_wider_than_stripline(monkeypatch):
    ms = await _tool(monkeypatch)(skew_budget_ps=10.0, geometry="microstrip")
    sl = await _tool(monkeypatch)(skew_budget_ps=10.0, geometry="stripline")
    assert ms["tolerance_mils"] > sl["tolerance_mils"]


@pytest.mark.asyncio
async def test_negative_length_returns_reason(monkeypatch):
    out = await _tool(monkeypatch)(lengths={"A": -5}, skew_budget_ps=10.0)
    assert out["ok"] is False
    assert "reason" in out
