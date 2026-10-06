# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for parallel-bus authoring (pure Python)."""

from __future__ import annotations

import pytest

from eda_agent.design.buses import detect_buses
from eda_agent.design.plan import DesignPlan
from eda_agent.design.plan_blocks import connect_bus


def _two_ic_plan() -> DesignPlan:
    """An MCU and a memory, only their power rails wired so far."""
    return DesignPlan.model_validate({
        "spec": "bus", "summary": "mcu + memory awaiting a data bus",
        "sheets": [{"name": "main"}],
        "parts": [
            {"refdes": "U1", "lib_ref": "MCU"},
            {"refdes": "U2", "lib_ref": "SRAM"},
        ],
        "nets": [
            {"name": "VCC", "is_power": True,
             "pins": [{"refdes": "U1", "pin": "1"}, {"refdes": "U2", "pin": "1"}]},
            {"name": "GND", "is_ground": True,
             "pins": [{"refdes": "U1", "pin": "2"}, {"refdes": "U2", "pin": "2"}]},
        ],
    })


def test_bus_joins_corresponding_pins():
    res = connect_bus(_two_ic_plan(), [
        {"refdes": "U1", "pins": [10, 11, 12, 13]},
        {"refdes": "U2", "pins": [3, 4, 5, 6]},
    ])
    assert res.added_nets == ["D0", "D1", "D2", "D3"]
    plan = res.plan
    d0 = next(n for n in plan.nets if n.name == "D0")
    # bit 0 joins U1.10 and U2.3 (the i-th pin of each side)
    assert {(p.refdes, p.pin) for p in d0.pins} == {("U1", "10"), ("U2", "3")}
    d3 = next(n for n in plan.nets if n.name == "D3")
    assert {(p.refdes, p.pin) for p in d3.pins} == {("U1", "13"), ("U2", "6")}
    assert DesignPlan.model_validate(plan.model_dump()).cross_check() == []


def test_bus_explicit_names_and_prefix():
    res = connect_bus(_two_ic_plan(), [
        {"refdes": "U1", "pins": [20, 21]},
        {"refdes": "U2", "pins": [7, 8]},
    ], net_names=["A0", "A1"])
    assert res.added_nets == ["A0", "A1"]

    res2 = connect_bus(_two_ic_plan(), [
        {"refdes": "U1", "pins": [20, 21]},
        {"refdes": "U2", "pins": [7, 8]},
    ], net_prefix="ADDR", start_index=4)
    assert res2.added_nets == ["ADDR4", "ADDR5"]


def test_bus_three_endpoints():
    # a shared bus tapping three parts (MCU + two peripherals)
    plan = DesignPlan.model_validate({
        "spec": "bus", "summary": "three-way bus",
        "sheets": [{"name": "main"}],
        "parts": [
            {"refdes": "U1", "lib_ref": "MCU"},
            {"refdes": "U2", "lib_ref": "DAC"},
            {"refdes": "U3", "lib_ref": "ADC"},
        ],
        "nets": [
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "U1", "pin": "2"}, {"refdes": "U2", "pin": "2"},
                {"refdes": "U3", "pin": "2"}]},
        ],
    })
    res = connect_bus(plan, [
        {"refdes": "U1", "pins": [10, 11]},
        {"refdes": "U2", "pins": [3, 4]},
        {"refdes": "U3", "pins": [5, 6]},
    ], net_prefix="SD")
    sd0 = next(n for n in res.plan.nets if n.name == "SD0")
    assert {p.refdes for p in sd0.pins} == {"U1", "U2", "U3"}


def test_bus_merges_into_existing_net():
    plan = _two_ic_plan()
    # pre-existing D0 with one pin already on it
    plan = plan.model_copy(update={"nets": [*plan.nets, DesignPlan.model_validate({
        "spec": "x", "summary": "x", "sheets": [{"name": "main"}],
        "parts": [{"refdes": "U1", "lib_ref": "MCU"}],
        "nets": [{"name": "D0", "pins": [
            {"refdes": "U1", "pin": "10"}, {"refdes": "U1", "pin": "99"}]}],
    }).nets[0]]})
    res = connect_bus(plan, [
        {"refdes": "U1", "pins": [10]},
        {"refdes": "U2", "pins": [3]},
    ])
    d0 = next(n for n in res.plan.nets if n.name == "D0")
    # U1.10 already present (not duplicated), U2.3 merged in
    assert sum(1 for p in d0.pins if (p.refdes, p.pin) == ("U1", "10")) == 1
    assert any((p.refdes, p.pin) == ("U2", "3") for p in d0.pins)
    assert "D0" in res.extended_nets


def test_bus_needs_two_endpoints():
    with pytest.raises(ValueError):
        connect_bus(_two_ic_plan(), [{"refdes": "U1", "pins": [10, 11]}])


def test_bus_width_must_match():
    with pytest.raises(ValueError):
        connect_bus(_two_ic_plan(), [
            {"refdes": "U1", "pins": [10, 11, 12]},
            {"refdes": "U2", "pins": [3, 4]},
        ])


def test_bus_net_names_length_checked():
    with pytest.raises(ValueError):
        connect_bus(_two_ic_plan(), [
            {"refdes": "U1", "pins": [10, 11]},
            {"refdes": "U2", "pins": [3, 4]},
        ], net_names=["only_one"])


def test_bus_unknown_refdes_rejected():
    with pytest.raises(ValueError):
        connect_bus(_two_ic_plan(), [
            {"refdes": "U1", "pins": [10, 11]},
            {"refdes": "U9", "pins": [3, 4]},   # not in plan
        ])


def test_bus_invalid_net_name_clear_error():
    with pytest.raises(ValueError, match="invalid net name"):
        connect_bus(_two_ic_plan(), [
            {"refdes": "U1", "pins": [10, 11]},
            {"refdes": "U2", "pins": [3, 4]},
        ], net_names=["D0", "9BAD"])   # second bit name starts with a digit


def test_repeated_bus_pin_is_a_detectable_short():
    # bit 0 and bit 1 both tap U1.10 -> one pin on two nets -> ERC short
    from eda_agent.design.plan_erc import check_plan_erc
    res = connect_bus(_two_ic_plan(), [
        {"refdes": "U1", "pins": [10, 10]},
        {"refdes": "U2", "pins": [3, 4]},
    ])
    erc = check_plan_erc(DesignPlan.model_validate(res.plan.model_dump()))
    assert not erc.passed
    assert any(i.code == "shorted_pin" for i in erc.errors)


def test_authored_bus_is_detected_as_a_bus():
    """An 8-bit bus authored here is recognised by the bus drawer."""
    res = connect_bus(_two_ic_plan(), [
        {"refdes": "U1", "pins": [10, 11, 12, 13, 14, 15, 16, 17]},
        {"refdes": "U2", "pins": [3, 4, 5, 6, 7, 8, 9, 20]},
    ])
    buses = detect_buses(res.plan)
    assert len(buses) == 1
    assert len(buses[0].nets) == 8
