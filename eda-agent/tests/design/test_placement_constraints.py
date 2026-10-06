# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the unified role-driven placement-constraint aggregator."""

from __future__ import annotations

from eda_agent.design.placement_constraints import (
    infer_placement_constraints,
    merge_groups,
)
from eda_agent.design.plan import DesignPlan, Net, Part, PinRef, Sheet


def _net(name, pins, **kw):
    return Net(name=name, pins=[PinRef(refdes=r, pin=p) for r, p in pins], **kw)


def _plan(parts, nets):
    return DesignPlan(
        spec="c", summary="constraints plan", sheets=[Sheet(name="main")],
        zones=[], parts=parts, nets=nets,
    )


def _mixed_plan():
    """A USB diff pair (series R1/R2) plus an analog sensor and a digital MCU,
    so both a match_group (the pair) and keepout_groups (domains) arise."""
    parts = [
        Part(refdes="J1", lib_ref="USB"), Part(refdes="U1", lib_ref="PHY"),
        Part(refdes="R1", lib_ref="RES"), Part(refdes="R2", lib_ref="RES"),
        Part(refdes="A1", lib_ref="AMP"), Part(refdes="U3", lib_ref="MCU"),
    ]
    nets = [
        # Differential pair with series resistors (role differential).
        _net("DP_C", [("J1", "1"), ("R1", "1")], role="differential"),
        _net("DP_U", [("R1", "2"), ("U1", "10")], role="differential"),
        _net("DM_C", [("J1", "2"), ("R2", "1")], role="differential"),
        _net("DM_U", [("R2", "2"), ("U1", "11")], role="differential"),
        # Analog-sensitive net on A1; digital net on U3.
        _net("ASENSE", [("A1", "1"), ("U1", "5")], role="analog_sensitive"),
        _net("CTRL", [("U3", "3"), ("U1", "6")], role="control"),
        # Rails to give the ICs >=3 pins for the diff-pair endpoint test.
        _net("VBUS", [("J1", "8"), ("U1", "9")], is_power=True),
        _net("GND", [("J1", "7"), ("U1", "6b"), ("A1", "2"), ("U3", "2")],
             is_ground=True),
    ]
    return _plan(parts, nets)


def test_aggregator_returns_both_constraint_sets():
    c = infer_placement_constraints(_mixed_plan())
    # Differential series resistors share a match group.
    assert c.match_groups.get("R1") == c.match_groups.get("R2")
    assert c.match_groups.get("R1") is not None
    # Analog vs digital keepout domains.
    assert c.keepout_groups.get("A1") == "analog"
    assert c.keepout_groups.get("U3") == "digital"
    assert not c.is_empty()


def test_aggregator_empty_on_plain_design():
    plan = _plan(
        parts=[Part(refdes="U1", lib_ref="IC"), Part(refdes="R1", lib_ref="RES")],
        nets=[_net("N", [("U1", "1"), ("R1", "1")]),
              _net("GND", [("U1", "2"), ("R1", "2")], is_ground=True)],
    )
    c = infer_placement_constraints(plan)
    assert c.is_empty()
    assert c.match_groups == {} and c.keepout_groups == {}


def test_match_and_keepout_independent_on_same_part():
    """A differential (=digital) series resistor can be BOTH matched to its
    pair-mate and kept in the digital domain -- the two dicts don't collide."""
    # R1/R2 are on differential nets, so they are both a match pair AND digital.
    c = infer_placement_constraints(_mixed_plan())
    assert "R1" in c.match_groups
    # R1 sits on a differential (digital-role) net, so it is also digital-domain.
    assert c.keepout_groups.get("R1") == "digital"
    # Same refdes, two independent tag kinds -- no conflict.
    assert c.match_groups["R1"] != c.keepout_groups["R1"]


def test_merge_explicit_wins():
    inferred = {"R1": "analog", "R2": "analog"}
    explicit = {"R1": "digital"}                 # planner override
    merged = merge_groups(inferred, explicit)
    assert merged["R1"] == "digital"             # explicit wins
    assert merged["R2"] == "analog"              # inferred retained


def test_merge_handles_none_explicit():
    inferred = {"R1": "analog"}
    assert merge_groups(inferred, None) == {"R1": "analog"}
