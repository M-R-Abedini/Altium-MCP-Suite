# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for single-part authoring (add_component)."""

from __future__ import annotations

import pytest

from eda_agent.design.plan import DesignPlan
from eda_agent.design.plan_blocks import add_component


def _rails_plan() -> DesignPlan:
    """Two connectors already establishing VCC and GND (2-pin nets)."""
    return DesignPlan.model_validate({
        "spec": "t", "summary": "power rails present, IC not yet placed",
        "sheets": [{"name": "main"}],
        "parts": [
            {"refdes": "J1", "lib_ref": "CONN"},
            {"refdes": "J2", "lib_ref": "CONN"},
        ],
        "nets": [
            {"name": "VCC", "is_power": True,
             "pins": [{"refdes": "J1", "pin": "1"}, {"refdes": "J2", "pin": "1"}]},
            {"name": "GND", "is_ground": True,
             "pins": [{"refdes": "J1", "pin": "2"}, {"refdes": "J2", "pin": "2"}]},
        ],
    })


def test_add_part_wires_pins_and_merges_shared_net():
    res = add_component(
        _rails_plan(), refdes="U1", lib_ref="MCU",
        connections={"1": "VCC", "9": "VCC", "2": "GND"},
        value="STM32", footprint="LQFP48")
    assert res.added_refdes == ["U1"]
    plan = res.plan
    u1 = next(p for p in plan.parts if p.refdes == "U1")
    assert u1.value == "STM32" and u1.footprint == "LQFP48"
    vcc = next(n for n in plan.nets if n.name == "VCC")
    # both VCC pins of the IC folded onto the one existing VCC net
    assert {(p.refdes, p.pin) for p in vcc.pins if p.refdes == "U1"} \
        == {("U1", "1"), ("U1", "9")}
    gnd = next(n for n in plan.nets if n.name == "GND")
    assert any(p.refdes == "U1" and p.pin == "2" for p in gnd.pins)
    assert DesignPlan.model_validate(plan.model_dump()).cross_check() == []


def test_created_net_gets_power_flag():
    # a brand-new rail this part introduces is flagged on creation
    res = add_component(
        _rails_plan(), refdes="U1", lib_ref="LDO",
        connections={"3": "V3V3"}, power_nets=["V3V3"])
    v33 = next(n for n in res.plan.nets if n.name == "V3V3")
    assert v33.is_power is True
    assert "V3V3" in res.added_nets


def test_existing_net_flags_preserved():
    # VCC already exists as power; routing a pin onto it must not re-type it
    res = add_component(
        _rails_plan(), refdes="U1", lib_ref="MCU",
        connections={"1": "VCC"}, power_nets=[])  # not re-flagged here
    vcc = next(n for n in res.plan.nets if n.name == "VCC")
    assert vcc.is_power is True   # kept from the base plan


def test_net_roles_applied_to_created_nets():
    res = add_component(
        _rails_plan(), refdes="U1", lib_ref="MCU",
        connections={"5": "OSC_IN"}, net_roles={"OSC_IN": "clock"})
    osc = next(n for n in res.plan.nets if n.name == "OSC_IN")
    assert osc.role == "clock"


def test_lib_path_and_metadata_fields():
    res = add_component(
        _rails_plan(), refdes="U1", lib_ref="MCU",
        connections={"1": "VCC"}, lib_path="C:/lib/MCU.SchLib",
        manufacturer="ST", mpn="STM32F030", datasheet_url="http://x")
    u1 = next(p for p in res.plan.parts if p.refdes == "U1")
    assert u1.lib_path == "C:/lib/MCU.SchLib"
    assert u1.manufacturer == "ST" and u1.mpn == "STM32F030"
    assert u1.datasheet_url == "http://x"


def test_duplicate_refdes_rejected():
    with pytest.raises(ValueError):
        add_component(_rails_plan(), refdes="J1", lib_ref="X",
                      connections={"1": "VCC"})


def test_empty_connections_rejected():
    with pytest.raises(ValueError):
        add_component(_rails_plan(), refdes="U1", lib_ref="MCU",
                      connections={})


def test_original_plan_not_mutated():
    base = _rails_plan()
    n_before = len(base.parts)
    add_component(base, refdes="U1", lib_ref="MCU", connections={"1": "VCC"})
    assert len(base.parts) == n_before


def test_invalid_net_name_clear_error():
    # a net name starting with a digit must raise a NAME error, not a
    # masked "net needs 2 pins" downstream failure
    with pytest.raises(ValueError, match="invalid net name"):
        add_component(_rails_plan(), refdes="U1", lib_ref="LDO",
                      connections={"3": "3V3"})


def test_part_centric_then_validates_when_completed():
    # author an IC purely via connections, completing every net with the
    # connectors -> a fully valid plan, no raw net JSON written
    res = add_component(
        _rails_plan(), refdes="U1", lib_ref="MCU",
        connections={"1": "VCC", "2": "GND", "3": "VCC", "4": "GND"})
    final = DesignPlan.model_validate(res.plan.model_dump())
    assert final.cross_check() == []
    # IC contributed two pins to each rail
    vcc = next(n for n in final.nets if n.name == "VCC")
    assert sum(1 for p in vcc.pins if p.refdes == "U1") == 2
