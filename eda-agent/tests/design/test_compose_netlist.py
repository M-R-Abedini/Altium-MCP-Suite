# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for bulk netlist authoring (compose_netlist)."""

from __future__ import annotations

import pytest

from eda_agent.design.buses import detect_buses
from eda_agent.design.plan import DesignPlan
from eda_agent.design.plan_blocks import compose_netlist
from eda_agent.design.plan_erc import check_plan_erc


def _shell() -> DesignPlan:
    """A bare shell: one connector establishing VCC and GND."""
    return DesignPlan.model_validate({
        "spec": "board", "summary": "shell awaiting parts",
        "sheets": [{"name": "main"}],
        "parts": [{"refdes": "J1", "lib_ref": "CONN"}],
        "nets": [
            {"name": "VCC", "is_power": True,
             "pins": [{"refdes": "J1", "pin": "1"}, {"refdes": "J1", "pin": "3"}]},
            {"name": "GND", "is_ground": True,
             "pins": [{"refdes": "J1", "pin": "2"}, {"refdes": "J1", "pin": "4"}]},
        ],
    })


def test_whole_board_in_one_pass():
    ops = [
        {"op": "add_part", "refdes": "U1", "lib_ref": "MCU",
         "connections": {"1": "VCC", "20": "VCC", "10": "GND",
                         "5": "OSC_IN", "6": "OSC_OUT", "14": "NRST"},
         "power_nets": ["VCC"], "ground_nets": ["GND"]},
        {"op": "add_part", "refdes": "U2", "lib_ref": "SRAM",
         "connections": {"1": "VCC", "16": "GND"},
         "power_nets": ["VCC"], "ground_nets": ["GND"]},
        {"op": "add_block", "block": "decoupling",
         "params": {"power_net": "VCC", "ground_net": "GND",
                    "lib_ref": "CAP", "value": "100nF", "count": 3}},
        {"op": "add_block", "block": "crystal",
         "params": {"xin_net": "OSC_IN", "xout_net": "OSC_OUT",
                    "ground_net": "GND", "lib_ref": "XTAL",
                    "cap_lib_ref": "CAP", "cap_value": "18pF"}},
        {"op": "add_block", "block": "pullup",
         "params": {"signal_net": "NRST", "rail_net": "VCC",
                    "lib_ref": "RES", "value": "10k"}},
        {"op": "connect_bus", "endpoints": [
            {"refdes": "U1", "pins": [40, 41, 42, 43]},
            {"refdes": "U2", "pins": [2, 3, 4, 5]}]},
    ]
    res = compose_netlist(_shell(), ops)
    final = DesignPlan.model_validate(res.plan.model_dump())
    # MCU + SRAM + 3 decap + crystal + 2 load caps + reset R
    assert {"U1", "U2", "Y1", "R1"} <= {p.refdes for p in final.parts}
    assert len([p for p in final.parts if p.refdes.startswith("C")]) == 5
    assert final.cross_check() == []
    erc = check_plan_erc(final)
    assert erc.passed, [i.code for i in erc.errors]
    # the 4-bit bus is recognised for drawing
    assert any(len(b.nets) == 4 for b in detect_buses(final))
    # accumulated reporting across all ops
    assert "U1" in res.added_refdes and "Y1" in res.added_refdes


def test_opamp_amplifiers_compose_from_primitives():
    """An op-amp amplifier needs no dedicated block: the inverting form is
    op-amp + two series_resistor, the non-inverting form is op-amp +
    voltage_divider feedback. Both must validate clean -- this documents
    that the authoring primitives already cover analog amplifiers."""
    shell = DesignPlan.model_validate({
        "spec": "amp", "summary": "opamp shell", "sheets": [{"name": "main"}],
        "parts": [{"refdes": "J1", "lib_ref": "CONN"}],
        "nets": [
            {"name": "VCC", "is_power": True, "pins": [
                {"refdes": "J1", "pin": "1"}, {"refdes": "J1", "pin": "3"}]},
            {"name": "VEE", "is_power": True, "pins": [
                {"refdes": "J1", "pin": "5"}, {"refdes": "J1", "pin": "7"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "J1", "pin": "2"}, {"refdes": "J1", "pin": "4"}]},
            {"name": "VIN", "pins": [
                {"refdes": "J1", "pin": "6"}, {"refdes": "J1", "pin": "8"}]},
        ],
    })
    inverting = compose_netlist(shell, [
        {"op": "add_part", "refdes": "U1", "lib_ref": "OPAMP",
         "connections": {"7": "VCC", "4": "VEE", "3": "GND",
                         "2": "INV", "6": "VOUT"},
         "power_nets": ["VCC", "VEE"]},
        {"op": "add_block", "block": "series_resistor",
         "params": {"net_a": "VIN", "net_b": "INV", "lib_ref": "RES",
                    "value": "10k"}},
        {"op": "add_block", "block": "series_resistor",
         "params": {"net_a": "INV", "net_b": "VOUT", "lib_ref": "RES",
                    "value": "100k"}},
    ])
    inv = DesignPlan.model_validate(inverting.plan.model_dump())
    assert inv.cross_check() == [] and check_plan_erc(inv).passed

    non_inverting = compose_netlist(shell, [
        {"op": "add_part", "refdes": "U1", "lib_ref": "OPAMP",
         "connections": {"7": "VCC", "4": "VEE", "3": "VIN",
                         "2": "FB", "6": "VOUT"},
         "power_nets": ["VCC", "VEE"]},
        {"op": "add_block", "block": "voltage_divider",
         "params": {"rail_net": "VOUT", "ground_net": "GND",
                    "lib_ref_top": "RES", "value_top": "100k",
                    "lib_ref_bottom": "RES", "value_bottom": "10k",
                    "output_net": "FB"}},
    ])
    ni = DesignPlan.model_validate(non_inverting.plan.model_dump())
    assert ni.cross_check() == [] and check_plan_erc(ni).passed


def test_later_op_sees_earlier_part():
    # connect_bus references U1 added by the first op in the same call
    ops = [
        {"op": "add_part", "refdes": "U1", "lib_ref": "MCU",
         "connections": {"1": "VCC"}},
        {"op": "add_part", "refdes": "U2", "lib_ref": "MEM",
         "connections": {"1": "VCC"}},
        {"op": "connect_bus", "endpoints": [
            {"refdes": "U1", "pins": [10, 11]},
            {"refdes": "U2", "pins": [2, 3]}]},
    ]
    res = compose_netlist(_shell(), ops)
    assert "D0" in res.added_nets and "D1" in res.added_nets


def test_empty_operations_is_noop():
    res = compose_netlist(_shell(), [])
    assert res.added_refdes == [] and res.added_nets == []
    assert {p.refdes for p in res.plan.parts} == {"J1"}


def test_failing_op_reports_its_index():
    ops = [
        {"op": "add_part", "refdes": "U1", "lib_ref": "MCU",
         "connections": {"1": "VCC"}},
        {"op": "add_block", "block": "decoupling",
         "params": {"power_net": "VCC"}},   # missing ground_net + lib_ref
    ]
    with pytest.raises(ValueError, match="operation 1"):
        compose_netlist(_shell(), ops)


def test_unknown_op_rejected():
    with pytest.raises(ValueError, match="operation 0"):
        compose_netlist(_shell(), [{"op": "frobnicate"}])


def test_duplicate_refdes_across_ops_rejected():
    ops = [
        {"op": "add_part", "refdes": "U1", "lib_ref": "MCU",
         "connections": {"1": "VCC"}},
        {"op": "add_part", "refdes": "U1", "lib_ref": "OTHER",
         "connections": {"2": "GND"}},   # collides with op 0
    ]
    with pytest.raises(ValueError, match="operation 1"):
        compose_netlist(_shell(), ops)
