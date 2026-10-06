# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Wrong-way power glyphs, judged by consistency rather than convention.

The Altium audit asserts absolute angles, because Altium's power
symbols carry a style (``ePowerBar``, ``ePowerGndPower``) that says
which way each is meant to face. EasyEDA has no such style: a net flag
is a net flag, and which rotation value points down has never been
measured against a live editor.

So this compares each glyph against the others OF ITS OWN KIND, and the
distinction matters more than it sounds. Asserting an unmeasured
convention would produce confident findings about nothing, which is the
exact failure this backend has been full of: a plausible rule, written
down, believed.

The component types come from ESCH_PrimitiveComponentType in the
official reference, which documents six values. Two of them carry a net
and face a direction, and those are the two this reads.
"""

from __future__ import annotations

import asyncio

import pytest

import eda_agent.tools.easyeda as ez


class _FakeMcp:
    """Only ``.tool()``, which is all registration touches."""

    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


@pytest.fixture
def audit(monkeypatch):
    """The registered audit, with the bridge replaced by a canned reply.

    The reply shape is not invented: it is the field set recorded from a
    live sheet in easyeda_verified (componentType, net, rotation,
    mirror, x, y).
    """
    box = {}

    def fake_call(command, params=None, timeout=None):
        assert command == "sch.components", command
        return box["reply"]

    monkeypatch.setattr(ez, "_call", fake_call)
    mcp = _FakeMcp()
    ez.register_easyeda_tools(mcp)
    fn = mcp.tools["easyeda_audit_power_port_orientation"]

    def run(components):
        box["reply"] = {"ok": True, "components": components}
        return asyncio.run(fn())

    return run


def _glyph(net, rotation, mirror=False, kind="netflag"):
    return {"componentType": kind, "net": net, "rotation": rotation,
            "mirror": mirror, "x": 0, "y": 0}


def test_one_flipped_ground_among_several_is_reported(audit):
    out = audit([_glyph("GND", 270)] * 3 + [_glyph("GND", 90)])
    assert out["checked"] == 4
    assert out["violations"] == 1
    assert out["items"][0]["rotation"] == 90
    assert out["items"][0]["expected"] == "rotation=270,mirror=False"


def test_a_consistent_sheet_reports_nothing(audit):
    out = audit([_glyph("GND", 270)] * 3)
    assert out["violations"] == 0
    assert out["groups"]["ground"]["agreed"] == "rotation=270,mirror=False"


def test_an_even_split_judges_nobody(audit):
    """The property that keeps this honest.

    Two against two says nothing about which pair is wrong. Reporting
    either would be inventing the convention this audit exists to avoid
    inventing.
    """
    out = audit([_glyph("GND", 270), _glyph("GND", 90)])
    assert out["violations"] == 0
    assert out["groups"]["ground"]["agreed"] is None
    assert "no majority" in out["note"]


def test_a_plurality_is_not_a_majority(audit):
    """Three orientations, the commonest holding two of five."""
    out = audit([_glyph("GND", 270), _glyph("GND", 270),
                 _glyph("GND", 90), _glyph("GND", 0), _glyph("GND", 180)])
    assert out["violations"] == 0, (
        "2 of 5 is a plurality, not a majority, and judging the other "
        "three against it asserts a convention no majority supports")


def test_grounds_and_rails_are_judged_separately(audit):
    """They point opposite ways, so one pool would flag whichever is
    less numerous."""
    out = audit([_glyph("GND", 270), _glyph("GND", 270),
                 _glyph("+3V3", 90), _glyph("+3V3", 90),
                 _glyph("+3V3", 270)])
    assert out["violations"] == 1
    assert out["items"][0]["kind"] == "rail"
    assert out["items"][0]["net"] == "+3V3"


def test_mirroring_is_part_of_the_orientation(audit):
    """A mirrored glyph can read wrongly at the same angle."""
    out = audit([_glyph("GND", 270), _glyph("GND", 270),
                 _glyph("GND", 270, mirror=True)])
    assert out["violations"] == 1
    assert out["items"][0]["mirror"] is True


@pytest.mark.parametrize("kind", ["part", "sheet", "block_symbol",
                                  "short_symbol"])
def test_other_component_types_are_not_power_glyphs(audit, kind):
    """The four remaining ESCH_PrimitiveComponentType values."""
    out = audit([_glyph("GND", 270, kind=kind)] * 3)
    assert out["checked"] == 0


def test_netports_count_as_well_as_netflags(audit):
    out = audit([_glyph("GND", 270, kind="netport")] * 3
                + [_glyph("GND", 90, kind="netport")])
    assert out["checked"] == 4
    assert out["violations"] == 1


def test_an_empty_sheet_says_so_rather_than_passing_silently(audit):
    out = audit([])
    assert out["checked"] == 0
    assert "nothing to compare" in out["note"]


def test_a_failed_read_is_passed_through_not_reported_clean(monkeypatch):
    """A refusal must never arrive as zero violations.

    The worst sentence an audit can produce is a clean bill of health
    from a read that did not happen, and this backend has produced it
    before.
    """
    refusal = {"ok": False, "reason": "no schematic is open"}
    monkeypatch.setattr(ez, "_call",
                        lambda command, params=None, timeout=None: refusal)
    mcp = _FakeMcp()
    ez.register_easyeda_tools(mcp)
    out = asyncio.run(mcp.tools["easyeda_audit_power_port_orientation"]())
    assert out["ok"] is False
    assert "violations" not in out


@pytest.mark.parametrize("net,expected_kind", [
    ("GND", "ground"), ("AGND", "ground"), ("DGND", "ground"),
    ("VSS", "ground"), ("EARTH", "ground"), ("PWR_GND", "ground"),
    ("+3V3", "rail"), ("VCC", "rail"), ("VDD", "rail"), ("+5V", "rail"),
])
def test_ground_and_rail_are_told_apart(audit, net, expected_kind):
    out = audit([_glyph(net, 0)] * 3)
    assert expected_kind in out["groups"]
