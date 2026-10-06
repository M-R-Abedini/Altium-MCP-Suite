# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for plan editing (the modify complement to the add tools)."""

from __future__ import annotations

import pytest

from eda_agent.design.bom import generate_bom
from eda_agent.design.plan import DesignPlan
from eda_agent.design.plan_edit import edit_plan
from eda_agent.design.plan_erc import check_plan_erc


def _plan() -> DesignPlan:
    return DesignPlan.model_validate({
        "spec": "t", "summary": "edit base", "sheets": [{"name": "main"}],
        "parts": [
            {"refdes": "U1", "lib_ref": "MCU"},
            {"refdes": "R1", "lib_ref": "RES", "value": "10k"},
            {"refdes": "R2", "lib_ref": "RES", "value": "10k"},
        ],
        "nets": [
            {"name": "VCC", "is_power": True, "pins": [
                {"refdes": "U1", "pin": "1"}, {"refdes": "R1", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "U1", "pin": "2"}, {"refdes": "R2", "pin": "2"}]},
            {"name": "SIG", "pins": [
                {"refdes": "R1", "pin": "2"}, {"refdes": "R2", "pin": "1"}]},
        ],
    })


def test_set_part_changes_value():
    res = edit_plan(_plan(), [
        {"op": "set_part", "refdes": "R1", "value": "4k7",
         "mpn": "RC0402FR-074K7L", "manufacturer": "Yageo"}])
    r1 = next(p for p in res.plan.parts if p.refdes == "R1")
    assert r1.value == "4k7" and r1.mpn == "RC0402FR-074K7L"
    assert r1.manufacturer == "Yageo"


def test_set_part_unknown_refdes():
    with pytest.raises(ValueError, match="operation 0"):
        edit_plan(_plan(), [{"op": "set_part", "refdes": "R9", "value": "1k"}])


def test_set_part_no_fields_rejected():
    with pytest.raises(ValueError, match="no settable field"):
        edit_plan(_plan(), [{"op": "set_part", "refdes": "R1"}])


def test_delete_part_scrubs_nets():
    # delete R2: it's the 2nd pin of GND and SIG
    res = edit_plan(_plan(), [{"op": "delete_part", "refdes": "R2"}])
    plan = res.plan
    assert all(p.refdes != "R2" for p in plan.parts)
    # R2 removed from every net it touched
    for net in plan.nets:
        assert all(pr.refdes != "R2" for pr in net.pins)
    # SIG had R1.2 + R2.1 -> now 1 pin -> flagged floating, kept
    assert any("floating" in n and "SIG" in n for n in res.notes)
    # GND had U1.2 + R2.2 -> now 1 pin -> flagged
    assert any("deleted part R2" in n for n in res.notes)


def test_delete_part_drops_emptied_net():
    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "two parts on one private net",
        "sheets": [{"name": "main"}],
        "parts": [{"refdes": "R1", "lib_ref": "RES"},
                  {"refdes": "R2", "lib_ref": "RES"}],
        "nets": [
            {"name": "VCC", "is_power": True, "pins": [
                {"refdes": "R1", "pin": "1"}, {"refdes": "R2", "pin": "1"}]},
            {"name": "MID", "pins": [
                {"refdes": "R1", "pin": "2"}, {"refdes": "R2", "pin": "2"}]},
        ],
    })
    # deleting BOTH parts should drop MID entirely
    res = edit_plan(plan, [{"op": "delete_part", "refdes": "R1"},
                           {"op": "delete_part", "refdes": "R2"}])
    assert all(n.name != "MID" for n in res.plan.nets)
    assert any("dropped now-empty net 'MID'" in n for n in res.notes)


def test_rename_net():
    res = edit_plan(_plan(), [
        {"op": "rename_net", "old": "SIG", "new": "DATA0"}])
    names = {n.name for n in res.plan.nets}
    assert "DATA0" in names and "SIG" not in names
    data = next(n for n in res.plan.nets if n.name == "DATA0")
    assert {(p.refdes, p.pin) for p in data.pins} == {("R1", "2"), ("R2", "1")}


def test_rename_net_collision_rejected():
    with pytest.raises(ValueError, match="already exists"):
        edit_plan(_plan(), [{"op": "rename_net", "old": "SIG", "new": "VCC"}])


def test_rename_net_invalid_name():
    with pytest.raises(ValueError, match="invalid net name"):
        edit_plan(_plan(), [{"op": "rename_net", "old": "SIG", "new": "9BAD"}])


def test_merge_nets_folds_pins():
    # merge SIG into VCC: VCC keeps power flag, absorbs SIG's pins
    res = edit_plan(_plan(), [
        {"op": "merge_nets", "into": "VCC", "from": "SIG"}])
    plan = res.plan
    assert all(n.name != "SIG" for n in plan.nets)
    vcc = next(n for n in plan.nets if n.name == "VCC")
    assert vcc.is_power is True
    pins = {(p.refdes, p.pin) for p in vcc.pins}
    assert {("U1", "1"), ("R1", "1"), ("R1", "2"), ("R2", "1")} <= pins


def test_merge_nets_dedupes_shared_pin():
    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "overlap", "sheets": [{"name": "main"}],
        "parts": [{"refdes": "R1", "lib_ref": "RES"},
                  {"refdes": "R2", "lib_ref": "RES"}],
        "nets": [
            {"name": "A", "pins": [{"refdes": "R1", "pin": "1"},
                                   {"refdes": "R2", "pin": "1"}]},
            {"name": "B", "pins": [{"refdes": "R1", "pin": "1"},
                                   {"refdes": "R2", "pin": "2"}]},
        ],
    })
    res = edit_plan(plan, [{"op": "merge_nets", "into": "A", "from": "B"}])
    a = next(n for n in res.plan.nets if n.name == "A")
    # R1.1 appears once despite being on both nets
    assert sum(1 for p in a.pins if (p.refdes, p.pin) == ("R1", "1")) == 1


def test_merge_into_self_rejected():
    with pytest.raises(ValueError, match="into itself"):
        edit_plan(_plan(), [{"op": "merge_nets", "into": "VCC", "from": "VCC"}])


def test_set_net_fixes_power_flag():
    # a net authored without the power flag gets fixed in place
    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "unflagged rail", "sheets": [{"name": "main"}],
        "parts": [{"refdes": "U1", "lib_ref": "MCU"},
                  {"refdes": "C1", "lib_ref": "CAP"}],
        "nets": [
            {"name": "VCC", "pins": [{"refdes": "U1", "pin": "1"},
                                     {"refdes": "C1", "pin": "1"}]},
            {"name": "GND", "is_ground": True,
             "pins": [{"refdes": "U1", "pin": "2"}, {"refdes": "C1", "pin": "2"}]},
        ],
    })
    res = edit_plan(plan, [
        {"op": "set_net", "net": "VCC", "is_power": True, "role": "power"}])
    vcc = next(n for n in res.plan.nets if n.name == "VCC")
    assert vcc.is_power is True and vcc.role == "power"


def test_set_net_rejects_contradictory_force_flags():
    # force_label and force_wires are mutually exclusive -> caught here
    with pytest.raises(ValueError, match="operation 0"):
        edit_plan(_plan(), [{"op": "set_net", "net": "SIG",
                             "force_label": True, "force_wires": True}])


def test_set_net_no_field_rejected():
    with pytest.raises(ValueError, match="no settable field"):
        edit_plan(_plan(), [{"op": "set_net", "net": "SIG"}])


def test_connect_pin_adds_to_net():
    res = edit_plan(_plan(), [
        {"op": "connect_pin", "net": "SIG", "refdes": "U1", "pin": "7"}])
    sig = next(n for n in res.plan.nets if n.name == "SIG")
    assert any(p.refdes == "U1" and p.pin == "7" for p in sig.pins)


def test_connect_pin_idempotent():
    # connecting an already-present pin is a no-op, not a duplicate
    res = edit_plan(_plan(), [
        {"op": "connect_pin", "net": "VCC", "refdes": "U1", "pin": "1"}])
    vcc = next(n for n in res.plan.nets if n.name == "VCC")
    assert sum(1 for p in vcc.pins if (p.refdes, p.pin) == ("U1", "1")) == 1


def test_connect_pin_creates_new_net():
    res = edit_plan(_plan(), [
        {"op": "connect_pin", "net": "NEWNET", "refdes": "U1", "pin": "8"}])
    assert any(n.name == "NEWNET" for n in res.plan.nets)
    assert any("created net" in m for m in res.notes)


def test_connect_pin_unknown_refdes():
    with pytest.raises(ValueError, match="operation 0"):
        edit_plan(_plan(), [{"op": "connect_pin", "net": "SIG",
                             "refdes": "Z9", "pin": "1"}])


def test_connect_pin_invalid_net_name():
    with pytest.raises(ValueError, match="invalid net name"):
        edit_plan(_plan(), [{"op": "connect_pin", "net": "9BAD",
                             "refdes": "U1", "pin": "1"}])


def test_disconnect_pin_removes():
    res = edit_plan(_plan(), [
        {"op": "disconnect_pin", "net": "SIG", "refdes": "R2", "pin": "1"}])
    sig = next(n for n in res.plan.nets if n.name == "SIG")
    assert not any(p.refdes == "R2" for p in sig.pins)
    assert any("floating" in m for m in res.notes)   # SIG now 1 pin


def test_disconnect_pin_not_present():
    with pytest.raises(ValueError, match="not on net"):
        edit_plan(_plan(), [{"op": "disconnect_pin", "net": "SIG",
                             "refdes": "U1", "pin": "99"}])


def test_unknown_op_indexed():
    with pytest.raises(ValueError, match="operation 0"):
        edit_plan(_plan(), [{"op": "frobnicate"}])


def test_edits_chain_and_stay_valid():
    # rename then set value then validate clean
    res = edit_plan(_plan(), [
        {"op": "rename_net", "old": "SIG", "new": "DATA"},
        {"op": "set_part", "refdes": "R1", "value": "1k"},
    ])
    final = DesignPlan.model_validate(res.plan.model_dump())
    assert final.cross_check() == []
    assert check_plan_erc(final).passed


def test_original_plan_not_mutated():
    base = _plan()
    edit_plan(base, [{"op": "delete_part", "refdes": "R1"}])
    assert any(p.refdes == "R1" for p in base.parts)


def test_delete_part_scrubs_the_bom():
    # a deleted part must not dangle in the BOM (cross_check won't catch it)
    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "bom scrub", "sheets": [{"name": "main"}],
        "parts": [
            {"refdes": "R1", "lib_ref": "RES", "value": "10k"},
            {"refdes": "R2", "lib_ref": "RES", "value": "10k"},
            {"refdes": "R3", "lib_ref": "RES", "value": "1k"},
        ],
        "nets": [
            {"name": "VCC", "is_power": True, "pins": [
                {"refdes": "R1", "pin": "1"}, {"refdes": "R2", "pin": "1"},
                {"refdes": "R3", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "R1", "pin": "2"}, {"refdes": "R2", "pin": "2"},
                {"refdes": "R3", "pin": "2"}]},
        ],
    })
    plan = plan.model_copy(update={"bom": generate_bom(plan)})
    # R1+R2 share a BOM line (qty 2); R3 its own
    res = edit_plan(plan, [{"op": "delete_part", "refdes": "R2"}])
    bom = {tuple(bl.refdes_list): bl.qty for bl in res.plan.bom}
    assert ("R1",) in bom and bom[("R1",)] == 1   # qty decremented, R2 gone
    assert ("R3",) in bom
    # no BOM line references the deleted part
    assert all("R2" not in bl.refdes_list for bl in res.plan.bom)


def test_delete_last_part_of_a_bom_line_drops_it():
    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "drop bom line", "sheets": [{"name": "main"}],
        "parts": [{"refdes": "R1", "lib_ref": "RES", "value": "10k"},
                  {"refdes": "U1", "lib_ref": "MCU", "mpn": "X", "manufacturer": "Y"}],
        "nets": [{"name": "N", "pins": [
            {"refdes": "R1", "pin": "1"}, {"refdes": "U1", "pin": "1"}]}],
    })
    plan = plan.model_copy(update={"bom": generate_bom(plan)})
    res = edit_plan(plan, [{"op": "delete_part", "refdes": "U1"}])
    assert all("U1" not in bl.refdes_list for bl in res.plan.bom)
    assert any("dropped BOM line" in n for n in res.notes)


def test_set_part_identity_change_warns_bom_stale():
    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "stale", "sheets": [{"name": "main"}],
        "parts": [{"refdes": "R1", "lib_ref": "RES", "value": "10k"},
                  {"refdes": "R2", "lib_ref": "RES", "value": "10k"}],
        "nets": [{"name": "N", "pins": [
            {"refdes": "R1", "pin": "1"}, {"refdes": "R2", "pin": "1"}]}],
    })
    plan = plan.model_copy(update={"bom": generate_bom(plan)})
    res = edit_plan(plan, [{"op": "set_part", "refdes": "R1", "value": "4k7"}])
    assert any("BOM may be stale" in n for n in res.notes)


def test_set_part_without_bom_does_not_warn():
    # no BOM present -> no staleness note (nothing to go stale)
    res = edit_plan(_plan(), [{"op": "set_part", "refdes": "R1", "value": "1k"}])
    assert not any("BOM" in n for n in res.notes)
