# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the DRC-feedback repair planner (route/repair.py)."""

from __future__ import annotations

from eda_agent.route.repair import (
    BUCKETS,
    NUDGE_STEP_MILS,
    classify_violations,
    plan_drc_repairs,
    plan_repairs,
)


# ---------------------------------------------------------------------------
# Synthetic violation builders, mirroring BuildViolationJson's shape.
# ---------------------------------------------------------------------------

def _prim(type_: str = "", net: str = "", x: int | None = None,
          y: int | None = None, layer: str = "Top Layer") -> dict:
    prim: dict = {"detail": f"{type_} detail", "type": type_, "net": net,
                  "layer": layer}
    if x is not None:
        prim["x_mils"] = x
    if y is not None:
        prim["y_mils"] = y
    return prim


def _violation(rule: str, name: str = "", description: str = "",
               p1: dict | None = None, p2: dict | None = None,
               x: int = 0, y: int = 0) -> dict:
    return {
        "name": name or rule,
        "description": description,
        "rule": rule,
        "x_mils": x, "y_mils": y, "layer": "Top Layer",
        "primitive1": p1 if p1 is not None else _prim(),
        "primitive2": p2 if p2 is not None else _prim(),
    }


def _net_clearance(net1: str, net2: str) -> dict:
    return _violation(
        "Clearance",
        name="Clearance Constraint Violation",
        description=f"Clearance Constraint: Between Track on Top Layer and "
                    f"Track on Top Layer Net {net1} and Net {net2}",
        p1=_prim("Track", net1, 100, 100),
        p2=_prim("Track", net2, 105, 100),
    )


def _pad_clearance(net: str, track_x: int = 110, track_y: int = 100,
                   pad_x: int | None = 100, pad_y: int | None = 100) -> dict:
    return _violation(
        "Clearance",
        name="Clearance Constraint Violation",
        description=f"Clearance Constraint: Between Track Net {net} and Pad",
        p1=_prim("Track", net, track_x, track_y),
        p2=_prim("Pad", "GND", pad_x, pad_y),
    )


def _unrouted(net: str) -> dict:
    return _violation(
        "UnRoutedNet",
        name="Un-Routed Net Constraint Violation",
        description=f"Un-Routed Net Constraint: Net {net}",
        p1=_prim("Pad", net, 200, 200),
        p2=_prim("Pad", net, 400, 200),
    )


def _antenna(net: str) -> dict:
    return _violation(
        "NetAntennae",
        name="Net Antennae Violation",
        description=f"Net Antennae: Track on Top Layer Net {net}",
        p1=_prim("Track", net, 300, 300),
        p2=_prim(),
    )


def _width(net: str, description: str) -> dict:
    return _violation(
        "Width",
        name="Width Constraint Violation",
        description=description,
        p1=_prim("Track", net, 50, 50),
        p2=_prim(),
    )


def _unknown(rule: str = "Acute Angle") -> dict:
    return _violation(
        rule,
        name=f"{rule} Violation",
        description=f"{rule}: Track on Top Layer",
        p1=_prim("Track", "NetX", 10, 10),
        p2=_prim(),
    )


# ---------------------------------------------------------------------------
# classify_violations
# ---------------------------------------------------------------------------

class TestClassify:
    def test_every_bucket_exists_and_total_matches(self):
        payload = {
            "violation_count": 6,
            "violations": [
                _net_clearance("A", "B"),
                _pad_clearance("A"),
                _unrouted("C"),
                _antenna("D"),
                _width("E", "Actual Width = 8mil Min = 10mil"),
                _unknown(),
            ],
        }
        result = classify_violations(payload)
        assert result["ok"] is True
        assert set(result["buckets"]) == set(BUCKETS)
        assert result["counts"] == {
            "net_clearance": 1, "pad_clearance": 1, "unrouted": 1,
            "antenna": 1, "width": 1, "other": 1,
        }
        assert result["total"] == 6

    def test_accepts_bare_list(self):
        result = classify_violations([_unrouted("N1")])
        assert result["ok"] is True
        assert result["counts"]["unrouted"] == 1

    def test_track_track_clearance_is_net_clearance(self):
        result = classify_violations([_net_clearance("SIG1", "SIG2")])
        assert result["counts"]["net_clearance"] == 1

    def test_track_pad_clearance_is_pad_clearance(self):
        result = classify_violations([_pad_clearance("SIG1")])
        assert result["counts"]["pad_clearance"] == 1

    def test_via_component_clearance_is_pad_clearance(self):
        v = _violation(
            "Clearance", description="Clearance Constraint",
            p1=_prim("Via", "SIG1", 10, 10),
            p2=_prim("Component", "", 20, 10),
        )
        assert classify_violations([v])["counts"]["pad_clearance"] == 1

    def test_clearance_unknown_types_two_nets_is_net_clearance(self):
        v = _violation(
            "Clearance", description="Clearance Constraint",
            p1=_prim("", "A", 10, 10), p2=_prim("", "B", 20, 10),
        )
        assert classify_violations([v])["counts"]["net_clearance"] == 1

    def test_clearance_no_nets_no_types_is_other(self):
        v = _violation("Clearance", description="Clearance Constraint",
                       p1=_prim(), p2=_prim())
        assert classify_violations([v])["counts"]["other"] == 1

    def test_unknown_rule_goes_to_other(self):
        result = classify_violations([_unknown("Mystery Rule")])
        assert result["counts"]["other"] == 1

    def test_empty_input(self):
        result = classify_violations([])
        assert result["ok"] is True
        assert result["total"] == 0
        assert all(result["counts"][b] == 0 for b in BUCKETS)

    def test_non_list_input_fails(self):
        result = classify_violations("garbage")
        assert result["ok"] is False
        assert "reason" in result

    def test_non_dict_entry_fails(self):
        result = classify_violations([42])
        assert result["ok"] is False
        assert "violations[0]" in result["reason"]

    def test_payload_dict_without_violations_key(self):
        result = classify_violations({"violation_count": 0})
        assert result["ok"] is True
        assert result["total"] == 0

    def test_missing_primitives_does_not_crash(self):
        v = {"name": "Clearance Constraint Violation", "rule": "Clearance",
             "description": "Clearance Constraint"}
        result = classify_violations([v])
        assert result["ok"] is True
        assert result["counts"]["other"] == 1


# ---------------------------------------------------------------------------
# plan_repairs
# ---------------------------------------------------------------------------

def _plan(violations: list[dict], max_rounds: int = 5) -> dict:
    classified = classify_violations(violations)
    assert classified["ok"] is True
    plan = plan_repairs(classified, max_rounds=max_rounds)
    assert plan["ok"] is True
    return plan


class TestPlanRepairs:
    def test_empty_input_is_idempotent(self):
        first = plan_repairs(classify_violations([]))
        second = plan_repairs(classify_violations([]))
        assert first == second
        assert first["actions"] == []
        assert first["rounds_used"] == 0
        assert first["ripped_nets"] == []

    def test_worst_offender_ripped_first(self):
        # B touches 2 violations, D and E one each: rip B, then the D/E
        # conflict's worst (tie -> larger name, mirroring the pipeline cull).
        plan = _plan([
            _net_clearance("A", "B"),
            _net_clearance("B", "C"),
            _net_clearance("D", "E"),
        ])
        rips = [a for a in plan["actions"] if a["action"] == "rip_and_reroute"]
        assert [a["net"] for a in rips] == ["B", "E"]
        assert plan["rounds_used"] == 2
        assert not any(a["action"] == "escalate" for a in plan["actions"])

    def test_tie_broken_deterministically_by_name(self):
        plan = _plan([_net_clearance("X", "Y")])
        rips = [a for a in plan["actions"] if a["action"] == "rip_and_reroute"]
        # Equal counts: max((count, name)) picks the larger name.
        assert rips[0]["net"] == "Y"

    def test_round_bounding_emits_escalate(self):
        plan = _plan([
            _net_clearance("A", "B"),
            _net_clearance("B", "C"),
            _net_clearance("D", "E"),
        ], max_rounds=1)
        rips = [a for a in plan["actions"] if a["action"] == "rip_and_reroute"]
        assert [a["net"] for a in rips] == ["B"]
        assert plan["rounds_used"] == 1
        escalates = [a for a in plan["actions"] if a["action"] == "escalate"]
        assert len(escalates) == 1
        assert "D" in escalates[0]["reason"] and "E" in escalates[0]["reason"]

    def test_zero_rounds_escalates_everything(self):
        plan = _plan([_net_clearance("A", "B")], max_rounds=0)
        assert plan["rounds_used"] == 0
        assert plan["ripped_nets"] == []
        assert [a["action"] for a in plan["actions"]] == ["escalate"]

    def test_one_rip_can_clear_many_violations(self):
        # B is on every violation: one round suffices.
        plan = _plan([
            _net_clearance("A", "B"),
            _net_clearance("B", "C"),
            _net_clearance("B", "D"),
        ], max_rounds=1)
        assert plan["rounds_used"] == 1
        assert plan["ripped_nets"] == ["B"]
        assert not any(a["action"] == "escalate" for a in plan["actions"])

    def test_unrouted_nets_get_reroute_sorted(self):
        plan = _plan([_unrouted("ZED"), _unrouted("ALPHA")])
        rips = [a for a in plan["actions"] if a["action"] == "rip_and_reroute"]
        assert [a["net"] for a in rips] == ["ALPHA", "ZED"]

    def test_antenna_gets_rip(self):
        plan = _plan([_antenna("STUBNET")])
        assert plan["ripped_nets"] == ["STUBNET"]

    def test_net_never_ripped_twice(self):
        # Same net unrouted AND with an antenna: one action only.
        plan = _plan([_unrouted("N1"), _antenna("N1")])
        rips = [a for a in plan["actions"] if a["action"] == "rip_and_reroute"]
        assert len(rips) == 1
        assert plan["ripped_nets"] == ["N1"]

    def test_single_pad_clearance_becomes_nudge_x(self):
        # Track at (110,100), pad at (100,100): push +x by one step.
        plan = _plan([_pad_clearance("SIG", track_x=110, track_y=100)])
        nudges = [a for a in plan["actions"] if a["action"] == "nudge"]
        assert len(nudges) == 1
        assert nudges[0]["net"] == "SIG"
        assert (nudges[0]["dx"], nudges[0]["dy"]) == (NUDGE_STEP_MILS, 0)
        assert (nudges[0]["x_mils"], nudges[0]["y_mils"]) == (110, 100)

    def test_nudge_dominant_axis_negative_y(self):
        # Track below the pad: push -y.
        plan = _plan([_pad_clearance("SIG", track_x=100, track_y=80)])
        nudges = [a for a in plan["actions"] if a["action"] == "nudge"]
        assert (nudges[0]["dx"], nudges[0]["dy"]) == (0, -NUDGE_STEP_MILS)

    def test_repeated_pad_clearance_on_net_becomes_rip(self):
        plan = _plan([
            _pad_clearance("SIG", track_x=110),
            _pad_clearance("SIG", track_x=300, track_y=300, pad_x=290,
                           pad_y=300),
        ])
        assert plan["ripped_nets"] == ["SIG"]
        assert not any(a["action"] == "nudge" for a in plan["actions"])

    def test_pad_clearance_missing_geometry_falls_back_to_rip(self):
        plan = _plan([_pad_clearance("SIG", pad_x=None, pad_y=None)])
        assert plan["ripped_nets"] == ["SIG"]

    def test_pad_clearance_coincident_geometry_falls_back_to_rip(self):
        plan = _plan([_pad_clearance("SIG", track_x=100, track_y=100,
                                     pad_x=100, pad_y=100)])
        assert plan["ripped_nets"] == ["SIG"]

    def test_pad_vs_pad_clearance_escalates(self):
        v = _violation(
            "Clearance", description="Clearance Constraint",
            p1=_prim("Pad", "A", 10, 10), p2=_prim("Pad", "B", 12, 10),
        )
        plan = _plan([v])
        escalates = [a for a in plan["actions"] if a["action"] == "escalate"]
        assert len(escalates) == 1
        assert "placement" in escalates[0]["reason"]

    def test_ripped_net_suppresses_its_nudge(self):
        # SIG is the clearance worst offender AND has a pad conflict:
        # the rip supersedes the nudge.
        plan = _plan([
            _net_clearance("SIG", "A"),
            _net_clearance("SIG", "B"),
            _pad_clearance("SIG"),
        ])
        assert "SIG" in plan["ripped_nets"]
        assert not any(a["action"] == "nudge" for a in plan["actions"])

    def test_width_below_min_widens(self):
        plan = _plan([_width(
            "PWR", "Width Constraint: Actual Width = 8mil "
                   "Acceptable Min = 10mil, Max = 50mil")])
        widths = [a for a in plan["actions"] if a["action"] in ("widen",
                                                                "narrow")]
        assert widths == [{"action": "widen", "net": "PWR",
                           "reason": "width constraint violation"}]

    def test_width_above_max_narrows(self):
        plan = _plan([_width(
            "PWR", "Width Constraint: Actual Width = 60mil "
                   "Acceptable Min = 10mil, Max = 50mil")])
        widths = [a for a in plan["actions"] if a["action"] in ("widen",
                                                                "narrow")]
        assert widths[0]["action"] == "narrow"

    def test_width_mm_units_parse(self):
        plan = _plan([_width(
            "PWR", "Width Constraint: Actual Width = 0.1mm "
                   "Min = 0.2mm, Max = 5.08mm")])
        widths = [a for a in plan["actions"] if a["action"] in ("widen",
                                                                "narrow")]
        assert widths[0]["action"] == "widen"

    def test_width_unparsable_defaults_to_widen(self):
        plan = _plan([_width("PWR", "Width Constraint: out of bounds")])
        widths = [a for a in plan["actions"] if a["action"] in ("widen",
                                                                "narrow")]
        assert widths[0]["action"] == "widen"

    def test_width_skipped_for_ripped_net(self):
        plan = _plan([
            _net_clearance("PWR", "A"),
            _net_clearance("PWR", "B"),
            _width("PWR", "Actual Width = 8mil Min = 10mil"),
        ])
        assert "PWR" in plan["ripped_nets"]
        assert not any(a["action"] in ("widen", "narrow")
                       for a in plan["actions"])

    def test_other_bucket_escalates_with_rule_names(self):
        plan = _plan([_unknown("Acute Angle"), _unknown("Hole Size")])
        escalates = [a for a in plan["actions"] if a["action"] == "escalate"]
        assert len(escalates) == 1
        assert "Acute Angle" in escalates[0]["reason"]
        assert "Hole Size" in escalates[0]["reason"]

    def test_action_order_rips_before_nudges_before_width(self):
        plan = _plan([
            _net_clearance("A", "B"),
            _pad_clearance("SIG"),
            _width("PWR", "Actual Width = 8mil Min = 10mil"),
        ])
        kinds = [a["action"] for a in plan["actions"]]
        assert kinds.index("rip_and_reroute") < kinds.index("nudge")
        assert kinds.index("nudge") < kinds.index("widen")

    def test_accepts_bare_buckets_mapping(self):
        classified = classify_violations([_unrouted("N1")])
        plan = plan_repairs(classified["buckets"])
        assert plan["ok"] is True
        assert plan["ripped_nets"] == ["N1"]

    def test_malformed_buckets_fails(self):
        result = plan_repairs("nope")
        assert result["ok"] is False
        assert "reason" in result

    def test_failed_classification_propagates(self):
        result = plan_repairs({"ok": False, "buckets": {},
                               "reason": "upstream"})
        assert result["ok"] is False

    def test_negative_max_rounds_fails(self):
        result = plan_repairs(classify_violations([]), max_rounds=-1)
        assert result["ok"] is False

    def test_non_integer_max_rounds_fails(self):
        result = plan_repairs(classify_violations([]), max_rounds=2.5)
        assert result["ok"] is False


# ---------------------------------------------------------------------------
# plan_drc_repairs (one-call wrapper)
# ---------------------------------------------------------------------------

class TestPlanDrcRepairs:
    def test_end_to_end_payload(self):
        payload = {
            "violation_count": 4,
            "violations": [
                _net_clearance("A", "B"),
                _unrouted("C"),
                _pad_clearance("D"),
                _unknown(),
            ],
        }
        plan = plan_drc_repairs(payload, max_rounds=3)
        assert plan["ok"] is True
        assert plan["counts"]["net_clearance"] == 1
        assert plan["counts"]["other"] == 1
        kinds = [a["action"] for a in plan["actions"]]
        assert "rip_and_reroute" in kinds
        assert "nudge" in kinds
        assert "escalate" in kinds

    def test_deterministic_across_calls(self):
        payload = [
            _net_clearance("A", "B"),
            _net_clearance("B", "C"),
            _unrouted("Z"),
            _antenna("Q"),
            _pad_clearance("M"),
            _width("W", "Actual Width = 8mil Min = 10mil"),
            _unknown(),
        ]
        assert plan_drc_repairs(payload) == plan_drc_repairs(payload)

    def test_classification_error_propagates(self):
        result = plan_drc_repairs(None)
        assert result["ok"] is False

    def test_empty_payload(self):
        plan = plan_drc_repairs({"violation_count": 0, "violations": []})
        assert plan["ok"] is True
        assert plan["actions"] == []
