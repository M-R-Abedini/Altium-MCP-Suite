# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Plan statistics tests."""

from __future__ import annotations

import pytest

from eda_agent.design.plan_stats import summarize_plan
from eda_agent.design.plan import DesignPlan, Net, Part, PinRef, Sheet


def _net(name, pins, **kw):
    return Net(name=name, pins=[PinRef(refdes=r, pin=p) for r, p in pins], **kw)


def _plan(parts, nets):
    return DesignPlan(
        spec="s", summary="stats plan", sheets=[Sheet(name="main")],
        zones=[], parts=parts, nets=nets,
    )


def _demo_plan():
    parts = [
        Part(refdes="U1", lib_ref="MCU"),
        Part(refdes="R1", lib_ref="RES"), Part(refdes="R2", lib_ref="RES"),
        Part(refdes="C1", lib_ref="CAP"),
        Part(refdes="J1", lib_ref="HDR"),
    ]
    nets = [
        _net("VCC", [("J1", "1"), ("U1", "1"), ("C1", "1")], is_power=True),
        _net("GND", [("J1", "2"), ("U1", "2"), ("C1", "2"), ("R2", "2")],
             is_ground=True),
        # A high-fanout signal: U1 broadcast to R1, R2 and back to itself.
        _net("BUSY", [("U1", "3"), ("U1", "4"), ("R1", "1"), ("R2", "1")]),
        _net("MID", [("R1", "2"), ("U1", "5")]),
    ]
    return _plan(parts, nets)


def test_part_counts_by_kind():
    s = summarize_plan(_demo_plan())
    assert s.part_count == 5
    assert s.parts_by_kind == {"C": 1, "J": 1, "R": 2, "U": 1}


def test_power_and_ground_rails():
    s = summarize_plan(_demo_plan())
    assert s.power_rails == ("VCC",)
    assert s.ground_nets == ("GND",)


def test_ic_and_passive_counts():
    s = summarize_plan(_demo_plan())
    # U1 has pins 1..5 -> >=4 -> IC. R1/R2/C1 are 2-pin passives (R1 has pins
    # 1 and 2 across BUSY/MID; R2 pins 1,2; C1 pins 1,2).
    assert s.ic_count == 1
    assert s.passive_count == 3


def test_highest_fanout_signal_excludes_power_ground():
    s = summarize_plan(_demo_plan())
    # BUSY reaches U1, R1, R2 = 3 distinct parts (the widest signal net).
    assert s.highest_fanout_signal == ("BUSY", 3)


def test_net_count_and_avg_degree():
    s = summarize_plan(_demo_plan())
    assert s.net_count == 4
    # degrees: VCC=3, GND=4, BUSY=3, MID=2 -> mean 3.0.
    assert s.avg_net_degree == pytest.approx(3.0)


def test_empty_plan():
    plan = _plan(parts=[Part(refdes="U1", lib_ref="IC")],
                 nets=[_net("N", [("U1", "1"), ("U1", "2")])])
    s = summarize_plan(plan)
    assert s.part_count == 1 and s.net_count == 1
    # The only net is single-part -> no power/ground, hotspot is that net.
    assert s.power_rails == () and s.ground_nets == ()
