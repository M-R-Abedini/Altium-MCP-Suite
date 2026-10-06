# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Differential-pair detection tests, pure Python, no Altium round-trips."""

from __future__ import annotations

from eda_agent.design.diffpairs import (
    DiffPair,
    detect_diff_pairs,
    diff_pair_match_groups,
)
from eda_agent.design.plan import DesignPlan, Net, Part, PinRef, Sheet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _net(name, pins, **kw):
    return Net(name=name, pins=[PinRef(refdes=r, pin=p) for r, p in pins], **kw)


def _plan(parts, nets):
    return DesignPlan(
        spec="diffpair test",
        summary="diffpair test plan",
        sheets=[Sheet(name="main")],
        zones=[],
        parts=parts,
        nets=nets,
    )


def _endpoints_with_rails(parts, signal_nets):
    """Give the endpoint devices >=3 pins by adding shared VBUS / GND rails,
    so they read as connectors / ICs rather than 2-pin passives."""
    nets = list(signal_nets)
    nets.append(_net("VBUS", [("J1", "8"), ("U1", "9")], is_power=True))
    nets.append(_net("GND", [("J1", "7"), ("U1", "6")], is_ground=True))
    return _plan(parts, nets)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def test_detect_simple_diff_pair():
    """Two differential nets directly between a connector and a controller."""
    plan = _endpoints_with_rails(
        parts=[Part(refdes="J1", lib_ref="USB"), Part(refdes="U1", lib_ref="PHY")],
        signal_nets=[
            _net("DP", [("J1", "1"), ("U1", "10")], role="differential"),
            _net("DM", [("J1", "2"), ("U1", "11")], role="differential"),
        ],
    )
    pairs = detect_diff_pairs(plan)
    assert len(pairs) == 1
    assert pairs[0].endpoints == frozenset({"J1", "U1"})
    assert set(pairs[0].nets) == {"DP", "DM"}
    assert pairs[0].series_parts == ()


def test_detect_pair_with_series_resistors():
    """Series resistors split each leg into two segments; the two segments are
    re-joined through the resistor and the legs pair on {J1, U1}."""
    plan = _endpoints_with_rails(
        parts=[
            Part(refdes="J1", lib_ref="USB"), Part(refdes="U1", lib_ref="PHY"),
            Part(refdes="R1", lib_ref="RES"), Part(refdes="R2", lib_ref="RES"),
        ],
        signal_nets=[
            _net("DP_C", [("J1", "1"), ("R1", "1")], role="differential"),
            _net("DP_U", [("R1", "2"), ("U1", "10")], role="differential"),
            _net("DM_C", [("J1", "2"), ("R2", "1")], role="differential"),
            _net("DM_U", [("R2", "2"), ("U1", "11")], role="differential"),
        ],
    )
    pairs = detect_diff_pairs(plan)
    assert len(pairs) == 1
    dp = pairs[0]
    assert dp.endpoints == frozenset({"J1", "U1"})
    assert set(dp.nets) == {"DP_C", "DP_U", "DM_C", "DM_U"}
    assert dp.series_parts == ("R1", "R2")
    # Each leg is two segments.
    assert sorted(len(leg) for leg in dp.legs) == [2, 2]


def test_non_differential_nets_ignored():
    """The same topology without the differential role yields no pairs --
    structure alone cannot call two parallel single-ended nets a pair."""
    plan = _endpoints_with_rails(
        parts=[Part(refdes="J1", lib_ref="USB"), Part(refdes="U1", lib_ref="PHY")],
        signal_nets=[
            _net("DP", [("J1", "1"), ("U1", "10")]),
            _net("DM", [("J1", "2"), ("U1", "11")]),
        ],
    )
    assert detect_diff_pairs(plan) == []


def test_two_pairs_to_different_endpoints_kept_separate():
    """A USB pair on {J1,U1} and an LVDS pair on {J2,U2} are detected as two
    distinct pairs -- endpoint grouping isolates them."""
    parts = [
        Part(refdes="J1", lib_ref="USB"), Part(refdes="U1", lib_ref="PHY"),
        Part(refdes="J2", lib_ref="HDR"), Part(refdes="U2", lib_ref="LVDS"),
    ]
    nets = [
        _net("AP", [("J1", "1"), ("U1", "10")], role="differential"),
        _net("AM", [("J1", "2"), ("U1", "11")], role="differential"),
        _net("BP", [("J2", "1"), ("U2", "10")], role="differential"),
        _net("BM", [("J2", "2"), ("U2", "11")], role="differential"),
        # Rails to give all four devices >=3 pins.
        _net("V1", [("J1", "8"), ("U1", "9")], is_power=True),
        _net("G1", [("J1", "7"), ("U1", "6")], is_ground=True),
        _net("V2", [("J2", "8"), ("U2", "9")], is_power=True),
        _net("G2", [("J2", "7"), ("U2", "6")], is_ground=True),
    ]
    pairs = detect_diff_pairs(_plan(parts, nets))
    assert len(pairs) == 2
    endpoint_sets = sorted(tuple(sorted(p.endpoints)) for p in pairs)
    assert endpoint_sets == [("J1", "U1"), ("J2", "U2")]


def test_ambiguous_multilane_group_is_skipped():
    """Four differential nets between the SAME two devices (a 2-lane bus with
    no series elements to disambiguate) cannot be paired structurally, so the
    group is skipped rather than guessed."""
    plan = _endpoints_with_rails(
        parts=[Part(refdes="J1", lib_ref="HDR"), Part(refdes="U1", lib_ref="FPGA")],
        signal_nets=[
            _net("L0P", [("J1", "1"), ("U1", "10")], role="differential"),
            _net("L0M", [("J1", "2"), ("U1", "11")], role="differential"),
            _net("L1P", [("J1", "3"), ("U1", "12")], role="differential"),
            _net("L1M", [("J1", "4"), ("U1", "13")], role="differential"),
        ],
    )
    assert detect_diff_pairs(plan) == []


def test_esd_to_ground_is_a_series_leaf_not_a_bridge():
    """A 2-pin ESD part from one leg to ground does not merge the leg with the
    ground domain -- ground is not differential -- but it is recorded as a
    series element on the pair."""
    plan = _endpoints_with_rails(
        parts=[
            Part(refdes="J1", lib_ref="USB"), Part(refdes="U1", lib_ref="PHY"),
            Part(refdes="D1", lib_ref="ESD"), Part(refdes="D2", lib_ref="ESD"),
        ],
        signal_nets=[
            _net("DP", [("J1", "1"), ("U1", "10"), ("D1", "1")],
                 role="differential"),
            _net("DM", [("J1", "2"), ("U1", "11"), ("D2", "1")],
                 role="differential"),
            # ESD diodes return to ground (not differential).
            _net("ESD_GND", [("D1", "2"), ("D2", "2")], is_ground=True),
        ],
    )
    pairs = detect_diff_pairs(plan)
    assert len(pairs) == 1
    assert pairs[0].endpoints == frozenset({"J1", "U1"})
    assert pairs[0].series_parts == ("D1", "D2")


def test_detect_is_deterministic():
    plan = _endpoints_with_rails(
        parts=[Part(refdes="J1", lib_ref="USB"), Part(refdes="U1", lib_ref="PHY")],
        signal_nets=[
            _net("DP", [("J1", "1"), ("U1", "10")], role="differential"),
            _net("DM", [("J1", "2"), ("U1", "11")], role="differential"),
        ],
    )
    assert detect_diff_pairs(plan) == detect_diff_pairs(plan)


# ---------------------------------------------------------------------------
# match_group output (feeds pcb_plan_placement)
# ---------------------------------------------------------------------------


def test_match_groups_groups_series_resistors():
    """The two series resistors of a pair share one match_group tag."""
    plan = _endpoints_with_rails(
        parts=[
            Part(refdes="J1", lib_ref="USB"), Part(refdes="U1", lib_ref="PHY"),
            Part(refdes="R1", lib_ref="RES"), Part(refdes="R2", lib_ref="RES"),
        ],
        signal_nets=[
            _net("DP_C", [("J1", "1"), ("R1", "1")], role="differential"),
            _net("DP_U", [("R1", "2"), ("U1", "10")], role="differential"),
            _net("DM_C", [("J1", "2"), ("R2", "1")], role="differential"),
            _net("DM_U", [("R2", "2"), ("U1", "11")], role="differential"),
        ],
    )
    groups = diff_pair_match_groups(plan)
    assert groups.get("R1") == groups.get("R2")
    assert groups.get("R1") is not None


def test_match_groups_split_by_kind():
    """Series resistors and AC-coupling caps on the same pair form SEPARATE
    matched groups (R with R, C with C)."""
    plan = _endpoints_with_rails(
        parts=[
            Part(refdes="J1", lib_ref="USB"), Part(refdes="U1", lib_ref="PHY"),
            Part(refdes="R1", lib_ref="RES"), Part(refdes="R2", lib_ref="RES"),
            Part(refdes="C1", lib_ref="CAP"), Part(refdes="C2", lib_ref="CAP"),
        ],
        signal_nets=[
            _net("DP_A", [("J1", "1"), ("R1", "1")], role="differential"),
            _net("DP_B", [("R1", "2"), ("C1", "1")], role="differential"),
            _net("DP_C", [("C1", "2"), ("U1", "10")], role="differential"),
            _net("DM_A", [("J1", "2"), ("R2", "1")], role="differential"),
            _net("DM_B", [("R2", "2"), ("C2", "1")], role="differential"),
            _net("DM_C", [("C2", "2"), ("U1", "11")], role="differential"),
        ],
    )
    groups = diff_pair_match_groups(plan)
    assert groups["R1"] == groups["R2"]
    assert groups["C1"] == groups["C2"]
    assert groups["R1"] != groups["C1"]      # different kinds -> different groups


def test_match_groups_lone_series_part_untagged():
    """A series element on only ONE leg has no counterpart, so it is left
    untagged (nothing to match it to)."""
    plan = _endpoints_with_rails(
        parts=[
            Part(refdes="J1", lib_ref="USB"), Part(refdes="U1", lib_ref="PHY"),
            Part(refdes="R1", lib_ref="RES"),
        ],
        signal_nets=[
            _net("DP_C", [("J1", "1"), ("R1", "1")], role="differential"),
            _net("DP_U", [("R1", "2"), ("U1", "10")], role="differential"),
            # DM has no series resistor.
            _net("DM", [("J1", "2"), ("U1", "11")], role="differential"),
        ],
    )
    groups = diff_pair_match_groups(plan)
    assert "R1" not in groups
    assert groups == {}


def test_match_groups_empty_without_pairs():
    plan = _endpoints_with_rails(
        parts=[Part(refdes="J1", lib_ref="USB"), Part(refdes="U1", lib_ref="PHY")],
        signal_nets=[
            _net("DP", [("J1", "1"), ("U1", "10")]),     # no differential role
            _net("DM", [("J1", "2"), ("U1", "11")]),
        ],
    )
    assert diff_pair_match_groups(plan) == {}
