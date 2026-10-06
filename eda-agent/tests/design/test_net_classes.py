# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Net-class assignment tests."""

from __future__ import annotations

from eda_agent.design.net_classes import classify_nets, net_class_of
from eda_agent.design.plan import DesignPlan, Net, Part, PinRef, Sheet


def _net(name, **kw):
    return Net(name=name, pins=[PinRef(refdes="U1", pin="1"),
                                PinRef(refdes="U2", pin="1")], **kw)


def _plan(nets):
    return DesignPlan(
        spec="nc", summary="net class plan", sheets=[Sheet(name="main")],
        zones=[], parts=[Part(refdes="U1", lib_ref="IC"),
                         Part(refdes="U2", lib_ref="IC")],
        nets=nets)


def test_flags_win_over_role():
    assert net_class_of(_net("GND", is_ground=True, role="signal")) == "ground"
    assert net_class_of(_net("VCC", is_power=True)) == "power"


def test_roles_map_to_classes():
    assert net_class_of(_net("DP", role="differential")) == "differential"
    assert net_class_of(_net("CLK", role="clock")) == "clock"
    assert net_class_of(_net("AIN", role="analog_sensitive")) == "analog"
    assert net_class_of(_net("FB", role="feedback")) == "analog"
    assert net_class_of(_net("SW", role="switch")) == "switch"
    assert net_class_of(_net("IL", role="high_current")) == "high_current"
    assert net_class_of(_net("EN", role="control")) == "control"


def test_unknown_role_is_signal():
    assert net_class_of(_net("X", role="weird")) == "signal"
    assert net_class_of(_net("Y")) == "signal"


def test_classify_groups_and_per_net():
    plan = _plan([
        _net("VCC", is_power=True),
        _net("GND", is_ground=True),
        _net("DP", role="differential"),
        _net("DM", role="differential"),
        _net("SIG"),
    ])
    rep = classify_nets(plan)
    assert rep.by_net["VCC"] == "power"
    assert rep.by_net["GND"] == "ground"
    assert rep.groups["differential"] == ("DM", "DP")   # sorted
    assert rep.groups["signal"] == ("SIG",)
    # deterministic
    assert classify_nets(plan) == rep
