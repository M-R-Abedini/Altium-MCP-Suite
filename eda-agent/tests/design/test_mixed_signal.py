# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Mixed-signal domain inference tests, pure Python."""

from __future__ import annotations

from eda_agent.design.mixed_signal import (
    classify_domains,
    infer_keepout_groups,
)
from eda_agent.design.plan import DesignPlan, Net, Part, PinRef, Sheet


def _net(name, pins, **kw):
    return Net(name=name, pins=[PinRef(refdes=r, pin=p) for r, p in pins], **kw)


def _plan(parts, nets):
    return DesignPlan(
        spec="mixed", summary="mixed-signal plan",
        sheets=[Sheet(name="main")], zones=[], parts=parts, nets=nets,
    )


def _adc_plan():
    """Sensor amp U2 (analog) -> ADC U1 (boundary) -> MCU U3 (digital)."""
    parts = [
        Part(refdes="U1", lib_ref="ADC"),
        Part(refdes="U2", lib_ref="OPAMP"),
        Part(refdes="U3", lib_ref="MCU"),
    ]
    nets = [
        # Sensitive analog input from the amp to the ADC.
        _net("ASENSE", [("U2", "6"), ("U1", "1")], role="analog_sensitive"),
        # Digital SPI from the ADC to the MCU.
        _net("SCLK", [("U1", "8"), ("U3", "10")], role="clock"),
        _net("SDATA", [("U1", "9"), ("U3", "11")], role="control"),
        # Shared rails (no domain).
        _net("VCC", [("U1", "16"), ("U2", "7"), ("U3", "1")], is_power=True),
        _net("GND", [("U1", "15"), ("U2", "4"), ("U3", "2")], is_ground=True),
    ]
    return _plan(parts, nets)


def test_classify_analog_digital_boundary():
    d = classify_domains(_adc_plan())
    assert d.analog == ("U2",)          # only sensitive net
    assert d.digital == ("U3",)         # only clock/control nets
    assert d.boundary == ("U1",)        # ADC touches both -> bridge


def test_infer_keepout_tags_each_domain():
    groups = infer_keepout_groups(_adc_plan())
    assert groups["U2"] == "analog"
    assert groups["U3"] == "digital"
    # The boundary ADC is left untagged.
    assert "U1" not in groups


def test_boundary_part_untagged():
    # A part touching one analog and one digital net is the boundary.
    plan = _plan(
        parts=[Part(refdes="U1", lib_ref="ADC")],
        nets=[
            _net("A", [("U1", "1"), ("U1", "2")], role="analog_sensitive"),
            _net("D", [("U1", "3"), ("U1", "4")], role="clock"),
        ],
    )
    d = classify_domains(plan)
    assert d.boundary == ("U1",)
    assert d.analog == () and d.digital == ()


def test_single_domain_yields_no_tags():
    # Only analog nets -> nothing to separate -> empty (separation would be 0).
    plan = _plan(
        parts=[Part(refdes="U1", lib_ref="AMP"), Part(refdes="U2", lib_ref="AMP")],
        nets=[
            _net("A1", [("U1", "1"), ("U2", "2")], role="analog_sensitive"),
            _net("A2", [("U1", "3"), ("U2", "4")], role="feedback"),
        ],
    )
    assert infer_keepout_groups(plan) == {}


def test_no_roles_yields_no_tags():
    plan = _plan(
        parts=[Part(refdes="U1", lib_ref="IC"), Part(refdes="U2", lib_ref="IC")],
        nets=[_net("N", [("U1", "1"), ("U2", "1")])],
    )
    assert infer_keepout_groups(plan) == {}
    assert classify_domains(plan) == \
        classify_domains(plan)            # deterministic


def test_feedback_is_analog_switch_is_digital():
    # A regulator feedback node is sensitive analog; an SMPS switch node is a
    # digital/switching aggressor.
    plan = _plan(
        parts=[Part(refdes="R1", lib_ref="RES"), Part(refdes="L1", lib_ref="IND")],
        nets=[
            _net("FB", [("R1", "1"), ("R1", "2")], role="feedback"),
            _net("SW", [("L1", "1"), ("L1", "2")], role="switch"),
        ],
    )
    groups = infer_keepout_groups(plan)
    assert groups["R1"] == "analog"
    assert groups["L1"] == "digital"


def test_deterministic():
    p = _adc_plan()
    assert infer_keepout_groups(p) == infer_keepout_groups(p)
