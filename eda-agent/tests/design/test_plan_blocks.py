# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for canonical circuit-block authoring (pure Python)."""

from __future__ import annotations

import pytest

from eda_agent.design.plan import DesignPlan
from eda_agent.design.plan_blocks import (
    BLOCK_SPECS,
    add_block,
    describe_blocks,
)


def _base_plan() -> DesignPlan:
    """An MCU on VCC/GND -- the typical thing blocks attach to."""
    return DesignPlan.model_validate({
        "spec": "test",
        "summary": "base plan with one IC on power rails",
        "sheets": [{"name": "main"}],
        "parts": [
            {"refdes": "U1", "lib_ref": "MCU", "value": "MCU"},
            {"refdes": "C1", "lib_ref": "CAP", "value": "1uF"},
        ],
        "nets": [
            {"name": "VCC", "is_power": True,
             "pins": [{"refdes": "U1", "pin": "1"}, {"refdes": "C1", "pin": "1"}]},
            {"name": "GND", "is_ground": True,
             "pins": [{"refdes": "U1", "pin": "2"}, {"refdes": "C1", "pin": "2"}]},
        ],
    })


def test_unknown_block_rejected():
    with pytest.raises(ValueError):
        add_block(_base_plan(), "frobnicate", {})


def test_describe_blocks_covers_every_block():
    described = {b["name"] for b in describe_blocks()}
    assert described == set(BLOCK_SPECS)
    for b in describe_blocks():
        assert b["summary"] and b["required"]
        # the universal optional params are always offered
        assert {"sheet", "lib_path", "pins"} <= set(b["optional"])


def test_spec_required_matches_enforcement():
    """Drift guard: every key BLOCK_SPECS marks required is actually
    enforced -- dropping it must raise. Ties the documented contract to
    the block's real _require() behaviour."""
    minimal = {
        "decoupling": {"power_net": "VCC", "ground_net": "GND", "lib_ref": "C"},
        "pullup": {"signal_net": "S", "rail_net": "VCC", "lib_ref": "R"},
        "pulldown": {"signal_net": "S", "ground_net": "GND", "lib_ref": "R"},
        "series_resistor": {"net_a": "A", "net_b": "B", "lib_ref": "R"},
        "voltage_divider": {"rail_net": "VCC", "ground_net": "GND",
                            "lib_ref_top": "R", "lib_ref_bottom": "R"},
        "rc_lowpass": {"input_net": "VCC", "ground_net": "GND",
                       "lib_ref_r": "R", "lib_ref_c": "C"},
        "rc_highpass": {"input_net": "VCC", "ground_net": "GND",
                        "lib_ref_r": "R", "lib_ref_c": "C"},
        "led_indicator": {"anode_net": "VCC", "cathode_net": "GND",
                          "lib_ref_r": "R", "lib_ref_led": "D"},
        "crystal": {"xin_net": "XI", "xout_net": "XO", "ground_net": "GND",
                    "lib_ref": "Y", "cap_lib_ref": "C"},
        "pi_filter": {"input_net": "VCC", "ground_net": "GND",
                      "lib_ref_l": "L", "lib_ref_c": "C"},
        "mosfet_low_side": {"control_net": "GPIO", "load_net": "LOAD",
                            "ground_net": "GND", "lib_ref_r": "R",
                            "lib_ref_fet": "FET"},
        "mosfet_high_side": {"control_net": "GPIO", "load_net": "LOAD",
                             "rail_net": "VCC", "lib_ref_r": "R",
                             "lib_ref_fet": "FET"},
    }
    for name, spec in BLOCK_SPECS.items():
        full = minimal[name]
        assert set(full) == set(spec["required"])    # fixture stays in sync
        for key in spec["required"]:
            broken = {k: v for k, v in full.items() if k != key}
            with pytest.raises(ValueError):
                add_block(_base_plan(), name, broken)


def test_invalid_net_name_clear_error():
    # a block creating a net with an illegal name raises a NAME error
    with pytest.raises(ValueError, match="invalid net name"):
        add_block(_base_plan(), "pullup", {
            "signal_net": "3BADNET", "rail_net": "VCC", "lib_ref": "RES"})


def test_unrecognised_param_warned():
    res = add_block(_base_plan(), "decoupling", {
        "power_net": "VCC", "ground_net": "GND", "lib_ref": "CAP",
        "val": "100nF"})   # typo for 'value'
    assert any("unrecognised" in n and "val" in n for n in res.notes)


def test_known_params_not_warned():
    res = add_block(_base_plan(), "decoupling", {
        "power_net": "VCC", "ground_net": "GND", "lib_ref": "CAP",
        "value": "100nF", "count": 2, "lib_path": "x.SchLib", "sheet": "main"})
    assert not any("unrecognised" in n for n in res.notes)


def test_decoupling_adds_caps_on_both_rails():
    res = add_block(_base_plan(), "decoupling", {
        "power_net": "VCC", "ground_net": "GND",
        "lib_ref": "CAP", "value": "100nF", "count": 3,
        "footprint": "C0402"})
    # 3 caps allocated past the existing C1 -> C2, C3, C4
    assert res.added_refdes == ["C2", "C3", "C4"]
    plan = res.plan
    assert len(plan.parts) == 2 + 3
    vcc = next(n for n in plan.nets if n.name == "VCC")
    gnd = next(n for n in plan.nets if n.name == "GND")
    for rd in ("C2", "C3", "C4"):
        assert any(p.refdes == rd and p.pin == "1" for p in vcc.pins)
        assert any(p.refdes == rd and p.pin == "2" for p in gnd.pins)
    # every new cap carries the decoup role + the supplied footprint
    caps = [p for p in plan.parts if p.refdes in ("C2", "C3", "C4")]
    assert all(p.role == "decoup_cap" and p.footprint == "C0402" for p in caps)
    assert res.extended_nets == ["VCC", "GND"]


def test_decoupling_result_validates_clean():
    res = add_block(_base_plan(), "decoupling", {
        "power_net": "VCC", "ground_net": "GND", "lib_ref": "CAP",
        "value": "100nF", "count": 2})
    # round-trips through the schema + cross-check with no errors
    reparsed = DesignPlan.model_validate(res.plan.model_dump())
    assert reparsed.cross_check() == []


def test_decoupling_count_must_be_positive():
    with pytest.raises(ValueError):
        add_block(_base_plan(), "decoupling", {
            "power_net": "VCC", "ground_net": "GND", "lib_ref": "CAP",
            "count": 0})


def test_decoupling_missing_param():
    with pytest.raises(ValueError):
        add_block(_base_plan(), "decoupling", {"power_net": "VCC"})


def test_pullup_to_rail():
    res = add_block(_base_plan(), "pullup", {
        "signal_net": "NRST", "rail_net": "VCC", "lib_ref": "RES",
        "value": "10k"})
    assert res.added_refdes == ["R1"]
    plan = res.plan
    r = next(p for p in plan.parts if p.refdes == "R1")
    assert r.role == "pullup" and r.value == "10k"
    vcc = next(n for n in plan.nets if n.name == "VCC")
    nrst = next(n for n in plan.nets if n.name == "NRST")
    assert any(p.refdes == "R1" and p.pin == "1" for p in vcc.pins)   # rail
    assert any(p.refdes == "R1" and p.pin == "2" for p in nrst.pins)  # signal
    assert "NRST" in res.added_nets


def test_pulldown_to_ground():
    res = add_block(_base_plan(), "pulldown", {
        "signal_net": "BOOT", "ground_net": "GND", "lib_ref": "RES",
        "value": "10k"})
    plan = res.plan
    gnd = next(n for n in plan.nets if n.name == "GND")
    assert any(p.refdes == "R1" and p.pin == "2" for p in gnd.pins)
    boot = next(n for n in plan.nets if n.name == "BOOT")
    assert any(p.refdes == "R1" and p.pin == "1" for p in boot.pins)
    assert gnd.is_ground is True


def test_series_resistor_bridges_two_nets():
    res = add_block(_base_plan(), "series_resistor", {
        "net_a": "TX", "net_b": "TX_T", "lib_ref": "RES", "value": "33"})
    plan = res.plan
    r = next(p for p in plan.parts if p.refdes == "R1")
    assert r.role == "series"
    tx = next(n for n in plan.nets if n.name == "TX")
    txt = next(n for n in plan.nets if n.name == "TX_T")
    assert any(p.pin == "1" for p in tx.pins)
    assert any(p.pin == "2" for p in txt.pins)


def test_voltage_divider_makes_midpoint_net():
    # rail is an existing net (VCC) so the result is fully connected
    res = add_block(_base_plan(), "voltage_divider", {
        "rail_net": "VCC", "ground_net": "GND",
        "lib_ref_top": "RES", "value_top": "100k",
        "lib_ref_bottom": "RES", "value_bottom": "33k",
        "output_net": "VSENSE"})
    assert res.added_refdes == ["R1", "R2"]
    plan = res.plan
    vsense = next(n for n in plan.nets if n.name == "VSENSE")
    # midpoint carries Rtop.2 and Rbot.1 -> 2 pins, schema-valid
    assert len(vsense.pins) == 2
    rt = next(p for p in plan.parts if p.refdes == "R1")
    rb = next(p for p in plan.parts if p.refdes == "R2")
    assert rt.role == "rdiv_top" and rt.value == "100k"
    assert rb.role == "rdiv_bot" and rb.value == "33k"
    assert DesignPlan.model_validate(plan.model_dump()).cross_check() == []


def test_voltage_divider_auto_output_net():
    res = add_block(_base_plan(), "voltage_divider", {
        "rail_net": "VBAT", "ground_net": "GND",
        "lib_ref_top": "RES", "lib_ref_bottom": "RES"})
    assert "VDIV_OUT" in res.added_nets
    assert any("VDIV_OUT" in note for note in res.notes)


def test_rc_lowpass_topology():
    # input is an existing net (VCC, used here as a reference to filter)
    res = add_block(_base_plan(), "rc_lowpass", {
        "input_net": "VCC", "ground_net": "GND",
        "lib_ref_r": "RES", "value_r": "1k",
        "lib_ref_c": "CAP", "value_c": "100nF",
        "output_net": "AIN_F"})
    assert res.added_refdes == ["R1", "C2"]   # R from R-pool, C past C1
    plan = res.plan
    out = next(n for n in plan.nets if n.name == "AIN_F")
    # output node bonds the series R and the shunt C
    assert {(p.refdes) for p in out.pins} == {"R1", "C2"}
    gnd = next(n for n in plan.nets if n.name == "GND")
    assert any(p.refdes == "C2" and p.pin == "2" for p in gnd.pins)
    # roles align with the placement priors (filter_r|ic / filter_c|ic)
    roles = {p.refdes: p.role for p in plan.parts}
    assert roles["R1"] == "filter_r" and roles["C2"] == "filter_c"
    assert DesignPlan.model_validate(plan.model_dump()).cross_check() == []


def test_rc_highpass_series_c_shunt_r():
    # series C (input->out) + shunt R (out->gnd): the mirror of rc_lowpass
    res = add_block(_base_plan(), "rc_highpass", {
        "input_net": "VCC", "ground_net": "GND",
        "lib_ref_c": "CAP", "value_c": "100nF",
        "lib_ref_r": "RES", "value_r": "10k",
        "output_net": "AC_OUT"})
    assert res.added_refdes == ["C2", "R1"]   # series C first, then shunt R
    plan = res.plan
    out = next(n for n in plan.nets if n.name == "AC_OUT")
    # output node bonds the series cap and the shunt resistor
    assert {p.refdes for p in out.pins} == {"C2", "R1"}
    # the series cap sits between input and output (not on ground)
    vcc = next(n for n in plan.nets if n.name == "VCC")
    assert any(p.refdes == "C2" for p in vcc.pins)
    gnd = next(n for n in plan.nets if n.name == "GND")
    assert any(p.refdes == "R1" and p.pin == "2" for p in gnd.pins)
    roles = {p.refdes: p.role for p in plan.parts}
    assert roles["C2"] == "filter_c" and roles["R1"] == "filter_r"
    assert DesignPlan.model_validate(plan.model_dump()).cross_check() == []


def test_led_indicator_series_r_then_led():
    res = add_block(_base_plan(), "led_indicator", {
        "anode_net": "VCC", "cathode_net": "GND",
        "lib_ref_r": "RES", "value_r": "1k",
        "lib_ref_led": "LED", "value_led": "GREEN"})
    assert res.added_refdes == ["R1", "D1"]
    plan = res.plan
    # a fresh mid node ties R.2 to LED anode
    mid = next(n for n in plan.nets if n.name.startswith("LED_A"))
    assert {p.refdes for p in mid.pins} == {"R1", "D1"}
    led = next(p for p in plan.parts if p.refdes == "D1")
    assert led.role == "indicator"
    # limit resistor role matches the led_limit|ic / indicator|led_limit priors
    r = next(p for p in plan.parts if p.refdes == "R1")
    assert r.role == "led_limit"
    gnd = next(n for n in plan.nets if n.name == "GND")
    assert any(p.refdes == "D1" and p.pin == "K" for p in gnd.pins)


def test_custom_pin_names_honoured():
    res = add_block(_base_plan(), "decoupling", {
        "power_net": "VCC", "ground_net": "GND", "lib_ref": "CAP",
        "pins": ["A", "B"]})
    plan = res.plan
    vcc = next(n for n in plan.nets if n.name == "VCC")
    assert any(p.refdes == "C2" and p.pin == "A" for p in vcc.pins)


def test_lib_path_applied_to_block_parts():
    res = add_block(_base_plan(), "decoupling", {
        "power_net": "VCC", "ground_net": "GND", "lib_ref": "CAP",
        "lib_path": "C:/libs/Passives.SchLib", "count": 2})
    new = [p for p in res.plan.parts if p.refdes in ("C2", "C3")]
    assert all(p.lib_path == "C:/libs/Passives.SchLib" for p in new)


def test_original_plan_not_mutated():
    base = _base_plan()
    before_parts = len(base.parts)
    add_block(base, "pullup", {
        "signal_net": "NRST", "rail_net": "VCC", "lib_ref": "RES"})
    assert len(base.parts) == before_parts   # input plan untouched


def test_crystal_block_matched_load_caps():
    # MCU oscillator pins pre-exist as OSC_IN / OSC_OUT nets.
    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "mcu with osc pins", "sheets": [{"name": "main"}],
        "parts": [{"refdes": "U1", "lib_ref": "MCU"}],
        "nets": [
            {"name": "OSC_IN",
             "pins": [{"refdes": "U1", "pin": "5"}, {"refdes": "U1", "pin": "7"}]},
            {"name": "OSC_OUT",
             "pins": [{"refdes": "U1", "pin": "6"}, {"refdes": "U1", "pin": "8"}]},
            {"name": "GND", "is_ground": True,
             "pins": [{"refdes": "U1", "pin": "2"}, {"refdes": "U1", "pin": "4"}]},
        ],
    })
    res = add_block(plan, "crystal", {
        "xin_net": "OSC_IN", "xout_net": "OSC_OUT", "ground_net": "GND",
        "lib_ref": "XTAL", "value": "8MHz",
        "cap_lib_ref": "CAP", "cap_value": "18pF"})
    assert res.added_refdes == ["Y1", "C1", "C2"]
    plan2 = res.plan
    y = next(p for p in plan2.parts if p.refdes == "Y1")
    assert y.role == "crystal" and y.value == "8MHz"
    caps = [p for p in plan2.parts if p.refdes in ("C1", "C2")]
    # both load caps share ONE value -> matched pair
    assert {c.value for c in caps} == {"18pF"}
    assert {c.role for c in caps} == {"crystal_cap_l", "crystal_cap_r"}
    gnd = next(n for n in plan2.nets if n.name == "GND")
    assert sum(1 for p in gnd.pins if p.refdes in ("C1", "C2")) == 2
    assert DesignPlan.model_validate(plan2.model_dump()).cross_check() == []


def test_crystal_missing_param():
    with pytest.raises(ValueError):
        add_block(_base_plan(), "crystal", {
            "xin_net": "OSC_IN", "xout_net": "OSC_OUT"})


def test_pi_filter_c_l_c_topology():
    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "rail to filter", "sheets": [{"name": "main"}],
        "parts": [{"refdes": "J1", "lib_ref": "CONN"}],
        "nets": [
            {"name": "VRAW", "is_power": True,
             "pins": [{"refdes": "J1", "pin": "1"}, {"refdes": "J1", "pin": "3"}]},
            {"name": "GND", "is_ground": True,
             "pins": [{"refdes": "J1", "pin": "2"}, {"refdes": "J1", "pin": "4"}]},
        ],
    })
    res = add_block(plan, "pi_filter", {
        "input_net": "VRAW", "ground_net": "GND",
        "lib_ref_l": "FERRITE", "value_l": "600R",
        "lib_ref_c": "CAP", "value_c": "100nF",
        "output_net": "VFILT"})
    assert res.added_refdes == ["C1", "L1", "C2"]
    plan2 = res.plan
    out = next(n for n in plan2.nets if n.name == "VFILT")
    # output node bonds the series L and the output shunt cap
    assert {p.refdes for p in out.pins} == {"L1", "C2"}
    vraw = next(n for n in plan2.nets if n.name == "VRAW")
    assert {p.refdes for p in vraw.pins if p.refdes in ("C1", "L1")} == {"C1", "L1"}
    roles = {p.refdes: p.role for p in plan2.parts}
    assert roles["C1"] == "pi_cap_in" and roles["L1"] == "pi_series" \
        and roles["C2"] == "pi_cap_out"
    assert DesignPlan.model_validate(plan2.model_dump()).cross_check() == []


def test_pi_filter_auto_output_net():
    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "rail", "sheets": [{"name": "main"}],
        "parts": [{"refdes": "J1", "lib_ref": "CONN"}],
        "nets": [
            {"name": "VRAW", "is_power": True,
             "pins": [{"refdes": "J1", "pin": "1"}, {"refdes": "J1", "pin": "3"}]},
            {"name": "GND", "is_ground": True,
             "pins": [{"refdes": "J1", "pin": "2"}, {"refdes": "J1", "pin": "4"}]},
        ],
    })
    res = add_block(plan, "pi_filter", {
        "input_net": "VRAW", "ground_net": "GND",
        "lib_ref_l": "FERRITE", "lib_ref_c": "CAP"})
    assert "PI_OUT" in res.added_nets


def test_mosfet_low_side_gate_network():
    # MCU GPIO drives a relay coil's low side; coil high side on VCC.
    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "gpio drives a load", "sheets": [{"name": "main"}],
        "parts": [
            {"refdes": "U1", "lib_ref": "MCU"},
            {"refdes": "K1", "lib_ref": "RELAY"},
        ],
        "nets": [
            {"name": "GPIO", "pins": [{"refdes": "U1", "pin": "9"},
                                      {"refdes": "U1", "pin": "10"}]},
            {"name": "RELAY_LO", "pins": [{"refdes": "K1", "pin": "1"},
                                          {"refdes": "K1", "pin": "2"}]},
            {"name": "GND", "is_ground": True,
             "pins": [{"refdes": "U1", "pin": "2"}, {"refdes": "U1", "pin": "4"}]},
        ],
    })
    res = add_block(plan, "mosfet_low_side", {
        "control_net": "GPIO", "load_net": "RELAY_LO", "ground_net": "GND",
        "lib_ref_r": "RES", "value_r_gate": "100",
        "lib_ref_fet": "2N7002", "value_r_pulldown": "10k"})
    # gate series R, FET, gate pull-down R
    assert res.added_refdes == ["R1", "Q1", "R2"]
    plan2 = res.plan
    roles = {p.refdes: p.role for p in plan2.parts}
    assert roles["R1"] == "gate_series" and roles["Q1"] == "switch" \
        and roles["R2"] == "gate_pulldown"
    # gate net ties Rgate.2, Q.G, Rpd.1 -> 3 pins
    gate = next(n for n in plan2.nets if n.name.startswith("GATE"))
    assert {p.refdes for p in gate.pins} == {"R1", "Q1", "R2"}
    # FET drain on the load, source to ground
    fet = next(n for n in plan2.nets if n.name == "RELAY_LO")
    assert any(p.refdes == "Q1" and p.pin == "D" for p in fet.pins)
    gnd = next(n for n in plan2.nets if n.name == "GND")
    assert any(p.refdes == "Q1" and p.pin == "S" for p in gnd.pins)
    assert DesignPlan.model_validate(plan2.model_dump()).cross_check() == []


def test_mosfet_high_side_pmos_to_rail():
    # PMOS high-side: source->rail, drain->load, gate pulled UP to rail
    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "high-side load switch",
        "sheets": [{"name": "main"}],
        "parts": [{"refdes": "U1", "lib_ref": "MCU"},
                  {"refdes": "J1", "lib_ref": "LOAD"}],
        "nets": [
            {"name": "GPIO", "pins": [{"refdes": "U1", "pin": "9"},
                                      {"refdes": "U1", "pin": "10"}]},
            {"name": "VBUS", "is_power": True,
             "pins": [{"refdes": "U1", "pin": "1"}, {"refdes": "U1", "pin": "3"}]},
            {"name": "SW_OUT", "pins": [{"refdes": "J1", "pin": "1"},
                                        {"refdes": "J1", "pin": "2"}]},
        ],
    })
    res = add_block(plan, "mosfet_high_side", {
        "control_net": "GPIO", "load_net": "SW_OUT", "rail_net": "VBUS",
        "lib_ref_r": "RES", "value_r_gate": "100",
        "lib_ref_fet": "AO3401", "value_r_pullup": "10k"})
    assert res.added_refdes == ["R1", "Q1", "R2"]   # gate R, PMOS, pull-up R
    plan2 = res.plan
    roles = {p.refdes: p.role for p in plan2.parts}
    assert roles["R1"] == "gate_series" and roles["Q1"] == "switch" \
        and roles["R2"] == "gate_pullup"
    # PMOS source on the rail, drain on the load
    vbus = next(n for n in plan2.nets if n.name == "VBUS")
    assert any(p.refdes == "Q1" and p.pin == "S" for p in vbus.pins)
    swout = next(n for n in plan2.nets if n.name == "SW_OUT")
    assert any(p.refdes == "Q1" and p.pin == "D" for p in swout.pins)
    # gate pulled UP to the rail (not down to ground)
    assert any(p.refdes == "R2" for p in vbus.pins)
    assert DesignPlan.model_validate(plan2.model_dump()).cross_check() == []


def test_mosfet_high_side_pullup_optional():
    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "no pullup", "sheets": [{"name": "main"}],
        "parts": [{"refdes": "U1", "lib_ref": "MCU"},
                  {"refdes": "J1", "lib_ref": "LOAD"}],
        "nets": [
            {"name": "GPIO", "pins": [{"refdes": "U1", "pin": "9"},
                                      {"refdes": "U1", "pin": "10"}]},
            {"name": "VBUS", "is_power": True,
             "pins": [{"refdes": "U1", "pin": "1"}, {"refdes": "U1", "pin": "3"}]},
            {"name": "SW_OUT", "pins": [{"refdes": "J1", "pin": "1"},
                                        {"refdes": "J1", "pin": "2"}]},
        ],
    })
    res = add_block(plan, "mosfet_high_side", {
        "control_net": "GPIO", "load_net": "SW_OUT", "rail_net": "VBUS",
        "lib_ref_r": "RES", "lib_ref_fet": "AO3401", "pullup": False})
    assert res.added_refdes == ["R1", "Q1"]   # no pull-up resistor


def test_mosfet_low_side_pulldown_optional():
    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "no pulldown", "sheets": [{"name": "main"}],
        "parts": [{"refdes": "U1", "lib_ref": "MCU"},
                  {"refdes": "K1", "lib_ref": "RELAY"}],
        "nets": [
            {"name": "GPIO", "pins": [{"refdes": "U1", "pin": "9"},
                                      {"refdes": "U1", "pin": "10"}]},
            {"name": "RELAY_LO", "pins": [{"refdes": "K1", "pin": "1"},
                                          {"refdes": "K1", "pin": "2"}]},
            {"name": "GND", "is_ground": True,
             "pins": [{"refdes": "U1", "pin": "2"}, {"refdes": "U1", "pin": "4"}]},
        ],
    })
    res = add_block(plan, "mosfet_low_side", {
        "control_net": "GPIO", "load_net": "RELAY_LO", "ground_net": "GND",
        "lib_ref_r": "RES", "lib_ref_fet": "2N7002", "pulldown": False})
    assert res.added_refdes == ["R1", "Q1"]   # no pull-down resistor


def test_mosfet_custom_fet_pins():
    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "numbered fet", "sheets": [{"name": "main"}],
        "parts": [{"refdes": "U1", "lib_ref": "MCU"},
                  {"refdes": "K1", "lib_ref": "RELAY"}],
        "nets": [
            {"name": "GPIO", "pins": [{"refdes": "U1", "pin": "9"},
                                      {"refdes": "U1", "pin": "10"}]},
            {"name": "RELAY_LO", "pins": [{"refdes": "K1", "pin": "1"},
                                          {"refdes": "K1", "pin": "2"}]},
            {"name": "GND", "is_ground": True,
             "pins": [{"refdes": "U1", "pin": "2"}, {"refdes": "U1", "pin": "4"}]},
        ],
    })
    res = add_block(plan, "mosfet_low_side", {
        "control_net": "GPIO", "load_net": "RELAY_LO", "ground_net": "GND",
        "lib_ref_r": "RES", "lib_ref_fet": "FET", "pulldown": False,
        "fet_pins": ["1", "2", "3"]})   # gate, drain, source
    fet_q = next(p for p in res.plan.parts if p.refdes == "Q1")
    gnd = next(n for n in res.plan.nets if n.name == "GND")
    assert any(p.refdes == "Q1" and p.pin == "3" for p in gnd.pins)  # source


def test_series_capacitor_via_refdes_prefix():
    # A bare series (AC-coupling) cap needs no dedicated block: series_resistor
    # with a cap lib_ref and refdes_prefix='C' gives a correctly-prefixed cap
    # bridging two nets.
    res = add_block(_base_plan(), "series_resistor", {
        "net_a": "VCC", "net_b": "GND", "lib_ref": "CAP", "value": "1uF",
        "refdes_prefix": "C"})
    part = next(p for p in res.plan.parts if p.refdes in res.added_refdes)
    assert part.refdes.startswith("C") and part.value == "1uF"
    assert part.role == "series"


def test_i2c_pullups_via_two_pullup_blocks():
    # I2C pull-ups need no dedicated block: two pullup blocks on SDA/SCL.
    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "i2c", "sheets": [{"name": "main"}],
        "parts": [{"refdes": "U1", "lib_ref": "MCU"}],
        "nets": [
            {"name": "VCC", "is_power": True, "pins": [
                {"refdes": "U1", "pin": "1"}, {"refdes": "U1", "pin": "8"}]},
            {"name": "SDA", "pins": [{"refdes": "U1", "pin": "5"},
                                     {"refdes": "U1", "pin": "9"}]},
            {"name": "SCL", "pins": [{"refdes": "U1", "pin": "6"},
                                     {"refdes": "U1", "pin": "10"}]},
        ],
    })
    r = add_block(plan, "pullup", {"signal_net": "SDA", "rail_net": "VCC",
                                   "lib_ref": "RES", "value": "4k7"})
    r = add_block(r.plan, "pullup", {"signal_net": "SCL", "rail_net": "VCC",
                                     "lib_ref": "RES", "value": "4k7"})
    pulls = [p for p in r.plan.parts if p.role == "pullup"]
    assert len(pulls) == 2
    assert DesignPlan.model_validate(r.plan.model_dump()).cross_check() == []


def test_refdes_allocation_skips_gaps():
    # plan already using R1, R3 -> next should be R2 then R4
    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "gap test", "sheets": [{"name": "main"}],
        "parts": [
            {"refdes": "U1", "lib_ref": "MCU"},
            {"refdes": "R1", "lib_ref": "RES"},
            {"refdes": "R3", "lib_ref": "RES"},
        ],
        "nets": [
            {"name": "VCC", "is_power": True,
             "pins": [{"refdes": "U1", "pin": "1"}, {"refdes": "R1", "pin": "1"}]},
            {"name": "GND", "is_ground": True,
             "pins": [{"refdes": "U1", "pin": "2"}, {"refdes": "R3", "pin": "1"}]},
        ],
    })
    res = add_block(plan, "series_resistor", {
        "net_a": "VCC", "net_b": "GND", "lib_ref": "RES"})
    assert res.added_refdes == ["R2"]   # fills the gap before R3
