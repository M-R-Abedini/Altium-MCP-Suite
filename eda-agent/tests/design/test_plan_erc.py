# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Offline plan ERC-lite tests."""

from __future__ import annotations

from eda_agent.design.plan_erc import check_plan_erc
from eda_agent.design.plan import DesignPlan, Net, Part, PinRef, Sheet


def _net(name, pins, **kw):
    return Net(name=name, pins=[PinRef(refdes=r, pin=p) for r, p in pins], **kw)


def _plan(parts, nets):
    return DesignPlan(
        spec="erc", summary="erc test", sheets=[Sheet(name="main")],
        zones=[], parts=parts, nets=nets,
    )


def _codes(report):
    return sorted(i.code for i in report.issues)


# --- floating nets (all pins on one part) -----------------------------------


def test_net_on_single_part_is_floating_error():
    plan = _plan(
        parts=[Part(refdes="U1", lib_ref="IC"), Part(refdes="R1", lib_ref="RES")],
        nets=[
            _net("GOOD", [("U1", "1"), ("R1", "1")]),
            _net("SELF", [("U1", "8"), ("U1", "9")]),   # both pins on U1
        ],
    )
    rep = check_plan_erc(plan)
    floats = [i for i in rep.issues if i.code == "floating_net"]
    assert len(floats) == 1
    assert floats[0].refs == ("SELF",)
    assert floats[0].severity == "error"
    assert rep.passed is False                     # an error fails the check


def test_net_across_two_parts_is_not_floating():
    plan = _plan(
        parts=[Part(refdes="U1", lib_ref="IC"), Part(refdes="R1", lib_ref="RES")],
        nets=[_net("N", [("U1", "1"), ("R1", "1")])],
    )
    rep = check_plan_erc(plan)
    assert not [i for i in rep.issues if i.code == "floating_net"]


# --- unconnected parts ------------------------------------------------------


def test_unconnected_part_is_warning():
    plan = _plan(
        parts=[Part(refdes="U1", lib_ref="IC"), Part(refdes="R2", lib_ref="RES"),
               Part(refdes="R9", lib_ref="RES")],
        nets=[_net("N", [("U1", "1"), ("R2", "1")])],   # R9 on nothing
    )
    rep = check_plan_erc(plan)
    orphans = [i for i in rep.issues if i.code == "unconnected_part"]
    assert len(orphans) == 1 and orphans[0].refs == ("R9",)
    assert orphans[0].severity == "warning"
    assert rep.passed is True                       # warnings don't fail


# --- missing decoupling -----------------------------------------------------


def _ic_plan(with_decap: bool, to_ground: bool = True):
    """MCU U1 (>=4 pins) on VCC/GND from a connector J1, plus an optional
    decoupling cap C1."""
    parts = [Part(refdes="U1", lib_ref="MCU"), Part(refdes="J1", lib_ref="HDR")]
    nets = [
        _net("VCC", [("J1", "1"), ("U1", "1"), ("U1", "2")], is_power=True),
        _net("GND", [("J1", "2"), ("U1", "5")], is_ground=True),
        _net("SIG", [("U1", "6"), ("J1", "3")]),     # gives U1 a 4th pin
    ]
    if with_decap:
        parts.append(Part(refdes="C1", lib_ref="CAP"))
        bottom = "GND" if to_ground else "SIG"
        nets[0].pins.append(PinRef(refdes="C1", pin="1"))
        for n in nets:
            if n.name == bottom:
                n.pins.append(PinRef(refdes="C1", pin="2"))
    return _plan(parts, nets)


def test_ic_without_decoupling_warns():
    rep = check_plan_erc(_ic_plan(with_decap=False))
    decap = [i for i in rep.issues if i.code == "missing_decoupling"]
    assert len(decap) == 1
    assert decap[0].refs == ("U1", "VCC")
    assert decap[0].severity == "warning"


def test_ic_with_decoupling_to_ground_is_clean():
    rep = check_plan_erc(_ic_plan(with_decap=True, to_ground=True))
    assert not [i for i in rep.issues if i.code == "missing_decoupling"]


def test_cap_not_to_ground_does_not_count_as_decoupling():
    # A cap from VCC to a signal net is not a bypass cap -> still warns.
    rep = check_plan_erc(_ic_plan(with_decap=True, to_ground=False))
    assert [i for i in rep.issues if i.code == "missing_decoupling"]


def test_two_pin_part_is_not_an_ic_for_decoupling():
    # A 2-pin part on a power rail must not demand its own decap.
    plan = _plan(
        parts=[Part(refdes="R1", lib_ref="RES"), Part(refdes="J1", lib_ref="HDR")],
        nets=[_net("VCC", [("R1", "1"), ("J1", "1")], is_power=True),
              _net("GND", [("R1", "2"), ("J1", "2")], is_ground=True)],
    )
    rep = check_plan_erc(plan)
    assert not [i for i in rep.issues if i.code == "missing_decoupling"]


# --- aggregate / housekeeping ----------------------------------------------


def test_clean_plan_reports_nothing():
    rep = check_plan_erc(_ic_plan(with_decap=True, to_ground=True))
    assert rep.issues == ()
    assert rep.passed is True


def test_errors_and_warnings_buckets():
    plan = _plan(
        parts=[Part(refdes="U1", lib_ref="IC"), Part(refdes="R1", lib_ref="RES"),
               Part(refdes="X1", lib_ref="MNT")],
        nets=[_net("GOOD", [("U1", "1"), ("R1", "1")]),
              _net("SELF", [("U1", "8"), ("U1", "9")])],    # floating + X1 orphan
    )
    rep = check_plan_erc(plan)
    assert len(rep.errors) == 1 and rep.errors[0].code == "floating_net"
    assert any(i.code == "unconnected_part" for i in rep.warnings)


def test_deterministic():
    plan = _ic_plan(with_decap=False)
    assert _codes(check_plan_erc(plan)) == _codes(check_plan_erc(plan))


# --- malformed passive values ----------------------------------------------


def test_malformed_passive_value_warns():
    plan = _plan(
        parts=[Part(refdes="R1", lib_ref="RES", value="10kk"),   # typo
               Part(refdes="J1", lib_ref="HDR")],
        nets=[_net("N", [("R1", "1"), ("J1", "1")]),
              _net("M", [("R1", "2"), ("J1", "2")])],
    )
    rep = check_plan_erc(plan)
    bad = [i for i in rep.issues if i.code == "malformed_value"]
    assert len(bad) == 1 and bad[0].refs == ("R1",)
    assert bad[0].severity == "warning"


def test_good_passive_value_is_clean():
    for good in ("10k", "4k7", "100nF", "2R2", "10uH"):
        plan = _plan(
            parts=[Part(refdes="R1", lib_ref="RES", value=good),
                   Part(refdes="J1", lib_ref="HDR")],
            nets=[_net("N", [("R1", "1"), ("J1", "1")]),
                  _net("M", [("R1", "2"), ("J1", "2")])],
        )
        rep = check_plan_erc(plan)
        assert not [i for i in rep.issues if i.code == "malformed_value"], good


def test_ic_nonnumeric_value_not_flagged():
    # An IC's "value" is a part number, not an engineering magnitude -> ignored.
    plan = _plan(
        parts=[Part(refdes="U1", lib_ref="MCU", value="STM32F030"),
               Part(refdes="J1", lib_ref="HDR")],
        nets=[_net("N", [("U1", "1"), ("J1", "1")]),
              _net("M", [("U1", "2"), ("J1", "2")])],
    )
    rep = check_plan_erc(plan)
    assert not [i for i in rep.issues if i.code == "malformed_value"]


# --- matched-value mismatch (composed from value_checks) --------------------


def test_matched_value_mismatch_flows_through_erc():
    # Differential pair with mismatched series resistors -> warning via ERC.
    plan = _plan(
        parts=[Part(refdes="J1", lib_ref="USB"), Part(refdes="U1", lib_ref="PHY"),
               Part(refdes="R1", lib_ref="RES", value="22"),
               Part(refdes="R2", lib_ref="RES", value="47")],
        nets=[_net("DP_C", [("J1", "1"), ("R1", "1")], role="differential"),
              _net("DP_U", [("R1", "2"), ("U1", "10")], role="differential"),
              _net("DM_C", [("J1", "2"), ("R2", "1")], role="differential"),
              _net("DM_U", [("R2", "2"), ("U1", "11")], role="differential"),
              _net("VBUS", [("J1", "8"), ("U1", "9")], is_power=True),
              _net("GND", [("J1", "7"), ("U1", "6")], is_ground=True)],
    )
    rep = check_plan_erc(plan)
    mm = [i for i in rep.issues if i.code == "matched_value_mismatch"]
    assert len(mm) == 1
    assert rep.passed is True                       # it is a warning


# --- shorted pins (one pin on multiple nets) --------------------------------


def test_shorted_pin_on_two_nets_is_error():
    plan = _plan(
        parts=[Part(refdes="U1", lib_ref="IC"), Part(refdes="R1", lib_ref="RES")],
        nets=[_net("A", [("U1", "1"), ("R1", "1")]),
              _net("B", [("U1", "1"), ("R1", "2")])],   # U1.1 on A AND B
    )
    rep = check_plan_erc(plan)
    shorts = [i for i in rep.issues if i.code == "shorted_pin"]
    assert len(shorts) == 1
    assert shorts[0].severity == "error"
    assert "U1" in shorts[0].refs and "A" in shorts[0].refs and "B" in shorts[0].refs
    assert rep.passed is False


def test_clean_pins_no_short():
    plan = _plan(
        parts=[Part(refdes="U1", lib_ref="IC"), Part(refdes="R1", lib_ref="RES")],
        nets=[_net("A", [("U1", "1"), ("R1", "1")]),
              _net("B", [("U1", "2"), ("R1", "2")])],   # distinct pins
    )
    rep = check_plan_erc(plan)
    assert not [i for i in rep.issues if i.code == "shorted_pin"]


def test_shorted_passive_both_pins_one_net_is_error():
    # R1 has both pins on net A -> inert resistor, a shorted_component error.
    plan = _plan(
        parts=[Part(refdes="U1", lib_ref="IC"), Part(refdes="R1", lib_ref="RES")],
        nets=[_net("A", [("U1", "1"), ("R1", "1"), ("R1", "2")]),
              _net("B", [("U1", "2"), ("U1", "3")])],
    )
    rep = check_plan_erc(plan)
    shorts = [i for i in rep.issues if i.code == "shorted_component"]
    assert len(shorts) == 1
    assert shorts[0].severity == "error"
    assert shorts[0].refs == ("R1", "A")
    assert rep.passed is False


def test_passive_across_two_nets_is_not_shorted():
    plan = _plan(
        parts=[Part(refdes="U1", lib_ref="IC"), Part(refdes="C1", lib_ref="CAP")],
        nets=[_net("VCC", [("U1", "1"), ("C1", "1")], is_power=True),
              _net("GND", [("U1", "2"), ("C1", "2")], is_ground=True)],
    )
    rep = check_plan_erc(plan)
    assert not [i for i in rep.issues if i.code == "shorted_component"]


def test_connector_pins_on_one_net_not_shorted_component():
    # A connector with several pins on GND is legitimate; only R/C/L are scoped.
    plan = _plan(
        parts=[Part(refdes="J1", lib_ref="HDR"), Part(refdes="U1", lib_ref="IC")],
        nets=[_net("GND", [("J1", "1"), ("J1", "2"), ("U1", "2")], is_ground=True),
              _net("SIG", [("J1", "3"), ("U1", "1")])],
    )
    rep = check_plan_erc(plan)
    assert not [i for i in rep.issues if i.code == "shorted_component"]


def test_contradictory_power_ground_flags_is_error():
    plan = _plan(
        parts=[Part(refdes="U1", lib_ref="IC"), Part(refdes="R1", lib_ref="RES")],
        nets=[_net("WEIRD", [("U1", "1"), ("R1", "1")],
                   is_power=True, is_ground=True),
              _net("N2", [("U1", "2"), ("R1", "2")])],
    )
    rep = check_plan_erc(plan)
    bad = [i for i in rep.issues if i.code == "contradictory_net_flags"]
    assert len(bad) == 1 and bad[0].refs == ("WEIRD",)
    assert bad[0].severity == "error" and rep.passed is False
