# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for length-matching / skew-budget calculations.

Cross-checked against the ns/in == ps/mil identity and FR4 numbers: stripline
Er=4.2 gives ~0.174 ps/mil, so 5 ps of skew is ~29 mils of length and a
1000-mil mismatch is ~174 ps.
"""

import pytest

from eda_agent.design.signal_integrity import effective_dielectric_constant
from eda_agent.design.length_matching import (
    length_for_skew,
    skew_for_length,
    match_tolerance_for_rise_time,
    match_group_report,
    intra_pair_skew,
    assess_length_match,
)

_STRIP_FR4 = effective_dielectric_constant(4.2, "stripline")  # == 4.2


# --------------------------------------------------------------------------- #
# Skew <-> length conversion
# --------------------------------------------------------------------------- #
def test_five_ps_is_about_29_mils_on_fr4_stripline():
    assert length_for_skew(5.0, _STRIP_FR4) == pytest.approx(28.8, abs=0.5)


def test_thousand_mil_mismatch_is_about_174_ps():
    assert skew_for_length(1000.0, _STRIP_FR4) == pytest.approx(173.6, abs=1.0)


def test_skew_length_round_trip():
    for skew in (1.0, 5.0, 25.0, 100.0):
        L = length_for_skew(skew, _STRIP_FR4)
        assert skew_for_length(L, _STRIP_FR4) == pytest.approx(skew, rel=1e-9)


def test_faster_dielectric_gives_more_mils_per_ps():
    # A lower Er_eff (faster wave) means a given skew spans MORE length.
    fast = length_for_skew(10.0, 2.5)
    slow = length_for_skew(10.0, 4.2)
    assert fast > slow


def test_conversions_reject_negative():
    with pytest.raises(ValueError):
        length_for_skew(-1, _STRIP_FR4)
    with pytest.raises(ValueError):
        skew_for_length(-1, _STRIP_FR4)


# --------------------------------------------------------------------------- #
# Rise-time tolerance
# --------------------------------------------------------------------------- #
def test_tolerance_from_rise_time_matches_skew_budget():
    # 10 % of a 0.5 ns edge = 50 ps budget -> same as length_for_skew(50).
    tol = match_tolerance_for_rise_time(0.5, _STRIP_FR4, fraction=0.1)
    assert tol == pytest.approx(length_for_skew(50.0, _STRIP_FR4))


def test_tighter_fraction_smaller_window():
    strict = match_tolerance_for_rise_time(1.0, _STRIP_FR4, fraction=0.05)
    loose = match_tolerance_for_rise_time(1.0, _STRIP_FR4, fraction=0.2)
    assert strict < loose
    assert loose == pytest.approx(4 * strict, rel=1e-9)


def test_tolerance_rejects_bad_input():
    with pytest.raises(ValueError):
        match_tolerance_for_rise_time(0, _STRIP_FR4)
    with pytest.raises(ValueError):
        match_tolerance_for_rise_time(1.0, _STRIP_FR4, fraction=2.0)


# --------------------------------------------------------------------------- #
# Group report
# --------------------------------------------------------------------------- #
def test_group_targets_longest_and_reports_compensation():
    rep = match_group_report(
        {"D0": 1000, "D1": 1200, "D2": 1180}, _STRIP_FR4)
    assert rep.target_length_mils == 1200
    comp = {m.name: m.compensation_mils for m in rep.members}
    assert comp == {"D0": 200, "D1": 0, "D2": 20}
    # Worst skew is the longest-vs-shortest pair (1200 - 1000 = 200 mils).
    assert rep.worst_skew_ps == pytest.approx(skew_for_length(200, _STRIP_FR4))


def test_group_flags_matched_against_skew_budget():
    # 50 ps budget ~= 288 mils tolerance: the 200-mil mismatch passes.
    rep = match_group_report(
        {"D0": 1000, "D1": 1200}, _STRIP_FR4, skew_budget_ps=50.0)
    assert rep.tolerance_mils == pytest.approx(length_for_skew(50.0, _STRIP_FR4))
    assert rep.all_matched is True
    assert all(m.within_tolerance for m in rep.members)


def test_group_flags_unmatched_when_over_budget():
    # 5 ps budget ~= 29 mils: a 200-mil mismatch fails.
    rep = match_group_report(
        {"D0": 1000, "D1": 1200}, _STRIP_FR4, skew_budget_ps=5.0)
    assert rep.all_matched is False
    bad = [m for m in rep.members if not m.within_tolerance]
    assert [m.name for m in bad] == ["D0"]


def test_diff_pair_intra_skew():
    # A P/N pair is just a 2-net group with a 5-mil mismatch (~0.87 ps).
    rep = match_group_report(
        {"USB_P": 1500, "USB_N": 1495}, _STRIP_FR4, skew_budget_ps=1.0)
    assert rep.worst_skew_ps == pytest.approx(skew_for_length(5, _STRIP_FR4))
    # 1 ps budget -> ~5.8 mil window, so the 5-mil mismatch just passes...
    assert rep.all_matched is True
    # ...but a tight 0.5 ps budget (~2.9 mil window) fails it.
    tight = match_group_report(
        {"USB_P": 1500, "USB_N": 1495}, _STRIP_FR4, skew_budget_ps=0.5)
    assert tight.all_matched is False


def test_explicit_tolerance_overrides_budget():
    rep = match_group_report(
        {"A": 1000, "B": 1100}, _STRIP_FR4, tolerance_mils=50)
    assert rep.tolerance_mils == 50
    # 100-mil mismatch > 50 -> unmatched.
    assert rep.all_matched is False


def test_no_budget_means_no_match_verdict():
    rep = match_group_report({"A": 1000, "B": 1100}, _STRIP_FR4)
    assert rep.tolerance_mils is None
    assert rep.all_matched is False  # cannot certify without a budget
    # ...but every member is reported as within_tolerance (nothing to violate).
    assert all(m.within_tolerance for m in rep.members)


def test_single_member_trivially_matched():
    rep = match_group_report({"solo": 800}, _STRIP_FR4, skew_budget_ps=5.0)
    assert rep.target_length_mils == 800
    assert rep.worst_skew_ps == 0
    assert rep.all_matched is True


def test_empty_group_rejected():
    with pytest.raises(ValueError):
        match_group_report({}, _STRIP_FR4)


def test_negative_length_rejected():
    with pytest.raises(ValueError):
        match_group_report({"A": -10}, _STRIP_FR4)


# --------------------------------------------------------------------------- #
# Front end
# --------------------------------------------------------------------------- #
def test_assess_resolves_er_eff_and_tolerance():
    out = assess_length_match(
        dielectric_constant=4.2, geometry="stripline", skew_budget_ps=10.0)
    assert out["er_eff"] == 4.2
    assert out["tolerance_mils"] == pytest.approx(length_for_skew(10.0, 4.2))
    assert "report" not in out  # no lengths supplied


def test_assess_budget_from_rise_time():
    out = assess_length_match(
        geometry="stripline", rise_time_ns=0.5, match_fraction=0.1)
    assert out["skew_budget_ps"] == pytest.approx(50.0)


def test_assess_with_lengths_includes_report():
    out = assess_length_match(
        geometry="stripline", skew_budget_ps=50.0,
        lengths={"D0": 1000, "D1": 1200})
    assert "report" in out
    assert out["report"].target_length_mils == 1200


def test_assess_microstrip_faster_than_stripline():
    # Microstrip Er_eff < bulk Er -> faster -> more mils per ps -> wider window.
    ms = assess_length_match(geometry="microstrip", skew_budget_ps=10.0)
    sl = assess_length_match(geometry="stripline", skew_budget_ps=10.0)
    assert ms["tolerance_mils"] > sl["tolerance_mils"]


# --------------------------------------------------------------------------- #
# Intra-pair (P-vs-N) skew
# --------------------------------------------------------------------------- #
def test_intra_pair_simple_mismatch():
    # Single-segment legs: P is 50 mils longer than N.
    r = intra_pair_skew((("DP",), ("DM",)), {"DP": 1050.0, "DM": 1000.0},
                        _STRIP_FR4)
    assert r.leg_lengths == (1050.0, 1000.0)
    assert r.mismatch_mils == pytest.approx(50.0)
    assert r.longer_leg == 0
    assert r.compensation_mils == pytest.approx(50.0)  # lengthen the short (N) leg
    assert r.skew_ps == pytest.approx(skew_for_length(50.0, _STRIP_FR4))


def test_intra_pair_sums_split_segments():
    # A series resistor splits each leg; the leg length is the segment sum.
    legs = (("DP_C", "DP_U"), ("DM_C", "DM_U"))
    lengths = {"DP_C": 300.0, "DP_U": 700.0,    # P total 1000
               "DM_C": 320.0, "DM_U": 660.0}    # N total 980
    r = intra_pair_skew(legs, lengths, _STRIP_FR4)
    assert r.leg_lengths == (1000.0, 980.0)
    assert r.mismatch_mils == pytest.approx(20.0)
    assert r.longer_leg == 0


def test_intra_pair_within_and_over_budget():
    legs = (("DP",), ("DM",))
    # 20-mil mismatch; 5 ps budget ~= 29 mils on FR4 stripline -> within.
    ok = intra_pair_skew(legs, {"DP": 1020.0, "DM": 1000.0}, _STRIP_FR4,
                         skew_budget_ps=5.0)
    assert ok.within_tolerance
    # 40-mil mismatch exceeds the same ~29-mil window -> over.
    bad = intra_pair_skew(legs, {"DP": 1040.0, "DM": 1000.0}, _STRIP_FR4,
                          skew_budget_ps=5.0)
    assert not bad.within_tolerance
    assert bad.tolerance_mils == pytest.approx(length_for_skew(5.0, _STRIP_FR4))


def test_intra_pair_no_budget_is_always_within():
    r = intra_pair_skew((("DP",), ("DM",)), {"DP": 2000.0, "DM": 1000.0},
                        _STRIP_FR4)
    assert r.tolerance_mils is None
    assert r.within_tolerance


def test_intra_pair_zero_mismatch():
    r = intra_pair_skew((("DP",), ("DM",)), {"DP": 1000.0, "DM": 1000.0},
                        _STRIP_FR4, skew_budget_ps=1.0)
    assert r.mismatch_mils == 0.0
    assert r.skew_ps == 0.0
    assert r.within_tolerance


def test_intra_pair_explicit_tolerance_wins_over_budget():
    r = intra_pair_skew((("DP",), ("DM",)), {"DP": 1010.0, "DM": 1000.0},
                        _STRIP_FR4, tolerance_mils=5.0, skew_budget_ps=999.0)
    assert r.tolerance_mils == pytest.approx(5.0)
    assert not r.within_tolerance  # 10 > 5


def test_intra_pair_rejects_wrong_leg_count():
    with pytest.raises(ValueError):
        intra_pair_skew((("DP",),), {"DP": 1.0}, _STRIP_FR4)


def test_intra_pair_rejects_missing_length():
    with pytest.raises(ValueError):
        intra_pair_skew((("DP",), ("DM",)), {"DP": 1000.0}, _STRIP_FR4)


def test_intra_pair_rejects_empty_leg():
    with pytest.raises(ValueError):
        intra_pair_skew((("DP",), ()), {"DP": 1000.0}, _STRIP_FR4)
