# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for connectivity (ERC-style) review over a solved netlist (roadmap V1).

Synthetic: the checks consume a netlist dict, built here directly or via the
solver on hand-made geometry, so no schematic fixture is needed.
"""

from __future__ import annotations

from eda_agent.fileio.netlist_solver import solve_nets
from eda_agent.fileio.review import ERROR, WARNING, review_connectivity


def _pin(component, pin, x, y):
    return {"component": component, "pin": pin, "x": x, "y": y}


def test_single_pin_net_flagged_as_warning():
    # A-B connected by a wire; C is isolated (no wire) -> only C is lone.
    solved = solve_nets(
        [_pin("A", "1", 0, 0), _pin("B", "1", 20, 0), _pin("C", "1", 50, 50)],
        [{"x1": 0, "y1": 0, "x2": 20, "y2": 0}])
    sp = [f for f in review_connectivity(solved) if f["check"] == "single_pin_net"]
    assert len(sp) == 1
    assert sp[0]["severity"] == WARNING
    assert sp[0]["designator"] == "C.1"


def test_fully_connected_net_has_no_single_pin_finding():
    solved = solve_nets(
        [_pin("A", "1", 0, 0), _pin("B", "1", 20, 0)],
        [{"x1": 0, "y1": 0, "x2": 20, "y2": 0}])
    assert not [f for f in review_connectivity(solved)
                if f["check"] == "single_pin_net"]


def test_net_short_flagged_when_two_names_on_one_net():
    # GND and VCC ports on the two ends of one wire -> the rails are shorted.
    solved = solve_nets(
        [_pin("A", "1", 10, 0)],
        [{"x1": 0, "y1": 0, "x2": 20, "y2": 0}],
        power_ports=[{"x": 0, "y": 0, "name": "GND"},
                     {"x": 20, "y": 0, "name": "VCC"}])
    assert solved["name_conflicts"], "solver should record the name conflict"
    shorts = [f for f in review_connectivity(solved) if f["check"] == "net_short"]
    assert shorts and shorts[0]["severity"] == ERROR
    assert "GND" in shorts[0]["message"] and "VCC" in shorts[0]["message"]


def test_distinct_named_nets_are_not_a_short():
    # Two separate wires, each its own named net -> no conflict.
    solved = solve_nets(
        [_pin("A", "1", 0, 0), _pin("B", "1", 100, 0)],
        [{"x1": 0, "y1": 0, "x2": 20, "y2": 0},
         {"x1": 100, "y1": 0, "x2": 120, "y2": 0}],
        power_ports=[{"x": 20, "y": 0, "name": "GND"},
                     {"x": 120, "y": 0, "name": "VCC"}])
    assert solved["name_conflicts"] == []
    assert not [f for f in review_connectivity(solved) if f["check"] == "net_short"]


def test_empty_netlist_has_no_findings():
    assert review_connectivity({"nets": {}, "pin_nets": {},
                                "name_conflicts": []}) == []


def test_connectivity_is_opt_in_on_file_review():
    # The buck fixture is a broken emit (rail shorts). The DEFAULT review must
    # not surface them (component-level only, keeps its contract); the opt-in
    # must surface the real net_short defects.
    from pathlib import Path
    from eda_agent.fileio.review import review_schematic_file
    fixture = (Path(__file__).resolve().parent / "integration" / "fixtures"
               / "main.SchDoc")

    default = review_schematic_file(fixture)
    assert not [f for f in default["findings"] if f["check"] == "net_short"]

    with_conn = review_schematic_file(fixture, check_connectivity=True)
    assert [f for f in with_conn["findings"] if f["check"] == "net_short"]
