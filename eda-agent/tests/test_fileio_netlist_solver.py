# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the geometric netlist solver (roadmap V1).

All synthetic: small hand-built geometries with a known expected netlist, so
CI needs no schematic fixture. The solver is additionally validated end-to-end
against a live-Altium compiled netlist during development (a 555 astable,
24/24 nodes), but that file is not committed.
"""

from __future__ import annotations

from eda_agent.fileio.netlist_solver import (
    pin_electrical_end,
    solve_nets,
)


def _pin(component, pin, x, y):
    return {"component": component, "pin": pin, "x": x, "y": y}


def _net_of(res, comp, pin):
    return res["pin_nets"].get(f"{comp}.{pin}")


def test_pin_electrical_end_directions():
    assert pin_electrical_end(10, 10, 20, 0) == (30, 10)    # right
    assert pin_electrical_end(10, 10, 20, 90) == (10, 30)   # up
    assert pin_electrical_end(10, 10, 20, 180) == (-10, 10)  # left
    assert pin_electrical_end(10, 10, 20, 270) == (10, -10)  # down


def test_two_pins_on_one_wire_are_one_net():
    res = solve_nets(
        [_pin("A", "1", 10, 0), _pin("B", "1", 30, 0)],
        [{"x1": 10, "y1": 0, "x2": 30, "y2": 0}])
    assert _net_of(res, "A", "1") == _net_of(res, "B", "1")
    assert len(res["nets"]) == 1


def test_t_junction_connects_endpoint_on_span():
    # Vertical wire's endpoint lands on the horizontal wire's span (a T).
    res = solve_nets(
        [_pin("A", "1", 0, 0), _pin("B", "1", 40, 0), _pin("C", "1", 20, 20)],
        [{"x1": 0, "y1": 0, "x2": 40, "y2": 0},
         {"x1": 20, "y1": 0, "x2": 20, "y2": 20}])
    assert _net_of(res, "A", "1") == _net_of(res, "B", "1") == _net_of(res, "C", "1")
    assert len(res["nets"]) == 1


def test_pin_tap_on_wire_span_connects():
    # A pin ending in the MIDDLE of a wire taps that net.
    res = solve_nets(
        [_pin("A", "1", 0, 0), _pin("B", "1", 40, 0), _pin("C", "1", 20, 0)],
        [{"x1": 0, "y1": 0, "x2": 40, "y2": 0}])
    assert len({_net_of(res, c, "1") for c in "ABC"}) == 1


def test_junction_dot_connects_a_crossing():
    # Two wires cross mid-span; a junction dot at the crossing joins them.
    res = solve_nets(
        [_pin("A", "1", 0, 0), _pin("B", "1", 40, 0),
         _pin("C", "1", 20, -20), _pin("D", "1", 20, 20)],
        [{"x1": 0, "y1": 0, "x2": 40, "y2": 0},
         {"x1": 20, "y1": -20, "x2": 20, "y2": 20}],
        junctions=[{"x": 20, "y": 0}])
    assert len({_net_of(res, c, "1") for c in "ABCD"}) == 1


def test_crossing_without_junction_stays_separate():
    # The anti-over-merge guarantee: a bare crossing (no junction, no shared
    # endpoint) must NOT merge the two wires.
    res = solve_nets(
        [_pin("A", "1", 0, 0), _pin("B", "1", 40, 0),
         _pin("C", "1", 20, -20), _pin("D", "1", 20, 20)],
        [{"x1": 0, "y1": 0, "x2": 40, "y2": 0},
         {"x1": 20, "y1": -20, "x2": 20, "y2": 20}])
    assert _net_of(res, "A", "1") == _net_of(res, "B", "1")
    assert _net_of(res, "C", "1") == _net_of(res, "D", "1")
    assert _net_of(res, "A", "1") != _net_of(res, "C", "1")
    assert len(res["nets"]) == 2


def test_power_port_names_and_binds_net():
    res = solve_nets(
        [_pin("A", "1", 0, 0)],
        [{"x1": 0, "y1": 0, "x2": 20, "y2": 0}],
        power_ports=[{"x": 20, "y": 0, "name": "GND"}])
    assert _net_of(res, "A", "1") == "GND"


def test_net_label_names_net():
    res = solve_nets(
        [_pin("A", "1", 0, 0), _pin("B", "1", 20, 0)],
        [{"x1": 0, "y1": 0, "x2": 20, "y2": 0}],
        net_labels=[{"x": 10, "y": 0, "name": "SIG"}])
    assert _net_of(res, "A", "1") == "SIG"


def test_auto_name_uses_alphabetically_first_component():
    # Unnamed net auto-names after the first component's pin (C1 before R2).
    res = solve_nets(
        [_pin("R2", "1", 0, 0), _pin("C1", "1", 20, 0)],
        [{"x1": 0, "y1": 0, "x2": 20, "y2": 0}])
    assert _net_of(res, "C1", "1") == "NetC1_1"


def test_same_name_labels_connect_across_disconnected_wires():
    # Altium connects by NAME: two "SW" labels on physically separate wires
    # are one net, with no wire between them.
    res = solve_nets(
        [_pin("A", "1", 0, 0), _pin("B", "1", 100, 0)],
        [{"x1": 0, "y1": 0, "x2": 20, "y2": 0},
         {"x1": 100, "y1": 0, "x2": 120, "y2": 0}],
        net_labels=[{"x": 20, "y": 0, "name": "SW"},
                    {"x": 120, "y": 0, "name": "SW"}])
    assert _net_of(res, "A", "1") == _net_of(res, "B", "1") == "SW"
    assert len(res["nets"]) == 1


def test_same_name_power_ports_connect_across_sheet():
    # A GND port at each ground pin ties them together with no wire between.
    res = solve_nets(
        [_pin("A", "1", 0, 0), _pin("B", "1", 200, 0), _pin("C", "1", 400, 0)],
        [{"x1": 0, "y1": 0, "x2": 20, "y2": 0},
         {"x1": 200, "y1": 0, "x2": 220, "y2": 0},
         {"x1": 400, "y1": 0, "x2": 420, "y2": 0}],
        power_ports=[{"x": 20, "y": 0, "name": "GND"},
                     {"x": 220, "y": 0, "name": "GND"},
                     {"x": 420, "y": 0, "name": "GND"}])
    assert (_net_of(res, "A", "1") == _net_of(res, "B", "1")
            == _net_of(res, "C", "1") == "GND")
    assert len(res["nets"]["GND"]) == 3


def test_different_named_nets_stay_separate():
    # By-name merge must only merge the SAME name, never distinct names.
    res = solve_nets(
        [_pin("A", "1", 0, 0), _pin("B", "1", 100, 0)],
        [{"x1": 0, "y1": 0, "x2": 20, "y2": 0},
         {"x1": 100, "y1": 0, "x2": 120, "y2": 0}],
        net_labels=[{"x": 20, "y": 0, "name": "SDA"},
                    {"x": 120, "y": 0, "name": "SCL"}])
    assert _net_of(res, "A", "1") != _net_of(res, "B", "1")
    assert res["name_conflicts"] == []


def test_isolated_pin_is_its_own_single_pin_net():
    # A pin touching no wire forms a lone net -- the raw material for a
    # future floating-pin / single-pin-net ERC check.
    res = solve_nets(
        [_pin("A", "1", 0, 0), _pin("B", "1", 30, 0)],
        [{"x1": 0, "y1": 0, "x2": 20, "y2": 0}])  # wire misses B (ends at 20)
    assert _net_of(res, "A", "1") != _net_of(res, "B", "1")
    assert len(res["nets"]["NetB_1"]) == 1
