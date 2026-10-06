# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Recognised-sub-circuit parameter report tests."""

from __future__ import annotations

import math

import pytest

from eda_agent.design.motif_descriptions import describe_motifs
from eda_agent.design.plan import DesignPlan, Net, Part, PinRef, Sheet


def _net(name, pins, **kw):
    return Net(name=name, pins=[PinRef(refdes=r, pin=p) for r, p in pins], **kw)


def _plan(parts, nets):
    return DesignPlan(
        spec="md", summary="motif desc plan", sheets=[Sheet(name="main")],
        zones=[], parts=parts, nets=nets,
    )


def _by_name(descs):
    return {d.motif_name: d for d in descs}


def test_voltage_divider_ratio():
    # Rtop=R1 on the rail (10k), Rbot=R2 to ground (20k) -> ratio 20/30.
    plan = _plan(
        parts=[Part(refdes="J1", lib_ref="HDR"),
               Part(refdes="R1", lib_ref="RES", value="10k"),
               Part(refdes="R2", lib_ref="RES", value="20k")],
        nets=[_net("VRAIL", [("J1", "1"), ("R1", "1")], is_power=True),
              _net("MID", [("R1", "2"), ("R2", "1")]),
              _net("GND", [("J1", "2"), ("R2", "2")], is_ground=True)],
    )
    d = _by_name(describe_motifs(plan))["voltage_divider"]
    assert d.params["ratio"] == pytest.approx(20.0 / 30.0)
    assert "0.667" in d.summary
    assert set(d.parts) == {"R1", "R2"}


def test_fb_divider_gain():
    plan = _plan(
        parts=[Part(refdes="U1", lib_ref="REG"),
               Part(refdes="R1", lib_ref="RES", value="31.6k"),
               Part(refdes="R2", lib_ref="RES", value="10k")],
        nets=[_net("VOUT", [("U1", "3"), ("R1", "1")], is_power=True),
              _net("FB", [("R1", "2"), ("R2", "1"), ("U1", "5")]),
              _net("GND", [("R2", "2"), ("U1", "2")], is_ground=True)],
    )
    d = _by_name(describe_motifs(plan))["fb_divider"]
    assert d.params["gain"] == pytest.approx(1.0 + 31600 / 10000)


def test_rc_lowpass_cutoff():
    plan = _plan(
        parts=[Part(refdes="J1", lib_ref="HDR"),
               Part(refdes="R1", lib_ref="RES", value="1.6k"),
               Part(refdes="C1", lib_ref="CAP", value="100nF")],
        nets=[_net("IN", [("J1", "1"), ("R1", "1")]),
              _net("OUT", [("R1", "2"), ("C1", "1")]),
              _net("GND", [("J1", "2"), ("C1", "2")], is_ground=True)],
    )
    d = _by_name(describe_motifs(plan))["rc_lowpass"]
    assert d.params["f_cutoff_hz"] == pytest.approx(
        1.0 / (2 * math.pi * 1600 * 100e-9))


def test_crystal_load_value():
    plan = _plan(
        parts=[Part(refdes="U1", lib_ref="MCU"), Part(refdes="Y1", lib_ref="XTAL"),
               Part(refdes="C1", lib_ref="CAP", value="22pF"),
               Part(refdes="C2", lib_ref="CAP", value="22pF")],
        nets=[_net("XIN", [("U1", "3"), ("Y1", "1"), ("C1", "1")]),
              _net("XOUT", [("U1", "4"), ("Y1", "2"), ("C2", "1")]),
              _net("GND", [("C1", "2"), ("C2", "2"), ("U1", "2")],
                   is_ground=True)],
    )
    d = _by_name(describe_motifs(plan))["crystal_load"]
    # CL = C/2 + Cstray = 11 pF + 5 pF = 16 pF.
    assert d.params["c_load_f"] == pytest.approx(16e-12)


def test_unparseable_values_skipped():
    plan = _plan(
        parts=[Part(refdes="J1", lib_ref="HDR"),
               Part(refdes="R1", lib_ref="RES", value="bogus"),
               Part(refdes="R2", lib_ref="RES", value="20k")],
        nets=[_net("VRAIL", [("J1", "1"), ("R1", "1")], is_power=True),
              _net("MID", [("R1", "2"), ("R2", "1")]),
              _net("GND", [("J1", "2"), ("R2", "2")], is_ground=True)],
    )
    # The divider is recognised but cannot be valued -> not described.
    assert describe_motifs(plan) == []


def test_no_parametric_motif_is_empty():
    plan = _plan(
        parts=[Part(refdes="U1", lib_ref="IC"),
               Part(refdes="R1", lib_ref="RES", value="10k")],
        nets=[_net("N", [("U1", "1"), ("R1", "1")]),
              _net("GND", [("U1", "2"), ("R1", "2")], is_ground=True)],
    )
    assert describe_motifs(plan) == []
