# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Matched-value consistency check tests."""

from __future__ import annotations

from eda_agent.design.value_checks import check_matched_values
from eda_agent.design.plan import DesignPlan, Net, Part, PinRef, Sheet


def _net(name, pins, **kw):
    return Net(name=name, pins=[PinRef(refdes=r, pin=p) for r, p in pins], **kw)


def _plan(parts, nets):
    return DesignPlan(
        spec="vc", summary="value-check plan", sheets=[Sheet(name="main")],
        zones=[], parts=parts, nets=nets,
    )


def _codes(issues):
    return [i.code for i in issues]


# --- crystal load caps ------------------------------------------------------


def _crystal_plan(c1_val, c2_val):
    parts = [
        Part(refdes="U1", lib_ref="MCU"),
        Part(refdes="Y1", lib_ref="XTAL"),
        Part(refdes="C1", lib_ref="CAP", value=c1_val),
        Part(refdes="C2", lib_ref="CAP", value=c2_val),
    ]
    nets = [
        _net("XIN", [("U1", "3"), ("Y1", "1"), ("C1", "1")]),
        _net("XOUT", [("U1", "4"), ("Y1", "2"), ("C2", "1")]),
        _net("GND", [("C1", "2"), ("C2", "2"), ("U1", "2")], is_ground=True),
    ]
    return _plan(parts, nets)


def test_crystal_unequal_load_caps_warn():
    issues = check_matched_values(_crystal_plan("22pF", "18pF"))
    assert _codes(issues) == ["matched_value_mismatch"]
    assert set(issues[0].refs) == {"C1", "C2"}


def test_crystal_equal_load_caps_clean_across_notation():
    # 22pF and 22p are the same value in different notation -> no warning.
    assert check_matched_values(_crystal_plan("22pF", "22p")) == []


def test_crystal_unparseable_value_skipped():
    # A typo'd value is not a *mismatch* here (the ERC malformed check owns it).
    assert check_matched_values(_crystal_plan("22pF", "22pp")) == []


# --- differential-pair matched series ---------------------------------------


def _diff_plan(r1_val, r2_val):
    parts = [
        Part(refdes="J1", lib_ref="USB"), Part(refdes="U1", lib_ref="PHY"),
        Part(refdes="R1", lib_ref="RES", value=r1_val),
        Part(refdes="R2", lib_ref="RES", value=r2_val),
    ]
    nets = [
        _net("DP_C", [("J1", "1"), ("R1", "1")], role="differential"),
        _net("DP_U", [("R1", "2"), ("U1", "10")], role="differential"),
        _net("DM_C", [("J1", "2"), ("R2", "1")], role="differential"),
        _net("DM_U", [("R2", "2"), ("U1", "11")], role="differential"),
        _net("VBUS", [("J1", "8"), ("U1", "9")], is_power=True),
        _net("GND", [("J1", "7"), ("U1", "6")], is_ground=True),
    ]
    return _plan(parts, nets)


def test_diffpair_unequal_series_resistors_warn():
    issues = check_matched_values(_diff_plan("22", "33"))
    assert _codes(issues) == ["matched_value_mismatch"]
    assert set(issues[0].refs) == {"R1", "R2"}


def test_diffpair_equal_series_resistors_clean():
    assert check_matched_values(_diff_plan("22", "22R")) == []


# --- nothing matched --------------------------------------------------------


def test_plain_design_has_no_matched_value_issues():
    plan = _plan(
        parts=[Part(refdes="U1", lib_ref="IC"),
               Part(refdes="R1", lib_ref="RES", value="10k")],
        nets=[_net("N", [("U1", "1"), ("R1", "1")]),
              _net("GND", [("U1", "2"), ("R1", "2")], is_ground=True)],
    )
    assert check_matched_values(plan) == []
