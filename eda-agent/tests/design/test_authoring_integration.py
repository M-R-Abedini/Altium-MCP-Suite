# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""End-to-end integration of the authoring tools on one realistic board.

The unit tests cover each authoring tool in isolation; this exercises the
WHOLE chain on a single plan -- compose_netlist (add_part + several block
types + connect_bus) -> edit_plan (post-review fixes) -> generate_bom ->
validate -- so a cross-tool integration break (one tool's output that the
next can't consume) is caught here even when every unit test still passes.
It doubles as living documentation of the canonical authoring flow.
"""

from __future__ import annotations

from eda_agent.design.bom import generate_bom
from eda_agent.design.plan import DesignPlan
from eda_agent.design.plan_blocks import compose_netlist
from eda_agent.design.plan_edit import edit_plan
from eda_agent.design.plan_erc import check_plan_erc


def _shell() -> DesignPlan:
    """A connector establishing the supply rails + a couple of raw I/O nets."""
    return DesignPlan.model_validate({
        "spec": "sensor board",
        "summary": "MCU + I2C sensor, integration fixture",
        "sheets": [{"name": "main"}],
        "parts": [{"refdes": "J1", "lib_ref": "CONN"}],
        "nets": [
            {"name": "VCC", "is_power": True, "pins": [
                {"refdes": "J1", "pin": "1"}, {"refdes": "J1", "pin": "3"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "J1", "pin": "2"}, {"refdes": "J1", "pin": "4"}]},
            {"name": "LOAD", "pins": [
                {"refdes": "J1", "pin": "5"}, {"refdes": "J1", "pin": "6"}]},
        ],
    })


def test_full_authoring_chain_produces_a_valid_board():
    # 1) CONSTRUCT -- core parts + every common peripheral, in one bulk call
    res = compose_netlist(_shell(), [
        # MCU: power + the signal nets the blocks/bus attach to
        {"op": "add_part", "refdes": "U1", "lib_ref": "MCU",
         "connections": {"1": "VCC", "2": "GND", "5": "OSC_IN",
                         "6": "OSC_OUT", "7": "SDA", "8": "SCL",
                         "9": "NRST", "10": "PB0", "12": "GATE_CTRL",
                         "20": "D0", "21": "D1", "22": "D2", "23": "D3"},
         "power_nets": ["VCC"], "ground_nets": ["GND"]},
        # I2C sensor sharing VCC/GND/SDA/SCL + a parallel data bus to the MCU
        {"op": "add_part", "refdes": "U2", "lib_ref": "SENSOR",
         "connections": {"1": "VCC", "8": "GND", "5": "SDA", "6": "SCL"},
         "power_nets": ["VCC"], "ground_nets": ["GND"]},
        {"op": "add_block", "block": "decoupling",
         "params": {"power_net": "VCC", "ground_net": "GND",
                    "lib_ref": "CAP", "value": "100nF", "count": 4}},
        {"op": "add_block", "block": "crystal",
         "params": {"xin_net": "OSC_IN", "xout_net": "OSC_OUT",
                    "ground_net": "GND", "lib_ref": "XTAL",
                    "cap_lib_ref": "CAP", "cap_value": "18pF"}},
        # I2C pull-ups (two pullup blocks)
        {"op": "add_block", "block": "pullup",
         "params": {"signal_net": "SDA", "rail_net": "VCC",
                    "lib_ref": "RES", "value": "4k7"}},
        {"op": "add_block", "block": "pullup",
         "params": {"signal_net": "SCL", "rail_net": "VCC",
                    "lib_ref": "RES", "value": "4k7"}},
        # reset pull-up + status LED
        {"op": "add_block", "block": "pullup",
         "params": {"signal_net": "NRST", "rail_net": "VCC",
                    "lib_ref": "RES", "value": "10k"}},
        {"op": "add_block", "block": "led_indicator",
         "params": {"anode_net": "PB0", "cathode_net": "GND",
                    "lib_ref_r": "RES", "value_r": "1k", "lib_ref_led": "LED"}},
        # low-side switch driving the connector's LOAD net
        {"op": "add_block", "block": "mosfet_low_side",
         "params": {"control_net": "GATE_CTRL", "load_net": "LOAD",
                    "ground_net": "GND", "lib_ref_r": "RES",
                    "lib_ref_fet": "2N7002", "mpn": "2N7002",
                    "manufacturer": "onsemi"}},
        # parallel data bus MCU<->sensor
        {"op": "connect_bus", "endpoints": [
            {"refdes": "U1", "pins": [20, 21, 22, 23]},
            {"refdes": "U2", "pins": [2, 3, 4, 7]}]},
    ])
    constructed = DesignPlan.model_validate(res.plan.model_dump())
    assert constructed.cross_check() == []
    erc = check_plan_erc(constructed)
    assert erc.passed, [i.code for i in erc.errors]
    # the data bus is recognised for drawing
    from eda_agent.design.buses import detect_buses
    assert any(len(b.nets) == 4 for b in detect_buses(constructed))

    # 2) MODIFY -- post-review fixes via edit_plan
    edited = edit_plan(res.plan, [
        {"op": "set_part", "refdes": "R1", "value": "2k2"},   # tune a pull-up
        {"op": "set_net", "net": "LOAD", "role": "high_current"},
        {"op": "rename_net", "old": "PB0", "new": "LED_CTRL"},
    ])
    plan2 = DesignPlan.model_validate(edited.plan.model_dump())
    assert plan2.cross_check() == [] and check_plan_erc(plan2).passed
    assert any(n.name == "LED_CTRL" for n in plan2.nets)
    assert next(n for n in plan2.nets if n.name == "LOAD").role == "high_current"

    # 3) OUTPUT -- derive the BOM from the authored parts
    bom = generate_bom(plan2)
    refs = {rd for line in bom for rd in line.refdes_list}
    parts = {p.refdes for p in plan2.parts}
    assert refs == parts                      # every part appears once in the BOM
    # the FET carries its mpn; the generic passives are flagged for sourcing
    fet_line = next(b for b in bom if b.mpn == "2N7002")
    assert fet_line.refdes_list == ["Q1"]
    from eda_agent.design.bom import bom_summary
    assert "C1" in bom_summary(bom)["lines_without_mpn"]

    # a realistic board lands a healthy part count (MCU+sensor+conn+4 decap
    # +crystal+2 load caps +3 pull-ups +LED+R +FET+2 gate R)
    assert len(plan2.parts) >= 16
