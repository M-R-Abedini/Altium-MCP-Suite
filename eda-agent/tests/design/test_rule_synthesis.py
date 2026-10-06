# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Rule + stackup synthesis tests.

All capability numbers below are SYNTHETIC test-fixture values, not any
real fab's capabilities.
"""

from __future__ import annotations

import math

from eda_agent.design.impedance_sizing import trace_width_for_impedance
from eda_agent.design.net_classes import classify_nets
from eda_agent.design.plan import DesignPlan, Net, Part, PinRef, Sheet
from eda_agent.design.rule_synthesis import synthesize_rules
from eda_agent.design.trace_sizing import trace_width_for_current


def _profile_dict(**overrides) -> dict:
    d = {
        "name": "TestFab synthetic profile",
        "source": "synthetic test fixture, not a real fab",
        "copper_layer_counts": [2],
        "min_track_mils": 7.0,
        "min_gap_mils": 8.0,
        "min_drill_mils": 9.0,
        "min_annular_ring_mils": 5.0,
        "min_hole_to_hole_mils": 11.0,
        "min_mask_sliver_mils": 3.0,
        "min_silk_width_mils": 4.0,
        "stackups": [
            {
                "name": "test-2L",
                "layers": [
                    {"name": "Top", "kind": "copper",
                     "thickness_mils": 1.4, "copper_oz": 1.0},
                    {"name": "Core", "kind": "core",
                     "thickness_mils": 6.0, "er": 4.0},
                    {"name": "Bottom", "kind": "copper",
                     "thickness_mils": 1.4, "copper_oz": 1.0},
                ],
            },
        ],
    }
    d.update(overrides)
    return d


_NCM = {
    "VBUS": "power",
    "GND": "ground",
    "USB_DP": "differential",
    "USB_DM": "differential",
    "SIG1": "signal",
}


def _rule(res, name):
    matches = [r for r in res["rules"] if r["name"] == name]
    assert len(matches) == 1, f"{name} not found once in {res['rules']}"
    return matches[0]


# --- fab-floor rules -----------------------------------------------------------

def test_baseline_rules_from_profile():
    res = synthesize_rules(_profile_dict(), _NCM)
    assert res["ok"] is True
    clr = _rule(res, "Clearance_Fab_Min")
    assert clr["rule_type"] == "clearance"
    assert clr["value"] == 8
    assert clr["scope"] == "All"
    assert clr["net_scope"] == "different_nets"
    wid = _rule(res, "Width_Fab_Min")
    assert wid["rule_type"] == "width"
    assert wid["value"] == 7 and wid["favored_value"] == 7
    via = _rule(res, "Via_Fab_Min_Drill")
    assert via["rule_type"] == "via_size"
    assert via["value"] == 9


def test_via_pad_and_floor_notes():
    res = synthesize_rules(_profile_dict(), _NCM)
    text = " ".join(res["notes"])
    # min drill 9 + 2 * annular 5 = 19
    assert "19 mil" in text
    assert "hole-to-hole 11.0 mil" in text
    assert "synthetic test fixture" in text   # source cited


def test_fractional_minimums_round_up():
    res = synthesize_rules(
        _profile_dict(min_track_mils=6.2, min_gap_mils=7.1), _NCM)
    assert _rule(res, "Width_Fab_Min")["value"] == 7
    assert _rule(res, "Clearance_Fab_Min")["value"] == 8


# --- per-class width from current ---------------------------------------------

def test_class_width_matches_calculator():
    res = synthesize_rules(
        _profile_dict(), _NCM, {"class_current_a": {"power": 3.0}})
    assert res["ok"] is True
    direct = trace_width_for_current(3.0, copper_oz=1.0)
    expect = math.ceil(direct.recommended_width_mils)
    rule = _rule(res, "Width_power")
    assert rule["rule_type"] == "width"
    assert rule["value"] == expect
    assert rule["favored_value"] == expect
    assert rule["scope"] == "InNetClass('power')"
    assert expect > 7   # fixture current chosen above the floor


def test_class_width_clamped_to_fab_minimum():
    res = synthesize_rules(
        _profile_dict(), _NCM, {"class_current_a": {"power": 0.1}})
    direct = trace_width_for_current(0.1, copper_oz=1.0)
    assert direct.recommended_width_mils < 7.0   # fixture precondition
    assert _rule(res, "Width_power")["value"] == 7
    assert any("clamped to 7 mil" in n for n in res["notes"])


def test_class_current_options_passthrough():
    opts = {"class_current_a": {"power": 3.0}, "delta_t_c": 20.0,
            "track_margin": 0.0, "layer": "internal"}
    res = synthesize_rules(_profile_dict(), _NCM, opts)
    direct = trace_width_for_current(
        3.0, copper_oz=1.0, delta_t_c=20.0, margin=0.0, layer="internal")
    assert _rule(res, "Width_power")["value"] == \
        math.ceil(direct.recommended_width_mils)


def test_class_current_for_absent_class_is_noted_not_ruled():
    res = synthesize_rules(
        _profile_dict(), _NCM, {"class_current_a": {"high_current": 5.0}})
    assert res["ok"] is True
    assert not [r for r in res["rules"] if r["name"] == "Width_high_current"]
    assert any("high_current" in n and "skipped" in n for n in res["notes"])


def test_negative_current_rejected():
    res = synthesize_rules(
        _profile_dict(), _NCM, {"class_current_a": {"power": -1.0}})
    assert res["ok"] is False
    assert "power" in res["reason"]


# --- differential rules ---------------------------------------------------------

def test_diff_rules_match_impedance_sizing():
    res = synthesize_rules(
        _profile_dict(), _NCM, {"diff_pair_target_ohms": 90.0})
    assert res["ok"] is True
    # default spacing is the profile min gap (8); stackup h=6, er=4.0, 1 oz
    direct = trace_width_for_impedance(
        90.0, "microstrip_diff", 6.0, dielectric_constant=4.0,
        copper_oz=1.0, spacing_mils=8.0)
    assert direct.feasible
    wid = _rule(res, "Width_differential")
    assert wid["value"] == math.ceil(direct.width_mils)
    assert wid["scope"] == "InNetClass('differential')"
    gap = _rule(res, "DiffPair_Gap")
    assert gap["rule_type"] == "differential_pairs"
    assert gap["value"] == gap["favored_value"] == gap["max_value"] == 8
    assert gap["scope"] == "IsDifferentialPair"


def test_diff_explicit_spacing_used():
    res = synthesize_rules(
        _profile_dict(), _NCM,
        {"diff_pair_target_ohms": 90.0, "diff_pair_spacing_mils": 12.0})
    direct = trace_width_for_impedance(
        90.0, "microstrip_diff", 6.0, dielectric_constant=4.0,
        copper_oz=1.0, spacing_mils=12.0)
    assert _rule(res, "Width_differential")["value"] == \
        math.ceil(direct.width_mils)
    assert _rule(res, "DiffPair_Gap")["value"] == 12


def test_diff_spacing_below_min_gap_clamped():
    res = synthesize_rules(
        _profile_dict(), _NCM,
        {"diff_pair_target_ohms": 90.0, "diff_pair_spacing_mils": 2.0})
    assert _rule(res, "DiffPair_Gap")["value"] == 8
    assert any("below the fab minimum gap" in n for n in res["notes"])
    # the width was solved at the CLAMPED spacing
    direct = trace_width_for_impedance(
        90.0, "microstrip_diff", 6.0, dielectric_constant=4.0,
        copper_oz=1.0, spacing_mils=8.0)
    assert _rule(res, "Width_differential")["value"] == \
        math.ceil(direct.width_mils)


def test_diff_high_target_width_clamped_with_note():
    res = synthesize_rules(
        _profile_dict(), _NCM, {"diff_pair_target_ohms": 150.0})
    direct = trace_width_for_impedance(
        150.0, "microstrip_diff", 6.0, dielectric_constant=4.0,
        copper_oz=1.0, spacing_mils=8.0)
    assert direct.feasible and direct.width_mils < 7.0  # fixture precondition
    assert _rule(res, "Width_differential")["value"] == 7
    assert any("clamped to 7 mil" in n and "impedance" in n
               for n in res["notes"])


def test_diff_infeasible_target_skipped_with_note():
    res = synthesize_rules(
        _profile_dict(), _NCM, {"diff_pair_target_ohms": 250.0})
    assert res["ok"] is True
    assert not [r for r in res["rules"]
                if r["name"].startswith(("Width_differential", "DiffPair"))]
    assert any("infeasible" in n for n in res["notes"])


def test_diff_skipped_without_target():
    res = synthesize_rules(_profile_dict(), _NCM)
    assert res["ok"] is True
    assert not [r for r in res["rules"] if "diff" in r["name"].lower()]
    assert any("diff_pair_target_ohms" in n for n in res["notes"])


def test_diff_skipped_without_stackup():
    res = synthesize_rules(
        _profile_dict(stackups=[]), _NCM, {"diff_pair_target_ohms": 90.0})
    assert res["ok"] is True
    assert not [r for r in res["rules"] if "Diff" in r["name"]]
    assert any("no stackup" in n for n in res["notes"])
    assert res["stackup"] is None
    assert res["stackup_ops"] == []


def test_diff_skipped_without_er():
    d = _profile_dict()
    del d["stackups"][0]["layers"][1]["er"]
    res = synthesize_rules(d, _NCM, {"diff_pair_target_ohms": 90.0})
    assert res["ok"] is True
    assert not [r for r in res["rules"] if "Diff" in r["name"]]
    assert any("no er" in n for n in res["notes"])


def test_diff_rejects_non_differential_geometry():
    res = synthesize_rules(
        _profile_dict(), _NCM,
        {"diff_pair_target_ohms": 90.0, "geometry": "microstrip"})
    assert res["ok"] is False
    assert "differential" in res["reason"]


def test_no_diff_options_needed_without_differential_class():
    ncm = {"VBUS": "power", "GND": "ground"}
    res = synthesize_rules(_profile_dict(), ncm)
    assert res["ok"] is True
    assert not any("diff_pair_target_ohms" in n for n in res["notes"])


# --- floors hold everywhere -----------------------------------------------------

def test_no_rule_below_profile_minimums():
    res = synthesize_rules(
        _profile_dict(), _NCM,
        {"class_current_a": {"power": 0.1, "signal": 0.05},
         "diff_pair_target_ohms": 150.0, "diff_pair_spacing_mils": 1.0})
    assert res["ok"] is True
    for r in res["rules"]:
        if r["rule_type"] == "width":
            assert r["value"] >= 7, r
        elif r["rule_type"] == "clearance":
            assert r["value"] >= 8, r
        elif r["rule_type"] == "via_size":
            assert r["value"] >= 9, r
        elif r["rule_type"] == "differential_pairs":
            assert r["value"] >= 8, r


# --- stackup ops ----------------------------------------------------------------

def test_stackup_ops_two_layer():
    res = synthesize_rules(_profile_dict(), _NCM)
    assert res["stackup"] == "test-2L"
    ops = res["stackup_ops"]
    assert [op["layer"] for op in ops] == ["TopLayer", "BottomLayer"]
    top = ops[0]
    assert top["name"] == "Top"
    assert top["copper_thickness_mils"] == 1        # round(1.4)
    assert top["dielectric_type"] == "core"
    assert top["dielectric_height_mils"] == 6
    assert top["dielectric_constant"] == 4.0
    bottom = ops[1]
    assert bottom["name"] == "Bottom"
    assert "dielectric_type" not in bottom          # nothing below it


def test_stackup_ops_four_layer_names_and_spans():
    d = _profile_dict(copper_layer_counts=[4], stackups=[{
        "name": "test-4L",
        "layers": [
            {"name": "Top", "kind": "copper", "thickness_mils": 1.4,
             "copper_oz": 1.0},
            {"name": "PP1", "kind": "prepreg", "thickness_mils": 2.0,
             "er": 3.8},
            {"name": "Core1", "kind": "core", "thickness_mils": 4.0,
             "er": 4.4},
            {"name": "Mid1", "kind": "copper", "thickness_mils": 1.4,
             "copper_oz": 1.0},
            {"name": "Core2", "kind": "core", "thickness_mils": 20.0,
             "er": 4.4},
            {"name": "Mid2", "kind": "copper", "thickness_mils": 1.4,
             "copper_oz": 1.0},
            {"name": "PP2", "kind": "prepreg", "thickness_mils": 6.0,
             "er": 3.8},
            {"name": "Bottom", "kind": "copper", "thickness_mils": 1.4,
             "copper_oz": 1.0},
        ]}])
    res = synthesize_rules(d, _NCM)
    ops = res["stackup_ops"]
    assert [op["layer"] for op in ops] == \
        ["TopLayer", "MidLayer1", "MidLayer2", "BottomLayer"]
    # combined 2-ply span under the top copper: 6 mil, weighted er 4.2
    assert ops[0]["dielectric_height_mils"] == 6
    assert ops[0]["dielectric_constant"] == 4.2
    assert any("combined" in n for n in res["notes"])


def test_stackup_selected_by_name():
    d = _profile_dict()
    d["stackups"].append({
        "name": "test-2L-thick",
        "layers": [
            {"name": "Top", "kind": "copper", "thickness_mils": 1.4,
             "copper_oz": 1.0},
            {"name": "Core", "kind": "core", "thickness_mils": 40.0,
             "er": 4.0},
            {"name": "Bottom", "kind": "copper", "thickness_mils": 1.4,
             "copper_oz": 1.0},
        ]})
    res = synthesize_rules(d, _NCM, {"stackup": "test-2L-thick",
                                     "diff_pair_target_ohms": 90.0})
    assert res["stackup"] == "test-2L-thick"
    assert res["stackup_ops"][0]["dielectric_height_mils"] == 40
    direct = trace_width_for_impedance(
        90.0, "microstrip_diff", 40.0, dielectric_constant=4.0,
        copper_oz=1.0, spacing_mils=8.0)
    assert _rule(res, "Width_differential")["value"] == \
        math.ceil(direct.width_mils)


def test_unknown_stackup_name_rejected():
    res = synthesize_rules(_profile_dict(), _NCM, {"stackup": "nope"})
    assert res["ok"] is False
    assert "nope" in res["reason"]


# --- input shapes ---------------------------------------------------------------

def test_unknown_option_rejected():
    res = synthesize_rules(_profile_dict(), _NCM, {"min_track": 5})
    assert res["ok"] is False
    assert "min_track" in res["reason"]


def test_invalid_profile_propagates():
    res = synthesize_rules(_profile_dict(min_track_mils=-1.0), _NCM)
    assert res["ok"] is False
    assert "invalid fab profile" in res["reason"]


def test_bad_net_class_map_rejected():
    res = synthesize_rules(_profile_dict(), ["GND"])
    assert res["ok"] is False


def test_accepts_net_class_report():
    def _net(name, **kw):
        return Net(name=name, pins=[PinRef(refdes="U1", pin="1"),
                                    PinRef(refdes="U2", pin="1")], **kw)
    plan = DesignPlan(
        spec="rs", summary="rule synthesis plan", sheets=[Sheet(name="main")],
        zones=[], parts=[Part(refdes="U1", lib_ref="IC"),
                         Part(refdes="U2", lib_ref="IC")],
        nets=[_net("VBUS", is_power=True), _net("GND", is_ground=True),
              _net("USB_DP", role="differential"),
              _net("USB_DM", role="differential")])
    report = classify_nets(plan)
    res = synthesize_rules(
        _profile_dict(), report,
        {"class_current_a": {"power": 3.0}, "diff_pair_target_ohms": 90.0})
    assert res["ok"] is True
    assert _rule(res, "Width_power")
    assert _rule(res, "Width_differential")


def test_empty_net_class_map_gives_fab_floors_only():
    res = synthesize_rules(_profile_dict(), {})
    assert res["ok"] is True
    assert {r["name"] for r in res["rules"]} == \
        {"Clearance_Fab_Min", "Width_Fab_Min", "Via_Fab_Min_Drill"}


def test_determinism():
    opts = {"class_current_a": {"power": 3.0, "high_current": 5.0},
            "diff_pair_target_ohms": 90.0}
    ncm = dict(_NCM, IL="high_current")
    a = synthesize_rules(_profile_dict(), ncm, opts)
    b = synthesize_rules(_profile_dict(), ncm, opts)
    assert a == b
